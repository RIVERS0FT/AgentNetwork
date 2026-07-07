import asyncio
import json
import os
import uuid
import time
import random
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_network import state
from agent_network.agent_model import AgentRegistry, Agent
from agent_network.logger import get_logger
from agent_network.real_packet_store import packet_stats
from agent_network.network_emulation import normalize_profile
from agent_network.experiment_manifest import audit_session, create_manifest, finalize_manifest
from agent_network.scene_def import get_api_config, SceneDefinition, AgentDef
from agent_network.container_runtime import get_runtime

router = APIRouter()
logger = get_logger()
_SCENES_DIR = Path("scenes")
_llm_config: Dict[str, str] = {}
_pending_scene_def: Optional[SceneDefinition] = None
_pending_config: Dict[str, str] = {}
_comm_matrix: Dict[str, set] = {}
_pending_seed: int = 0

_TOPOLOGY_NETWORK_FIELDS = ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit")
_TOPOLOGY_LINK_FIELDS = {"endpoint_a", "endpoint_b", "channel_id", *_TOPOLOGY_NETWORK_FIELDS}


class SimulationRunRequest(BaseModel):
    scene: str = ""
    seed: Optional[int] = None


def _get_effective_llm_config() -> Dict[str, str]:
    config = get_api_config()
    config.update(_llm_config)
    return config


def _get_runtime_with_status_listener():
    runtime = get_runtime()
    if not hasattr(runtime, "_status_listener_set"):
        def on_status(agent_id, status):
            agent = AgentRegistry.get(agent_id)
            if agent:
                agent.status = status
        runtime.on_status_change = on_status
        runtime._status_listener_set = True
    return runtime


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
    for ca, _ in created_cas:
        if ca.status == "error" or not ca.url:
            continue
        try:
            if enabled:
                resp = requests_module.post(
                    f"{ca.url}/capture/start",
                    json={
                        "agent_id": ca.agent_id,
                        "runtime_container": ca.container_name,
                        "runtime_container_id": ca.container_id,
                        "runtime_ip": ca.container_ip,
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "network_profiles": (network_profiles or {}).get(ca.agent_id, []),
                        "pcap_dir": "/app/data/pcap",
                        "interface": "any",
                    },
                    timeout=3,
                )
            else:
                resp = requests_module.post(f"{ca.url}/capture/stop", timeout=60)
            body = resp.json() if resp.status_code == 200 else {"status": "http_error", "http_status": resp.status_code}
            accepted = {"started", "running"} if enabled else {"stopped", "not_running"}
            success = resp.status_code == 200 and body.get("status") in accepted
            ok += int(success)
            failed += int(not success)
            agents.append({"agent_id": ca.agent_id, "container": ca.container_name, **body})
        except Exception as exc:
            failed += 1
            agents.append({"agent_id": ca.agent_id, "container": ca.container_name, "status": "error", "error": str(exc)})
    return {"success": ok, "failed": failed, "agents": agents}


def _configure_network(created_cas: List[tuple], topology: List[Dict], requests_module) -> Dict[str, Any]:
    agents = {ca.agent_id.lower(): ca for ca, _ in created_cas}
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
            for field in ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit")
        ):
            validation_errors.append(f"conflicting network profiles for {source}->{target}")
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
            network = normalize_profile({
                field: edge.get(field, 0)
                for field in _TOPOLOGY_NETWORK_FIELDS
            })
        except ValueError as exc:
            validation_errors.append(f"{endpoint_a}<->{endpoint_b}: {exc}")
            continue
        if not any(network.values()):
            continue
        add_profile(endpoint_a, endpoint_b, network)
        add_profile(endpoint_b, endpoint_a, network)

    profiles = {agent_id: list(items.values()) for agent_id, items in profile_maps.items()}
    requested = sum(len(items) for items in profiles.values())

    if validation_errors:
        for ca, _ in created_cas:
            try:
                requests_module.post(f"{ca.url}/network/clear", json={"interface": "eth0"}, timeout=5)
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
    for agent_id, ca in agents.items():
        try:
            response = requests_module.post(
                f"{ca.url}/network/configure",
                json={"interface": "eth0", "profiles": profiles[agent_id]},
                timeout=15,
            )
            body = response.json() if response.status_code == 200 else {"status": "http_error", "http_status": response.status_code}
        except Exception as exc:
            body = {"status": "error", "error": str(exc)}
        if profiles[agent_id] and body.get("status") != "configured":
            failed += 1
        results.append({"agent_id": agent_id, "container": ca.container_name, **body})

    if failed:
        for ca, _ in created_cas:
            try:
                requests_module.post(f"{ca.url}/network/clear", json={"interface": "eth0"}, timeout=5)
            except Exception:
                pass
    return {"requested_profiles": requested, "failed": failed, "agents": results}


