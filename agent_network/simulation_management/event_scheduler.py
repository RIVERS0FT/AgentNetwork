"""Per-simulation event queue and blocking event-driven dispatcher."""

from __future__ import annotations

import heapq
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .models import (
    SimulationEvent,
    SimulationEventStatus,
    SimulationRun,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class SimulationEventScheduler:
    """Dispatch ready events without fixed rounds or periodic empty spinning."""

    def __init__(self, run: SimulationRun):
        self.run = run
        self._queue: list[tuple[float, int, SimulationEvent]] = []
        self._events: list[SimulationEvent] = []
        self._sequence = 0
        self._version = 0

    def enqueue(
        self,
        event_type: str,
        target_agent_id: str,
        payload: dict[str, Any] | None = None,
        *,
        source_agent_id: str = "",
        available_at: float | None = None,
    ) -> SimulationEvent:
        with self.run.control.condition:
            self._sequence += 1
            event = SimulationEvent(
                simulation_id=self.run.simulation_id,
                sequence=self._sequence,
                event_type=str(event_type),
                source_agent_id=str(source_agent_id).lower(),
                target_agent_id=str(target_agent_id).lower(),
                payload=dict(payload or {}),
                available_at=(
                    time.monotonic() if available_at is None else float(available_at)
                ),
            )
            heapq.heappush(self._queue, (event.available_at, event.sequence, event))
            self._events.append(event)
            self._version += 1
            self._touch()
            self.run.control.condition.notify_all()
        return event

    def run_loop(
        self,
        dispatch: Callable[[list[SimulationEvent]], list[dict[str, Any]]],
        *,
        readiness_probe: Callable[[], Iterable[str]] | None = None,
        health_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        batches: list[dict[str, Any]] = []
        idle_deadline = time.monotonic() + self.run.runtime_config.idle_timeout_seconds

        while True:
            if self.run.control.force_stop_event.is_set():
                self._cancel_pending()
                return self._result(
                    self.run.control.reason or "forced_stop", batches
                )
            if self.run.control.stop_event.is_set():
                self._cancel_pending()
                return self._result(
                    self.run.control.reason or "user_stopped", batches
                )

            batch = self._take_ready(
                self.run.resource_plan.get("max_parallel_agents", 1)
            )
            if batch:
                idle_deadline = (
                    time.monotonic()
                    + self.run.runtime_config.idle_timeout_seconds
                )
                results = dispatch(batch)
                self._complete_batch(batch, results)
                batches.append(
                    {
                        "events": [event.to_dict() for event in batch],
                        "results": results,
                    }
                )
                if health_check and not health_check():
                    self._cancel_pending()
                    return self._result("capture_incomplete", batches)
                if readiness_probe:
                    for agent_id in readiness_probe():
                        normalized = str(agent_id).lower()
                        with self.run.control.condition:
                            already_queued = any(
                                event.target_agent_id == normalized
                                and event.status == SimulationEventStatus.PENDING
                                for _, _, event in self._queue
                            )
                            if not already_queued:
                                self.enqueue("agent_ready", normalized)
                continue

            with self.run.control.condition:
                now = time.monotonic()
                if self._queue:
                    wait_seconds = max(0.0, self._queue[0][0] - now)
                else:
                    wait_seconds = max(0.0, idle_deadline - now)
                    if wait_seconds <= 0:
                        if (
                            self.run.failed_event_count
                            and not self.run.processed_event_count
                        ):
                            reason = "all_agents_failed"
                        elif self.run.processed_event_count:
                            reason = "tasks_completed"
                        else:
                            reason = "idle_completed"
                        return self._result(reason, batches)
                version = self._version
                self.run.control.condition.wait_for(
                    lambda: self._version != version
                    or self.run.control.stop_event.is_set()
                    or self.run.control.force_stop_event.is_set(),
                    timeout=wait_seconds,
                )

    def _take_ready(self, max_parallel: int) -> list[SimulationEvent]:
        with self.run.control.condition:
            now = time.monotonic()
            selected: list[SimulationEvent] = []
            deferred: list[tuple[float, int, SimulationEvent]] = []
            busy_targets: set[str] = set()
            while self._queue and len(selected) < max(1, int(max_parallel)):
                available_at, sequence, event = heapq.heappop(self._queue)
                if available_at > now:
                    deferred.append((available_at, sequence, event))
                    break
                if event.target_agent_id in busy_targets:
                    deferred.append((available_at, sequence, event))
                    continue
                event.status = SimulationEventStatus.DISPATCHED
                event.dispatched_at = _now_iso()
                selected.append(event)
                busy_targets.add(event.target_agent_id)
            for item in deferred:
                heapq.heappush(self._queue, item)
            if selected:
                self._touch()
        return selected

    def _complete_batch(
        self,
        events: list[SimulationEvent],
        results: list[dict[str, Any]],
    ) -> None:
        by_agent = {
            str(result.get("agent_id") or "").lower(): result
            for result in results
        }
        for event in events:
            result = by_agent.get(event.target_agent_id)
            event.completed_at = _now_iso()
            if result is None:
                event.status = SimulationEventStatus.FAILED
                event.error = "Agent event produced no result"
                self.run.failed_event_count += 1
            elif result.get("error") or result.get("status") == "error":
                event.status = SimulationEventStatus.FAILED
                event.error = str(result.get("error") or "Agent event failed")
                self.run.failed_event_count += 1
            else:
                event.status = SimulationEventStatus.COMPLETED
                self.run.processed_event_count += 1
        self._touch()

    def _cancel_pending(self) -> None:
        with self.run.control.condition:
            while self._queue:
                _, _, event = heapq.heappop(self._queue)
                if event.status == SimulationEventStatus.PENDING:
                    event.status = SimulationEventStatus.CANCELLED
                    event.completed_at = _now_iso()
                    self.run.cancelled_event_count += 1
            self._version += 1
            self._touch()

    def _touch(self) -> None:
        self.run.last_activity_at = _now_iso()

    def _result(self, stop_reason: str, batches: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "stop_reason": stop_reason,
            "processed_event_count": self.run.processed_event_count,
            "failed_event_count": self.run.failed_event_count,
            "cancelled_event_count": self.run.cancelled_event_count,
            "events": [event.to_dict() for event in self._events],
            "batches": batches,
        }
