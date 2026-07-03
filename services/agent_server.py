#!/usr/bin/env python3
"""Agent container runtime HTTP service."""

import os
import sys
import json
import time
import asyncio
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Any
import uvicorn
import requests

from agent_network.logger import get_logger
from agent_network.comm import DirectBus
from agent_network.a2a import (
    A2AProtocolError,
    build_agent_card,
    build_jsonrpc_error,
    build_jsonrpc_success,
    extract_message_send,
)
from agent_network.a2a_middleware import A2ATiming, content_size_bytes, emit_a2a_network_event, normalize_agent_id
from agent_network.full_packet_capture import start_full_capture, stop_full_capture
from agent_network.adapters.base import AgentContext
from agent_network.adapters.claude_code import ClaudeCodeAdapter
from agent_network.adapters.direct_llm import DirectLLMAdapter
from agent_network.adapters.openclaw import OpenCLAWAdapter

AGENT_ID = os.environ.get("AGENT_ID", "agent-001")
AGENT_ROLE = os.environ.get("AGENT_ROLE", "generic")
AGENT_NAME = os.environ.get("AGENT_NAME", AGENT_ID)
AGENT_PORT = int(os.environ.get("PORT", "8000"))
SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000")

AGENT_CORE_GOAL = os.environ.get("AGENT_CORE_GOAL", "")
AGENT_ACTION_SPACE = json.loads(os.environ.get("AGENT_ACTION_SPACE", "[]"))
AGENT_INITIAL_ASSETS = json.loads(os.environ.get("AGENT_INITIAL_ASSETS", "{}"))
AGENT_SYSTEM_PROMPT = os.environ.get("AGENT_SYSTEM_PROMPT", "")
AGENT_INTERACTION_PARADIGM = os.environ.get("AGENT_INTERACTION_PARADIGM", "")
AGENT_PARADIGM_HINT = os.environ.get("AGENT_PARADIGM_HINT", "")

BACKEND = os.environ.get("AGENT_BACKEND", "openclaw")
if BACKEND == "claudecode":
    BACKEND = "claude-code"
if BACKEND in {"direct-llm", "directllm"}:
    BACKEND = "direct_llm"

SUPPORTED_BACKENDS = {"openclaw", "claude-code", "direct_llm"}
if BACKEND not in SUPPORTED_BACKENDS:
    raise RuntimeError(f"Unsupported AGENT_BACKEND={BACKEND!r}.")

API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

comm = DirectBus(server_url=SERVER_URL)
logger = get_logger()
backend_label = {"openclaw": "OpenCLAW", "claude-code": "Claude Code", "direct_llm": "Direct LLM"}.get(BACKEND, BACKEND)
app = FastAPI(title=f"Agent {AGENT_NAME} ({backend_label})")

turn = 0
inbox: list = []
_event_queue: list = []


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _safe_post_json(url: str, json_data: dict, timeout: float = 3) -> bool:
    try:
        requests.post(url, json=json_data, timeout=timeout)
        return True
    except Exception:
        return False


def _append_inbox(from_agent: str, content: str, msg_type: str = "direct", metadata: Dict[str, Any] = None):
    item = {"from": from_agent, "content": content, "type": msg_type}
    if metadata:
        item.update({k: v for k, v in metadata.items() if v is not None})
    inbox.append(item)
    if len(inbox) > 50:
        inbox.pop(0)


def _clear_inbox():
    inbox.clear()


def _inbox_size() -> int:
    return len(inbox)


def _log_agent(event: str, detail: str, **kw):
    action_type = kw.get("action_type", event)
    target = kw.get("target", kw.get("to", ""))
    _safe_post_json(f"{SERVER_URL}/api/logs/agent", {
        "agent_id": AGENT_ID,
        "agent_name": AGENT_NAME,
        "event": event,
        "detail": detail,
        "timestamp": _now_iso(),
        "from_agent": AGENT_ID,
        "to_agent": target if action_type in ("send_message", "broadcast") else "",
        "action": action_type,
        "action_status": kw.get("status", "success"),
        "details": {k: v for k, v in kw.items() if k not in ("action_type", "target")},
    }, timeout=2)


