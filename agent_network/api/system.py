import os
import json
from datetime import datetime
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Request

try:
    import psutil
except ImportError:
    psutil = None

from agent_network import state
from agent_network.agent_model import AgentRegistry
from agent_network.event_bus import PacketRecorder
from agent_network.logger import get_logger
from agent_network.comm import RemoteBus

router = APIRouter()
logger = get_logger()
comm = RemoteBus(message_bus_url=state.MESSAGE_BUS_URL)

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
        "tools": {
            "registered": len(state.active_tools_module.ToolRegistry.list_tools())
            if state.active_tools_module and hasattr(state.active_tools_module, "ToolRegistry") else 0,
            "stats": {"total_calls": 0}
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

    if state.service_state.get("active_engine"):
        eng = state.service_state["active_engine"]
        if hasattr(eng, "reload_config"):
            eng.reload_config(config)

    return {"status": "success"}


# ═══════════════════════════════════════════════
# 工具与技能调试接口
# ═══════════════════════════════════════════════

@router.get("/tools")
async def list_tools():
    """列出当前场景 ToolRegistry 中的原子工具。"""
    if state.active_tools_module and hasattr(state.active_tools_module, "ToolRegistry"):
        return {"tools": state.active_tools_module.ToolRegistry.list_tools()}
    return {"tools": []}


@router.post("/tools/execute")
async def execute_tool(req: Request):
    """Debug-only direct Tool execution.

    Normal Agent execution must go through backend-native MCP tool calling. This
    endpoint is disabled by default to avoid a second production tool path.
    """
    if os.environ.get("ENABLE_DEBUG_TOOL_EXECUTE") != "1":
        raise HTTPException(
            status_code=403,
            detail=(
                "Direct server-side tool execution is disabled. "
                "Tools should be called through backend-native MCP tool calling."
            )
        )

    data = await req.json()
    tool_name = data.get("tool_name")
    params = data.get("params", {})
    if state.active_tools_module and hasattr(state.active_tools_module, "ToolRegistry"):
        try:
            result = state.active_tools_module.ToolRegistry.execute(tool_name, **params)
            return {"tool": tool_name, "result": result}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found. Active scene has no tools.")


@router.get("/tools/stats")
async def tool_stats():
    """工具调用统计"""
    return {"total_calls": 0}
