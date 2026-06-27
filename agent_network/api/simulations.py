import asyncio
import json
import uuid
import time
import random
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from agent_network import state
from agent_network.agent_model import AgentRegistry, Agent
from agent_network.logger import get_logger
from agent_network.event_bus import PacketRecorder
from agent_network.scene_def import get_api_config, SceneDefinition, AgentDef
from agent_network.container_runtime import get_runtime

router = APIRouter()
logger = get_logger()

_SCENES_DIR = Path("scenes")
_simulation_results: List[Dict[str, Any]] = []
_llm_config: Dict[str, str] = {}


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


def _control_agent_capture(created_cas: List[tuple], enabled: bool, requests_module) -> Dict[str, Any]:
    success = 0
    failed = 0
    for ca, _ in created_cas:
        if ca.status == "error" or not ca.url:
            continue
        try:
            endpoint = "/capture/start" if enabled else "/capture/stop"
            resp = requests_module.post(f"{ca.url}{endpoint}", timeout=2)
            if resp.status_code == 200:
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {"success": success, "failed": failed}


def _force_layout(agents: List[Any], links: List[Dict], width: float = 400, height: float = 400, margin: float = 60) -> Dict[str, tuple]:
    if not agents:
        return {}
    return {
        agent.agent_id: (
            random.uniform(margin, width - margin),
            random.uniform(margin, height - margin),
        )
        for agent in agents
    }


def _publish_comm_policy(requests_module, matrix: Dict[str, set]) -> Dict[str, Any]:
    """Push communication policy to bus, where it is enforced at /relay."""
    snapshot = {key: sorted(values) for key, values in matrix.items()}
    try:
        resp = requests_module.post(
            f"{state.MESSAGE_BUS_URL}/policy/comm_matrix",
            json={"enabled": True, "matrix": snapshot},
            timeout=3,
        )
        if resp.status_code != 200:
            logger.system(
                "comm_policy_update_failed",
                "通信权限矩阵下发到 bus 失败",
                details={"status_code": resp.status_code, "matrix": snapshot},
            )
            return {"ok": False, "status_code": resp.status_code, "matrix": snapshot}
        logger.system(
            "comm_policy_updated",
            "通信权限矩阵已下发到 bus",
            details={"enabled": True, "matrix": snapshot},
        )
        return {"ok": True, "matrix": snapshot}
    except Exception as e:
        logger.system(
            "comm_policy_update_failed",
            "通信权限矩阵下发到 bus 失败",
            details={"error": str(e), "matrix": snapshot},
        )
        return {"ok": False, "error": str(e), "matrix": snapshot}


_pending_scene_def: Optional[SceneDefinition] = None
_pending_layout: Dict[str, tuple] = {}
_pending_config: Dict[str, str] = {}
_comm_matrix: Dict[str, set] = {}


