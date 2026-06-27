import pytest

from agent_network.container_runtime import ContainerAgent, ContainerRuntime


def _runtime(monkeypatch):
    monkeypatch.setattr(ContainerRuntime, "_init_docker", lambda self: setattr(self, "_docker_client", None))
    return ContainerRuntime(message_bus_url="http://message-bus:9000")


def test_container_runtime_rejects_brain_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        runtime._normalize_backend("brain")

    assert "Backend 'brain' has been removed" in str(exc.value)


def test_container_runtime_normalizes_claudecode_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    assert runtime._normalize_backend("claudecode") == "claude-code"


def test_container_runtime_rejects_unknown_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        runtime._normalize_backend("unknown")

    assert "Unsupported backend" in str(exc.value)


def test_run_all_skips_agent_without_task_or_messages(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.agents["agent_a"] = ContainerAgent(
        agent_id="agent_a",
        name="Agent A",
        role="planner",
        url="http://agent-a:8000",
        status="idle",
    )

    results = runtime.run_all({"tasks": {}, "messages": []})

    assert results == [
        {
            "agent_id": "agent_a",
            "status": "skipped",
            "reason": "no_task_or_message",
            "outbound_messages": [],
            "tool_events": [],
            "state_changes": [],
        }
    ]


def test_run_all_posts_structured_context_without_local_agent_execution(monkeypatch):
    runtime = _runtime(monkeypatch)
    ca = ContainerAgent(
        agent_id="agent_a",
        name="Agent A",
        role="planner",
        url="http://agent-a:8000",
        status="idle",
    )
    ca._extra_meta = {
        "skills_list": [{"name": "planning", "sop_content": "SOP"}],
        "core_goal": "Coordinate",
        "action_space": ["send_message"],
        "scene_key": "demo_scene",
        "allowed_tools": ["write_plan"],
    }
    runtime.agents["agent_a"] = ca

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"agent_id": "agent_a", "status": "completed", "application_events": []}

    def fake_post(url, json, timeout):
        posted["url"] = url
        posted["json"] = json
        posted["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("agent_network.container_runtime.requests.post", fake_post)

    results = runtime.run_all({"tasks": {"agent_a": ["Do the assigned work"]}, "messages": []})

    assert results[0]["status"] == "completed"
    assert posted["url"] == "http://agent-a:8000/run"
    assert posted["json"]["task"] == "Do the assigned work"
    assert posted["json"]["scene_key"] == "demo_scene"
    assert posted["json"]["skills"] == [{"name": "planning", "sop_content": "SOP"}]
    assert posted["json"]["allowed_tools"] == ["send_message", "write_plan"]
