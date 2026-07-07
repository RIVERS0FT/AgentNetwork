from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from agent_network.agent_model import AgentRegistry, Agent

router = APIRouter()

@router.get("/")
async def list_agents():
    """列出所有已注册 Agent"""
    return [a.get_status() for a in AgentRegistry.list_all()]

@router.get("/discover")
async def discover_agents(role: str = None, skill_ref: str = None):
    """服务发现：按角色或技能引用查询 Agent。"""
    agents = AgentRegistry.find_agent(role=role, skill_ref=skill_ref)
    return [a.get_status() for a in agents]

@router.get("/discover/best")
async def discover_best_agent(skill_ref: str):
    """按能力评分查找最优 Agent"""
    agent = AgentRegistry.find_best_agent(skill_ref)
    if not agent:
        raise HTTPException(status_code=404, detail=f"No agent found for skill_ref '{skill_ref}'")
    return agent.get_status()

@router.get("/stats")
async def agent_stats():
    """Agent 注册中心统计信息"""
    return AgentRegistry.get_stats()

@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """获取单个 Agent 状态"""
    agent = AgentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.get_status()

@router.post("/")
async def register_agent(req: Dict[str, Any]):
    """注册新 Agent (用于手动创建或外部接入)"""
    agent_id = req.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    agent = Agent(
        agent_id=agent_id,
        role=req.get("role", "generic"),
        name=req.get("name", agent_id),
        skill_refs=req.get("skill_refs", []),
        capability_scores=req.get("capability_scores", {})
    )
    AgentRegistry.register(agent)
    agent.start()
    return agent.get_status()

@router.post("/{agent_id}/task")
async def send_agent_task(agent_id: str, req: Dict[str, Any]):
    """向 Agent 发送任务"""
    agent = AgentRegistry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    task_content = req.get("task", "")
    target = req.get("target")
    kwargs = req.get("kwargs", {})
    
    target_agent = AgentRegistry.get(target) if target else None
    msg = agent.send_task(task_content, target=target_agent, **kwargs)
    return {"message_id": msg.message_id, "status": "sent"}

@router.delete("/{agent_id}")
async def unregister_agent(agent_id: str):
    """注销并停止 Agent"""
    agent = AgentRegistry.get(agent_id)
    if agent:
        agent.stop()
        AgentRegistry.unregister(agent_id)
    return {"status": "success", "agent_id": agent_id}