def _setup_scene(scene_def: SceneDefinition) -> Dict[str, Any]:
    global _pending_scene_def, _pending_layout

    AgentRegistry.reset()
    PacketRecorder.reset()
    state.agent_logs.clear()
    logger.reset()

    from agent_network.comm import RemoteBus
    remote_bus = RemoteBus(message_bus_url=state.MESSAGE_BUS_URL)
    layout_pos = _force_layout(scene_def.agents, scene_def.workflow)

    for ad in scene_def.agents:
        agent = Agent(
            agent_id=ad.agent_id,
            role=ad.role,
            name=ad.name,
            skills=ad.skills,
            tags=ad.tags,
        )
        agent.set_comm(remote_bus)
        lx, ly = layout_pos.get(ad.agent_id, (random.uniform(50, 350), random.uniform(50, 350)))
        agent.x = lx
        agent.y = ly
        agent.pending_task_descs = ad.tasks
        agent.extra_meta = ad.extra_meta
        AgentRegistry.register(agent)
        agent.start()

    _pending_scene_def = scene_def
    _pending_layout = layout_pos

    return {
        "agents": [a.get_status() for a in AgentRegistry.list_all()],
        "agent_stats": AgentRegistry.get_stats(),
        "relationships": scene_def.workflow,
        "scene_name": scene_def.scene_name,
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
            extra_meta=ad.extra_meta if ad.extra_meta else None,
        )
        created_cas.append((ca, ad.tasks))
        if ca.status == "error":
            assign_errors.append({
                "agent_id": ca.agent_id,
                "name": ca.name,
                "error": getattr(ca, "_assign_error", "unknown"),
            })
        else:
            agent = AgentRegistry.get(ca.agent_id)
            if agent:
                agent.container_url = ca.url

    assigned_count = sum(1 for ca, _ in created_cas if ca.status != "error")
    logger.system(
        "container_pool",
        f"容器分配完成: {assigned_count}/{len(scene_def.agents)} Agent 分配成功",
        details={"total_agents": len(scene_def.agents), "assigned": assigned_count, "errors": assign_errors},
    )

    if assign_errors:
        created_cas = [(ca, tasks) for ca, tasks in created_cas if ca.status != "error"]
        logger.system(
            "container_pool",
            f"警告: {len(assign_errors)} 个 Agent 分配失败，将被跳过",
            details={"skipped": [e["agent_id"] for e in assign_errors]},
        )

    for ca, _ in created_cas:
        try:
            _req.post(f"{ca.url}/reset", timeout=3)
        except Exception:
            pass

    time.sleep(1)
    for ca, _ in created_cas:
        try:
            _req.post(
                f"{state.MESSAGE_BUS_URL}/register",
                params={"agent_id": ca.agent_id, "url": ca.url, "name": ca.name},
                timeout=3,
            )
            runtime._set_status(ca, "idle", {"phase": "bus_register"})
        except Exception:
            runtime._set_status(ca, "error", {"phase": "bus_register", "error": "message_bus_register_failed"})

    _comm_matrix.clear()
    for edge in (scene_def.workflow or []):
        if edge.get("can_direct_chat", True) is False:
            continue
        src = edge.get("from", "").lower()
        dst = edge.get("to", "").lower()
        if src and dst:
            _comm_matrix.setdefault(src, set()).add(dst)
            _comm_matrix.setdefault(dst, set()).add(src)

    state.agent_logs.clear()
    logger.start_session(scene_def.scene_name)
    state.reset_token_usage_state(getattr(logger, "_session_id", ""))
    policy_update = _publish_comm_policy(_req, _comm_matrix)

    try:
        _req.post(f"{state.MESSAGE_BUS_URL}/session/start", params={"session_dir": logger._session_dir}, timeout=3)
    except Exception:
        pass

    state.simulation_active = True
    capture_start = _control_agent_capture(created_cas, True, _req)
    logger.system("capture_control", "network_capture started", details={"enabled": True, **capture_start})

    talk_id = f"talk-{uuid.uuid4().hex[:12]}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    channel_map: Dict[str, str] = {}
    for edge in (scene_def.workflow or []):
        src = edge.get("from", "")
        dst = edge.get("to", "")
        ch = edge.get("channel_id", "")
        if src and dst:
            channel_map[f"{src}->{dst}"] = ch
            channel_map[f"{src.lower()}->{dst.lower()}"] = ch

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
                logger.system("simulation_stopped", "用户手动停止仿真", details={"round": round_num + 1})
                break

            state.current_turn = round_num + 1
            context = {
                "round": state.current_turn,
                "total_rounds": max_rounds,
                "scene": scene_def.scene_name,
                "agents": [{"id": ca.agent_id, "role": ca.role, "name": ca.name} for ca, _ in created_cas],
                "tasks": {ca.agent_id: tasks for ca, tasks in created_cas},
                "comm_matrix": {k: list(v) for k, v in _comm_matrix.items()},
                "channel_map": channel_map,
                "talk": talk_id,
            }
            round_result = runtime.run_round(context)
            results_log.append(round_result)

            results_list = round_result.get("results", [])
            meaningful_events = 0
            for res in results_list:
                meaningful_events += len(res.get("outbound_messages", []))
                meaningful_events += len(res.get("tool_events", []))
                meaningful_events += len(res.get("application_events", []))
                if res.get("status") == "skipped":
                    continue
                if res.get("final_message"):
                    meaningful_events += 1

            if meaningful_events == 0:
                silent_rounds += 1
            else:
                silent_rounds = 0

            if silent_rounds >= stalemate_threshold:
                stop_reason = f"stalemate_{stalemate_threshold}_silent_rounds"
                break

            time.sleep(0.3)
        else:
            stop_reason = "hard_limit"
    finally:
        state.simulation_active = False
        capture_stop = _control_agent_capture(created_cas, False, _req)
        logger.system("capture_control", "network_capture stopped", details={"enabled": False, **capture_stop})
        final_status = "error" if stop_reason == "user_stopped" else "idle"
        for ca, _ in created_cas:
            if ca.status != "error":
                runtime._set_status(ca, final_status, {"phase": "simulation:finish", "stop_reason": stop_reason})

    state.current_relationships = scene_def.workflow
    registry_agents = [a.get_status() for a in AgentRegistry.list_all()]
    actual_rounds = len(results_log)
    runtime_agent_count = len(runtime.agents)

    logger.system(
        "simulation_complete",
        f"仿真完成: {scene_def.scene_name} | {actual_rounds}轮 | {runtime_agent_count}/{len(scene_def.agents)} Agent | {stop_reason}",
        details={"scene": scene_def.scene_name, "rounds": actual_rounds, "agent_count": runtime_agent_count, "agent_defined": len(scene_def.agents), "stop_reason": stop_reason},
    )

    return {
        "simulation_name": scene_def.scene_name,
        "duration_seconds": round(len(results_log) * 1.5 if results_log else 1.5, 2),
        "agents": registry_agents,
        "agent_stats": AgentRegistry.get_stats(),
        "packet_stats": PacketRecorder.get_stats(),
        "max_rounds": max_rounds,
        "rounds": actual_rounds,
        "stop_reason": stop_reason,
        "results_log": results_log,
        "relationships": scene_def.workflow,
        "comm_policy": policy_update,
        "container_mode": "pool",
    }


