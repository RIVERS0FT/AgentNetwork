import asyncio
import json
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
from agent_network.scene_def import get_api_config, SceneDefinition, AgentDef
from agent_network.container_runtime import get_runtime

router = APIRouter()
logger = get_logger()
_SCENES_DIR = Path("scenes")
_llm_config: Dict[str, str] = {}
_pending_scene_def: Optional[SceneDefinition] = None
_pending_config: Dict[str, str] = {}
_comm_matrix: Dict[str, set] = {}


class SimulationRunRequest(BaseModel):
    scene: str = ""


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
                        "pcap_dir": "/app/data/pcap",
                        "interface": "any",
                    },
                    timeout=3,
                )
            else:
                resp = requests_module.post(f"{ca.url}/capture/stop", timeout=6)
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
        agent = Agent(agent_id=ad.agent_id, role=ad.role, name=ad.name, skills=ad.skills, tags=ad.tags)
        agent.set_comm(direct_bus)
        agent.x, agent.y = pos.get(ad.agent_id, (100, 100))
        agent.pending_task_descs = ad.tasks
        agent.extra_meta = ad.extra_meta
        AgentRegistry.register(agent)
        agent.start()
    _pending_scene_def = scene_def
    return {"agents": [a.get_status() for a in AgentRegistry.list_all()], "agent_stats": AgentRegistry.get_stats(), "relationships": scene_def.workflow, "scene_name": scene_def.scene_name, "network_mode": "direct"}


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
        ca = runtime.assign_agent(agent_id=ad.agent_id, role=ad.role, name=ad.name, extra_meta=ad.extra_meta if ad.extra_meta else None)
        created_cas.append((ca, ad.tasks))
        if ca.status == "error":
            assign_errors.append({"agent_id": ca.agent_id, "error": getattr(ca, "_assign_error", "unknown")})
        else:
            agent = AgentRegistry.get(ca.agent_id)
            if agent:
                agent.container_url = ca.url

    created_cas = [(ca, tasks) for ca, tasks in created_cas if ca.status != "error"]
    for ca, _ in created_cas:
        try:
            _req.post(f"{ca.url}/reset", timeout=3)
        except Exception:
            pass
    time.sleep(1)

    agent_directory = {ca.agent_id.lower(): ca.url for ca, _ in created_cas if ca.url}
    runtime_agent_ids = {ca.agent_id.lower(): (ca.container_name or ca.agent_id).lower() for ca, _ in created_cas}
    _comm_matrix.clear()
    for edge in (scene_def.workflow or []):
        if edge.get("can_direct_chat", True) is False:
            continue
        src = edge.get("from", "").lower()
        dst = edge.get("to", "").lower()
        if src and dst:
            _comm_matrix.setdefault(src, set()).add(dst)
            runtime_dst = runtime_agent_ids.get(dst)
            if runtime_dst:
                _comm_matrix.setdefault(src, set()).add(runtime_dst)
            if edge.get("bidirectional", False):
                _comm_matrix.setdefault(dst, set()).add(src)
                runtime_src = runtime_agent_ids.get(src)
                if runtime_src:
                    _comm_matrix.setdefault(dst, set()).add(runtime_src)

    logger.start_session(scene_def.scene_name)
    session_id = getattr(logger, "_session_id", "")
    talk_id = f"talk-{uuid.uuid4().hex[:12]}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    state.reset_token_usage_state(session_id)
    state.simulation_active = True
    capture_start = _capture(created_cas, True, _req, session_id=session_id, trace_id=talk_id)
    logger.system("capture_control", "full capture started", details={"session_id": session_id, **capture_start})

    max_rounds = state.termination_config.get("max_rounds", 20)
    stalemate_threshold = state.termination_config.get("stalemate_rounds", 3)
    results_log = []
    silent_rounds = 0
    stop_reason = "hard_limit"
    state.simulation_stop_requested = False

    try:
        for round_num in range(max_rounds):
            if state.simulation_stop_requested:
                stop_reason = "user_stopped"
                break
            state.current_turn = round_num + 1
            context = {
                "round": state.current_turn,
                "total_rounds": max_rounds,
                "scene": scene_def.scene_name,
                "agents": [{"id": ca.agent_id, "role": ca.role, "name": ca.name} for ca, _ in created_cas],
                "tasks": {ca.agent_id: tasks for ca, tasks in created_cas},
                "comm_matrix": {k: list(v) for k, v in _comm_matrix.items()},
                "agent_directory": agent_directory,
                "talk": talk_id,
                "trace_id": talk_id,
                "network_mode": "direct",
            }
            round_result = runtime.run_round(context)
            results_log.append(round_result)
            results = round_result.get("results", [])
            if results and all(res.get("error") for res in results):
                stop_reason = "all_agents_failed"
                break
            meaningful = sum(len(res.get("application_events", [])) + len(res.get("tool_events", [])) + (1 if res.get("final_message") else 0) for res in results)
            silent_rounds = silent_rounds + 1 if meaningful == 0 else 0
            if silent_rounds >= stalemate_threshold:
                stop_reason = f"stalemate_{stalemate_threshold}_silent_rounds"
                break
            time.sleep(0.3)
    finally:
        state.simulation_active = False
        capture_stop = _capture(created_cas, False, _req, session_id=session_id, trace_id=talk_id)
        logger.system("capture_control", "full capture stopped", details={"session_id": session_id, **capture_stop})
        for ca, _ in created_cas:
            if ca.status != "error":
                runtime._set_status(ca, "idle", {"phase": "simulation:finish", "stop_reason": stop_reason})

    return {"simulation_name": scene_def.scene_name, "agents": [a.get_status() for a in AgentRegistry.list_all()], "agent_stats": AgentRegistry.get_stats(), "packet_stats": packet_stats(session_id=session_id), "rounds": len(results_log), "max_rounds": max_rounds, "stop_reason": stop_reason, "results_log": results_log, "relationships": scene_def.workflow, "comm_policy": {"mode": "direct", "protocol": "a2a", "matrix": {k: list(v) for k, v in _comm_matrix.items()}}, "agent_directory": agent_directory, "runtime_agent_ids": runtime_agent_ids, "network_mode": "direct", "assign_errors": assign_errors}


