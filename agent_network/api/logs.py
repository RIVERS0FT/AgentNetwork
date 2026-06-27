import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, FileResponse

from agent_network import state
from agent_network.logger import get_logger, normalize_log_timestamp, infer_log_layer, is_agent_message_record, is_agent_network_record, is_behavior_record
from agent_network.event_bus import PacketRecorder

router = APIRouter()
logger = get_logger()

def _beijing_time(utc_str: str = "") -> str:
    return normalize_log_timestamp(utc_str)

async def _ws_broadcast(message: dict):
    if not state.ws_clients:
        return
    dead_clients = set()
    for client in state.ws_clients:
        try:
            await client.send_json(message)
        except Exception:
            dead_clients.add(client)
    for c in dead_clients:
        state.ws_clients.remove(c)

# ═══════════════════════════════════════════════
# 日志查询 & 导出 API
# ═══════════════════════════════════════════════

@router.get("/")
async def query_logs(
    agent_id: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    layer: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(default=100, le=1000),
):
    entries = logger.query(
        agent_id=agent_id, level=level, event=event, layer=layer,
        category=category, keyword=keyword, limit=limit
    )
    return {"backend": "memory", "total": len(entries), "entries": entries}

@router.get("/stats")
async def log_stats():
    return logger.get_index_stats()

@router.get("/agent/{agent_id}")
async def agent_logs(agent_id: str, limit: int = 50):
    return {"agent_id": agent_id, "entries": logger.get_agent_timeline(agent_id, limit)}

@router.get("/application")
async def application_logs(
    trace_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    limit: int = Query(default=100, le=1000)
):
    entries = logger.query(layer="agent_application", trace_id=trace_id, agent_id=agent_id, task_id=task_id, event=event, limit=limit)
    return {"total": len(entries), "entries": entries}

@router.get("/network")
async def network_logs(
    trace_id: Optional[str] = Query(None),
    limit: int = Query(default=100, le=1000)
):
    entries = logger.query(layer="agent_network", trace_id=trace_id, limit=limit)
    return {"total": len(entries), "entries": entries}

@router.get("/messages")
async def message_logs(
    trace_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    limit: int = 50
):
    entries = logger.query(
        layer="agent_application",
        event="agent_message",
        trace_id=trace_id,
        agent_id=agent_id,
        task_id=task_id,
        limit=limit
    )
    return {"total": len(entries), "entries": entries}

@router.get("/token-usage")
async def token_usage_logs():
    return state.get_token_usage_snapshot()

@router.get("/export")
async def export_logs(fmt: str = Query(default="jsonl", pattern="^(jsonl|json|csv)$"), limit: int = Query(default=0)):
    content = logger.export(fmt=fmt, limit=limit)
    media_types = {"jsonl": "application/x-ndjson", "json": "application/json", "csv": "text/csv"}
    return PlainTextResponse(content, media_type=media_types.get(fmt, "text/plain"))

@router.get("/export/file")
async def export_logs_file(fmt: str = Query(default="jsonl", pattern="^(jsonl|json|csv)$"), limit: int = Query(default=0)):
    import tempfile
    filename = f"agent_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt if fmt != 'jsonl' else 'jsonl'}"
    filepath = os.path.join(tempfile.gettempdir(), filename)
    logger.export_file(filepath, fmt=fmt, limit=limit)
    return FileResponse(filepath, filename=filename)

@router.get("/files")
async def list_log_files():
    return {"files": logger.list_log_files()}

@router.get("/download/{filename:path}")
async def download_log_file(filename: str):
    log_dir = os.path.realpath(logger._log_dir)
    filepath = os.path.realpath(os.path.join(log_dir, filename))
    if not filepath.startswith(log_dir + os.sep) and filepath != log_dir:
        raise HTTPException(403, "Path traversal denied")
    if not os.path.isfile(filepath):
        raise HTTPException(404, f"Log file '{filename}' not found")
    return FileResponse(filepath, filename=os.path.basename(filename))

