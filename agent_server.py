#!/usr/bin/env python3
"""
Agent 容器运行时 — 每个 Docker 容器内运行的 HTTP 服务

接收消息 → LLM 决策 → 发送消息 → 返回状态

环境变量:
  AGENT_ID: Agent ID
  AGENT_ROLE: 角色
  AGENT_NAME: 名称
  MESSAGE_BUS_URL: 消息总线地址
  LLM_API_KEY: LLM API Key（可选）
  LLM_MODEL: 模型名（可选）
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uvicorn
import requests

from agent_network.brain import Brain, Action
from agent_network.message import Message
from agent_network.metrics import MetricsRegistry


# ═══════════════════════════════════════════════
# Agent 身份
# ═══════════════════════════════════════════════

AGENT_ID = os.environ.get("AGENT_ID", "agent-001")
AGENT_ROLE = os.environ.get("AGENT_ROLE", "scout")
AGENT_NAME = os.environ.get("AGENT_NAME", AGENT_ID)
MESSAGE_BUS = os.environ.get("MESSAGE_BUS_URL", "http://localhost:9000")
LOG_COLLECTOR_URL = os.environ.get("LOG_COLLECTOR_URL", "")
PACKET_MONITOR_URL = os.environ.get("PACKET_MONITOR_URL", "")

app = FastAPI(title=f"Agent {AGENT_NAME}")
metrics = MetricsRegistry()

# 注册 Prometheus /metrics 端点
MetricsRegistry.add_metrics_route(app)

# LLM 配置
brain_config = {}
if os.environ.get("LLM_API_KEY"):
    brain_config["api_key"] = os.environ["LLM_API_KEY"]
    brain_config["model"] = os.environ.get("LLM_MODEL", "")
    brain_config["provider"] = os.environ.get("LLM_PROVIDER", "auto")

brain = Brain(role=AGENT_ROLE, name=AGENT_NAME, config=brain_config)
inbox: List[Dict] = []
turn = 0


# ═══════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════

class MessageIn(BaseModel):
    from_id: str
    from_name: str = ""
    content: str
    type: str = "message"


class DecideRequest(BaseModel):
    context: Dict[str, Any] = {}


@app.get("/status")
async def status():
    """Agent 状态"""
    metrics.set_agent_active(AGENT_ID, AGENT_ROLE, "running")
    return {
        "agent_id": AGENT_ID,
        "name": AGENT_NAME,
        "role": AGENT_ROLE,
        "turn": turn,
        "inbox_size": len(inbox),
        "has_llm": bool(brain_config.get("api_key")),
    }


@app.post("/message")
async def receive_message(msg: MessageIn):
    """接收来自其他 Agent 的消息"""
    inbox.append({
        "from": msg.from_name or msg.from_id,
        "content": msg.content,
        "type": msg.type,
    })
    if len(inbox) > 50:
        inbox.pop(0)
    return {"received": True, "inbox_size": len(inbox)}


@app.post("/decide")
async def decide(req: DecideRequest = None):
    """触发 LLM 决策，返回 Action"""
    global turn
    turn += 1
    ctx = req.context if req else {}
    ctx["round"] = turn
    action = brain.decide(inbox, ctx)

    # 记录操作指标
    metrics.record_agent_operation(AGENT_ID, "decide", "completed")

    # 转发日志到 Log Collector
    if LOG_COLLECTOR_URL:
        try:
            requests.post(f"{LOG_COLLECTOR_URL}/api/logs/ingest", json={
                "level": "INFO", "event": "agent_decide",
                "agent_id": AGENT_ID, "agent_name": AGENT_NAME, "agent_role": AGENT_ROLE,
                "index": "logs-agent",
                "message": f"Decision: {action.type} → {action.target or 'self'}: {action.content[:200]}",
                "details": action.to_dict(),
            }, timeout=1)
        except Exception:
            pass

    return {
        "agent_id": AGENT_ID,
        "agent_name": AGENT_NAME,
        "turn": turn,
        "has_llm": bool(brain_config.get("api_key")),
        **action.to_dict(),
    }


@app.post("/act")
async def act():
    """执行最近一次决策并发送消息到消息总线"""
    ctx = {"round": turn}
    action = brain.decide(inbox, ctx)
    result = {"action": action.to_dict()}

    # 如果是发送消息，通过消息总线转发
    if action.type in ("send_message", "broadcast"):
        try:
            relay_start = time.time()
            resp = requests.post(f"{MESSAGE_BUS}/relay", json={
                "from_id": AGENT_ID,
                "from_name": AGENT_NAME,
                "to": action.target if action.type == "send_message" else "broadcast",
                "content": action.content,
                "reasoning": action.reasoning,
            }, timeout=5)
            result["relayed"] = resp.json() if resp.ok else {"error": resp.status_code}

            # 转发数据包到 Packet Monitor
            if PACKET_MONITOR_URL:
                try:
                    requests.post(f"{PACKET_MONITOR_URL}/api/packets/ingest", json={
                        "from_id": AGENT_ID, "from_name": AGENT_NAME,
                        "to": action.target if action.type == "send_message" else "broadcast",
                        "content": action.content, "reasoning": action.reasoning,
                        "type": action.type,
                        "direction": "outbound",
                    }, timeout=1)
                except Exception:
                    pass

            metrics.record_agent_operation(AGENT_ID, "act", "completed")
        except Exception as e:
            result["relay_error"] = str(e)
            metrics.record_agent_operation(AGENT_ID, "act", "error")
            metrics.record_error(service="agent_server", error_type="relay_failed")
    else:
        metrics.record_agent_operation(AGENT_ID, action.type, "completed")

    # 转发日志到 Log Collector
    if LOG_COLLECTOR_URL:
        try:
            requests.post(f"{LOG_COLLECTOR_URL}/api/logs/ingest", json={
                "level": "INFO", "event": "agent_act",
                "agent_id": AGENT_ID, "agent_name": AGENT_NAME, "agent_role": AGENT_ROLE,
                "index": "logs-agent",
                "message": f"Action: {action.type}: {action.content[:200]}",
                "details": result,
            }, timeout=1)
        except Exception:
            pass

    return result


@app.get("/inbox")
async def get_inbox():
    """查看收件箱"""
    return {"inbox": inbox[-20:]}


@app.post("/clear")
async def clear():
    """清空收件箱"""
    inbox.clear()
    return {"cleared": True}


# ═══════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"[Agent] {AGENT_NAME} ({AGENT_ROLE}) starting on port {port}")
    print(f"[Agent] Message bus: {MESSAGE_BUS}")
    print(f"[Agent] LLM: {'enabled' if brain_config.get('api_key') else 'disabled'}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