def _normalize_backend(scene_name: str, role_id: str, backend: str) -> str:
    backend = (backend or "openclaw").strip()
    if backend == "claudecode":
        return "claude-code"
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses unsupported backend '{backend}'.")
    return backend


def _build_scene_from_folder(scene_name: str) -> SceneDefinition:
    folder = _SCENES_DIR / scene_name
    meta = json.loads((folder / "meta_and_roles.json").read_text(encoding="utf-8"))
    instances = json.loads((folder / "instances_and_skills.json").read_text(encoding="utf-8"))
    topology = json.loads((folder / "network_topology.json").read_text(encoding="utf-8"))
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
        raw_skills = instance.get("skill_refs") or instance.get("skills") or []
        skills = [s.get("skill_name") or s.get("name") for s in raw_skills] if raw_skills and isinstance(raw_skills[0], dict) else raw_skills
        skills = [s for s in skills if s]
        allowed_tools = instance.get("tool_refs") or []
        backend = _normalize_backend(scene_name, role_id, role.get("model_backbone", "openclaw"))
        core_goal = role.get("core_goal", "")
        paradigm = role.get("primary_interaction_paradigm", "")
        agents.append(AgentDef(agent_id=role_id.lower(), role="generic", name=role.get("name", role_id), skills=skills[:4], tags=[paradigm] if paradigm else [], tasks=[core_goal] if core_goal else [], extra_meta={"identity": role.get("identity", ""), "core_goal": core_goal, "initial_assets": role.get("initial_assets", {}), "action_space": ["send_message", "broadcast"] + allowed_tools, "background_rules": bg, "backend": backend, "interaction_paradigm": paradigm, "scene_key": scene_name, "scene_title": title, "allowed_skills": skills, "allowed_tools": allowed_tools, "skill_execution_mode": "backend_native_mcp"}))
    relationships = []
    for subnet in topology.get("sub_networks", []):
        for edge in subnet.get("edges", []):
            weight = edge.get("weight")
            if weight is None:
                weight = 70 if edge.get("paradigm") == "COLLABORATION" else -50
            relationships.append({"from": edge["source"].lower(), "to": edge["target"].lower(), "relation_type": edge.get("paradigm", ""), "value": weight, "can_direct_chat": edge.get("direct_chat", True), "bidirectional": edge.get("bidirectional", False), "channel_id": edge.get("channel_id", ""), "network": edge.get("network", {})})
    return SceneDefinition(scene_name=title, description=bg, agents=agents, workflow=relationships, event_triggers=[])
