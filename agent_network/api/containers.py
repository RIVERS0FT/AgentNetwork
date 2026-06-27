from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any, List
from agent_network import state
from agent_network.container_runtime import get_runtime

router = APIRouter()

def _get_runtime_with_status_listener():
    runtime = get_runtime()
    if not hasattr(runtime, '_status_listener_set'):
        def on_status(agent_id, status):
            from agent_network.agent_model import AgentRegistry
            a = AgentRegistry.get(agent_id)
            if a:
                a.status = status
        runtime.on_status_change = on_status
        runtime._status_listener_set = True
    return runtime

@router.post("/create")
async def create_container(req: Request):
    """启动 Agent 的隔离容器"""
    data = await req.json()
    agent_id = data.get("agent_id")
    role = data.get("role", "scout")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required")
    runtime = _get_runtime_with_status_listener()
    container_id = runtime.create_container(agent_id, role)
    if not container_id:
        raise HTTPException(status_code=500, detail="Failed to create container")
    return {"agent_id": agent_id, "container_id": container_id, "status": "created"}

@router.post("/{agent_id}/stop")
async def stop_container(agent_id: str):
    """停止 Agent 容器"""
    runtime = _get_runtime_with_status_listener()
    success = runtime.stop_container(agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Container not found or failed to stop")
    return {"agent_id": agent_id, "status": "stopped"}

@router.post("/stop-all")
async def stop_all_containers():
    """停止所有管理的容器"""
    runtime = _get_runtime_with_status_listener()
    runtime.stop_all()
    return {"status": "all_stopped"}

@router.get("/status")
async def containers_status():
    """获取所有容器状态"""
    runtime = _get_runtime_with_status_listener()
    return {"containers": runtime.list_containers()}

@router.post("/decide-all")
async def containers_decide_all():
    """容器模式：批量执行决定阶段"""
    runtime = _get_runtime_with_status_listener()
    return runtime.decide_all()

@router.post("/act-all")
async def containers_act_all():
    """容器模式：批量执行动作阶段"""
    runtime = _get_runtime_with_status_listener()
    return runtime.act_all()

@router.post("/round")
async def containers_run_round():
    """容器模式：执行一个完整的 tick 轮次 (decide -> act -> sync)"""
    runtime = _get_runtime_with_status_listener()
    return runtime.run_round()