def _capture_health(created_cas: List[tuple], requests_module) -> Dict[str, Any]:
    agents = []
    failed = 0
    for ca, _ in created_cas:
        try:
            response = requests_module.get(f"{ca.url}/capture/status", timeout=3)
            body = response.json() if response.status_code == 200 else {"status": "http_error", "http_status": response.status_code}
        except Exception as exc:
            body = {"status": "error", "error": str(exc)}
        if body.get("status") != "running":
            failed += 1
        agents.append({"agent_id": ca.agent_id, "container": ca.container_name, **body})
    return {"healthy": failed == 0, "failed": failed, "agents": agents}


def _layout(agents: List[Any]) -> Dict[str, tuple]:
    return {a.agent_id: (random.uniform(60, 340), random.uniform(60, 340)) for a in agents}


def _setup_scene(scene_def: SceneDefinition) -> Dict[str, Any]:
    global _pending_scene_def
    AgentRegistry.reset()
    state.agent_logs.clear()
    logger.reset()
    from agent_network.comm import DirectBus
    direct_bus = DirectBus()
    pos = _layout(scene_def.agents)
    for ad in scene_def.agents:
        agent = Agent(
            agent_id=ad.agent_id,
            role=ad.role,
            name=ad.name,
            core_goal=ad.core_goal,
            backend=ad.backend,
            skill_refs=ad.skill_refs,
            allowed_tools=ad.allowed_tools,
        )
        agent.set_comm(direct_bus)
        agent.x, agent.y = pos.get(ad.agent_id, (100, 100))
        agent.pending_task_descs = ad.tasks
        AgentRegistry.register(agent)
        agent.start()
    _pending_scene_def = scene_def
    return {
        "agents": [a.get_status() for a in AgentRegistry.list_all()],
        "agent_stats": AgentRegistry.get_stats(),
        "topology": scene_def.topology,
        "scene_key": scene_def.scene_key,
        "scene_title": scene_def.title,
        "network_mode": "direct",
        "seed": _pending_seed,
    }


