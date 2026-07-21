import threading
import time
from types import SimpleNamespace

import pytest

from agent_network.simulation_management import (
    SimulationManager,
    SimulationRuntimeConfig,
    SimulationState,
)
from agent_network.simulation_management.resource_allocator import (
    ResourceAllocationError,
)


class FakeRuntime:
    _docker_client = None

    def __init__(self):
        self.cancel_calls = []
        self.force_calls = 0

    def cancel_agent_tasks(self, states=None):
        self.cancel_calls.append(states)
        return []

    def force_stop_all(self):
        self.force_calls += 1
        return []


def _scene(_name="demo"):
    return SimpleNamespace(
        scene_key="demo",
        agents=[
            SimpleNamespace(agent_id="agent_a"),
            SimpleNamespace(agent_id="agent_b"),
        ],
    )


def _manager(run_handler):
    runtime = FakeRuntime()
    manager = SimulationManager(
        scene_loader=_scene,
        setup_handler=lambda run: {
            "scene": run.scene_definition.scene_key,
            "seed": run.seed,
        },
        run_handler=run_handler,
        runtime_provider=lambda: runtime,
    )
    return manager, runtime


def _wait_for(run, state, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run.state == state:
            return
        time.sleep(0.01)
    raise AssertionError(f"run did not reach {state}: {run.state}")


def test_configure_resolves_per_agent_resources_and_start_completes():
    manager, _runtime = _manager(lambda _run: {"status": "completed"})
    config = SimulationRuntimeConfig.from_dict(
        {
            "duration_seconds": 10,
            "resource_allocation": {
                "default_agent": {"cpu_cores": 0.5, "memory_mb": 256},
                "agent_overrides": {
                    "agent_b": {"cpu_cores": 1.5, "memory_mb": 512}
                },
                "max_parallel_agents": 1,
            },
        }
    )

    run = manager.configure("demo", config, seed=7)

    assert run.state == SimulationState.CONFIGURED
    assert run.seed == 7
    assert run.scene_definition.scene_key == "demo"
    assert run.resource_plan["agents"]["agent_a"]["cpu_cores"] == 0.5
    assert run.resource_plan["agents"]["agent_b"]["memory_mb"] == 512
    assert run.resource_plan["max_parallel_agents"] == 1

    manager.start(run.simulation_id)

    assert run.state == SimulationState.COMPLETED
    assert run.stop_reason == "completed"
    assert run.control.terminal_event.is_set()


def test_configure_rejects_resource_override_for_unknown_agent():
    manager, _runtime = _manager(lambda _run: {})
    config = SimulationRuntimeConfig.from_dict(
        {
            "resource_allocation": {
                "agent_overrides": {"missing": {"memory_mb": 256}}
            }
        }
    )

    with pytest.raises(ResourceAllocationError):
        manager.configure("demo", config)


def test_stop_requests_submitted_task_callbacks_and_waits_for_shutdown():
    def run_handler(run):
        assert run.control.stop_event.wait(timeout=2)
        return {"status": "stopped", "stop_reason": run.control.reason}

    manager, runtime = _manager(run_handler)
    run = manager.configure(
        "demo",
        SimulationRuntimeConfig(
            duration_seconds=10,
            graceful_stop_timeout_seconds=2,
        ),
    )
    worker = threading.Thread(target=manager.start, args=(run.simulation_id,))
    worker.start()
    _wait_for(run, SimulationState.RUNNING)

    manager.stop(run.simulation_id)
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert run.state == SimulationState.STOPPED
    assert run.stop_reason == "user_stopped"
    assert runtime.cancel_calls == [{"TASK_STATE_SUBMITTED"}]


def test_force_stop_kills_runtime_and_is_idempotent():
    def run_handler(run):
        assert run.control.force_stop_event.wait(timeout=2)
        return {"status": "stopped"}

    manager, runtime = _manager(run_handler)
    run = manager.configure(
        "demo", SimulationRuntimeConfig(duration_seconds=10)
    )
    worker = threading.Thread(target=manager.start, args=(run.simulation_id,))
    worker.start()
    _wait_for(run, SimulationState.RUNNING)

    manager.force_stop(run.simulation_id)
    worker.join(timeout=2)
    manager.force_stop(run.simulation_id)

    assert not worker.is_alive()
    assert run.state == SimulationState.FORCE_STOPPED
    assert run.stop_reason == "user_force_stopped"
    assert runtime.force_calls == 1
    assert run.action_status == "already_stopped"


def test_duration_expiry_force_stops_simulation():
    def run_handler(run):
        assert run.control.force_stop_event.wait(timeout=2)
        return {"status": "stopped"}

    manager, runtime = _manager(run_handler)
    run = manager.configure(
        "demo", SimulationRuntimeConfig(duration_seconds=1)
    )

    manager.start(run.simulation_id)

    assert run.state == SimulationState.FORCE_STOPPED
    assert run.stop_reason == "duration_exceeded"
    assert runtime.force_calls == 1


def test_each_run_owns_non_serialized_execution_context():
    observed = []

    def setup_handler(run):
        run.execution_config = {"run_seed": str(run.seed)}
        return {"scene": run.scene_definition.scene_key, "seed": run.seed}

    def run_handler(run):
        observed.append(
            (
                run.scene_definition.scene_key,
                run.seed,
                dict(run.execution_config),
            )
        )
        return {"status": "completed"}

    manager = SimulationManager(
        scene_loader=lambda name: SimpleNamespace(
            scene_key=name,
            agents=[SimpleNamespace(agent_id="agent_a")],
        ),
        setup_handler=setup_handler,
        run_handler=run_handler,
        runtime_provider=FakeRuntime,
    )

    first = manager.configure(
        "first", SimulationRuntimeConfig(duration_seconds=10), seed=11
    )
    manager.start(first.simulation_id)
    second = manager.configure(
        "second", SimulationRuntimeConfig(duration_seconds=10), seed=22
    )
    manager.start(second.simulation_id)

    assert observed == [
        ("first", 11, {"run_seed": "11"}),
        ("second", 22, {"run_seed": "22"}),
    ]
    assert "scene_definition" not in first.to_dict()
    assert "execution_config" not in first.to_dict()
