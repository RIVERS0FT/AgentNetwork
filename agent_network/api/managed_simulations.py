from __future__ import annotations

import asyncio
import base64
import binascii
import os
import random
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from agent_network import state
from agent_network.agent_management import AgentRegistry, get_runtime
from agent_network.api import simulations as orchestration
from agent_network.capture_management import CaptureConfig, CaptureState, get_capture_coordinator
from agent_network.comm_management import A2A_MEDIA_TYPE, CommManager
from agent_network.file_management import FileManagerError, ResourceNotFoundError, ResourceNotReadyError
from agent_network.scene_management import SceneManager, get_scene_storage
from agent_network.simulation_management import (
    SimulationManager,
    SimulationManagerError,
    SimulationRuntimeConfig,
)
from agent_network.task_management import TaskManager, TaskManagerError

router = APIRouter()
scene_storage = get_scene_storage()


def _scene_is_occupied(scene_key: str) -> bool:
    return bool(state.simulation_active and state.current_scene_name == scene_key)


scene_manager = SceneManager(scene_storage, occupancy_checker=_scene_is_occupied)
captures = get_capture_coordinator()
_active_capture_id = ""
_task_db_path = os.environ.get(
    "ORCHESTRATOR_TASK_DB_PATH",
    os.path.join(os.environ.get("DATA_DIR", "data"), "tasks", "orchestrator.db"),
)
simulation_tasks = TaskManager(_task_db_path)
simulation_comm = CommManager(
    agent_id="srv",
    agent_name="Simulation Orchestrator",
    task_manager=simulation_tasks,
)


class AgentResourceRequest(BaseModel):
    cpu_cores: float = Field(default=1.0, ge=0.1, le=64)
    memory_mb: int = Field(default=1024, ge=128, le=1048576)
    pids_limit: int = Field(default=128, ge=16, le=65536)


class SimulationResourceRequest(BaseModel):
    default_agent: AgentResourceRequest = Field(default_factory=AgentResourceRequest)
    agent_overrides: dict[str, AgentResourceRequest] = Field(default_factory=dict)
    max_parallel_agents: int = Field(default=4, ge=1, le=256)


class SimulationRunRequest(BaseModel):
    scene: str = ""
    seed: Optional[int] = None
    duration_seconds: int = Field(default=3600, ge=1, le=604800)
    agent_timeout_seconds: int = Field(default=300, ge=1, le=86400)
    graceful_stop_timeout_seconds: int = Field(default=30, ge=0, le=3600)
    resource_allocation: SimulationResourceRequest = Field(
        default_factory=SimulationResourceRequest
    )


class SceneBatchUploadItemRequest(BaseModel):
    filename: str
    content_base64: str
    scene_key: str = ""


class SceneBatchUploadRequest(BaseModel):
    items: list[SceneBatchUploadItemRequest] = Field(default_factory=list)


class SceneKeyBatchRequest(BaseModel):
    scene_keys: list[str] = Field(default_factory=list)


