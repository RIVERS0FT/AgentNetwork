#!/usr/bin/env python3
"""Agent container runtime HTTP service."""

import os
import sys
import json
import asyncio
import threading
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List, Dict, Any
import uvicorn
import requests

from agent_network.logger import get_logger
from agent_network.comm import DirectBus
from agent_network.full_packet_capture import capture_status, start_full_capture, stop_full_capture
from agent_network.network_emulation import clear_network_emulation, configure_network_emulation
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
    suffix = " The brain backend has been removed." if BACKEND == "brain" else ""
    raise RuntimeError(f"Unsupported AGENT_BACKEND={BACKEND!r}.{suffix}")

API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

comm = DirectBus()
logger = get_logger()
backend_label = {"openclaw": "OpenCLAW", "claude-code": "Claude Code", "direct_llm": "Direct LLM"}.get(BACKEND, BACKEND)
app = FastAPI(title=f"Agent {AGENT_NAME} ({backend_label})")

turn = 0
inbox: list = []
_inbox_lock = threading.Lock()
_event_queue: list = []


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _safe_post_json(url: str, json_data: dict, timeout: float = 3) -> bool:
    try:
        requests.post(url, json=json_data, timeout=timeout)
        return True
    except Exception:
        return False


def _append_inbox(
    from_agent: str,
    content: str,
    msg_type: str = "direct",
    channel_id: str = "",
    talk: str = "",
):
    with _inbox_lock:
        inbox.append({
            "_inbox_id": uuid.uuid4().hex,
            "from": from_agent,
            "content": content,
            "type": msg_type,
            "channel_id": channel_id,
            "trace_id": talk,
        })
        if len(inbox) > 50:
            inbox.pop(0)


def _clear_inbox():
    with _inbox_lock:
        inbox.clear()


def _inbox_size() -> int:
    with _inbox_lock:
        return len(inbox)


def _snapshot_inbox() -> tuple[list, set[str]]:
    with _inbox_lock:
        messages = [{key: value for key, value in item.items() if key != "_inbox_id"} for item in inbox]
        message_ids = {item["_inbox_id"] for item in inbox}
        return messages, message_ids


def _ack_inbox(message_ids: set[str]) -> None:
    if not message_ids:
        return
    with _inbox_lock:
        inbox[:] = [item for item in inbox if item.get("_inbox_id") not in message_ids]


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



class MessageIn(BaseModel):
    from_id: str
    from_name: str = ""
    to: str = ""
    content: str
    type: str = "message"
    channel_id: str = ""
    talk: str = ""


class RunRequest(BaseModel):
    trace_id: str = ""
    agent_id: str = ""
    agent_name: str = ""
    role: str = ""
    core_goal: str = ""
    task: str = ""
    messages: List[Dict[str, Any]] = []
    skill_refs: List[str] = []
    allowed_tools: List[str] = []
    permissions: Dict[str, Any] = {}
    state_snapshot: Dict[str, Any] = {}
    tick: int = 0
    timeout_seconds: int = 60
    max_turns: int = 10
    scene_key: str = "default"
    agent_directory: Dict[str, str] = {}
    comm_matrix: Dict[str, List[str]] = {}
    simulation_seed: int = 0


def _make_adapter():
    if BACKEND == "claude-code":
        return ClaudeCodeAdapter()
    if BACKEND == "direct_llm":
        return DirectLLMAdapter()
    return OpenCLAWAdapter()


