import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse

from agent_network import state
from agent_network.log_manager import (
    get_log_manager,
    infer_log_type,
    normalize_log_timestamp,
)

router = APIRouter()
log_manager = get_log_manager()


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
    for client in dead_clients:
        state.ws_clients.remove(client)


def _file_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/")
async def query_logs(
    agent_id: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    log_type: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(default=100, le=1000),
):
    entries = log_manager.query(
        agent_id=agent_id,
        level=level,
        event=event,
        log_type=log_type,
        keyword=keyword,
        limit=limit,
    )
    return {"backend": "memory", "total": len(entries), "entries": entries}


@router.get("/stats")
async def log_stats():
    return log_manager.get_index_stats()


@router.get("/agent/{agent_id}")
async def agent_logs(agent_id: str, limit: int = 50):
    return {
        "agent_id": agent_id,
        "entries": log_manager.get_agent_timeline(agent_id, limit),
    }


@router.get("/application")
async def application_logs(
    trace_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    limit: int = Query(default=100, le=1000),
):
    entries = log_manager.query(
        log_type="application",
        trace_id=trace_id,
        agent_id=agent_id,
        task_id=task_id,
        event=event,
        limit=limit,
    )
    return {"total": len(entries), "entries": entries}


@router.get("/network")
async def network_logs(
    trace_id: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    limit: int = Query(default=100, le=1000),
):
    entries = log_manager.query(
        log_type="network",
        trace_id=trace_id,
        event=event,
        limit=limit,
    )
    return {"total": len(entries), "entries": entries}


@router.get("/system")
async def system_logs(
    level: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(default=100, le=1000),
):
    entries = log_manager.query(
        log_type="system",
        level=level,
        event=event,
        keyword=keyword,
        limit=limit,
    )
    return {"total": len(entries), "entries": entries}


@router.get("/messages")
async def message_logs(
    trace_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    limit: int = 50,
):
    entries = log_manager.query(
        log_type="application",
        event="agent_message",
        trace_id=trace_id,
        agent_id=agent_id,
        task_id=task_id,
        limit=limit,
    )
    return {"total": len(entries), "entries": entries}


@router.get("/token-usage")
async def token_usage_logs():
    return state.get_token_usage_snapshot()


@router.get("/export")
async def export_logs(
    fmt: str = Query(default="jsonl", pattern="^(jsonl|json|csv)$"),
    limit: int = Query(default=0),
    log_type: Optional[str] = Query(None),
):
    content = log_manager.export(fmt=fmt, limit=limit, log_type=log_type)
    media_types = {
        "jsonl": "application/x-ndjson",
        "json": "application/json",
        "csv": "text/csv",
    }
    return PlainTextResponse(content, media_type=media_types.get(fmt, "text/plain"))


@router.get("/export/file")
async def export_logs_file(
    fmt: str = Query(default="jsonl", pattern="^(jsonl|json|csv)$"),
    limit: int = Query(default=0),
    log_type: Optional[str] = Query(None),
):
    filename = f"agent_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}"
    filepath = os.path.join(tempfile.gettempdir(), filename)
    log_manager.export_file(
        filepath,
        fmt=fmt,
        limit=limit,
        log_type=log_type,
    )
    return FileResponse(filepath, filename=filename)


@router.get("/files")
async def list_log_files(include_hidden: bool = Query(default=False)):
    return {"files": log_manager.list_log_files(include_hidden=include_hidden)}


@router.get("/download/{session_id}/{log_type}")
async def download_managed_log(session_id: str, log_type: str):
    try:
        filepath = log_manager.get_download_path(session_id, log_type)
    except (ValueError, FileNotFoundError) as exc:
        raise _file_error(exc) from exc
    return FileResponse(filepath, filename=os.path.basename(filepath))


@router.get("/download/{filename:path}")
async def download_log_file_compat(filename: str):
    parts = Path(filename).parts
    if len(parts) != 2:
        raise HTTPException(400, "Expected '<session>/<log_type or filename>'")
    session_id, log_type = parts
    try:
        filepath = log_manager.get_download_path(session_id, log_type)
    except (ValueError, FileNotFoundError) as exc:
        raise _file_error(exc) from exc
    return FileResponse(filepath, filename=os.path.basename(filepath))