def _emit_inbound_a2a(
    *,
    source_id: str,
    status_code: int,
    latency_ms: float,
    request_id: Any = "",
    trace_id: str = "",
    channel_id: str = "",
    talk: str = "",
    content_bytes: int = 0,
    allowed: bool = None,
    error: str = "",
    path: str = "/a2a",
    extra: Dict[str, Any] = None,
):
    emit_a2a_network_event(
        server_url=SERVER_URL,
        direction="inbound",
        component=normalize_agent_id(AGENT_ID),
        source_id=source_id,
        target_id=normalize_agent_id(AGENT_ID),
        status_code=status_code,
        latency_ms=latency_ms,
        path=path,
        trace_id=trace_id,
        channel_id=channel_id,
        talk=talk,
        request_id=request_id,
        content_bytes=content_bytes,
        allowed=allowed,
        error=error,
        extra=extra or {},
    )


def _skill_names_from_legacy(skills: List[Dict[str, Any]]) -> List[str]:
    names = []
    for item in skills or []:
        if isinstance(item, dict):
            names.append(item.get("name") or item.get("skill_name") or "")
        elif isinstance(item, str):
            names.append(item)
    return list(dict.fromkeys([name for name in names if name]))


class MessageIn(BaseModel):
    from_id: str
    from_name: str = ""
    content: str
    type: str = "message"
    channel_id: str = ""
    talk: str = ""
    trace_id: str = ""


class RunRequest(BaseModel):
    trace_id: str = ""
    agent_id: str = ""
    agent_name: str = ""
    role: str = ""
    core_goal: str = ""
    task: str = ""
    messages: List[Dict[str, Any]] = []
    skills: List[Dict[str, Any]] = []
    allowed_skills: List[str] = []
    allowed_tools: List[str] = []
    permissions: Dict[str, Any] = {}
    state_snapshot: Dict[str, Any] = {}
    tick: int = 0
    timeout_seconds: int = 60
    max_turns: int = 10
    scene_key: str = "default"
    agent_directory: Dict[str, str] = {}
    comm_matrix: Dict[str, List[str]] = {}


def _make_adapter():
    if BACKEND == "claude-code":
        return ClaudeCodeAdapter()
    if BACKEND == "direct_llm":
        return DirectLLMAdapter()
    return OpenCLAWAdapter()


@app.post("/run")
async def run_agent(req: RunRequest):
    allowed_skills = req.allowed_skills or _skill_names_from_legacy(req.skills)
    comm.update_directory(req.agent_directory, req.comm_matrix)
    context = AgentContext(
        trace_id=req.trace_id,
        agent_id=(req.agent_id or AGENT_ID).lower(),
        agent_name=req.agent_name or AGENT_NAME,
        role=req.role or AGENT_ROLE,
        core_goal=req.core_goal or AGENT_CORE_GOAL,
        task=req.task,
        messages=req.messages or inbox,
        skills=req.skills or [],
        allowed_tools=req.allowed_tools,
        permissions=req.permissions,
        state_snapshot=req.state_snapshot,
        tick=req.tick,
        timeout_seconds=req.timeout_seconds,
        max_turns=req.max_turns,
        scene_key=req.scene_key or os.environ.get("AGENT_SCENE_KEY", "default"),
        allowed_skills=allowed_skills,
        agent_directory=req.agent_directory,
        comm_matrix=req.comm_matrix,
    )

    adapter = _make_adapter()
    result = await asyncio.to_thread(adapter.run_agent_task, context)

    for event in getattr(result, "application_events", []) or []:
        logger.emit_application_event(
            event=event.get("event", "agent_event"),
            actor=event.get("actor", {"agent_id": context.agent_id}),
            target=event.get("target", {}),
            task=event.get("task", {"goal": context.task}),
            conversation=event.get("conversation", {}),
            action=event.get("action", {}),
            content=event.get("content", {}),
            decision=event.get("decision", {}),
            skill=event.get("skill", {}),
            tool=event.get("tool", {}),
            state_change=event.get("state_change", {}),
            policy=event.get("policy", {}),
            result=event.get("result", {}),
            metrics=event.get("metrics", {}),
            links=event.get("links", {}),
            trace_id=event.get("trace_id", context.trace_id),
            tick=context.tick,
            component=context.agent_id,
            source="agent",
            debug={"schema_version": "application.v1", "emitter": "agent_server.run_agent"},
        )

    return result.__dict__