def _launch_containers(config: Dict[str, str], scene_def=None) -> Dict[str, Any]:
    global _comm_matrix
    if scene_def is None:
        scene_def = _pending_scene_def
    if not scene_def:
        return {"error": "No scene setup. Call /api/simulations/setup first."}

    import requests as _req
    runtime = _get_runtime_with_status_listener()
    runtime.reset()
    created_cas = []
    assign_errors = []

    for ad in scene_def.agents:
        ca = runtime.assign_agent(
            agent_id=ad.agent_id,
            role=ad.role,
            name=ad.name,
            core_goal=ad.core_goal,
            backend=ad.backend,
            skill_refs=ad.skill_refs,
            allowed_tools=ad.allowed_tools,
            scene_key=scene_def.scene_key,
        )
        created_cas.append((ca, ad.tasks))
        if ca.status == "error":
            assign_errors.append({"agent_id": ca.agent_id, "error": ca.assign_error or "unknown"})
        else:
            agent = AgentRegistry.get(ca.agent_id)
            if agent:
                agent.container_url = ca.url

    all_assignments = list(created_cas)
    created_cas = [(ca, tasks) for ca, tasks in created_cas if ca.status != "error"]
    for ca, _ in created_cas:
        try:
            _req.post(f"{ca.url}/reset", timeout=3)
        except Exception:
            pass
    time.sleep(1)

    agent_directory = {ca.agent_id.lower(): ca.url for ca, _ in created_cas if ca.url}
    _comm_matrix.clear()
    for edge in (scene_def.topology or []):
        endpoint_a = str(edge.get("endpoint_a", "")).lower()
        endpoint_b = str(edge.get("endpoint_b", "")).lower()
        if endpoint_a and endpoint_b:
            _comm_matrix.setdefault(endpoint_a, set()).add(endpoint_b)
            _comm_matrix.setdefault(endpoint_b, set()).add(endpoint_a)

    logger.start_session(scene_def.scene_key)
    session_id = getattr(logger, "_session_id", "")
    talk_id = f"talk-{uuid.uuid4().hex[:12]}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    state.reset_token_usage_state(session_id)
    experiment_agents = [
        {
            "agent_id": ca.agent_id,
            "name": ca.name,
            "role": ca.role,
            "backend": ca.backend,
            "runtime_container": ca.container_name,
            "runtime_container_id": ca.container_id,
            "runtime_ip": ca.container_ip,
            "image_id": ca.image_id,
        }
        for ca, _ in all_assignments
    ]
    create_manifest(
        session_id=session_id,
        scene_name=scene_def.scene_key,
        scene_dir=_SCENES_DIR / state.current_scene_name,
        trace_id=talk_id,
        seed=_pending_seed,
        agents=experiment_agents,
        llm_config=config,
        scheduler={
            "mode": "event_driven_rounds",
            "initial_task_delivery": "once",
            "max_rounds": state.termination_config.get("max_rounds", 20),
            "stalemate_rounds": state.termination_config.get("stalemate_rounds", 3),
        },
    )
    if assign_errors:
        finalize_manifest(
            session_id,
            status="error",
            stop_reason="assignment_failed",
            assignment_errors=assign_errors,
        )
        quality = audit_session(session_id, verify_hashes=True)
        return {
            "status": "error",
            "error": "one or more Agent containers could not be assigned",
            "session_id": session_id,
            "assignment_errors": assign_errors,
            "quality": quality,
        }
    network_emulation = _configure_network(created_cas, scene_def.topology or [], _req)
    logger.system("network_emulation", "Agent network profiles configured", details=network_emulation)
    if network_emulation["requested_profiles"] and network_emulation["failed"]:
        finalize_manifest(
            session_id,
            status="error",
            stop_reason="network_emulation_failed",
            network_emulation=network_emulation,
        )
        quality = audit_session(session_id, verify_hashes=True)
        return {
            "status": "error",
            "error": "requested Agent network emulation could not be applied",
            "session_id": session_id,
            "network_emulation": network_emulation,
            "quality": quality,
        }
    state.simulation_active = True
    applied_network_profiles = {
        item["agent_id"]: item.get("profiles", [])
        for item in network_emulation.get("agents", [])
    }
    capture_start = _capture(
        created_cas,
        True,
        _req,
        session_id=session_id,
        trace_id=talk_id,
        network_profiles=applied_network_profiles,
    )
    logger.system("capture_control", "full capture started", details={"session_id": session_id, **capture_start})
    if capture_start["failed"]:
        capture_stop = _capture(created_cas, False, _req, session_id=session_id, trace_id=talk_id)
        network_clear = _configure_network(created_cas, [], _req)
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
            "error": "one or more Agent packet captures failed to start",
            "session_id": session_id,
            "capture_start": capture_start,
            "capture_stop": capture_stop,
            "network_clear": network_clear,
            "quality": quality,
        }

    max_rounds = state.termination_config.get("max_rounds", 20)
    stalemate_threshold = state.termination_config.get("stalemate_rounds", 3)
    results_log = []
    silent_rounds = 0
    stop_reason = "hard_limit"
    state.simulation_stop_requested = False
    network_clear = {"requested_profiles": 0, "failed": 0, "agents": []}
    capture_health = {"healthy": True, "failed": 0, "agents": []}
    run_error = ""

    try:
        for round_num in range(max_rounds):
            if state.simulation_stop_requested:
                stop_reason = "user_stopped"
                break
            state.current_turn = round_num + 1
            context = {
                "round": state.current_turn,
                "total_rounds": max_rounds,
                "scene": scene_def.scene_key,
                "agents": [{"id": ca.agent_id, "role": ca.role, "name": ca.name} for ca, _ in created_cas],
                "tasks": {ca.agent_id: tasks for ca, tasks in created_cas} if round_num == 0 else {},
                "comm_matrix": {k: list(v) for k, v in _comm_matrix.items()},
                "agent_directory": agent_directory,
                "talk": talk_id,
                "trace_id": talk_id,
                "tick": state.current_turn,
                "simulation_seed": _pending_seed,
                "network_mode": "direct",
            }
            round_result = runtime.run_round(context)
            results_log.append(round_result)
            results = round_result.get("results", [])
            capture_health = _capture_health(created_cas, _req)
            if not capture_health["healthy"]:
                stop_reason = "capture_incomplete"
                logger.system("capture_health", "Agent capture stopped unexpectedly", details=capture_health)
                break
            if results and all(res.get("error") for res in results):
                stop_reason = "all_agents_failed"
                break
            meaningful = sum(len(res.get("application_events", [])) + len(res.get("tool_events", [])) + (1 if res.get("final_message") else 0) for res in results)
            silent_rounds = silent_rounds + 1 if meaningful == 0 else 0
            if silent_rounds >= stalemate_threshold:
                stop_reason = f"stalemate_{stalemate_threshold}_silent_rounds"
                break
            time.sleep(0.3)
    except Exception as exc:
        run_error = str(exc)
        stop_reason = "runtime_exception"
        logger.system("simulation_runtime_error", "Simulation runtime failed", details={"error": run_error})
    finally:
        state.simulation_active = False
        capture_stop = _capture(created_cas, False, _req, session_id=session_id, trace_id=talk_id)
        logger.system("capture_control", "full capture stopped", details={"session_id": session_id, **capture_stop})
        network_clear = _configure_network(created_cas, [], _req)
        logger.system("network_emulation", "Agent network profiles cleared", details=network_clear)
        for ca, _ in created_cas:
            if ca.status != "error":
                runtime._set_status(ca, "idle", {"phase": "simulation:finish", "stop_reason": stop_reason})

    experiment_status = "complete" if not run_error and stop_reason != "capture_incomplete" and capture_stop["failed"] == 0 else "error"
    finalize_manifest(
        session_id,
        status=experiment_status,
        stop_reason=stop_reason,
        rounds=len(results_log),
        network_emulation=network_emulation,
        network_clear=network_clear,
        capture_start=capture_start,
        capture_stop=capture_stop,
        error=run_error,
    )
    quality = audit_session(session_id, verify_hashes=True)
    return {"status": "error" if run_error else "completed", "error": run_error, "simulation_name": scene_def.title, "scene_key": scene_def.scene_key, "session_id": session_id, "seed": _pending_seed, "experiment_status": experiment_status, "quality": quality, "agents": [a.get_status() for a in AgentRegistry.list_all()], "agent_stats": AgentRegistry.get_stats(), "packet_stats": packet_stats(session_id=session_id), "capture_health": capture_health, "rounds": len(results_log), "max_rounds": max_rounds, "stop_reason": stop_reason, "results_log": results_log, "topology": scene_def.topology, "comm_policy": {"mode": "direct", "matrix": {k: list(v) for k, v in _comm_matrix.items()}}, "agent_directory": agent_directory, "network_mode": "direct", "network_emulation": network_emulation, "network_clear": network_clear, "assign_errors": assign_errors}


