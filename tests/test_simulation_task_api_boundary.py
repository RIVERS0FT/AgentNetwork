from pathlib import Path
import importlib


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "agent_network" / "api" / "managed_simulations.py").read_text(
    encoding="utf-8"
)


def test_simulation_task_api_uses_a2a_comm_manager_and_persistent_store():
    assert 'agent_id="srv"' in SOURCE
    assert "simulation_tasks = TaskManager(" in SOURCE
    assert "simulation_comm.delegate_task" in SOURCE
    assert "simulation_manager.enqueue_event" in SOURCE
    assert "run.state != SimulationState.RUNNING" in SOURCE
    assert '@router.post("/simulations/{simulation_id}/agents/{agent_id}/tasks")' in SOURCE
    assert '@router.get("/simulations/{simulation_id}/tasks/{task_id}")' in SOURCE
    assert '@router.post("/simulations/{simulation_id}/tasks/{task_id}:cancel")' in SOURCE


def test_managed_simulation_module_imports_with_public_comm_contract():
    module = importlib.import_module("agent_network.api.managed_simulations")
    assert module.simulation_comm.agent_id == "srv"


def test_simulation_callback_requires_token_and_updates_task_store():
    assert '@router.post("/simulations/{simulation_id}/task-events")' in SOURCE
    assert 'request.headers.get("X-A2A-Notification-Token"' in SOURCE
    assert "secrets.compare_digest" in SOURCE
    assert "simulation_comm.receive_task_event(payload)" in SOURCE
