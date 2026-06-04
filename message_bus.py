#!/usr/bin/env python3
"""
消息总线 — 运行在 Host，路由 Agent 容器间的消息

每个 Agent 容器通过 HTTP POST /relay 发送消息
消息总线根据 target 转发到目标 Agent 容器

同时记录所有消息，用于通信分析
"""

import os
import sys
import json
import time
from typing import Dict, List, Any
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn
import requests
import time

from agent_network.metrics import MetricsRegistry

app = FastAPI(title="Agent Message Bus")
metrics = MetricsRegistry()

# 注册 Prometheus /metrics 端点
MetricsRegistry.add_metrics_route(app)

# ── 可选的外部服务转发 ──
LOG_COLLECTOR_URL = os.environ.get("LOG_COLLECTOR_URL", "")
PACKET_MONITOR_URL = os.environ.get("PACKET_MONITOR_URL", "")


class RelayMessage(BaseModel):
    from_id: str
    from_name: str = ""
    to: str
    content: str
    reasoning: str = ""


# Agent 注册表: {agent_id: "http://host:port"}
agent_registry: Dict[str, str] = {}

# 消息记录
message_log: List[Dict] = []
stats = {
    "total_messages": 0,
    "by_source": {},
    "by_target": {},
    "start_time": datetime.now().isoformat(),
}


@app.get("/health")
async def health():
    return {"status": "ok", "agents": len(agent_registry)}


@app.post("/register")
async def register(agent_id: str, url: str):
    """Agent 容器注册自己"""
    agent_registry[agent_id] = url
    print(f"[Bus] Registered: {agent_id} @ {url}")
    return {"registered": agent_id, "total": len(agent_registry)}


@app.post("/unregister")
async def unregister(agent_id: str):
    agent_registry.pop(agent_id, None)
    return {"unregistered": agent_id}


@app.post("/relay")
async def relay(msg: RelayMessage):
    """转发消息到目标 Agent"""
    relay_start = time.time()

    stats["total_messages"] += 1
    stats["by_source"][msg.from_id] = stats["by_source"].get(msg.from_id, 0) + 1
    stats["by_target"][msg.to] = stats["by_target"].get(msg.to, 0) + 1

    entry = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "from": msg.from_id, "to": msg.to,
        "content": msg.content[:200], "reasoning": msg.reasoning[:100],
    }
    message_log.append(entry)

    # ── Prometheus 指标 ──
    metrics.record_message(source=msg.from_id, target=msg.to, msg_type="relay")

    # ── 日志收集器转发 ──
    if LOG_COLLECTOR_URL:
        try:
            requests.post(f"{LOG_COLLECTOR_URL}/api/logs/ingest", json={
                "level": "INFO", "event": "message_relayed",
                "agent_id": msg.from_id,
                "index": "logs-agent",
                "message": msg.content[:500],
                "details": {"to": msg.to, "reasoning": msg.reasoning[:200]},
            }, timeout=1)
        except Exception:
            pass

    # ── 数据包监控器转发 ──
    if PACKET_MONITOR_URL:
        try:
            requests.post(f"{PACKET_MONITOR_URL}/api/packets/ingest", json={
                "from_id": msg.from_id, "from_name": msg.from_name,
                "to": msg.to, "content": msg.content,
                "reasoning": msg.reasoning,
                "type": "relay",
                "direction": "outbound",
                "latency": (time.time() - relay_start) * 1000,
            }, timeout=1)
        except Exception:
            pass

    # 广播模式
    if msg.to == "broadcast":
        results = {}
        for aid, url in agent_registry.items():
            if aid != msg.from_id:
                try:
                    resp = requests.post(f"{url}/message", json={
                        "from_id": msg.from_id, "from_name": msg.from_name,
                        "content": msg.content,
                    }, timeout=5)
                    results[aid] = resp.status_code
                except Exception as e:
                    results[aid] = str(e)
        return {"broadcast": True, "targets": len(results), "results": results}

    # 单播
    target_url = agent_registry.get(msg.to)
    if not target_url:
        metrics.record_error(service="message-bus", error_type="target_not_found")
        return {"error": f"Target '{msg.to}' not found", "known": list(agent_registry.keys())}

    try:
        resp = requests.post(f"{target_url}/message", json={
            "from_id": msg.from_id, "from_name": msg.from_name,
            "content": msg.content,
        }, timeout=5)
        relay_latency = (time.time() - relay_start) * 1000
        metrics.message_latency_seconds.labels(
            from_role=msg.from_id, to_role=msg.to
        ).observe(relay_latency / 1000.0)
        return {"relayed": True, "to": msg.to, "status": resp.status_code}
    except Exception as e:
        metrics.record_error(service="message-bus", error_type="relay_error")
        return {"error": str(e), "to": msg.to}


@app.get("/agents")
async def list_agents():
    return {"agents": agent_registry, "count": len(agent_registry)}


@app.get("/messages")
async def get_messages(limit: int = 50):
    return {"total": len(message_log), "messages": message_log[-limit:]}


@app.get("/stats")
async def get_stats():
    return {
        **stats,
        "agent_count": len(agent_registry),
        "log_size": len(message_log),
    }


if __name__ == "__main__":
    port = int(os.environ.get("BUS_PORT", 9000))
    print(f"[Message Bus] Starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