def _normalize_backend(scene_name: str, role_id: str, backend: str) -> str:
    backend = (backend or "openclaw").strip()
    if backend == "brain":
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses removed backend 'brain'.")
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses unsupported backend '{backend}'.")
    return backend


def _build_scene_from_folder(scene_name: str) -> SceneDefinition:
    folder = _SCENES_DIR / scene_name
    meta = json.loads((folder / "meta_and_roles.json").read_text(encoding="utf-8"))
    instances = json.loads((folder / "instances_and_skills.json").read_text(encoding="utf-8"))
    topology_config = json.loads((folder / "network_topology.json").read_text(encoding="utf-8"))
    smeta = meta.get("scenario_metadata", {})
    title = smeta.get("title", scene_name)
    bg = smeta.get("global_rules", "")
    if smeta.get("max_rounds"):
        state.termination_config["max_rounds"] = int(smeta["max_rounds"])
    if smeta.get("stalemate_rounds"):
        state.termination_config["stalemate_rounds"] = int(smeta["stalemate_rounds"])
    state.current_scene_name = scene_name
    state.current_max_rounds = state.termination_config.get("max_rounds", 20)
    state.active_tools_module = None
    roles = meta.get("roles", {})
    containers = instances.get("container_instances", {})
    agents = []
    for role_id, role in roles.items():
        instance = containers.get(role_id, {})
        skill_refs = list(instance.get("skill_refs") or [])
        if not all(isinstance(item, str) and item for item in skill_refs):
            raise ValueError(
                f"Scene '{scene_name}' role '{role_id}' skill_refs must contain non-empty strings."
            )
        skill_refs = list(dict.fromkeys(skill_refs))
        allowed_tools = instance.get("tool_refs") or []
        backend = _normalize_backend(scene_name, role_id, role.get("model_backbone", "openclaw"))
        core_goal = role.get("core_goal", "")
        identity = role.get("identity", "") or role.get("name", role_id)
        agents.append(
            AgentDef(
                agent_id=role_id.lower(),
                role=identity,
                name=role.get("name", role_id),
                core_goal=core_goal,
                backend=backend,
                skill_refs=skill_refs,
                allowed_tools=list(allowed_tools),
                tasks=[],
            )
        )
    raw_topology = topology_config.get("topology")
    if not isinstance(raw_topology, list):
        raise ValueError(
            f"Scene '{scene_name}' network_topology.json must contain a root-level topology array."
        )

    agent_ids = {agent.agent_id for agent in agents}
    channel_ids = set()
    topology_edges = []
    for index, edge in enumerate(raw_topology):
        if not isinstance(edge, dict):
            raise ValueError(f"Scene '{scene_name}' topology[{index}] must be an object.")
        unexpected = set(edge) - _TOPOLOGY_LINK_FIELDS
        missing = {"endpoint_a", "endpoint_b", "channel_id"} - set(edge)
        if unexpected:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] has unsupported fields: {sorted(unexpected)}"
            )
        if missing:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] is missing fields: {sorted(missing)}"
            )
        endpoint_a = str(edge["endpoint_a"]).strip().lower()
        endpoint_b = str(edge["endpoint_b"]).strip().lower()
        channel_id = str(edge["channel_id"]).strip()
        if not endpoint_a or not endpoint_b or endpoint_a == endpoint_b:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] must connect two distinct endpoints."
            )
        unknown = {endpoint_a, endpoint_b} - agent_ids
        if unknown:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] references unknown agents: {sorted(unknown)}"
            )
        if not channel_id:
            raise ValueError(f"Scene '{scene_name}' topology[{index}] channel_id must be non-empty.")
        if channel_id in channel_ids:
            raise ValueError(f"Scene '{scene_name}' contains duplicate channel_id '{channel_id}'.")
        channel_ids.add(channel_id)
        network = normalize_profile({
            field: edge.get(field, 0)
            for field in _TOPOLOGY_NETWORK_FIELDS
        })
        topology_edges.append({
            "endpoint_a": endpoint_a,
            "endpoint_b": endpoint_b,
            "channel_id": channel_id,
            **network,
        })
    return SceneDefinition(
        scene_key=scene_name,
        title=title,
        description=bg,
        agents=agents,
        topology=topology_edges,
    )