@app.get("/status")
async def status():
    return {
        "agent_id": AGENT_ID,
        "name": AGENT_NAME,
        "role": AGENT_ROLE,
        "backend": BACKEND,
        "turn": turn,
        "inbox_size": _inbox_size(),
        "has_llm": bool(API_KEY),
        "core_goal": AGENT_CORE_GOAL or None,
        "action_space": AGENT_ACTION_SPACE,
        "initial_assets": AGENT_INITIAL_ASSETS,
        "network_mode": "direct",
        "agent_protocol": "a2a",
        "a2a_endpoint": "/a2a",
        "comm_policy": comm.policy_snapshot(),
    }


def _agent_base_url(request: Request) -> str:
    public_url = os.environ.get("AGENT_PUBLIC_URL", "").strip()
    if public_url:
        return public_url.rstrip("/")
    return str(request.base_url).rstrip("/")


@app.get("/.well-known/agent-card.json")
async def well_known_agent_card(request: Request):
    base = _agent_base_url(request)
    skills = [
        {
            "id": "send_message",
            "name": "send_message",
            "description": "Receive a direct A2A message/send text message.",
            "tags": ["agent-network", "a2a", "direct"],
            "examples": ["message/send with a text part and metadata.from_agent"],
        }
    ]
    for action in AGENT_ACTION_SPACE or []:
        if isinstance(action, str) and action not in {"send_message", "broadcast"}:
            skills.append({"id": action, "name": action, "description": f"Scene action: {action}", "tags": ["scene-action"]})
    return build_agent_card(
        agent_id=AGENT_ID,
        name=AGENT_NAME,
        description=f"{backend_label} agent for role {AGENT_ROLE}",
        role=AGENT_ROLE,
        url=f"{base}/a2a",
        skills=skills,
    )


@app.get("/agent-card.json")
async def agent_card_alias(request: Request):
    return await well_known_agent_card(request)


@app.post("/a2a")
async def receive_a2a(payload: Dict[str, Any], request: Request = None):
    timer = A2ATiming()
    request_id = payload.get("id") if isinstance(payload, dict) else None
    body_size = content_size_bytes(payload)
    try:
        msg = extract_message_send(payload)
    except A2AProtocolError as exc:
        _emit_inbound_a2a(
            source_id="",
            status_code=400,
            latency_ms=timer.latency_ms,
            request_id=request_id,
            content_bytes=body_size,
            allowed=False,
            error=str(exc),
        )
        return JSONResponse(status_code=400, content=build_jsonrpc_error(request_id, -32600, str(exc)))

    source_id = msg["from_id"]
    trace_id = msg.get("trace_id", "")
    channel_id = msg.get("channel_id", "")
    talk = msg.get("talk", "")
    allowed = comm.is_allowed(source_id, AGENT_ID)
    if not allowed:
        _emit_inbound_a2a(
            source_id=source_id,
            status_code=403,
            latency_ms=timer.latency_ms,
            request_id=msg.get("jsonrpc_id"),
            trace_id=trace_id,
            channel_id=channel_id,
            talk=talk,
            content_bytes=body_size,
            allowed=False,
            error="server_policy_denied",
        )
        return JSONResponse(
            status_code=403,
            content=build_jsonrpc_error(
                msg.get("jsonrpc_id"),
                -32003,
                "A2A direct policy denied",
                {"from_agent": source_id, "to_agent": normalize_agent_id(AGENT_ID)},
            ),
        )

    _append_inbox(
        source_id,
        msg["content"],
        "a2a",
        metadata={
            "from_name": msg.get("from_name", ""),
            "channel_id": channel_id,
            "talk": talk,
            "trace_id": trace_id,
            "message_id": msg.get("message_id", ""),
            "context_id": msg.get("context_id", ""),
            "protocol": "a2a",
        },
    )
    _log_agent(
        "agent_message",
        f"A2A message received from {source_id}",
        action_type="receive_message",
        source=source_id,
        content=msg["content"],
        trace_id=trace_id,
        channel_id=channel_id,
        talk=talk,
    )
    _emit_inbound_a2a(
        source_id=source_id,
        status_code=200,
        latency_ms=timer.latency_ms,
        request_id=msg.get("jsonrpc_id"),
        trace_id=trace_id,
        channel_id=channel_id,
        talk=talk,
        content_bytes=body_size,
        allowed=True,
        extra={"message_id": msg.get("message_id", "")},
    )
    return build_jsonrpc_success(
        msg.get("jsonrpc_id"),
        {
            "received": True,
            "inbox_size": _inbox_size(),
            "message": {
                "kind": "message",
                "role": "agent",
                "parts": [{"kind": "text", "text": "received"}],
                "contextId": msg.get("context_id") or talk or trace_id,
                "metadata": {"agent_id": normalize_agent_id(AGENT_ID), "protocol": "a2a"},
            },
        },
    )


