import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_network.agent_management import Agent, AgentRegistry, get_runtime
from agent_network.capture_management import packet_stats
from agent_network.comm_management import normalize_profile
from agent_network.experiment_manifest import (
    audit_session,
    create_manifest,
    finalize_manifest,
)
from agent_network.log_management import get_log_manager
from agent_network.native_capabilities import NativeCapabilityPolicy
from agent_network.scene_management import (
    AgentDef,
    SceneDefinition,
    get_api_config,
    get_scene_storage,
)
from agent_network.simulation_management import SimulationEventScheduler, state


router = APIRouter()
logger = get_log_manager()
_SCENES_DIR = Path("scenes")
_llm_config: Dict[str, str] = {}

_TOPOLOGY_NETWORK_FIELDS = ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit")
_TOPOLOGY_LINK_FIELDS = {
    "endpoint_a",
    "endpoint_b",
    "channel_id",
    *_TOPOLOGY_NETWORK_FIELDS,
}


class SimulationRunRequest(BaseModel):
    scene: str = ""
    seed: Optional[int] = None


def get_effective_llm_config() -> Dict[str, str]:
    config = get_api_config()
    config.update(_llm_config)
    return config


def _capture(
    created_cas: List[tuple],
    enabled: bool,
    requests_module,
    session_id: str = "",
    trace_id: str = "",
    network_profiles: Dict[str, List[Dict]] = None,
) -> Dict[str, Any]:
    ok = 0
    failed = 0
    agents = []
    for assignment, _ in created_cas:
        if assignment.status == "error" or not assignment.url:
            continue
        try:
            if enabled:
                response = requests_module.post(
                    f"{assignment.url}/capture/start",
                    json={
                        "agent_id": assignment.agent_id,
                        "runtime_container": assignment.container_name,
                        "runtime_container_id": assignment.container_id,
                        "runtime_ip": assignment.container_ip,
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "network_profiles": (network_profiles or {}).get(
                            assignment.agent_id, []
                        ),
                        "pcap_dir": "/app/data/pcap",
                        "interface": "any",
                    },
                    timeout=3,
                )
            else:
                response = requests_module.post(
                    f"{assignment.url}/capture/stop",
                    timeout=60,
                )
            body = (
                response.json()
                if response.status_code == 200
                else {
                    "status": "http_error",
                    "http_status": response.status_code,
                }
            )
            accepted = (
                {"started", "running"}
                if enabled
                else {"stopped", "not_running"}
            )
            success = (
                response.status_code == 200
                and body.get("status") in accepted
            )
            ok += int(success)
            failed += int(not success)
            agents.append(
                {
                    "agent_id": assignment.agent_id,
                    "container": assignment.container_name,
                    **body,
                }
            )
        except Exception as exc:
            failed += 1
            agents.append(
                {
                    "agent_id": assignment.agent_id,
                    "container": assignment.container_name,
                    "status": "error",
                    "error": str(exc),
                }
            )
    return {"success": ok, "failed": failed, "agents": agents}


