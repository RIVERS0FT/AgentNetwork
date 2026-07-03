"""Minimal A2A JSON-RPC helpers for direct Agent-to-Agent messaging.

The project only needs the A2A message data plane today, so this module keeps
that surface intentionally small: Agent Card discovery plus ``message/send``.
It avoids pulling an SDK into the simulation runtime while preserving the A2A
wire shape used by external clients.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

A2A_PROTOCOL_VERSION = "0.3.0"
A2A_MESSAGE_SEND_METHOD = "message/send"


class A2AProtocolError(ValueError):
    """Raised when an inbound A2A JSON-RPC payload is malformed."""


def _clean_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _text_from_parts(parts: Iterable[Dict[str, Any]]) -> str:
    texts: List[str] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind") or part.get("type")
        if kind in {"text", "text/plain", None} and part.get("text") is not None:
            texts.append(str(part.get("text", "")))
    return "\n".join([text for text in texts if text])


def build_message_send_request(
    *,
    from_id: str,
    from_name: str,
    target_id: str,
    content: str,
    channel_id: str = "",
    talk: str = "",
    trace_id: str = "",
    request_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a minimal A2A JSON-RPC ``message/send`` request."""

    from_agent = _clean_id(from_id)
    to_agent = _clean_id(target_id)
    trace = trace_id or talk or f"trace_{uuid.uuid4().hex[:12]}"
    context_id = talk or trace
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    request_id = request_id or f"a2a_{uuid.uuid4().hex[:12]}"

    meta = {
        "from_agent": from_agent,
        "from_name": from_name or from_agent,
        "to_agent": to_agent,
        "channel_id": channel_id or "",
        "talk": talk or "",
        "trace_id": trace,
        "network_mode": "direct",
        "protocol": "a2a",
    }
    if metadata:
        meta.update(metadata)

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": A2A_MESSAGE_SEND_METHOD,
        "params": {
            "message": {
                "kind": "message",
                "messageId": message_id,
                "role": "user",
                "parts": [{"kind": "text", "text": content or ""}],
                "contextId": context_id,
                "metadata": meta,
            }
        },
    }


def extract_message_send(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize an inbound A2A ``message/send`` request."""

    if not isinstance(payload, dict):
        raise A2AProtocolError("A2A payload must be a JSON object")
    if payload.get("jsonrpc") != "2.0":
        raise A2AProtocolError("A2A payload must use JSON-RPC 2.0")
    method = payload.get("method")
    if method != A2A_MESSAGE_SEND_METHOD:
        raise A2AProtocolError(f"unsupported A2A method: {method!r}")

    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise A2AProtocolError("A2A params must be an object")
    message = params.get("message") or {}
    if not isinstance(message, dict):
        raise A2AProtocolError("A2A params.message must be an object")

    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    content = _text_from_parts(message.get("parts") or [])
    if not content:
        raise A2AProtocolError("A2A message must contain at least one text part")

    from_id = _clean_id(metadata.get("from_agent") or metadata.get("from_id"))
    if not from_id:
        raise A2AProtocolError("A2A message metadata.from_agent is required")

    return {
        "jsonrpc_id": payload.get("id"),
        "method": method,
        "message_id": message.get("messageId") or message.get("message_id") or "",
        "context_id": message.get("contextId") or metadata.get("talk") or "",
        "from_id": from_id,
        "from_name": metadata.get("from_name") or from_id,
        "target_id": _clean_id(metadata.get("to_agent")),
        "content": content,
        "channel_id": metadata.get("channel_id", ""),
        "talk": metadata.get("talk") or message.get("contextId") or "",
        "trace_id": metadata.get("trace_id") or metadata.get("talk") or message.get("contextId") or "",
        "metadata": metadata,
        "raw_message": message,
    }


def build_jsonrpc_success(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def build_jsonrpc_error(request_id: Any, code: int, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    if data:
        body["error"]["data"] = data
    return body


def build_agent_card(
    *,
    agent_id: str,
    name: str,
    description: str,
    url: str,
    role: str = "",
    skills: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return a compact Agent Card for direct A2A discovery."""

    return {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": name or agent_id,
        "description": description or f"AgentNetwork direct A2A agent {agent_id}",
        "url": url.rstrip("/"),
        "version": "0.1.0",
        "provider": {"organization": "AgentNetworkSimulation"},
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "metadata": {
            "agent_id": _clean_id(agent_id),
            "role": role,
            "network_mode": "direct",
            "data_plane": "a2a",
        },
        "skills": skills
        or [
            {
                "id": "send_message",
                "name": "send_message",
                "description": "Receive a direct Agent-to-Agent text message.",
                "tags": ["agent-network", "direct", "message/send"],
                "examples": ["Send a text message to this agent."],
            }
        ],
    }