@router.post("/files/{session_id}/{log_type}/hide")
async def hide_log_file(session_id: str, log_type: str):
    try:
        return log_manager.hide_log(session_id, log_type)
    except (ValueError, FileNotFoundError) as exc:
        raise _file_error(exc) from exc


@router.post("/files/{session_id}/{log_type}/show")
async def show_log_file(session_id: str, log_type: str):
    try:
        return log_manager.show_log(session_id, log_type)
    except (ValueError, FileNotFoundError) as exc:
        raise _file_error(exc) from exc


@router.delete("/files/{session_id}/{log_type}")
async def delete_log_file(session_id: str, log_type: str):
    try:
        return log_manager.delete_log(session_id, log_type)
    except (ValueError, FileNotFoundError) as exc:
        raise _file_error(exc) from exc


@router.delete("/sessions/{session_id}/logs")
async def delete_session_logs(session_id: str):
    try:
        return log_manager.delete_session_logs(session_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _file_error(exc) from exc


@router.post("/agent")
async def agent_log_ingest(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}

    agent_id = body.get("agent_id", "?")
    event = body.get("event", "acting")
    detail = body.get("detail", "")
    details = body.get("details", {})
    action_status = body.get("action_status", "success")
    from_agent = body.get("from_agent", agent_id)
    to_agent = body.get("to_agent", "") or details.get("target", "")
    action_name = body.get("action", event)
    content_text = body.get("content", details.get("content", ""))
    reasoning = body.get("reasoning", details.get("reasoning", ""))
    tool_name = body.get("tool_name", details.get("tool_name", ""))

    try:
        record = log_manager.emit_application_event(
            event=event,
            actor={"agent_id": from_agent, "name": body.get("agent_name", "")},
            target={"agent_id": to_agent} if to_agent else {},
            action={
                "type": action_name,
                "name": action_name,
                "status": action_status,
                "duration_ms": details.get("duration_ms", 0),
            },
            content={
                "content_type": "agent_log",
                "text": content_text or detail,
                "summary": (
                    detail or content_text or f"[{agent_id}] {action_name}"
                )[:120],
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
            tool={
                "name": tool_name,
                "input": details.get("arguments", {}),
                "output": details.get("result", {}),
                "status": action_status,
            } if tool_name else {},
            result={
                "status": action_status,
                "message": detail or f"[{agent_id}] {action_name}",
                "error_message": "" if action_status != "failed" else detail,
            },
            trace_id=body.get("trace_id", details.get("trace_id", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    state_entry = {
        "timestamp": _beijing_time(body.get("timestamp", "")),
        "level": "ERROR" if action_status == "failed" else "INFO",
        "agent_id": agent_id,
        "agent_name": body.get("agent_name", "?"),
        "event": event,
        "detail": detail,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "action": action_name,
        "action_status": action_status,
    }
    state.agent_logs.append(state_entry)
    if len(state.agent_logs) > 500:
        state.agent_logs.pop(0)

    if state.ws_clients:
        asyncio.create_task(
            _ws_broadcast({"type": "agent_log", "data": state_entry})
        )
    return {"status": "ok", "total_logs": len(state.agent_logs), "record": record}


@router.post("/ingest")
async def log_ingest(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}

    log_type = infer_log_type(body)
    if (
        not state.simulation_active
        and log_type == "network"
        and body.get("event") == "llm_api_packet"
    ):
        return {"status": "dropped", "reason": "simulation_inactive"}

    try:
        record = log_manager.ingest(body, log_type=log_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if record.get("event") == "llm_api_call":
        token_updated = state.append_token_usage_record(record)
        await _ws_broadcast({"type": "log_entries", "data": [record]})
        if token_updated:
            await _ws_broadcast({
                "type": "token_usage",
                "data": state.get_token_usage_snapshot(),
            })
    return {"status": "ok", "log_type": log_type, "record": record}


@router.get("/agent")
async def agent_logs_get(limit: int = Query(default=200)):
    entries = log_manager.query(
        log_type="application",
        event="acting",
        limit=limit,
    )
    return {"logs": entries, "total": len(entries)}
