from __future__ import annotations

import concurrent.futures
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

import requests

from .models import CaptureConfig, CaptureSession, CaptureState, CaptureTarget
from .repository import CaptureRepository


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CaptureManager:
    """Control-plane facade for distributed Agent-local captures."""

    def __init__(self, repository: Optional[CaptureRepository] = None) -> None:
        self.repository = repository or CaptureRepository()
        self._sessions: Dict[str, CaptureSession] = {}
        self._lock = threading.RLock()

    def create_session(
        self,
        *,
        simulation_id: str,
        session_id: str,
        trace_id: str,
        targets: Iterable[dict],
        config: Optional[CaptureConfig] = None,
        capture_id: str = "",
    ) -> CaptureSession:
        config = config or CaptureConfig()
        config.validate()
        capture_id = capture_id or f"capture-{uuid.uuid4().hex}"
        target_map: Dict[str, CaptureTarget] = {}
        for value in targets:
            agent_id = str(value.get("agent_id", "")).strip().lower()
            runtime_url = str(value.get("runtime_url") or value.get("url") or "").rstrip("/")
            if not agent_id or not runtime_url:
                raise ValueError("each capture target requires agent_id and runtime_url")
            if agent_id in target_map:
                raise ValueError(f"duplicate capture target: {agent_id}")
            target_map[agent_id] = CaptureTarget(
                capture_id=capture_id,
                agent_id=agent_id,
                runtime_url=runtime_url,
                container_id=str(value.get("container_id", "")),
                container_name=str(value.get("container_name", "")),
                runtime_ip=str(value.get("runtime_ip", "")),
                interface=config.interface,
            )
        if not target_map:
            raise ValueError("at least one capture target is required")
        session = CaptureSession(
            capture_id=capture_id,
            simulation_id=simulation_id,
            session_id=session_id,
            trace_id=trace_id,
            config=config,
            expected_agents=sorted(target_map),
            targets=target_map,
        )
        with self._lock:
            if capture_id in self._sessions:
                raise ValueError(f"capture session already exists: {capture_id}")
            self._sessions[capture_id] = session
            self.repository.ensure_session(capture_id, session_id)
            self._persist(session)
        return session

    def _persist(self, session: CaptureSession) -> None:
        self.repository.write_session_manifest(session.capture_id, session.session_id, session.to_dict())

    def get_session(self, capture_id: str) -> CaptureSession:
        with self._lock:
            try:
                return self._sessions[capture_id]
            except KeyError as exc:
                raise KeyError(f"capture session not found: {capture_id}") from exc

    @staticmethod
    def _start_target(session: CaptureSession, target: CaptureTarget) -> dict:
        response = requests.post(
            f"{target.runtime_url}/internal/captures/start",
            json={
                "capture_id": session.capture_id,
                "session_id": session.session_id,
                "trace_id": session.trace_id,
                "agent_id": target.agent_id,
                "runtime_container": target.container_name,
                "runtime_container_id": target.container_id,
                "runtime_ip": target.runtime_ip,
                "config": session.config.__dict__,
            },
            timeout=10,
        )
        body = response.json() if response.content else {}
        if response.status_code != 200:
            raise RuntimeError(body.get("detail") or body.get("error") or f"HTTP {response.status_code}")
        return body

    @staticmethod
    def _stop_target(session: CaptureSession, target: CaptureTarget, reason: str) -> dict:
        response = requests.post(
            f"{target.runtime_url}/internal/captures/{session.capture_id}/stop",
            json={"reason": reason},
            timeout=max(10, int(session.config.stop_timeout_seconds) + 5),
        )
        body = response.json() if response.content else {}
        if response.status_code != 200:
            raise RuntimeError(body.get("detail") or body.get("error") or f"HTTP {response.status_code}")
        return body

    @staticmethod
    def _apply_target_result(target: CaptureTarget, body: dict) -> None:
        status = str(body.get("state") or body.get("status") or "").lower()
        mapping = {
            "started": CaptureState.RUNNING,
            "running": CaptureState.RUNNING,
            "stopped": CaptureState.STOPPED,
            "error": CaptureState.INCOMPLETE,
            "failed": CaptureState.FAILED,
            "incomplete": CaptureState.INCOMPLETE,
        }
        target.state = mapping.get(status, target.state)
        target.pid = int(body.get("pid") or 0)
        target.pcap_resource_id = str(body.get("pcap_resource_id") or target.pcap_resource_id)
        target.manifest_resource_id = str(body.get("manifest_resource_id") or target.manifest_resource_id)
        target.captured_bytes = int(body.get("captured_bytes") or body.get("pcap_bytes") or 0)
        target.sha256 = str(body.get("sha256") or "")
        target.error = str(body.get("error") or "")
        target.details = dict(body)

    def start_session(self, capture_id: str) -> CaptureSession:
        session = self.get_session(capture_id)
        with self._lock:
            if session.state == CaptureState.RUNNING:
                return session
            if session.state not in {CaptureState.CREATED, CaptureState.FAILED}:
                raise RuntimeError(f"capture cannot start from {session.state.value}")
            session.state = CaptureState.STARTING
            self._persist(session)

        failures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(session.targets)) as pool:
            future_map = {
                pool.submit(self._start_target, session, target): target
                for target in session.targets.values()
            }
            for future, target in future_map.items():
                try:
                    self._apply_target_result(target, future.result())
                    if target.state != CaptureState.RUNNING:
                        failures.append(target.agent_id)
                except Exception as exc:
                    target.state = CaptureState.FAILED
                    target.error = str(exc)
                    failures.append(target.agent_id)

        if failures:
            self._rollback_started(session, "capture_start_failed")
            session.state = CaptureState.FAILED
            session.error = "failed targets: " + ", ".join(sorted(failures))
            session.termination_reason = "capture_start_failed"
        else:
            session.state = CaptureState.RUNNING
            session.started_at = _now_iso()
        self._persist(session)
        return session

    def _rollback_started(self, session: CaptureSession, reason: str) -> None:
        running = [target for target in session.targets.values() if target.state == CaptureState.RUNNING]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(running))) as pool:
            future_map = {pool.submit(self._stop_target, session, target, reason): target for target in running}
            for future, target in future_map.items():
                try:
                    self._apply_target_result(target, future.result())
                except Exception as exc:
                    target.state = CaptureState.INCOMPLETE
                    target.error = str(exc)

    def check_health(self, capture_id: str) -> CaptureSession:
        session = self.get_session(capture_id)
        if session.state != CaptureState.RUNNING:
            return session
        failures = []
        for target in session.targets.values():
            try:
                response = requests.get(
                    f"{target.runtime_url}/internal/captures/{capture_id}", timeout=5
                )
                body = response.json() if response.content else {}
                if response.status_code != 200:
                    raise RuntimeError(body.get("detail") or f"HTTP {response.status_code}")
                self._apply_target_result(target, body)
                if target.state != CaptureState.RUNNING:
                    failures.append(target.agent_id)
            except Exception as exc:
                target.state = CaptureState.INCOMPLETE
                target.error = str(exc)
                failures.append(target.agent_id)
        if failures:
            session.state = CaptureState.INCOMPLETE
            session.error = "unhealthy targets: " + ", ".join(sorted(failures))
            session.termination_reason = "capture_incomplete"
        self._persist(session)
        return session

    def stop_session(self, capture_id: str, reason: str = "requested") -> CaptureSession:
        session = self.get_session(capture_id)
        if session.state == CaptureState.STOPPED:
            return session
        session.state = CaptureState.STOPPING
        self._persist(session)
        failures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(session.targets)) as pool:
            future_map = {
                pool.submit(self._stop_target, session, target, reason): target
                for target in session.targets.values()
                if target.state in {CaptureState.RUNNING, CaptureState.STARTING, CaptureState.INCOMPLETE}
            }
            for future, target in future_map.items():
                try:
                    self._apply_target_result(target, future.result())
                    if target.state != CaptureState.STOPPED:
                        failures.append(target.agent_id)
                except Exception as exc:
                    target.state = CaptureState.INCOMPLETE
                    target.error = str(exc)
                    failures.append(target.agent_id)
        session.stopped_at = _now_iso()
        session.termination_reason = reason
        session.state = CaptureState.INCOMPLETE if failures else CaptureState.STOPPED
        if failures:
            session.error = "incomplete targets: " + ", ".join(sorted(failures))
        self.repository.refresh_pcaps(capture_id, compute_sha256=True)
        self._persist(session)
        return session

    def list_artifacts(self, capture_id: str) -> list[dict]:
        self.get_session(capture_id)
        return [resource.to_dict() for resource in self.repository.list_resources(capture_id)]

    def query_packets(self, capture_id: str, agent_id: str = "", limit: int = 100):
        session = self.get_session(capture_id)
        from .packet_store import query_packets
        return query_packets(session_id=session.session_id, agent_id=agent_id or None, limit=limit)

    def stats(self, capture_id: str):
        session = self.get_session(capture_id)
        from .packet_store import packet_stats
        return packet_stats(session_id=session.session_id)

    def analyze(self, capture_id: str, agent_id: str = "", max_packets: int = 100_000):
        session = self.get_session(capture_id)
        from .packet_store import analyze_packets
        return analyze_packets(session_id=session.session_id, agent_id=agent_id or None, max_packets=max_packets)

    def audit(self, capture_id: str, verify_hashes: bool = True):
        session = self.get_session(capture_id)
        from agent_network.experiment_manifest import audit_session
        result = audit_session(session.session_id, verify_hashes=verify_hashes)
        session.audit_state = "passed" if result.get("passed") else "failed"
        self._persist(session)
        return result

    def build_bundle(self, capture_id: str):
        session = self.get_session(capture_id)
        from agent_network.experiment_manifest import build_bundle
        return build_bundle(session.session_id)


_manager: Optional[CaptureManager] = None
_manager_lock = threading.Lock()


def get_capture_manager() -> CaptureManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = CaptureManager()
        return _manager
