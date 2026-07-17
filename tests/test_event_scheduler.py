import threading
import time

from agent_network.simulation_management import (
    SimulationEventScheduler,
    SimulationEventStatus,
    SimulationRun,
    SimulationRuntimeConfig,
)


def _run(max_parallel_agents=2):
    run = SimulationRun(
        scene="demo",
        runtime_config=SimulationRuntimeConfig(
            duration_seconds=10,
            idle_timeout_seconds=0,
        ),
        seed=7,
    )
    run.resource_plan = {"max_parallel_agents": max_parallel_agents}
    return run


def test_scheduler_dispatches_stable_events_without_rounds():
    run = _run()
    scheduler = SimulationEventScheduler(run)
    first = scheduler.enqueue("initial_task", "agent_a", {"task": "one"})
    second = scheduler.enqueue("message_ready", "agent_a", {"task": "two"})
    third = scheduler.enqueue("initial_task", "agent_b", {"task": "three"})
    dispatched = []

    def dispatch(events):
        dispatched.append([event.event_id for event in events])
        return [
            {"agent_id": event.target_agent_id, "status": "completed"}
            for event in events
        ]

    result = scheduler.run_loop(dispatch)

    assert dispatched == [[first.event_id, third.event_id], [second.event_id]]
    assert result["stop_reason"] == "tasks_completed"
    assert result["processed_event_count"] == 3
    assert all(event.status == SimulationEventStatus.COMPLETED for event in (first, second, third))
    assert "round" not in result


def test_scheduler_marks_missing_agent_result_as_failure():
    run = _run(max_parallel_agents=1)
    scheduler = SimulationEventScheduler(run)
    event = scheduler.enqueue("initial_task", "agent_a")

    result = scheduler.run_loop(lambda _events: [])

    assert event.status == SimulationEventStatus.FAILED
    assert event.error == "Agent event produced no result"
    assert result["stop_reason"] == "all_agents_failed"


def test_enqueue_wakes_blocking_scheduler_without_lost_notification():
    run = _run(max_parallel_agents=1)
    run.runtime_config = SimulationRuntimeConfig(
        duration_seconds=10,
        idle_timeout_seconds=5,
    )
    scheduler = SimulationEventScheduler(run)
    dispatched = []

    def dispatch(events):
        dispatched.extend(events)
        run.control.stop_event.set()
        return [{"agent_id": events[0].target_agent_id, "status": "completed"}]

    worker = threading.Thread(target=scheduler.run_loop, args=(dispatch,))
    worker.start()
    time.sleep(0.05)
    event = scheduler.enqueue("delegated_task", "agent_a")
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert dispatched == [event]