class AgentTaskRequest(BaseModel):
    goal: str
    input: dict = Field(default_factory=dict)
    context_id: str = ""
    parent_task_id: str = ""
    idempotency_key: str = ""
    trace_id: str = ""


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResourceNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (ResourceNotReadyError, RuntimeError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _agent_directory() -> dict[str, str]:
    return {
        agent.agent_id.lower(): agent.container_url
        for agent in AgentRegistry.list_all()
        if agent.container_url
    }


def _require_simulation(simulation_id: str) -> None:
    try:
        simulation_manager.get_run(simulation_id)
    except SimulationManagerError as exc:
        raise _simulation_error(exc) from exc


def _require_simulation_task(simulation_id: str, task_id: str) -> dict:
    try:
        record = simulation_tasks.get_record(task_id)
    except TaskManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record.get("simulation_id") != simulation_id:
        raise HTTPException(status_code=404, detail="task not found in simulation")
    return record


def _capture_adapter(
    created_cas,
    enabled,
    requests_module,
    session_id: str = "",
    trace_id: str = "",
    network_profiles=None,
):
    del requests_module
    global _active_capture_id
    if enabled:
        targets = []
        for assignment, _ in created_cas:
            if assignment.status == "error" or not assignment.url:
                continue
            targets.append({
                "agent_id": assignment.agent_id,
                "runtime_url": assignment.url,
                "container_id": assignment.container_id,
                "container_name": assignment.container_name,
                "runtime_ip": assignment.container_ip,
            })
        try:
            max_bytes = max(0, int(os.environ.get("PCAP_MAX_BYTES", str(1024 * 1024 * 1024))))
        except ValueError:
            max_bytes = 1024 * 1024 * 1024
        session = captures.create_session(
            simulation_id=session_id,
            session_id=session_id,
            trace_id=trace_id,
            capture_id=session_id,
            targets=targets,
            config=CaptureConfig(
                interface="any",
                max_bytes=max_bytes,
                include_control_plane=os.environ.get("AGENT_CAPTURE_INCLUDE_CONTROL_PLANE", "0") == "1",
            ),
        )
        for agent_id, profiles in (network_profiles or {}).items():
            if agent_id in session.targets:
                session.targets[agent_id].details["network_profiles"] = profiles
        session = captures.start_session(session.capture_id)
        _active_capture_id = session.capture_id
    else:
        capture_id = _active_capture_id or session_id
        try:
            session = captures.stop_session(capture_id, "simulation_finished")
        except KeyError:
            return {"success": 0, "failed": 0, "agents": [], "status": "not_running"}
        _active_capture_id = ""
    agents = [target.to_dict() for target in session.targets.values()]
    success = sum(1 for target in session.targets.values() if target.state in {CaptureState.RUNNING, CaptureState.STOPPED})
    failed = len(session.targets) - success
    return {
        "capture_id": session.capture_id,
        "status": session.state.value,
        "success": success,
        "failed": failed,
        "agents": agents,
    }


def _capture_health_adapter(created_cas, requests_module):
    del created_cas, requests_module
    if not _active_capture_id:
        return {"healthy": False, "failed": 1, "agents": [], "status": "not_running"}
    try:
        session = captures.check_health(_active_capture_id)
    except KeyError:
        return {"healthy": False, "failed": 1, "agents": [], "status": "not_found"}
    agents = [target.to_dict() for target in session.targets.values()]
    failed = sum(1 for target in session.targets.values() if target.state != CaptureState.RUNNING)
    return {
        "capture_id": session.capture_id,
        "healthy": session.state == CaptureState.RUNNING and failed == 0,
        "failed": failed,
        "agents": agents,
    }


# The legacy orchestration module owns simulation execution for now, but its
# capture hooks are replaced by the single CaptureManager control-plane entry.
orchestration._capture = _capture_adapter
orchestration._capture_health = _capture_health_adapter


def _model_dict(model: BaseModel) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _setup_managed_run(scene_def, seed: int) -> dict:
    random.seed(seed)
    orchestration._pending_seed = seed
    orchestration._pending_config = orchestration._get_effective_llm_config()
    state.current_scene_name = scene_def.scene_key
    state.active_tools_module = None
    result = orchestration._setup_scene(scene_def)
    state.current_topology = result["topology"]
    return result


def _run_managed_simulation(run) -> dict:
    return orchestration._launch_containers(
        orchestration._pending_config,
        orchestration._pending_scene_def,
        simulation_run=run,
    )


simulation_manager = SimulationManager(
    scene_loader=scene_manager.build_definition,
    setup_handler=_setup_managed_run,
    run_handler=_run_managed_simulation,
    runtime_provider=get_runtime,
)


def _simulation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SimulationManagerError):
        status = 404 if exc.code == "SIMULATION_NOT_FOUND" else 409
        return HTTPException(status_code=status, detail=str(exc))
    return _http_error(exc)


def _runtime_config(req: SimulationRunRequest) -> SimulationRuntimeConfig:
    return SimulationRuntimeConfig.from_dict(
        {
            "duration_seconds": req.duration_seconds,
            "agent_timeout_seconds": req.agent_timeout_seconds,
            "graceful_stop_timeout_seconds": req.graceful_stop_timeout_seconds,
            "resource_allocation": _model_dict(req.resource_allocation),
        }
    )


def _configure_run(req: SimulationRunRequest):
    if not req.scene:
        raise HTTPException(status_code=400, detail="scene is required")
    try:
        seed = req.seed
        if seed is None:
            configured_seed = os.environ.get("SIMULATION_SEED", "").strip()
            seed = int(configured_seed) if configured_seed else None
        return simulation_manager.configure(
            req.scene,
            _runtime_config(req),
            seed=seed,
        )
    except HTTPException:
        raise
    except (ValueError, OSError, FileManagerError, RuntimeError) as exc:
        raise _simulation_error(exc) from exc


@router.post("/simulations/setup")
async def setup_simulation(req: SimulationRunRequest):
    run = _configure_run(req)
    return {
        **run.setup,
        "simulation_id": run.simulation_id,
        "simulation": run.to_dict(),
    }


@router.post("/simulations/configure")
async def configure_simulation(req: SimulationRunRequest):
    return _configure_run(req).to_dict()


@router.post("/simulations/launch")
async def launch_simulation():
    current = simulation_manager.current()
    if not current:
        raise HTTPException(status_code=409, detail="no configured simulation")
    try:
        run = await asyncio.to_thread(
            simulation_manager.start, current.simulation_id
        )
    except SimulationManagerError as exc:
        raise _simulation_error(exc) from exc
    return {**run.result, "simulation": run.to_dict()}


