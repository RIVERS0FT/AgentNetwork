"""Unified simulation configuration and lifecycle manager."""

from __future__ import annotations

import random
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from .models import (
    TERMINAL_STATES,
    SimulationRun,
    SimulationRuntimeConfig,
    SimulationState,
)
from .resource_allocator import SimulationResourceAllocator


class SimulationManagerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SimulationManager:
    """Own all simulation lifecycle state; APIs are adapters around this class."""

    def __init__(
        self,
        scene_loader: Callable[[str], Any],
        setup_handler: Callable[[Any, int], dict[str, Any]],
        run_handler: Callable[[SimulationRun], dict[str, Any]],
        runtime_provider: Callable[[], Any],
        allocator: SimulationResourceAllocator | None = None,
    ):
        self.scene_loader = scene_loader
        self.setup_handler = setup_handler
        self.run_handler = run_handler
        self.runtime_provider = runtime_provider
        self.allocator = allocator or SimulationResourceAllocator()
        self._runs: dict[str, SimulationRun] = {}
        self._lock = threading.RLock()
        self._current_id = ""

    def configure(
        self,
        scene: str,
        runtime_config: SimulationRuntimeConfig,
        seed: int | None = None,
    ) -> SimulationRun:
        with self._lock:
            current = self._runs.get(self._current_id)
            if current and current.state not in TERMINAL_STATES:
                raise SimulationManagerError(
                    "SIMULATION_ACTIVE", "another simulation is active"
                )
        scene_def = self.scene_loader(scene)
        resolved_seed = (
            int(seed)
            if seed is not None
            else random.SystemRandom().randrange(1, 2**31)
        )
        plan = self.allocator.build_plan(
            [agent.agent_id for agent in scene_def.agents],
            runtime_config.resource_allocation,
            self.runtime_provider(),
        )
        run = SimulationRun(
            scene=scene_def.scene_key,
            runtime_config=runtime_config,
            seed=resolved_seed,
            resource_plan=plan,
        )
        run.setup = self.setup_handler(scene_def, resolved_seed)
        run.mark_configured()
        run.action_status = "configured"
        with self._lock:
            self._runs[run.simulation_id] = run
            self._current_id = run.simulation_id
        return run

    def start(self, simulation_id: str) -> SimulationRun:
        run = self.get_run(simulation_id)
        with self._lock:
            if run.state != SimulationState.CONFIGURED:
                raise SimulationManagerError(
                    "INVALID_SIMULATION_STATE",
                    f"simulation cannot start from {run.state.value}",
                )
            run.state = SimulationState.STARTING
            run.action_status = "starting"
            run.mark_started()

        timer = threading.Timer(
            run.runtime_config.duration_seconds,
            self._duration_expired,
            args=(simulation_id,),
        )
        timer.daemon = True
        timer.start()
        try:
            run.result = self.run_handler(run) or {}
            run.error = str(run.result.get("error") or "")
            if run.control.force_stop_event.is_set():
                run.state = SimulationState.FORCE_STOPPED
            elif run.control.stop_event.is_set():
                run.state = SimulationState.STOPPED
            elif run.error or run.result.get("status") == "error":
                run.state = SimulationState.FAILED
            else:
                run.state = SimulationState.COMPLETED
            run.stop_reason = (
                run.control.reason
                or str(run.result.get("stop_reason") or "completed")
            )
        except Exception as exc:
            run.error = str(exc)
            run.stop_reason = run.control.reason or "runtime_exception"
            run.state = (
                SimulationState.FORCE_STOPPED
                if run.control.force_stop_event.is_set()
                else SimulationState.FAILED
            )
        finally:
            timer.cancel()
            run.stopped_at = datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
            run.action_status = "finished"
            run.control.terminal_event.set()
        return run

    def stop(self, simulation_id: str) -> SimulationRun:
        run = self.get_run(simulation_id)
        with self._lock:
            if run.state in TERMINAL_STATES:
                run.action_status = "already_stopped"
                return run
            if run.state == SimulationState.CONFIGURED:
                run.state = SimulationState.STOPPED
                run.stop_reason = "user_stopped_before_start"
                run.action_status = "stopped_before_start"
                run.stopped_at = datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
                run.control.terminal_event.set()
                return run
            run.state = SimulationState.STOPPING
            run.action_status = "stop_requested"
            run.control.reason = run.control.reason or "user_stopped"
            run.control.stop_event.set()
        runtime = self.runtime_provider()
        if hasattr(runtime, "cancel_agent_tasks"):
            runtime.cancel_agent_tasks(states={"TASK_STATE_SUBMITTED"})
        finished = run.control.terminal_event.wait(
            timeout=run.runtime_config.graceful_stop_timeout_seconds
        )
        if not finished:
            run.action_status = "stop_timeout"
        return run

    def force_stop(
        self,
        simulation_id: str,
        reason: str = "user_force_stopped",
        wait_seconds: float = 5,
    ) -> SimulationRun:
        run = self.get_run(simulation_id)
        with self._lock:
            if run.state in TERMINAL_STATES:
                run.action_status = "already_stopped"
                return run
            if run.state in {SimulationState.CREATED, SimulationState.CONFIGURED}:
                run.state = SimulationState.FORCE_STOPPED
                run.stop_reason = reason
                run.action_status = "force_stopped_before_start"
                run.stopped_at = datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
                run.control.stop_event.set()
                run.control.force_stop_event.set()
                run.control.terminal_event.set()
                return run
            run.state = SimulationState.FORCE_STOPPING
            run.action_status = "force_stop_requested"
            run.control.reason = reason
            run.control.stop_event.set()
            run.control.force_stop_event.set()
        runtime = self.runtime_provider()
        if hasattr(runtime, "force_stop_all"):
            runtime.force_stop_all()
        if wait_seconds > 0:
            run.control.terminal_event.wait(timeout=wait_seconds)
        return run

    def get_run(self, simulation_id: str) -> SimulationRun:
        with self._lock:
            run = self._runs.get(simulation_id)
        if not run:
            raise SimulationManagerError(
                "SIMULATION_NOT_FOUND", f"Simulation '{simulation_id}' was not found"
            )
        return run

    def current(self) -> SimulationRun | None:
        with self._lock:
            return self._runs.get(self._current_id)

    def list_runs(self) -> list[SimulationRun]:
        with self._lock:
            return list(self._runs.values())

    def _duration_expired(self, simulation_id: str) -> None:
        try:
            self.force_stop(
                simulation_id,
                reason="duration_exceeded",
                wait_seconds=0,
            )
        except SimulationManagerError:
            return
