from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any, List
import json
try:
    import psutil
except ImportError:
    psutil = None

from agent_network import state
from agent_network.agent import AgentRegistry
from datetime import datetime
from agent_network.event_bus import PacketRecorder
from agent_network.logger import get_logger

router = APIRouter()
logger = get_logger()

# ═══════════════════════════════════════════════
# 统计 & 设置
# ═══════════════════════════════════════════════

@router.get("/stats")
async def system_stats():
    """系统统计大盘 (含 Token 消耗、系统资源与仿真状态)"""
    if psutil:
        mem = psutil.virtual_memory()
        mem_stats = {
            "total_mb": mem.total // (1024 * 1024),
            "used_mb": mem.used // (1024 * 1024),
            "percent": mem.percent
        }
    else:
        mem_stats = {"total_mb": 0, "used_mb": 0, "percent": 0}

    token_usage = state.get_token_usage_snapshot()
    totals = token_usage.get("totals", {})

    stats = {
        "memory": mem_stats,
        "simulation": {
            "started_at": state.service_state["started_at"],
            "uptime_seconds": (
                datetime.now() - datetime.fromisoformat(state.service_state["started_at"])
            ).total_seconds() if "started_at" in state.service_state else 0,
            "simulations_run": state.service_state["simulations_run"],
        },
        "agents": AgentRegistry.get_stats(),
        "tools": {"registered": 0, "stats": {"total_calls": 0}},
        "skills": {
            "registered": len(state.active_skills_module.SkillRegistry.list_skills()) 
            if state.active_skills_module and hasattr(state.active_skills_module, "SkillRegistry") else 0
        },
        "packets": PacketRecorder.get_stats(),
        "logs": logger.get_index_stats(),
        "tokens": {
            "total_calls": totals.get("events", 0),
            "total_tokens": totals.get("total", 0),
            "provider_total": totals.get("provider_total", 0),
        }
    }
    return stats

@router.get("/settings")
async def get_settings():
    """获取当前配置 (优先从 config.json 读取)"""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

@router.post("/settings")
async def update_settings(req: Request):
    """更新全局配置并保存"""
    data = await req.json()
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {}

    config.update(data)
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    # Reload engine if active
    if state.service_state.get("active_engine"):
        eng = state.service_state["active_engine"]
        if hasattr(eng, "reload_config"):
            eng.reload_config(config)

    return {"status": "success"}

# ═══════════════════════════════════════════════
# 工具与技能 (兜底)
# ═══════════════════════════════════════════════

@router.get("/tools")
async def list_tools():
    """列出所有已注册工具"""
    return {"tools": []}

@router.post("/tools/execute")
async def execute_tool(req: Request):
    """执行工具"""
    raise HTTPException(status_code=404, detail="Tools have been removed from the platform.")

@router.get("/tools/stats")
async def tool_stats():
    """工具调用统计"""
    return {"total_calls": 0}

@router.get("/skills")
async def list_skills():
    """列出所有已注册技能"""
    if state.active_skills_module and hasattr(state.active_skills_module, "SkillRegistry"):
        return {"skills": state.active_skills_module.SkillRegistry.list_skills()}
    return {"skills": []}

@router.post("/skills/execute")
async def execute_skill(req: Request):
    """执行技能 — 优先用场景 skills，找不到则回退抛出 404"""
    data = await req.json()
    skill_name = data.get("skill_name")
    params = data.get("params", {})
    if state.active_skills_module and hasattr(state.active_skills_module, "SkillRegistry"):
        try:
            result = state.active_skills_module.SkillRegistry.execute(skill_name, **params)
            return {"skill": skill_name, "result": result}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
            
    # 兜底方案：如果当前场景没有技能模块或未找到技能
    raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found. Active scene has no skills.")
