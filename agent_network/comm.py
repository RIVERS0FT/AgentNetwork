import os
import time
import requests
from typing import Dict, List, Any
from abc import ABC, abstractmethod

from agent_network.a2a import build_message_send_request
from agent_network.a2a_middleware import (
    content_size_bytes,
    emit_a2a_network_event,
    is_direct_call_allowed,
    normalize_agent_id,
    normalize_comm_matrix,
)


class CommLayer(ABC):
    inbox: List[Dict[str, Any]]

    @abstractmethod
    def send(self, from_id: str, from_name: str, target: str, content: str,
             channel_id: str = "", talk: str = "", trace_id: str = "") -> bool:
        ...

    @abstractmethod
    def broadcast(self, from_id: str, from_name: str, content: str, allowed: set = None,
                  channel_id: str = "", talk: str = "", trace_id: str = "") -> bool:
        ...

    @abstractmethod
    def register_agent(self, agent_id: str, name: str, url: str = "") -> None:
        ...


class DirectBus(CommLayer):
    """Direct Agent-to-Agent data plane using A2A ``message/send``.

    Despite the historic class name, this is no longer a message bus. It is the
    local client-side communication middleware used inside each agent runtime:
    policy check -> trace injection -> A2A POST -> network-layer log emission.
    """

    def __init__(self, agent_directory: Dict[str, str] = None, comm_matrix: Dict[str, Any] = None,
                 server_url: str = None, timeout_seconds: float = 10.0, **_):
        self.agent_directory = {
            normalize_agent_id(k): self._normalize_base_url(v)
            for k, v in (agent_directory or {}).items()
            if v
        }
        self.comm_matrix = normalize_comm_matrix(comm_matrix)
        self.server_url = (server_url if server_url is not None else os.environ.get("SERVER_URL", "")).rstrip("/")
        self.timeout_seconds = float(timeout_seconds or 10.0)
        self.inbox: List[Dict[str, Any]] = []

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        value = str(url or "").rstrip("/")
        if value.endswith("/.well-known/agent-card.json"):
            return value[: -len("/.well-known/agent-card.json")]
        if value.endswith("/a2a"):
            return value[: -len("/a2a")]
        return value

    def update_directory(self, agent_directory: Dict[str, str] = None, comm_matrix: Dict[str, Any] = None):
        if agent_directory is not None:
            self.agent_directory = {
                normalize_agent_id(k): self._normalize_base_url(v)
                for k, v in agent_directory.items()
                if v
            }
        if comm_matrix is not None:
            self.comm_matrix = normalize_comm_matrix(comm_matrix)

    def _allowed(self, from_id: str, target: str) -> bool:
        return is_direct_call_allowed(self.comm_matrix, from_id, target)

    def is_allowed(self, from_id: str, target: str) -> bool:
        """Public helper used by server-side A2A middleware."""
        return self._allowed(from_id, target)

    def policy_snapshot(self) -> Dict[str, List[str]]:
        return {k: sorted(v) for k, v in self.comm_matrix.items()}

    def register_agent(self, agent_id: str, name: str, url: str = "") -> None:
        if agent_id and url:
            self.agent_directory[normalize_agent_id(agent_id)] = self._normalize_base_url(url)

    def _emit_outbound(self, **kwargs):
        emit_a2a_network_event(server_url=self.server_url, direction="outbound", **kwargs)

    def send(self, from_id: str, from_name: str, target: str, content: str,
             channel_id: str = "", talk: str = "", trace_id: str = "") -> bool:
        source_id = normalize_agent_id(from_id)
        target_id = normalize_agent_id(target)
        trace = trace_id or talk or ""

        if not self._allowed(source_id, target_id):
            self._emit_outbound(
                component=source_id,
                source_id=source_id,
                target_id=target_id,
                status_code=403,
                path="/a2a",
                trace_id=trace,
                channel_id=channel_id,
                talk=talk,
                request_id="",
                content_bytes=len((content or "").encode("utf-8")),
                allowed=False,
                error="client_policy_denied",
            )
            return False

        target_url = self.agent_directory.get(target_id)
        if not target_url:
            self._emit_outbound(
                component=source_id,
                source_id=source_id,
                target_id=target_id,
                status_code=0,
                path="/a2a",
                trace_id=trace,
                channel_id=channel_id,
                talk=talk,
                request_id="",
                content_bytes=len((content or "").encode("utf-8")),
                allowed=True,
                error="target_not_in_agent_directory",
            )
            return False

        payload = build_message_send_request(
            from_id=source_id,
            from_name=from_name,
            target_id=target_id,
            content=content,
            channel_id=channel_id,
            talk=talk,
            trace_id=trace,
        )
        request_id = payload.get("id", "")
        body_size = content_size_bytes(payload)
        start = time.time()
        status_code = 0
        error = ""
        ok = False
        try:
            resp = requests.post(
                f"{target_url.rstrip('/')}/a2a",
                json=payload,
                headers={
                    "X-Agent-Id": source_id,
                    "X-Trace-Id": trace,
                    "X-A2A-Protocol": "message/send",
                },
                timeout=self.timeout_seconds,
            )
            status_code = resp.status_code
            ok = resp.ok
            try:
                body = resp.json()
                if isinstance(body, dict) and body.get("error"):
                    ok = False
                    error = body.get("error", {}).get("message", "a2a_error")
            except Exception:
                body = None
            if not ok and not error:
                error = f"http_{status_code}"
            return ok
        except Exception as exc:
            error = str(exc)
            return False
        finally:
            latency_ms = (time.time() - start) * 1000.0
            self._emit_outbound(
                component=source_id,
                source_id=source_id,
                target_id=target_id,
                status_code=status_code,
                path="/a2a",
                trace_id=trace,
                channel_id=channel_id,
                talk=talk,
                request_id=request_id,
                content_bytes=body_size,
                allowed=True,
                error=error,
            )

    def broadcast(self, from_id: str, from_name: str, content: str, allowed: set = None,
                  channel_id: str = "", talk: str = "", trace_id: str = "") -> bool:
        source_id = normalize_agent_id(from_id)
        explicit_allowed = {normalize_agent_id(item) for item in (allowed or set())}
        ok_all = True
        for target_id in sorted(self.agent_directory.keys()):
            if target_id == source_id:
                continue
            if explicit_allowed and target_id not in explicit_allowed:
                continue
            ok_all = self.send(source_id, from_name, target_id, content, channel_id, talk, trace_id) and ok_all
        return ok_all


RemoteBus = DirectBus