def _configure_network(
    created_cas: List[tuple],
    topology: List[Dict],
    requests_module,
) -> Dict[str, Any]:
    agents = {
        assignment.agent_id.lower(): assignment
        for assignment, _ in created_cas
    }
    profile_maps = {agent_id: {} for agent_id in agents}
    validation_errors = []

    def add_profile(source: str, target: str, network: dict):
        profile = {
            "target_agent": target,
            "target_host": agents[target].container_name,
            "target_ip": agents[target].container_ip,
            **network,
        }
        previous = profile_maps[source].get(target)
        if previous and any(
            previous.get(field) != network.get(field)
            for field in _TOPOLOGY_NETWORK_FIELDS
        ):
            validation_errors.append(
                f"conflicting network profiles for {source}->{target}"
            )
            return
        profile_maps[source][target] = profile

    for edge in topology or []:
        endpoint_a = str(edge.get("endpoint_a", "")).lower()
        endpoint_b = str(edge.get("endpoint_b", "")).lower()
        if endpoint_a not in agents or endpoint_b not in agents:
            validation_errors.append(
                f"unknown topology endpoints: {endpoint_a}<->{endpoint_b}"
            )
            continue
        try:
            network = normalize_profile(
                {
                    field: edge.get(field, 0)
                    for field in _TOPOLOGY_NETWORK_FIELDS
                }
            )
        except ValueError as exc:
            validation_errors.append(
                f"{endpoint_a}<->{endpoint_b}: {exc}"
            )
            continue
        if not any(network.values()):
            continue
        add_profile(endpoint_a, endpoint_b, network)
        add_profile(endpoint_b, endpoint_a, network)

    profiles = {
        agent_id: list(items.values())
        for agent_id, items in profile_maps.items()
    }
    requested = sum(len(items) for items in profiles.values())

    if validation_errors:
        for assignment, _ in created_cas:
            try:
                requests_module.post(
                    f"{assignment.url}/network/clear",
                    json={"interface": "eth0"},
                    timeout=5,
                )
            except Exception:
                pass
        return {
            "requested_profiles": requested,
            "failed": len(validation_errors),
            "validation_errors": validation_errors,
            "agents": [],
        }

    results = []
    failed = 0
    for agent_id, assignment in agents.items():
        try:
            response = requests_module.post(
                f"{assignment.url}/network/configure",
                json={
                    "interface": "eth0",
                    "profiles": profiles[agent_id],
                },
                timeout=15,
            )
            body = (
                response.json()
                if response.status_code == 200
                else {
                    "status": "http_error",
                    "http_status": response.status_code,
                }
            )
        except Exception as exc:
            body = {"status": "error", "error": str(exc)}
        if profiles[agent_id] and body.get("status") != "configured":
            failed += 1
        results.append(
            {
                "agent_id": agent_id,
                "container": assignment.container_name,
                **body,
            }
        )

    if failed:
        for assignment, _ in created_cas:
            try:
                requests_module.post(
                    f"{assignment.url}/network/clear",
                    json={"interface": "eth0"},
                    timeout=5,
                )
            except Exception:
                pass

    return {
        "requested_profiles": requested,
        "failed": failed,
        "agents": results,
    }


def _capture_health(
    created_cas: List[tuple],
    requests_module,
) -> Dict[str, Any]:
    agents = []
    failed = 0
    for assignment, _ in created_cas:
        try:
            response = requests_module.get(
                f"{assignment.url}/capture/status",
                timeout=3,
            )
            body = (
                response.json()
                if response.status_code == 200
                else {
                    "status": "http_error",
                    "http_status": response.status_code,
                }
            )
        except Exception as exc:
            body = {"status": "error", "error": str(exc)}
        if body.get("status") != "running":
            failed += 1
        agents.append(
            {
                "agent_id": assignment.agent_id,
                "container": assignment.container_name,
                **body,
            }
        )
    return {"healthy": failed == 0, "failed": failed, "agents": agents}


def prepare_scene(scene_def: SceneDefinition, seed: int) -> Dict[str, Any]:
    AgentRegistry.reset()
    state.agent_logs.clear()
    logger.reset()

    from agent_network.comm_management import CommManager

    comm_manager = CommManager()
    for definition in scene_def.agents:
        agent = Agent(
            agent_id=definition.agent_id,
            role=definition.role,
            name=definition.name,
            core_goal=definition.core_goal,
            backend=definition.backend,
            skill_refs=definition.skill_refs,
            allowed_tools=definition.allowed_tools,
            native_capabilities=definition.native_capabilities,
        )
        agent.set_comm(comm_manager)
        agent.pending_task_descs = definition.tasks
        AgentRegistry.register(agent)
        agent.start()

    return {
        "agents": [
            agent.get_status()
            for agent in AgentRegistry.list_all()
        ],
        "agent_stats": AgentRegistry.get_stats(),
        "topology": scene_def.topology,
        "scene_key": scene_def.scene_key,
        "scene_title": scene_def.title,
        "network_mode": "a2a",
        "seed": seed,
    }


