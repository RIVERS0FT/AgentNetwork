#!/usr/bin/env python3
"""
数据包监控服务 — 对应架构文档 第九节：收包发包全链路记录

独立 FastAPI 服务，实时捕获和查询所有 Agent 间消息。
可作为 Docker 容器运行，向 Prometheus 暴露指标，支持 WebSocket 实时推送。

环境变量:
    PACKET_MONITOR_PORT    - 服务端口 (默认 9100)
    PACKET_BUFFER_SIZE     - 环形缓冲区大小 (默认 10000)
    MESSAGE_BUS_URL        - 消息总线地址 (用于注册回调，可选)
"""

import os
import sys
import json
import asyncio
from typing import List, Optional, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel
import uvicorn

from agent_network.packet_monitor import PacketMonitor
from agent_network.metrics import MetricsRegistry

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

PORT = int(os.environ.get("PACKET_MONITOR_PORT", 9100))
BUFFER_SIZE = int(os.environ.get("PACKET_BUFFER_SIZE", 10000))

app = FastAPI(title="Agent Packet Monitor", version="0.1.0")
monitor = PacketMonitor()
metrics = MetricsRegistry()

# 注册 Prometheus /metrics 端点
MetricsRegistry.add_metrics_route(app)


# ═══════════════════════════════════════════════
# 模型
# ═══════════════════════════════════════════════

class PacketIngestRequest(BaseModel):
    """message_bus 推送数据包"""
    from_id: str = ""
    from_name: str = ""
    source: str = ""
    to: str = ""
    target: str = ""
    content: str = ""
    reasoning: str = ""
    type: str = "relay"
    direction: str = "outbound"
    latency: float = 0.0
    token_usage: int = 0


class BatchIngestRequest(BaseModel):
    packets: List[PacketIngestRequest]


# ═══════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════

@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "packet-monitor",
        "buffer_size": len(monitor._buffer),
        "total_packets": monitor._stats["total_packets"],
        "subscribers": len(monitor._subscribers),
    }


@app.get("/api/packets")
async def query_packets(
    agent_id: str = Query(None, description="按 Agent ID 过滤"),
    direction: str = Query(None, description="方向: outbound / inbound"),
    message_type: str = Query(None, description="消息类型"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """查询数据包记录"""
    records = monitor.get_records(
        agent_id=agent_id,
        direction=direction,
        message_type=message_type,
        limit=limit,
        offset=offset,
    )
    return {
        "total": len(records),
        "limit": limit,
        "offset": offset,
        "records": records,
    }


@app.get("/api/packets/stats")
async def packet_stats():
    """数据包统计"""
    return monitor.get_stats()


@app.post("/api/packets/ingest")
async def ingest_packet(req: PacketIngestRequest):
    """接收数据包（由 message_bus / agent_server 推送）"""
    data = req.model_dump()

    # 字段标准化：同时支持 from_id/to（message_bus 格式）和 source/target
    if not data.get("source"):
        data["source"] = data.get("from_id", "unknown")
    if not data.get("target"):
        data["target"] = data.get("to", "broadcast")

    monitor.ingest_packet_dict(data, data.get("direction", "outbound"))
    return {"received": True, "total": monitor._stats["total_packets"]}


@app.post("/api/packets/ingest-batch")
async def ingest_batch(req: BatchIngestRequest):
    """批量接收数据包"""
    count = 0
    for pkt in req.packets:
        data = pkt.model_dump()
        if not data.get("source"):
            data["source"] = data.get("from_id", "unknown")
        if not data.get("target"):
            data["target"] = data.get("to", "broadcast")
        monitor.ingest_packet_dict(data, data.get("direction", "outbound"))
        count += 1
    return {"received": count, "total": monitor._stats["total_packets"]}


# ═══════════════════════════════════════════════
# WebSocket 实时推送
# ═══════════════════════════════════════════════

@app.websocket("/ws/packets")
async def ws_packets(websocket: WebSocket):
    """实时推送数据包流"""
    await websocket.accept()
    try:
        async for packet in monitor.subscribe():
            await websocket.send_json(packet)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[PacketMonitor] WebSocket error: {e}")


@app.websocket("/ws/stats")
async def ws_stats(websocket: WebSocket):
    """定时推送统计信息（每秒）"""
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(monitor.get_stats())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[PacketMonitor] WebSocket stats error: {e}")


# ═══════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    print(f"[Packet Monitor] Starting on port {PORT}")
    print(f"[Packet Monitor] Buffer size: {BUFFER_SIZE}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
