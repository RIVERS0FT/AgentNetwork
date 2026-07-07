from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from agent_network.agent_management import AgentRegistry, get_management


router = APIRouter()


def _management():
    return get_management()


@router.get("/")
async def list_agents():
    """列出所有已注册 Agent。"""
    return _management().list_agents()


@router.get("/discover")
async def discover_agents(role: str = None, skill_ref: str = None):
    """服务发现：按角色或技能引用查询 Agent。"""
    agents = AgentRegistry.find_agent(role=role, skill_ref=skill_ref)
    return [agent.get_status() for agent in agents]


@router.get("/discover/best")
async def discover_best_agent(skill_ref: str):
    """按能力评分查找最优 Agent。"""
    agent = AgentRegistry.find_best_agent(skill_ref)
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"No agent found for skill_ref '{skill_ref}'",
        )
    return agent.get_status()


@router.get("/stats")
async def agent_stats():
    """Agent 注册与运行时统计信息。"""
    return _management().get_stats()


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """获取单个 Agent 状态。"""
    agent = AgentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.get_status()


@router.post("/")
async def register_agent(req: Dict[str, Any]):
    """创建并注册 Agent 元数据。"""
    agent_id = req.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    if AgentRegistry.get(agent_id):
        raise HTTPException(status_code=409, detail="Agent already exists")

    agent = _management().create_agent(
        agent_id=agent_id,
        role=req.get("role", "generic"),
        name=req.get("name", agent_id),
        core_goal=req.get("core_goal", ""),
        backend=req.get("backend", "openclaw"),
        skill_refs=req.get("skill_refs", []),
        allowed_tools=req.get("allowed_tools", []),
        capability_scores=req.get("capability_scores", {}),
    )
    return agent.get_status()


@router.post("/{agent_id}/task")
async def send_agent_task(agent_id: str, req: Dict[str, Any]):
    """向 Agent 投递控制面任务消息。"""
    if not AgentRegistry.get(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        message = _management().send_task(
            agent_id=agent_id,
            task=req.get("task", ""),
            target_id=req.get("target"),
            **req.get("kwargs", {}),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"message_id": message.message_id, "status": "sent"}


@router.delete("/{agent_id}")
async def unregister_agent(agent_id: str):
    """停止运行时分配并注销 Agent。"""
    removed = _management().unregister_agent(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "success", "agent_id": agent_id}