def _normalize_backend(scene_name: str, role_id: str, backend: str) -> str:
    backend = (backend or "openclaw").strip()
    if backend == "claudecode":
        return "claude-code"
    if backend == "brain":
        raise ValueError(
            f"Scene '{scene_name}' role '{role_id}' uses removed backend 'brain'. "
            "Use 'openclaw' or 'claude-code'."
        )
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(
            f"Scene '{scene_name}' role '{role_id}' uses unsupported backend '{backend}'. "
            "Use 'openclaw' or 'claude-code'."
        )
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

    # Runtime boundary: srv must not import tools.py and must not read Skill.md
    # contents. It only reads scene manifests and passes allowlists. The Agent
    # container loads Skill.md as SOP/context and tools.py as MCP atomic tools.
    agents: List[AgentDef] = []
    for role_id, role in roles.items():
        instance = containers.get(role_id, {})
        raw_skills = instance.get("skill_refs") or instance.get("skills") or []
        if raw_skills and isinstance(raw_skills[0], dict):
            skills = [s.get("skill_name") or s.get("name") for s in raw_skills]
        else:
            skills = raw_skills
        skills = [s for s in skills if s]

        allowed_tools = instance.get("tool_refs") or []
        backend = _normalize_backend(scene_name, role_id, role.get("model_backbone", "openclaw"))
        paradigm = role.get("primary_interaction_paradigm", "")
        core_goal = role.get("core_goal", "")

        paradigm_hints = {
            "EXTERNAL_NEGOTIATION": "你处于对外谈判模式，需要在合作与竞争之间寻找平衡。",
            "COMPETITIVE_AGGRESSIVE": "你采取进攻性市场竞争策略，优先扩大份额而非短期利润。",
            "INTERNAL_COLLABORATION": "你注重内部协作，通过团队配合提升整体效率。",
            "REGULATORY_COMPLIANCE": "你需要确保所有行动符合监管要求，违规将带来严重后果。",
        }

        agent = AgentDef(
            agent_id=role_id.lower(),
            role="generic",
            name=role.get("name", role_id),
            skills=skills[:4],
            tags=[paradigm] if paradigm else [],
            tasks=[core_goal] if core_goal else [],
            extra_meta={
                "identity": role.get("identity", ""),
                "core_goal": core_goal,
                "initial_assets": role.get("initial_assets", {}),
                "action_space": ["send_message", "broadcast"] + allowed_tools,
                "background_rules": bg,
                "backend": backend,
                "interaction_paradigm": paradigm,
                "paradigm_hint": paradigm_hints.get(paradigm, ""),
                "pip_packages": instance.get("pip_packages", []),
                "runtime_engine": instance.get("runtime_engine", ""),
                "scene_key": scene_name,
                "scene_title": title,
                "allowed_skills": skills,
                "allowed_tools": allowed_tools,
                "skill_execution_mode": "backend_native_mcp",
            },
        )
        agents.append(agent)

    relationships = []
    for subnet in topology.get("sub_networks", []):
        for edge in subnet.get("edges", []):
            weight = edge.get("weight")
            if weight is None:
                weight = 70 if edge.get("paradigm") == "COLLABORATION" else -50
            relationships.append({
                "from": edge["source"].lower(),
                "to": edge["target"].lower(),
                "relation_type": edge.get("paradigm", ""),
                "value": weight,
                "can_direct_chat": edge.get("direct_chat", True),
                "channel_id": edge.get("channel_id", ""),
            })

    return SceneDefinition(
        scene_name=title,
        description=bg,
        agents=agents,
        workflow=relationships,
        event_triggers=[],
    )


