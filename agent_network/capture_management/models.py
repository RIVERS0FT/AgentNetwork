from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List


class CaptureState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class CaptureConfig:
    interface: str = "any"
    snap_length: int = 0
    max_bytes: int = 1024 * 1024 * 1024
    include_control_plane: bool = False
    bpf_filter: str = ""
    health_check_interval_seconds: float = 2.0
    stop_timeout_seconds: float = 5.0
    hash_algorithm: str = "sha256"
    projection_mode: str = "finalize"

    def validate(self) -> None:
        if not self.interface:
            raise ValueError("capture interface is required")
        if self.snap_length < 0:
            raise ValueError("snap_length must be >= 0")
        if self.max_bytes < 0:
            raise ValueError("max_bytes must be >= 0")
        if self.health_check_interval_seconds <= 0:
            raise ValueError("health_check_interval_seconds must be > 0")
        if self.stop_timeout_seconds <= 0:
            raise ValueError("stop_timeout_seconds must be > 0")
        if self.hash_algorithm != "sha256":
            raise ValueError("only sha256 is supported")
        if self.projection_mode != "finalize":
            raise ValueError("only finalize projection mode is supported")


@dataclass
class CaptureTarget:
    capture_id: str
    agent_id: str
    runtime_url: str
    container_id: str = ""
    container_name: str = ""
    runtime_ip: str = ""
    interface: str = "any"
    state: CaptureState = CaptureState.CREATED
    pid: int = 0
    pcap_resource_id: str = ""
    manifest_resource_id: str = ""
    captured_bytes: int = 0
    sha256: str = ""
    error: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


@dataclass
class CaptureSession:
    capture_id: str
    simulation_id: str
    session_id: str
    trace_id: str
    config: CaptureConfig
    expected_agents: List[str]
    targets: Dict[str, CaptureTarget]
    state: CaptureState = CaptureState.CREATED
    started_at: str = ""
    stopped_at: str = ""
    termination_reason: str = ""
    projection_state: str = "pending"
    audit_state: str = "pending"
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "simulation_id": self.simulation_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "config": asdict(self.config),
            "expected_agents": list(self.expected_agents),
            "targets": {key: value.to_dict() for key, value in self.targets.items()},
            "state": self.state.value,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "termination_reason": self.termination_reason,
            "projection_state": self.projection_state,
            "audit_state": self.audit_state,
            "error": self.error,
        }