@router.post("/simulations/{simulation_id}/start")
async def start_simulation(simulation_id: str):
    try:
        return (await asyncio.to_thread(
            simulation_manager.start, simulation_id
        )).to_dict()
    except SimulationManagerError as exc:
        raise _simulation_error(exc) from exc


@router.post("/simulations/stop")
async def stop_simulation():
    current = simulation_manager.current()
    if not current:
        raise HTTPException(status_code=404, detail="simulation not found")
    return (await asyncio.to_thread(
        simulation_manager.stop, current.simulation_id
    )).to_dict()


@router.post("/simulations/{simulation_id}/stop")
async def stop_simulation_by_id(simulation_id: str):
    try:
        return (await asyncio.to_thread(
            simulation_manager.stop, simulation_id
        )).to_dict()
    except SimulationManagerError as exc:
        raise _simulation_error(exc) from exc


@router.post("/simulations/{simulation_id}/force-stop")
async def force_stop_simulation(simulation_id: str):
    try:
        return (await asyncio.to_thread(
            simulation_manager.force_stop, simulation_id
        )).to_dict()
    except SimulationManagerError as exc:
        raise _simulation_error(exc) from exc


@router.get("/simulations")
async def list_simulations():
    return [run.to_dict() for run in simulation_manager.list_runs()]


@router.get("/simulations/{simulation_id}")
async def get_simulation(simulation_id: str):
    try:
        return simulation_manager.get_run(simulation_id).to_dict()
    except SimulationManagerError as exc:
        raise _simulation_error(exc) from exc


