#!/usr/bin/env python3
"""
日志收集服务 — 对应架构文档 第十节：日志系统设计

独立 FastAPI 服务，集中收集、存储和查询所有 Agent 日志。
支持 6 级索引 (L1-L5 + AUDIT)，可选 Elasticsearch 后端，WebSocket 实时推送。

环境变量:
    LOG_COLLECTOR_PORT    - 服务端口 (默认 9200)
    LOG_BUFFER_SIZE       - 内存缓冲大小 (默认 5000)
    ES_ENABLED            - 是否启用 ES ("true"/"false", 默认 false)
    ES_HOSTS              - ES 节点列表 (默认 http://elasticsearch:9200)
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

from agent_network.log_collector import LogCollector
from agent_network.metrics import MetricsRegistry

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

PORT = int(os.environ.get("LOG_COLLECTOR_PORT", 9200))
BUFFER_SIZE = int(os.environ.get("LOG_BUFFER_SIZE", 5000))
ES_ENABLED = os.environ.get("ES_ENABLED", "").lower() == "true"

app = FastAPI(title="Agent Log Collector", version="0.1.0")
collector = LogCollector()
metrics = MetricsRegistry()

# 注册 Prometheus /metrics 端点
MetricsRegistry.add_metrics_route(app)


# ═══════════════════════════════════════════════
# 模型
# ═══════════════════════════════════════════════

class LogEntryRequest(BaseModel):
    """单条日志摄入请求"""
    level: str = "INFO"
    event: str = ""
    agent_id: str = ""
    agent_name: str = ""
    agent_role: str = ""
    tool_name: str = ""
    level_type: str = ""  # L1/L2/L3/L4/L5/AUDIT
    index: str = "logs-system"
    message: str = ""
    details: Dict[str, Any] = {}
    prompt_text: str = ""
    response_text: str = ""
    packet_id: str = ""
    direction: str = ""
    source_agent: str = ""
    target_agent: str = ""
    latency_ms: float = 0.0
    token_usage: int = 0


class BatchLogRequest(BaseModel):
    """批量日志摄入请求"""
    entries: List[LogEntryRequest]
    index: str = "logs-system"


# ═══════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════

@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "log-collector",
        **collector.get_index_stats(),
    }


@app.post("/api/logs/ingest")
async def ingest_log(req: LogEntryRequest):
    """接收单条日志"""
    entry = req.model_dump()
    index_name = entry.pop("index", "logs-system")

    # 确定索引
    if not index_name.startswith("logs-"):
        index_name = f"logs-{index_name}"

    collector.ingest_log(entry, index_name)
    return {"received": True, "total": collector._stats["total_entries"]}


@app.post("/api/logs/ingest-batch")
async def ingest_batch(req: BatchLogRequest):
    """批量接收日志"""
    entries = []
    default_index = req.index if req.index.startswith("logs-") else f"logs-{req.index}"
    for item in req.entries:
        entry = item.model_dump()
        index_name = entry.pop("index", default_index)
        if not index_name.startswith("logs-"):
            index_name = f"logs-{index_name}"
        entries.append((entry, index_name))

    collector.ingest_batch(entries)
    return {"received": len(entries), "total": collector._stats["total_entries"]}


@app.get("/api/logs")
async def query_logs(
    agent_id: str = Query(None, description="按 Agent ID 过滤"),
    level: str = Query(None, description="日志等级: INFO/WARN/ERROR/FATAL/AUDIT"),
    level_type: str = Query(None, description="层级: L1/L2/L3/L4/L5/AUDIT"),
    event: str = Query(None, description="事件名包含"),
    index: str = Query(None, description="索引名: logs-system/logs-agent/..."),
    keyword: str = Query(None, description="全文搜索关键词"),
    backend: str = Query("memory", description="查询后端: memory / es"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    查询日志 — 对应架构文档 第十一节：日志搜索工具

    支持两种查询后端:
    - memory: 从内存缓冲查询（默认、快速）
    - es: 从 Elasticsearch 查询（需要 ES 可用）
    """
    if backend == "es" and ES_ENABLED:
        result = await collector.es_query(
            index=index or "logs-*",
            keyword=keyword or "",
            agent_id=agent_id,
            level=level,
            event=event,
            limit=limit,
        )
        return {"backend": "elasticsearch", **result}
    else:
        result = collector.query(
            agent_id=agent_id,
            level=level,
            level_type=level_type,
            event_contains=event,
            index=index,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
        return {"backend": "memory", **result}


@app.get("/api/logs/stats")
async def log_stats():
    """日志索引统计 — 对应架构文档 Elasticsearch 索引统计"""
    stats = collector.get_index_stats()

    # 如果 ES 可用，补充 ES 信息
    if ES_ENABLED and collector._es_client and collector._es_client.available:
        try:
            es_stats = await collector._es_client.get_index_stats()
            stats["es_indexes"] = es_stats
        except Exception:
            stats["es_indexes"] = {"error": "unavailable"}

    return stats


@app.get("/api/logs/analyze")
async def analyze_errors(
    agent_id: str = Query(None),
    hours: int = Query(1, description="最近 N 小时"),
):
    """
    AI 日志分析 — 对应架构文档 第十一节：AI日志分析

    聚合 ERROR/FATAL 日志，提供结构化分析数据。
    """
    # 内存查询
    errors = collector.query(
        agent_id=agent_id,
        level="ERROR",
        limit=1000,
    )
    fatal = collector.query(
        agent_id=agent_id,
        level="FATAL",
        limit=100,
    )

    # 按索引聚合
    by_index = {}
    for entry in errors["entries"]:
        idx = entry.get("index", "unknown")
        if idx not in by_index:
            by_index[idx] = {"count": 0, "events": {}}
        by_index[idx]["count"] += 1
        event = entry.get("event", "unknown")
        by_index[idx]["events"][event] = by_index[idx]["events"].get(event, 0) + 1

    # 如果 ES 可用，也查询 ES
    es_analysis = None
    if ES_ENABLED and collector._es_client and collector._es_client.available:
        try:
            es_analysis = await collector._es_client.analyze_errors(
                agent_id=agent_id, hours=hours
            )
        except Exception:
            es_analysis = {"error": "ES query failed"}

    return {
        "memory_errors": errors["total"],
        "memory_fatal": fatal["total"],
        "by_index": by_index,
        "elasticsearch": es_analysis,
        "suggestion": (
            "最近错误集中在: " + ", ".join(
                f"{idx}({info['count']}条)" for idx, info in
                sorted(by_index.items(), key=lambda x: x[1]["count"], reverse=True)[:3]
            ) if by_index else "无错误"
        ),
    }


# ═══════════════════════════════════════════════
# WebSocket 实时推送
# ═══════════════════════════════════════════════

@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """实时推送日志流"""
    await websocket.accept()
    try:
        async for entry in collector.subscribe():
            await websocket.send_json(entry)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[LogCollector] WebSocket error: {e}")


# ═══════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    print(f"[Log Collector] Starting on port {PORT}")
    print(f"[Log Collector] Buffer size: {BUFFER_SIZE}")
    print(f"[Log Collector] Elasticsearch: {'enabled' if ES_ENABLED else 'disabled'}")

    if ES_ENABLED:
        from agent_network.es_client import ESClient
        es = ESClient()
        ok = await es.initialize()
        if ok:
            collector.set_es_client(es)
            print("[Log Collector] Elasticsearch connected")
        else:
            print("[Log Collector] Elasticsearch unavailable, using memory-only mode")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