@router.post("/simulations/setup")
async def setup_simulation(req: SimulationRunRequest):
    global _pending_config, _pending_seed
    if not req.scene or not (_SCENES_DIR / req.scene).is_dir():
        raise HTTPException(status_code=400, detail=f"Scene '{req.scene}' not found")
    if req.seed is None:
        configured_seed = os.environ.get("SIMULATION_SEED", "").strip()
        _pending_seed = int(configured_seed) if configured_seed else random.SystemRandom().randrange(1, 2**31)
    else:
        _pending_seed = req.seed
    random.seed(_pending_seed)
    scene_def = _build_scene_from_folder(req.scene)
    _pending_config = _get_effective_llm_config()
    result = _setup_scene(scene_def)
    state.current_topology = result["topology"]
    return result


@router.post("/simulations/launch")
async def launch_simulation():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _launch_containers, _pending_config, _pending_scene_def)


@router.post("/simulations/stop")
async def stop_simulation():
    state.simulation_stop_requested = True
    return {"status": "stop_requested"}


@router.get("/scenes")
async def list_scenes():
    if not _SCENES_DIR.exists():
        return {"scenes": []}
    return {"scenes": [{"name": f.name, "format": "folder"} for f in sorted(_SCENES_DIR.iterdir(), key=lambda n: n.name.lower()) if f.is_dir() and (f / "meta_and_roles.json").exists()]}


@router.get("/scenes/state")
async def scene_state_unified():
    return {"scene": state.current_scene_name, "running": state.simulation_active, "round": state.current_turn, "max_rounds": state.current_max_rounds, "agents": [a.get_status() for a in AgentRegistry.list_all()], "custom": None}


@router.get("/scenes/{scene_name}")
async def read_scene(scene_name: str):
    folder = _SCENES_DIR / scene_name
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Scene '{scene_name}' not found")
    files = {}
    for key in ["meta_and_roles", "instances_and_skills", "network_topology"]:
        path = folder / f"{key}.json"
        if path.exists():
            files[key] = json.loads(path.read_text(encoding="utf-8"))
    return {"name": scene_name, "files": files}