@router.post("/simulations/{simulation_id}/agents/{agent_id}/tasks")
async def delegate_simulation_task(
    simulation_id: str,
    agent_id: str,
    req: AgentTaskRequest,
):
    """Delegate one durable A2A task from the orchestrator to one Agent."""
    _require_simulation(simulation_id)
    directory = _agent_directory()
    target_id = agent_id.lower()
    if target_id not in directory:
        raise HTTPException(status_code=404, detail="target Agent is not running")
    simulation_comm.update_directory(directory)
    callback_base = os.environ.get(
        "A2A_CALLBACK_BASE_URL",
        os.environ.get("SERVER_URL", "http://srv:8000"),
    ).rstrip("/")
    result = await asyncio.to_thread(
        simulation_comm.delegate_task,
        "srv",
        "Simulation Orchestrator",
        target_id,
        req.goal,
        req.input,
        req.context_id,
        req.trace_id,
        simulation_id,
        req.parent_task_id,
        req.idempotency_key,
        f"{callback_base}/api/simulations/{simulation_id}/task-events",
        secrets.token_urlsafe(32),
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    orchestration.logger.system(
        "simulation_task_delegated",
        agent_id="srv",
        details={
            "simulation_id": simulation_id,
            "task_id": result.task_id,
            "target_agent_id": target_id,
            "trace_id": req.trace_id,
        },
    )
    return JSONResponse(
        status_code=202,
        media_type=A2A_MEDIA_TYPE,
        content=result.to_dict(),
    )


@router.get("/simulations/{simulation_id}/tasks/{task_id}")
async def get_simulation_task(simulation_id: str, task_id: str):
    _require_simulation(simulation_id)
    return _require_simulation_task(simulation_id, task_id)["task"]


@router.get("/simulations/{simulation_id}/tasks")
async def list_simulation_tasks(
    simulation_id: str,
    status: str = Query(default=""),
    page_size: int = Query(default=50, ge=1, le=100),
):
    _require_simulation(simulation_id)
    return simulation_tasks.list_tasks(
        simulation_id=simulation_id,
        status=status,
        page_size=page_size,
        include_artifacts=True,
    )


@router.post("/simulations/{simulation_id}/tasks/{task_id}:cancel")
async def cancel_simulation_task(simulation_id: str, task_id: str):
    _require_simulation(simulation_id)
    _require_simulation_task(simulation_id, task_id)
    simulation_comm.update_directory(_agent_directory())
    try:
        task = await asyncio.to_thread(simulation_comm.cancel_remote_task, task_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(content=task, media_type=A2A_MEDIA_TYPE)


@router.post("/simulations/{simulation_id}/task-events")
async def receive_simulation_task_event(simulation_id: str, request: Request):
    """Receive authenticated A2A push callbacks from an assigned Agent."""
    try:
        payload = await request.json()
        event = payload.get("statusUpdate") or payload.get("artifactUpdate") or {}
        task_id = str(event.get("taskId") or "")
        record = _require_simulation_task(simulation_id, task_id)
        expected = str(record.get("callback", {}).get("token") or "")
        received = request.headers.get("X-A2A-Notification-Token", "")
        if expected and not secrets.compare_digest(received, expected):
            raise HTTPException(status_code=401, detail="invalid callback token")
        task = simulation_comm.receive_task_event(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    orchestration.logger.system(
        "simulation_task_callback_received",
        agent_id="srv",
        details={
            "simulation_id": simulation_id,
            "task_id": task_id,
            "state": (task.get("status") or {}).get("state", ""),
        },
    )
    return JSONResponse(
        content={"received": True, "task": task},
        media_type=A2A_MEDIA_TYPE,
    )


@router.get("/scenes")
async def list_scenes(include_hidden: bool = Query(default=False)):
    return {"scenes": scene_manager.list_scenes(include_hidden=include_hidden)}


@router.get("/scenes/state")
async def scene_state():
    return {
        "scene": state.current_scene_name,
        "running": state.simulation_active,
        "agents": [agent.get_status() for agent in orchestration.AgentRegistry.list_all()],
        "custom": None,
    }


@router.post("/scenes/upload")
async def upload_scene(request: Request, filename: str = Query(...), scene_key: str = Query(default="")):
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        return scene_manager.upload_one(filename=filename, content=content, scene_key=scene_key)
    except (ValueError, OSError, FileManagerError) as exc:
        raise _http_error(exc) from exc


@router.post("/scenes/batch/upload")
async def batch_upload_scenes(req: SceneBatchUploadRequest):
    prepared = []
    for item in req.items:
        try:
            content = base64.b64decode(item.content_base64, validate=True)
            prepared.append({
                "filename": item.filename,
                "scene_key": item.scene_key,
                "content": content,
            })
        except (binascii.Error, ValueError) as exc:
            prepared.append({
                "filename": item.filename,
                "scene_key": item.scene_key,
                "error_code": "invalid_base64",
                "error": str(exc),
            })
    result = await asyncio.to_thread(scene_manager.upload_many, prepared)
    return result.to_dict()


@router.post("/scenes/batch/download")
async def batch_download_scenes(req: SceneKeyBatchRequest):
    result = await asyncio.to_thread(scene_manager.download_many, req.scene_keys)
    payload = result.to_dict()
    if result.archive_resource_id:
        payload["download_url"] = f"/api/scenes/batch/download/{result.archive_resource_id}"
    return payload


@router.get("/scenes/batch/download/{resource_id}")
async def download_scene_batch(resource_id: str):
    try:
        descriptor = scene_manager.prepare_batch_download(resource_id)
    except (ValueError, OSError, FileManagerError) as exc:
        raise _http_error(exc) from exc
    return FileResponse(
        descriptor.internal_path,
        media_type=descriptor.media_type,
        filename=descriptor.logical_name,
    )


@router.post("/scenes/batch/delete")
async def batch_delete_scenes(req: SceneKeyBatchRequest):
    result = await asyncio.to_thread(scene_manager.delete_many, req.scene_keys)
    return result.to_dict()


@router.post("/scenes/batch/parse")
async def batch_parse_scenes(req: SceneKeyBatchRequest):
    result = await asyncio.to_thread(scene_manager.parse_many, req.scene_keys)
    return result.to_dict()


@router.get("/scenes/{scene_key}/download")
async def download_scene(scene_key: str):
    try:
        archive = scene_manager.create_archive(scene_key)
        descriptor = scene_manager.files.prepare_download(archive.resource_id)
    except (ValueError, OSError, FileManagerError) as exc:
        raise _http_error(exc) from exc
    return FileResponse(descriptor.internal_path, media_type=descriptor.media_type, filename=descriptor.logical_name)


@router.post("/scenes/{scene_key}/hide")
async def hide_scene(scene_key: str):
    try:
        resource = scene_manager.set_visibility(scene_key, False)
    except (ValueError, OSError, FileManagerError) as exc:
        raise _http_error(exc) from exc
    return resource.to_dict()


@router.post("/scenes/{scene_key}/show")
async def show_scene(scene_key: str):
    try:
        resource = scene_manager.set_visibility(scene_key, True)
    except (ValueError, OSError, FileManagerError) as exc:
        raise _http_error(exc) from exc
    return resource.to_dict()


@router.delete("/scenes/{scene_key}")
async def delete_scene(scene_key: str):
    try:
        resource = scene_manager.delete_one(scene_key)
    except (ValueError, OSError, RuntimeError, FileManagerError) as exc:
        raise _http_error(exc) from exc
    return resource.to_dict()


@router.get("/scenes/{scene_key}")
async def read_scene(scene_key: str):
    try:
        return scene_manager.details(scene_key)
    except (ValueError, OSError, FileManagerError) as exc:
        raise _http_error(exc) from exc