@app.post("/run")
async def run_agent(req: RunRequest):
    """The full ReAct loop is delegated to the selected backend adapter."""
    comm.update_directory(req.agent_directory, req.comm_matrix)
    pending_messages, pending_ids = _snapshot_inbox()
    effective_messages = req.messages or pending_messages
    context = AgentContext(
        trace_id=req.trace_id,
        agent_id=(req.agent_id or AGENT_ID).lower(),
        agent_name=req.agent_name or AGENT_NAME,
        role=req.role or AGENT_ROLE,
        core_goal=req.core_goal or AGENT_CORE_GOAL,
        task=req.task,
        messages=effective_messages,
        skill_refs=req.skill_refs,
        allowed_tools=req.allowed_tools,
        permissions=req.permissions,
        state_snapshot=req.state_snapshot,
        tick=req.tick,
        timeout_seconds=req.timeout_seconds,
        max_turns=req.max_turns,
        scene_key=req.scene_key or os.environ.get("AGENT_SCENE_KEY", "default"),
        agent_directory=req.agent_directory,
        comm_matrix=req.comm_matrix,
        simulation_seed=req.simulation_seed,
    )

    adapter = _make_adapter()
    result = await asyncio.to_thread(adapter.run_agent_task, context)
    if not req.messages and result.status != "error" and not result.error:
        _ack_inbox(pending_ids)

    for event in getattr(result, "application_events", []) or []:
        record = logger.emit_application_event(
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
        _safe_post_json(f"{SERVER_URL}/api/logs/ingest", record, timeout=2)

    if result.status == "error" or result.error:
        record = logger.emit_application_event(
            event="agent_run_failed",
            actor={"agent_id": context.agent_id, "name": context.agent_name, "backend": BACKEND},
            task={"goal": context.task, "status": "failed"},
            action={"type": "agent_run", "name": f"{BACKEND}_run", "status": "failed"},
            result={"status": "failed", "error_message": result.error or "agent runtime failed"},
            trace_id=context.trace_id,
            tick=context.tick,
            level="ERROR",
            component=context.agent_id,
            source="agent",
            debug={"schema_version": "application.v1", "emitter": "agent_server.run_agent"},
        )
        _safe_post_json(f"{SERVER_URL}/api/logs/ingest", record, timeout=2)

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
    }


@app.post("/message")
async def receive_message(msg: MessageIn, request: Request = None):
    _append_inbox(msg.from_id, msg.content, msg.type or "direct", msg.channel_id, msg.talk)
    target_id = msg.to or AGENT_ID
    _safe_post_json(f"{SERVER_URL}/api/logs/agent", {
        "agent_id": target_id,
        "agent_name": AGENT_NAME,
        "event": "agent_message_received",
        "detail": f"message received from {msg.from_id}",
        "timestamp": _now_iso(),
        "from_agent": msg.from_id,
        "to_agent": target_id,
        "action": "receive_message",
        "action_status": "success",
        "trace_id": msg.talk,
        "details": {
            "content": msg.content,
            "channel_id": msg.channel_id,
            "message_type": msg.type or "direct",
        },
    }, timeout=2)
    return {"received": True, "inbox_size": _inbox_size()}


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
    messages, _ = _snapshot_inbox()
    return {"inbox": messages[-20:]}


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
        network_profiles=body.get("network_profiles", []),
    )


@app.post("/capture/stop")
async def capture_stop():
    return stop_full_capture()


@app.get("/capture/status")
async def capture_health():
    return capture_status()


@app.post("/network/configure")
async def network_configure(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await asyncio.to_thread(
        configure_network_emulation,
        body.get("profiles", []),
        body.get("interface", "eth0"),
    )


@app.post("/network/clear")
async def network_clear(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await asyncio.to_thread(clear_network_emulation, body.get("interface", "eth0"))


@app.post("/reset")
async def reset_state():
    global turn, _event_queue
    stop_full_capture()
    clear_network_emulation()
    turn = 0
    _event_queue = []
    _clear_inbox()
    return {"status": "reset", "brain_cleared": False}


if __name__ == "__main__":
    print(f"[Agent {backend_label}] {AGENT_NAME} ({AGENT_ROLE}) starting on port {AGENT_PORT}")
    print(f"[Agent {backend_label}] Backend: {BACKEND} | Model: {MODEL} | Goal: {AGENT_CORE_GOAL or 'N/A'}")
    try:
        uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT, log_level="info")
    finally:
        stop_full_capture()
