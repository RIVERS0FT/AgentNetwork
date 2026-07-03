"""A2A direct-mode policy, trace and traffic logging helpers."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests


def normalize_agent_id(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_comm_matrix(comm_matrix: Optional[Dict[str, Any]]) -> Dict[str, set]:
    return {
        normalize_agent_id(source): {normalize_agent_id(item) for item in (targets or [])}
        for source, targets in (comm_matrix or {}).items()
    }


def is_direct_call_allowed(comm_matrix: Optional[Dict[str, Any]], from_id: str, target_id: str) -> bool:
    matrix = normalize_comm_matrix(comm_matrix)
    if not matrix:
        return True
    return normalize_agent_id(target_id) in matrix.get(normalize_agent_id(from_id), set())


def content_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return len(str(value or "").encode("utf-8"))


def emit_a2a_network_event(
    *,
    server_url: str,
    direction: str,
    component: str,
    source_id: str,
    target_id: str,
    status_code: int = 0,
    latency_ms: float = 0.0,
    method: str = "message/send",
    path: str = "/a2a",
    trace_id: str = "",
    channel_id: str = "",
    talk: str = "",
    request_id: Any = "",
    content_bytes: int = 0,
    allowed: Optional[bool] = None,
    error: str = "",
    extra: Optional[Dict[str, Any]] = None,
    timeout: float = 2.0,
) -> bool:
    """Post a network-layer A2A event to srv without failing caller logic."""

    if not server_url:
        return False
    failed = bool(error) or (status_code >= 400 if status_code else False)
    source = normalize_agent_id(source_id)
    target = normalize_agent_id(target_id)
    record = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "level": "ERROR" if failed else "INFO",
        "source": "agent",
        "component": component or source or target or "agent",
        "category": "agent_network",
        "layer": "agent_network",
        "event": f"a2a_http_{direction}",
        "actor": {"agent_id": source},
        "target": {"agent_id": target},
        "action": {
            "name": "a2a_message_send",
            "status": "failed" if failed else "success",
        },
        "message": f"A2A {direction}: {source or '?'} -> {target or '?'}",
        "payload": {
            "jsonrpc_method": method,
            "jsonrpc_id": request_id,
            "allowed": allowed,
            "error": error,
            **(extra or {}),
        },
        "network": {
            "protocol": "HTTP/A2A JSON-RPC",
            "method": "POST",
            "path": path,
            "status_code": status_code,
            "latency_ms": round(float(latency_ms or 0.0), 2),
            "message_type": "a2a_message",
            "payload_len": int(content_bytes or 0),
            "channel_id": channel_id or "",
        },
        "trace": {
            "trace_id": trace_id or talk or "",
            "talk": talk or "",
            "correlation_id": request_id or "",
        },
    }
    try:
        requests.post(f"{server_url.rstrip('/')}/api/logs/ingest", json=record, timeout=timeout)
        return True
    except Exception:
        return False


class A2ATiming:
    """Small timing context for middleware-style latency capture."""

    def __init__(self):
        self.started = time.time()

    @property
    def latency_ms(self) -> float:
        return (time.time() - self.started) * 1000.0