def run_simulation(
    config: Dict[str, str],
    scene_def: SceneDefinition,
    seed: int,
    simulation_run=None,
    capture_handler: Callable[..., Dict[str, Any]] | None = None,
    capture_health_handler: Callable[..., Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    if not scene_def:
        raise ValueError("scene_def is required")

    capture_handler = capture_handler or _capture
    capture_health_handler = capture_health_handler or _capture_health

    import requests as requests_module

    runtime = get_runtime()
    runtime.reset()
    resource_plan = getattr(simulation_run, "resource_plan", {}) or {}
    runtime_config = getattr(simulation_run, "runtime_config", None)
    control = getattr(simulation_run, "control", None)
    created_cas = []
    assign_errors = []

    for definition in scene_def.agents:
        assignment = runtime.assign_agent(
            agent_id=definition.agent_id,
            role=definition.role,
            name=definition.name,
            core_goal=definition.core_goal,
            backend=definition.backend,
            skill_refs=definition.skill_refs,
            allowed_tools=definition.allowed_tools,
            native_capabilities=definition.native_capabilities,
            scene_key=scene_def.scene_key,
            resource_limits=(resource_plan.get("agents") or {}).get(
                definition.agent_id.lower(), {}
            ),
        )
        created_cas.append((assignment, definition.tasks))
        if assignment.status == "error":
            assign_errors.append(
                {
                    "agent_id": assignment.agent_id,
                    "error": assignment.assign_error or "unknown",
                }
            )

    all_assignments = list(created_cas)
    created_cas = [
        (assignment, tasks)
        for assignment, tasks in created_cas
        if assignment.status != "error"
    ]
    for assignment, _ in created_cas:
        try:
            requests_module.post(
                f"{assignment.url}/reset",
                timeout=3,
            )
        except Exception:
            pass
    time.sleep(1)

    agent_directory = {
        assignment.agent_id.lower(): assignment.url
        for assignment, _ in created_cas
        if assignment.url
    }
    comm_matrix: Dict[str, set] = {}
    for edge in scene_def.topology or []:
        endpoint_a = str(edge.get("endpoint_a", "")).lower()
        endpoint_b = str(edge.get("endpoint_b", "")).lower()
        if endpoint_a and endpoint_b:
            comm_matrix.setdefault(endpoint_a, set()).add(endpoint_b)
            comm_matrix.setdefault(endpoint_b, set()).add(endpoint_a)

    for agent in AgentRegistry.list_all():
        if agent.comm:
            agent.comm.update_directory(agent_directory, comm_matrix)

    serialized_matrix = {
        source: sorted(targets)
        for source, targets in comm_matrix.items()
    }
    for assignment, _ in created_cas:
        try:
            response = requests_module.post(
                f"{assignment.url}/communication/configure",
                json={
                    "agent_id": assignment.agent_id,
                    "agent_name": assignment.name,
                    "agent_role": assignment.role,
                    "agent_directory": agent_directory,
                    "comm_matrix": serialized_matrix,
                },
                timeout=3,
            )
            response.raise_for_status()
        except Exception as exc:
            assign_errors.append(
                {
                    "agent_id": assignment.agent_id,
                    "error": f"communication configuration failed: {exc}",
                }
            )

    logger.start_session(scene_def.scene_key)
    session_id = getattr(logger, "_session_id", "")
    talk_id = (
        f"talk-{uuid.uuid4().hex[:12]}-"
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}"
    )
    state.reset_token_usage_state(session_id)
    experiment_agents = [
        {
            "agent_id": assignment.agent_id,
            "name": assignment.name,
            "role": assignment.role,
            "backend": assignment.backend,
            "runtime_container": assignment.container_name,
            "runtime_container_id": assignment.container_id,
            "runtime_ip": assignment.container_ip,
            "image_id": assignment.image_id,
            "native_capabilities": assignment.native_capabilities.to_dict(),
            "native_policy_sha256": assignment.native_capabilities.sha256,
        }
        for assignment, _ in all_assignments
    ]
    create_manifest(
        session_id=session_id,
        scene_name=scene_def.scene_key,
        scene_dir=_SCENES_DIR / scene_def.scene_key,
        trace_id=talk_id,
        seed=seed,
        agents=experiment_agents,
        llm_config=config,
        scheduler={
            "mode": "event_driven",
            "duration_seconds": getattr(
                runtime_config, "duration_seconds", 0
            ),
            "agent_timeout_seconds": getattr(
                runtime_config, "agent_timeout_seconds", 300
            ),
            "idle_timeout_seconds": getattr(
                runtime_config, "idle_timeout_seconds", 5
            ),
            "network_mode": getattr(runtime_config, "network_mode", "a2a"),
            "resource_plan": resource_plan,
        },
    )

    if assign_errors:
        rollback = runtime.force_stop_all()
        finalize_manifest(
            session_id,
            status="error",
            stop_reason="assignment_failed",
            assignment_errors=assign_errors,
            resource_rollback=rollback,
        )
        quality = audit_session(session_id, verify_hashes=True)
        return {
            "status": "error",
            "error": (
                "one or more Agent containers "
                "could not be assigned"
            ),
            "session_id": session_id,
            "assignment_errors": assign_errors,
            "resource_rollback": rollback,
            "quality": quality,
        }

    network_emulation = _configure_network(
        created_cas,
        scene_def.topology or [],
        requests_module,
    )
    logger.system(
        "network_emulation",
        "Agent network profiles configured",
        details=network_emulation,
    )
    if (
        network_emulation["requested_profiles"]
        and network_emulation["failed"]
    ):
        finalize_manifest(
            session_id,
            status="error",
            stop_reason="network_emulation_failed",
            network_emulation=network_emulation,
        )
        quality = audit_session(session_id, verify_hashes=True)
        return {
            "status": "error",
            "error": (
                "requested Agent network emulation "
                "could not be applied"
            ),
            "session_id": session_id,
            "network_emulation": network_emulation,
            "quality": quality,
        }

    state.simulation_active = True
    applied_network_profiles = {
        item["agent_id"]: item.get("profiles", [])
        for item in network_emulation.get("agents", [])
    }
    capture_start = capture_handler(
        created_cas,
        True,
        requests_module,
        session_id=session_id,
        trace_id=talk_id,
        network_profiles=applied_network_profiles,
    )
    logger.system(
        "capture_control",
        "full capture started",
        details={"session_id": session_id, **capture_start},
    )
    if capture_start["failed"]:
        capture_stop = capture_handler(
            created_cas,
            False,
            requests_module,
            session_id=session_id,
            trace_id=talk_id,
        )
        network_clear = _configure_network(
            created_cas,
            [],
            requests_module,
        )
        state.simulation_active = False
        finalize_manifest(
            session_id,
            status="error",
            stop_reason="capture_start_failed",
            network_emulation=network_emulation,
            capture_start=capture_start,
            capture_stop=capture_stop,
            network_clear=network_clear,
        )
        quality = audit_session(session_id, verify_hashes=True)
        return {
            "status": "error",
            "error": (
                "one or more Agent packet captures "
                "failed to start"
            ),
            "session_id": session_id,
            "capture_start": capture_start,
            "capture_stop": capture_stop,
            "network_clear": network_clear,
            "quality": quality,
        }

    scheduler = SimulationEventScheduler(simulation_run)
    simulation_run.scheduler = scheduler
    for assignment, tasks in created_cas:
        scheduler.enqueue(
            "initial_task",
            assignment.agent_id,
            {"task": "\n".join(task for task in tasks if task)},
        )
    scheduler_result = {
        "stop_reason": "idle_completed",
        "processed_event_count": 0,
        "failed_event_count": 0,
        "cancelled_event_count": 0,
        "events": [],
        "batches": [],
    }
    stop_reason = "idle_completed"
    network_clear = {
        "requested_profiles": 0,
        "failed": 0,
        "agents": [],
    }
    capture_health = {
        "healthy": True,
        "failed": 0,
        "agents": [],
    }
    run_error = ""

    try:
        def dispatch_events(events):
            context = {
                "scene": scene_def.scene_key,
                "agents": [
                    {
                        "id": assignment.agent_id,
                        "role": assignment.role,
                        "name": assignment.name,
                    }
                    for assignment, _ in created_cas
                ],
                "comm_matrix": {
                    key: list(value)
                    for key, value in comm_matrix.items()
                },
                "agent_directory": agent_directory,
                "talk": talk_id,
                "trace_id": talk_id,
                "simulation_seed": seed,
                "network_mode": getattr(runtime_config, "network_mode", "a2a"),
                "max_parallel_agents": int(
                    resource_plan.get("max_parallel_agents", 10)
                ),
                "agent_timeout_seconds": getattr(
                    runtime_config, "agent_timeout_seconds", 300
                ),
            }
            return runtime.run_events(events, context)

        def capture_is_healthy():
            nonlocal capture_health
            capture_health = capture_health_handler(
                created_cas,
                requests_module,
            )
            if not capture_health["healthy"]:
                logger.system(
                    "capture_health",
                    "Agent capture stopped unexpectedly",
                    details=capture_health,
                )
            return capture_health["healthy"]

        scheduler_result = scheduler.run_loop(
            dispatch_events,
            readiness_probe=runtime.ready_agent_ids,
            health_check=capture_is_healthy,
        )
        stop_reason = scheduler_result["stop_reason"]
    except Exception as exc:
        run_error = str(exc)
        stop_reason = "runtime_exception"
        logger.system(
            "simulation_runtime_error",
            "Simulation runtime failed",
            details={"error": run_error},
        )
    finally:
        state.simulation_active = False
        capture_stop = capture_handler(
            created_cas,
            False,
            requests_module,
            session_id=session_id,
            trace_id=talk_id,
        )
        logger.system(
            "capture_control",
            "full capture stopped",
            details={"session_id": session_id, **capture_stop},
        )
        network_clear = _configure_network(
            created_cas,
            [],
            requests_module,
        )
        logger.system(
            "network_emulation",
            "Agent network profiles cleared",
            details=network_clear,
        )
        forced = bool(control and control.force_stop_event.is_set())
        for assignment, _ in created_cas:
            if forced:
                runtime._set_status(assignment, "error")
            elif assignment.status != "error":
                runtime._set_status(assignment, "idle")

    controlled_stop = bool(control and control.stop_event.is_set())
    failed_stop = stop_reason in {
        "all_agents_failed",
        "capture_incomplete",
        "resource_limit_exceeded",
        "runtime_exception",
    }
    experiment_status = (
        "complete"
        if (
            not run_error
            and not controlled_stop
            and not failed_stop
            and capture_stop["failed"] == 0
        )
        else (
            "stopped"
            if controlled_stop and not run_error and not failed_stop
            else "error"
        )
    )
    finalize_manifest(
        session_id,
        status=experiment_status,
        stop_reason=stop_reason,
        processed_event_count=scheduler_result["processed_event_count"],
        failed_event_count=scheduler_result["failed_event_count"],
        cancelled_event_count=scheduler_result["cancelled_event_count"],
        events=scheduler_result["events"],
        network_emulation=network_emulation,
        network_clear=network_clear,
        capture_start=capture_start,
        capture_stop=capture_stop,
        error=run_error,
        native_children=[
            agent.get_status()
            for agent in AgentRegistry.list_all()
            if agent.runtime_kind != "managed"
        ],
    )
    quality = audit_session(session_id, verify_hashes=True)

    return {
        "status": (
            "error"
            if run_error or failed_stop
            else ("stopped" if controlled_stop else "completed")
        ),
        "error": run_error,
        "simulation_name": scene_def.title,
        "scene_key": scene_def.scene_key,
        "session_id": session_id,
        "seed": seed,
        "experiment_status": experiment_status,
        "quality": quality,
        "agents": [
            agent.get_status()
            for agent in AgentRegistry.list_all()
        ],
        "agent_stats": AgentRegistry.get_stats(),
        "packet_stats": packet_stats(session_id=session_id),
        "capture_health": capture_health,
        "processed_event_count": scheduler_result["processed_event_count"],
        "failed_event_count": scheduler_result["failed_event_count"],
        "cancelled_event_count": scheduler_result["cancelled_event_count"],
        "events": scheduler_result["events"],
        "stop_reason": stop_reason,
        "event_batches": scheduler_result["batches"],
        "topology": scene_def.topology,
        "comm_policy": {
            "mode": "direct",
            "matrix": {
                key: list(value)
                for key, value in comm_matrix.items()
            },
        },
        "agent_directory": agent_directory,
        "network_mode": "a2a",
        "network_emulation": network_emulation,
        "network_clear": network_clear,
        "assign_errors": assign_errors,
    }


def _normalize_backend(
    scene_name: str,
    role_id: str,
    backend: str,
) -> str:
    backend = (backend or "openclaw").strip()
    if backend == "brain":
        raise ValueError(
            f"Scene '{scene_name}' role '{role_id}' "
            "uses removed backend 'brain'."
        )
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(
            f"Scene '{scene_name}' role '{role_id}' "
            f"uses unsupported backend '{backend}'."
        )
    return backend


def _build_scene_from_folder(
    scene_name: str,
) -> SceneDefinition:
    # Kept only for internal callers while all parsing and validation are owned
    # by SceneStorage. The code below is unreachable legacy text and will be
    # removed when this compatibility function is deleted.
    return get_scene_storage().build_definition(scene_name)

    folder = _SCENES_DIR / scene_name
    meta = json.loads(
        (folder / "meta_and_roles.json").read_text(
            encoding="utf-8"
        )
    )
    instances = json.loads(
        (folder / "instances_and_skills.json").read_text(
            encoding="utf-8"
        )
    )
    topology_config = json.loads(
        (folder / "network_topology.json").read_text(
            encoding="utf-8"
        )
    )

    scenario_metadata = meta.get("scenario_metadata", {})
    title = scenario_metadata.get("title", scene_name)
    description = scenario_metadata.get("global_rules", "")

    state.current_scene_name = scene_name
    state.active_tools_module = None

    roles = meta.get("roles", {})
    containers = instances.get("container_instances", {})
    agents = []
    for role_id, role in roles.items():
        instance = containers.get(role_id, {})
        skill_refs = list(instance.get("skill_refs") or [])
        if not all(
            isinstance(item, str) and item
            for item in skill_refs
        ):
            raise ValueError(
                f"Scene '{scene_name}' role '{role_id}' "
                "skill_refs must contain non-empty strings."
            )
        skill_refs = list(dict.fromkeys(skill_refs))
        allowed_tools = instance.get("tool_refs") or []
        backend = _normalize_backend(
            scene_name,
            role_id,
            role.get("model_backbone", "openclaw"),
        )
        identity = (
            role.get("identity", "")
            or role.get("name", role_id)
        )
        agents.append(
            AgentDef(
                agent_id=role_id.lower(),
                role=identity,
                name=role.get("name", role_id),
                core_goal=role.get("core_goal", ""),
                backend=backend,
                skill_refs=skill_refs,
                allowed_tools=list(allowed_tools),
                native_capabilities=NativeCapabilityPolicy.from_dict(
                    instance.get("native_capabilities"), backend=backend
                ),
                tasks=[],
            )
        )

    raw_topology = topology_config.get("topology")
    if not isinstance(raw_topology, list):
        raise ValueError(
            f"Scene '{scene_name}' network_topology.json "
            "must contain a root-level topology array."
        )

    agent_ids = {
        agent.agent_id
        for agent in agents
    }
    channel_ids = set()
    topology_edges = []
    for index, edge in enumerate(raw_topology):
        if not isinstance(edge, dict):
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] "
                "must be an object."
            )

        unexpected = set(edge) - _TOPOLOGY_LINK_FIELDS
        missing = {
            "endpoint_a",
            "endpoint_b",
            "channel_id",
        } - set(edge)
        if unexpected:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] "
                f"has unsupported fields: {sorted(unexpected)}"
            )
        if missing:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] "
                f"is missing fields: {sorted(missing)}"
            )

        endpoint_a = str(edge["endpoint_a"]).strip().lower()
        endpoint_b = str(edge["endpoint_b"]).strip().lower()
        channel_id = str(edge["channel_id"]).strip()

        if (
            not endpoint_a
            or not endpoint_b
            or endpoint_a == endpoint_b
        ):
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] "
                "must connect two distinct endpoints."
            )

        unknown = {
            endpoint_a,
            endpoint_b,
        } - agent_ids
        if unknown:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] "
                f"references unknown agents: {sorted(unknown)}"
            )
        if not channel_id:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] "
                "channel_id must be non-empty."
            )
        if channel_id in channel_ids:
            raise ValueError(
                f"Scene '{scene_name}' contains duplicate "
                f"channel_id '{channel_id}'."
            )
        channel_ids.add(channel_id)

        network = normalize_profile(
            {
                field: edge.get(field, 0)
                for field in _TOPOLOGY_NETWORK_FIELDS
            }
        )
        topology_edges.append(
            {
                "endpoint_a": endpoint_a,
                "endpoint_b": endpoint_b,
                "channel_id": channel_id,
                **network,
            }
        )

    return SceneDefinition(
        scene_key=scene_name,
        title=title,
        description=description,
        agents=agents,
        topology=topology_edges,
    )