@router.post("/simulations/setup")
async def setup_simulation(req: SimulationRunRequest):
    global _pending_config
    if not req.scene or not (_SCENES_DIR / req.scene).is_dir():
        raise HTTPException(status_code=400, detail=f"Scene '{req.scene}' not found")
    try:
        scene_def = _build_scene_from_folder(req.scene)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _pending_config = _get_effective_llm_config()
    result = _setup_scene(scene_def)
    state.current_relationships = result["relationships"]
    return result


@router.post("/simulations/launch")
async def launch_simulation():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _launch_containers, _pending_config, _pending_scene_def)
    return result


@router.post("/simulations/stop")
async def stop_simulation():
    state.simulation_stop_requested = True
    return {"status": "stop_requested"}


@router.get("/scenes")
async def list_scenes():
    if not _SCENES_DIR.exists():
        return {"scenes": []}
    scenes = []
    for f in sorted(_SCENES_DIR.iterdir(), key=lambda n: n.name.lower()):
        if f.is_dir() and (f / "meta_and_roles.json").exists():
            scenes.append({"name": f.name, "format": "folder"})
    return {"scenes": scenes}


@router.get("/scenes/state")
async def scene_state_unified():
    agents = [a.get_status() for a in AgentRegistry.list_all()]
    return {
        "scene": state.current_scene_name,
        "running": state.simulation_active,
        "round": state.current_turn,
        "max_rounds": state.current_max_rounds,
        "agents": agents,
        "custom": None,
    }


@router.get("/scenes/{scene_name}")
async def read_scene(scene_name: str):
    folder = _SCENES_DIR / scene_name
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Scene '{scene_name}' not found")
    files = {}
    for key in ["meta_and_roles", "instances_and_skills", "network_topology"]:
        fpath = folder / f"{key}.json"
        if fpath.exists():
            files[key] = json.loads(fpath.read_text(encoding="utf-8"))
    if "meta_and_roles" not in files:
        raise HTTPException(status_code=404, detail=f"Folder scene '{scene_name}' missing meta_and_roles.json")
    title = files["meta_and_roles"].get("scenario_metadata", {}).get("title", scene_name)
    return {"name": scene_name, "title": title, "format": "folder", "files": files}


@router.get("/scenes/{scene_name}/panel", response_class=HTMLResponse)
async def scene_panel(scene_name: str):
    folder = _SCENES_DIR / scene_name
    panel_path = folder / "panel.html"
    if panel_path.exists():
        return HTMLResponse(content=panel_path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Panel not found")


@router.get("/scenes/{scene_name}/{filename:path}")
async def scene_asset(scene_name: str, filename: str):
    folder = _SCENES_DIR / scene_name
    file_path = folder / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Asset '{filename}' not found")
    return FileResponse(str(file_path))
