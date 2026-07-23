"""Simulation configuration and lifecycle models."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class SimulationState(str, Enum):
    CREATED = "CREATED"
    CONFIGURED = "CONFIGURED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FORCE_STOPPING = "FORCE_STOPPING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FORCE_STOPPED = "FORCE_STOPPED"
    FAILED = "FAILED"


class SimulationEventStatus(str, Enum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {
    SimulationState.COMPLETED,
    SimulationState.STOPPED,
    SimulationState.FORCE_STOPPED,
    SimulationState.FAILED,
}


@dataclass(frozen=True)
class AgentResourceLimit:
    cpu_cores: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 128

    def __post_init__(self) -> None:
        if not 0.1 <= float(self.cpu_cores) <= 64:
            raise ValueError("cpu_cores must be between 0.1 and 64")
        if not 128 <= int(self.memory_mb) <= 1048576:
            raise ValueError("memory_mb must be between 128 and 1048576")
        if not 16 <= int(self.pids_limit) <= 65536:
            raise ValueError("pids_limit must be between 16 and 65536")

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "AgentResourceLimit":
        return cls(**(value or {}))


@dataclass(frozen=True)
class SimulationResourceAllocation:
    default_agent: AgentResourceLimit = field(default_factory=AgentResourceLimit)
    agent_overrides: dict[str, AgentResourceLimit] = field(default_factory=dict)
    max_parallel_agents: int = 4

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_parallel_agents) <= 256:
            raise ValueError("max_parallel_agents must be between 1 and 256")

    @classmethod
    def from_dict(
        cls, value: dict[str, Any] | None
    ) -> "SimulationResourceAllocation":
        value = dict(value or {})
        overrides = {
            str(agent_id).lower(): AgentResourceLimit.from_dict(limits)
            for agent_id, limits in (value.get("agent_overrides") or {}).items()
        }
        return cls(
            default_agent=AgentResourceLimit.from_dict(value.get("default_agent")),
            agent_overrides=overrides,
            max_parallel_agents=int(value.get("max_parallel_agents", 4)),
        )


@dataclass(frozen=True)
class SimulationRuntimeConfig:
    duration_seconds: int = 3600
    agent_timeout_seconds: int = 300
    agent_startup_timeout_seconds: int = 60
    idle_timeout_seconds: int = 5
    graceful_stop_timeout_seconds: int = 30
    network_mode: str = "a2a"
    resource_allocation: SimulationResourceAllocation = field(
        default_factory=SimulationResourceAllocation
    )

    def __post_init__(self) -> None:
        if not 1 <= int(self.duration_seconds) <= 604800:
            raise ValueError("duration_seconds must be between 1 and 604800")
        if not 1 <= int(self.agent_timeout_seconds) <= 86400:
            raise ValueError("agent_timeout_seconds must be between 1 and 86400")
        if not 1 <= int(self.agent_startup_timeout_seconds) <= 3600:
            raise ValueError(
                "agent_startup_timeout_seconds must be between 1 and 3600"
            )
        if not 0 <= int(self.idle_timeout_seconds) <= 3600:
            raise ValueError("idle_timeout_seconds must be between 0 and 3600")
        if not 0 <= int(self.graceful_stop_timeout_seconds) <= 3600:
            raise ValueError(
                "graceful_stop_timeout_seconds must be between 0 and 3600"
            )
        if self.network_mode != "a2a":
            raise ValueError("network_mode must be 'a2a'")

    @classmethod
    def from_dict(
        cls, value: dict[str, Any] | None
    ) -> "SimulationRuntimeConfig":
        value = dict(value or {})
        return cls(
            duration_seconds=int(value.get("duration_seconds", 3600)),
            agent_timeout_seconds=int(value.get("agent_timeout_seconds", 300)),
            agent_startup_timeout_seconds=int(
                value.get("agent_startup_timeout_seconds", 60)
            ),
            idle_timeout_seconds=int(value.get("idle_timeout_seconds", 5)),
            graceful_stop_timeout_seconds=int(
                value.get("graceful_stop_timeout_seconds", 30)
            ),
            network_mode=str(value.get("network_mode", "a2a")),
            resource_allocation=SimulationResourceAllocation.from_dict(
                value.get("resource_allocation")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimulationControl:
    stop_event: threading.Event = field(default_factory=threading.Event)
    force_stop_event: threading.Event = field(default_factory=threading.Event)
    terminal_event: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    condition: threading.Condition = field(
        default_factory=threading.Condition,
        repr=False,
    )

    def wake(self) -> None:
        with self.condition:
            self.condition.notify_all()


@dataclass
class SimulationEvent:
    simulation_id: str
    sequence: int
    event_type: str
    target_agent_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    source_agent_id: str = ""
    event_id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex}")
    available_at: float = field(default_factory=time.monotonic)
    created_at: str = field(default_factory=_now_iso)
    dispatched_at: str = ""
    completed_at: str = ""
    status: SimulationEventStatus = SimulationEventStatus.PENDING
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "simulation_id": self.simulation_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "source_agent_id": self.source_agent_id,
            "target_agent_id": self.target_agent_id,
            "payload": self.payload,
            "created_at": self.created_at,
            "dispatched_at": self.dispatched_at,
            "completed_at": self.completed_at,
            "status": self.status.value,
            "error": self.error,
        }


@dataclass
class SimulationRun:
    scene: str
    runtime_config: SimulationRuntimeConfig
    seed: int
    scene_definition: Any = field(default=None, repr=False)
    execution_config: dict[str, Any] = field(default_factory=dict, repr=False)
    simulation_id: str = field(default_factory=lambda: f"sim-{uuid.uuid4().hex}")
    state: SimulationState = SimulationState.CREATED
    created_at: str = field(default_factory=_now_iso)
    configured_at: str = ""
    started_at: str = ""
    deadline_at: str = ""
    last_activity_at: str = ""
    stopped_at: str = ""
    stop_reason: str = ""
    error: str = ""
    action_status: str = ""
    setup: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    resource_plan: dict[str, Any] = field(default_factory=dict)
    processed_event_count: int = 0
    failed_event_count: int = 0
    cancelled_event_count: int = 0
    scheduler: Any = field(default=None, repr=False)
    control: SimulationControl = field(default_factory=SimulationControl, repr=False)

    def mark_configured(self) -> None:
        self.state = SimulationState.CONFIGURED
        self.configured_at = _now_iso()

    def mark_started(self) -> None:
        now = datetime.now(timezone.utc)
        self.state = SimulationState.RUNNING
        self.started_at = _now_iso()
        self.last_activity_at = self.started_at
        self.deadline_at = (
            now + timedelta(seconds=self.runtime_config.duration_seconds)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        try:
            started = datetime.fromisoformat(
                self.started_at.replace("Z", "+00:00")
            )
            finished = datetime.fromisoformat(
                (self.stopped_at or _now_iso()).replace("Z", "+00:00")
            )
        except ValueError:
            return 0.0
        return round(max(0.0, (finished - started).total_seconds()), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "scene": self.scene,
            "seed": self.seed,
            "state": self.state.value,
            "runtime_config": self.runtime_config.to_dict(),
            "created_at": self.created_at,
            "configured_at": self.configured_at,
            "started_at": self.started_at,
            "deadline_at": self.deadline_at,
            "last_activity_at": self.last_activity_at,
            "stopped_at": self.stopped_at,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "action_status": self.action_status,
            "elapsed_seconds": self.elapsed_seconds(),
            "setup": self.setup,
            "result": self.result,
            "resource_plan": self.resource_plan,
            "processed_event_count": self.processed_event_count,
            "failed_event_count": self.failed_event_count,
            "cancelled_event_count": self.cancelled_event_count,
        }