async def setup_simulation(
    req: SimulationRunRequest,
):
    del req
    raise RuntimeError(
        "legacy simulation lifecycle is removed; use SimulationManager"
    )


async def launch_simulation():
    raise RuntimeError(
        "legacy simulation lifecycle is removed; use SimulationManager"
    )


async def stop_simulation():
    raise RuntimeError(
        "legacy simulation lifecycle is removed; use SimulationManager"
    )


async def list_scenes():
    if not _SCENES_DIR.exists():
        return {"scenes": []}
    return {
        "scenes": [
            {"name": folder.name, "format": "folder"}
            for folder in sorted(
                _SCENES_DIR.iterdir(),
                key=lambda item: item.name.lower(),
            )
            if (
                folder.is_dir()
                and (folder / "meta_and_roles.json").exists()
            )
        ]
    }


async def scene_state_unified():
    return {
        "scene": state.current_scene_name,
        "running": state.simulation_active,
        "agents": [
            agent.get_status()
            for agent in AgentRegistry.list_all()
        ],
        "custom": None,
    }


async def read_scene(scene_name: str):
    folder = _SCENES_DIR / scene_name
    if not folder.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Scene '{scene_name}' not found",
        )

    files = {}
    for key in (
        "meta_and_roles",
        "instances_and_skills",
        "network_topology",
    ):
        path = folder / f"{key}.json"
        if path.exists():
            files[key] = json.loads(
                path.read_text(encoding="utf-8")
            )
    return {"name": scene_name, "files": files}