@router.post("/agent")
async def agent_log_ingest(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    agent_id = body.get("agent_id", "?")
    event = body.get("event", "act")
    detail = body.get("detail", "")
    details = body.get("details", {})
    action_status = body.get("action_status", "success")

    state.agent_logs.append({
        "timestamp": _beijing_time(body.get("timestamp", "")),
        "level": "ERROR" if action_status == "failed" else "INFO",
        "agent_id": agent_id,
        "agent_name": body.get("agent_name", "?"),
        "event": event,
        "detail": detail,
        "from_agent": body.get("from_agent", agent_id),
        "to_agent": body.get("to_agent", ""),
        "action": body.get("action", event),
        "action_status": action_status,
    })
    if len(state.agent_logs) > 500:
        state.agent_logs.pop(0)

        content_text = body.get("content", details.get("content", ""))
        reasoning = body.get("reasoning", details.get("reasoning", ""))

        logger.emit_application_event(
            event=event,
            actor={
                "agent_id": from_agent,
                "name": body.get("agent_name", ""),
            },
            target={"agent_id": to_agent} if to_agent else {},
            action={
                "type": action_name,
                "name": action_name,
                "status": action_status,
            },
            content={
                "content_type": "agent_log",
                "text": content_text or detail,
                "summary": (detail or content_text or f"[{agent_id}] {action_name}")[:120],
                "size_bytes": len((content_text or detail or "").encode("utf-8")),
            },
            decision={
                "decision_summary": reasoning[:200] if reasoning else "",
                "reasoning_visible": reasoning[:500] if reasoning else "",
            },
            skill={
                "name": body.get("skill_name", details.get("skill_name", "")),
                "input": body.get("skill_params", details.get("skill_params", {})),
                "output": body.get("skill_result", details.get("skill_result", {})),
                "status": action_status,
            },
            result={
                "status": action_status,
                "message": detail or f"[{agent_id}] {action_name}",
                "error_message": "" if action_status != "failed" else detail,
            },
            trace_id=body.get("trace_id", details.get("trace_id", "")),
            tick=body.get("tick", details.get("tick", 0)),
            level="ERROR" if action_status == "failed" else "INFO",
            component=agent_id,
            source="agent",
            debug={
                "schema_version": "application.v1",
                "emitter": "api.logs.agent_log_ingest",
                "legacy_agent_log_ingest": True,
            },
        )
        if state.ws_clients:
            asyncio.create_task(_ws_broadcast({"type": "agent_log", "data": state.agent_logs[-1]}))
        return {"status": "ok", "total_logs": len(state.agent_logs)}

@router.post("/ingest")
async def log_ingest(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not state.simulation_active and infer_log_layer(body) == "agent_network" and body.get("event") == "llm_api_packet":
        return {"status": "dropped", "reason": "simulation_inactive"}
    
    record = {
        "timestamp": body.get("timestamp", ""),
        "level": body.get("level", "INFO"),
        "source": body.get("source", "external"),
        "component": body.get("component", "unknown"),
        "category": body.get("category", "system"),
        "layer": body.get("layer", ""),
        "event": body.get("event", "log"),
        "actor": body.get("actor", {}),
        "target": body.get("target", {}),
        "action": body.get("action", {}),
        "message": body.get("message", ""),
        "payload": body.get("payload", {}),
        "network": body.get("network", {}),
        "trace": body.get("trace", {}),
    }
    if not record["message"] and record["payload"].get("content"):
        record["message"] = str(record["payload"]["content"])[:120]
    
    logger.ingest(record)
    if record.get("event") in {"llm_api_call", "llm_cli_call"}:
        token_updated = state.append_token_usage_record(record)
        await _ws_broadcast({"type": "log_entries", "data": [record]})
        if token_updated:
            await _ws_broadcast({"type": "token_usage", "data": state.get_token_usage_snapshot()})
    return {"status": "ok"}

@router.get("/agent")
async def agent_logs_get(limit: int = Query(default=200)):
    entries = logger.query(event="agent_action", limit=limit)
    return {"logs": entries, "total": len(entries)}