@app.post("/message")
async def receive_message(msg: MessageIn, request: Request = None):
    # Legacy direct endpoint kept for compatibility. It now uses the same
    # server-side policy and network logging middleware as /a2a.
    timer = A2ATiming()
    source_id = normalize_agent_id(msg.from_id)
    trace_id = msg.trace_id or msg.talk or ""
    allowed = comm.is_allowed(source_id, AGENT_ID)
    if not allowed:
        _emit_inbound_a2a(
            source_id=source_id,
            status_code=403,
            latency_ms=timer.latency_ms,
            trace_id=trace_id,
            channel_id=msg.channel_id,
            talk=msg.talk,
            content_bytes=len((msg.content or "").encode("utf-8")),
            allowed=False,
            error="server_policy_denied_legacy_message",
            path="/message",
        )
        raise HTTPException(status_code=403, detail="direct policy denied")

    _append_inbox(
        source_id,
        msg.content,
        msg.type or "direct",
        metadata={
            "from_name": msg.from_name,
            "channel_id": msg.channel_id,
            "talk": msg.talk,
            "trace_id": trace_id,
            "protocol": "legacy-message",
        },
    )
    _emit_inbound_a2a(
        source_id=source_id,
        status_code=200,
        latency_ms=timer.latency_ms,
        trace_id=trace_id,
        channel_id=msg.channel_id,
        talk=msg.talk,
        content_bytes=len((msg.content or "").encode("utf-8")),
        allowed=True,
        path="/message",
        extra={"legacy": True},
    )
    return {"received": True, "inbox_size": _inbox_size(), "deprecated": True, "replacement": "/a2a"}


@app.post("/event")
async def receive_event(event: Dict[str, Any]):
    event_name = event.get("event_name", "未知事件")
    impact = event.get("impact", "")
    t = event.get("turn", 0)
    _append_inbox("系统", f"⚠️ 事件 [{event_name}]: {impact}", "system")
    _event_queue.append({"event_name": event_name, "impact": impact, "turn": t})
    _log_agent("event_received", f"事件: {event_name} — {impact}", event_name=event_name, impact=impact, turn=t)
    return {"received": True, "event": event_name}


@app.get("/events")
async def list_events():
    return {"agent_id": AGENT_ID, "events": _event_queue}


@app.get("/inbox")
async def get_inbox():
    return {"inbox": inbox[-20:]}


@app.post("/clear")
async def clear():
    _clear_inbox()
    return {"cleared": True}


@app.post("/capture/start")
async def capture_start(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return start_full_capture(
        agent_id=body.get("agent_id") or AGENT_ID,
        session_id=body.get("session_id", ""),
        pcap_dir=body.get("pcap_dir") or os.environ.get("PCAP_DIR", "/app/data/pcap"),
        interface=body.get("interface", "any"),
        runtime_container=body.get("runtime_container") or AGENT_ID,
        runtime_container_id=body.get("runtime_container_id", ""),
        runtime_ip=body.get("runtime_ip", ""),
        trace_id=body.get("trace_id", ""),
        server_url=SERVER_URL,
    )


@app.post("/capture/stop")
async def capture_stop():
    return stop_full_capture()


@app.post("/reset")
async def reset_state():
    global turn, _event_queue
    stop_full_capture()
    turn = 0
    _event_queue = []
    inbox.clear()
    return {"status": "reset", "brain_cleared": False}


if __name__ == "__main__":
    print(f"[Agent {backend_label}] {AGENT_NAME} ({AGENT_ROLE}) starting on port {AGENT_PORT}")
    print(f"[Agent {backend_label}] Backend: {BACKEND} | Model: {MODEL} | Goal: {AGENT_CORE_GOAL or 'N/A'}")
    print("[Agent Direct] Protocol: A2A message/send | Endpoint: /a2a | Card: /.well-known/agent-card.json")
    try:
        uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT, log_level="info")
    finally:
        stop_full_capture()
