import pytest
from pathlib import Path

from agent_network.agent_management import ContainerAgent, ContainerRuntime


def _runtime(monkeypatch):
    monkeypatch.setattr(
        ContainerRuntime,
        "_init_docker",
        lambda self: setattr(self, "_docker_client", None),
    )
    return ContainerRuntime()


def test_container_runtime_rejects_brain_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        runtime._normalize_backend("brain")

    assert "Backend 'brain' has been removed" in str(exc.value)


def test_container_runtime_accepts_claude_code_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    assert runtime._normalize_backend("claude-code") == "claude-code"


def test_dynamic_containers_receive_native_audit_security_environment():
    source = (
        Path(__file__).resolve().parents[1]
        / "agent_network"
        / "agent_management.py"
    ).read_text(encoding="utf-8")
    for name in (
        "NATIVE_AUDIT_TOKEN",
        "NATIVE_AUDIT_REQUIRED",
        "AGENT_NATIVE_WORKSPACE",
        "AGENT_NATIVE_ALLOWED_ROOTS",
        "SCENE_DIR",
    ):
        assert f'"{name}"' in source


def test_container_runtime_converts_resource_limits_to_docker_options(monkeypatch):
    runtime = _runtime(monkeypatch)

    assert runtime._resource_kwargs(
        {"cpu_cores": 1.5, "memory_mb": 768, "pids_limit": 96}
    ) == {
        "nano_cpus": 1_500_000_000,
        "mem_limit": "768m",
        "pids_limit": 96,
    }


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
        skill_refs=["planning"],
        url="http://agent-a:8000",
        status="idle",
    )

    class StatusResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"inbox_size": 0}

    monkeypatch.setattr(
        "agent_network.agent_management.requests.get",
        lambda *_args, **_kwargs: StatusResponse(),
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
    assignment = ContainerAgent(
        agent_id="agent_a",
        name="Agent A",
        role="planner",
        core_goal="Coordinate",
        backend="openclaw",
        skill_refs=["planning"],
        allowed_tools=["write_plan"],
        scene_key="demo_scene",
        url="http://agent-a:8000",
        status="idle",
    )
    runtime.agents["agent_a"] = assignment

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "agent_id": "agent_a",
                "status": "completed",
                "application_events": [],
            }

    def fake_post(url, json, timeout):
        posted["url"] = url
        posted["json"] = json
        posted["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "agent_network.agent_management.requests.post",
        fake_post,
    )

    results = runtime.run_all(
        {
            "tasks": {"agent_a": ["Do the assigned work"]},
            "messages": [],
        }
    )

    assert results[0]["status"] == "completed"
    assert posted["url"] == "http://agent-a:8000/run"
    assert posted["json"]["task"] == "Do the assigned work"
    assert posted["json"]["scene_key"] == "demo_scene"
    assert "skills" not in posted["json"]
    assert "allowed_skills" not in posted["json"]
    assert posted["json"]["skill_refs"] == ["planning"]
    assert posted["json"]["allowed_tools"] == [
        "send_message",
        "delegate_task",
        "write_plan",
    ]
    assert posted["json"]["native_capabilities"]["enabled"] is True
    assert posted["json"]["native_capabilities"]["audit"]["required"] is True
    assert posted["json"]["core_goal"] == "Coordinate"


def test_run_all_wakes_agent_when_container_inbox_has_messages(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.agents["agent_a"] = ContainerAgent(
        agent_id="agent_a",
        name="Agent A",
        role="planner",
        url="http://agent-a:8000",
        status="idle",
    )

    class StatusResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"inbox_size": 1}

    class RunResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "agent_id": "agent_a",
                "status": "completed",
                "application_events": [],
            }

    posted = []
    monkeypatch.setattr(
        "agent_network.agent_management.requests.get",
        lambda *_args, **_kwargs: StatusResponse(),
    )
    monkeypatch.setattr(
        "agent_network.agent_management.requests.post",
        lambda url, **kwargs: posted.append(url) or RunResponse(),
    )

    result = runtime.run_all({"tasks": {}, "messages": []})

    assert result[0]["status"] == "completed"
    assert posted == ["http://agent-a:8000/run"]


def test_runtime_waits_until_agent_server_is_ready(monkeypatch):
    runtime = _runtime(monkeypatch)
    assignment = ContainerAgent(
        agent_id="agent_a",
        name="Agent A",
        role="planner",
        container_name="ag-o10",
        url="http://ag-o10:8000",
        status="idle",
    )
    calls = []

    class ReadyResponse:
        def raise_for_status(self):
            return None

    class FakeRequests:
        @staticmethod
        def get(url, timeout):
            calls.append((url, timeout))
            if len(calls) < 3:
                raise ConnectionError("connection refused")
            return ReadyResponse()

    monkeypatch.setattr(
        "agent_network.agent_management.time.sleep",
        lambda _seconds: None,
    )

    errors = runtime.wait_for_agents_ready(
        [assignment],
        timeout_seconds=1,
        poll_interval_seconds=0.01,
        requests_module=FakeRequests,
    )

    assert errors == []
    assert assignment.status == "idle"
    assert len(calls) == 3
    assert calls[0][0] == "http://ag-o10:8000/status"


def test_runtime_records_readiness_timeout(monkeypatch):
    runtime = _runtime(monkeypatch)
    assignment = ContainerAgent(
        agent_id="agent_a",
        name="Agent A",
        role="planner",
        container_name="ag-o10",
        url="http://ag-o10:8000",
        status="idle",
    )

    class Clock:
        current = 0.0

        @classmethod
        def monotonic(cls):
            return cls.current

        @classmethod
        def sleep(cls, seconds):
            cls.current += seconds

    class FakeRequests:
        @staticmethod
        def get(_url, timeout):
            Clock.current += timeout
            raise ConnectionError("connection refused")

    monkeypatch.setattr(
        "agent_network.agent_management.time.monotonic",
        Clock.monotonic,
    )
    monkeypatch.setattr(
        "agent_network.agent_management.time.sleep",
        Clock.sleep,
    )

    errors = runtime.wait_for_agents_ready(
        [assignment],
        timeout_seconds=1,
        poll_interval_seconds=0.1,
        requests_module=FakeRequests,
    )

    assert errors[0]["agent_id"] == "agent_a"
    assert "readiness timed out after 1s" in errors[0]["error"]
    assert "connection refused" in errors[0]["error"]
    assert assignment.status == "error"
    assert assignment.assign_error == errors[0]["error"]


def test_runtime_detects_container_exit_before_readiness(monkeypatch):
    runtime = _runtime(monkeypatch)
    assignment = ContainerAgent(
        agent_id="agent_a",
        name="Agent A",
        role="planner",
        container_name="ag-o10",
        url="http://ag-o10:8000",
        status="idle",
    )

    class ExitedContainer:
        attrs = {
            "State": {
                "Status": "exited",
                "ExitCode": 1,
                "Error": "gateway startup failed",
            }
        }

        def reload(self):
            return None

    class Containers:
        @staticmethod
        def get(_name):
            return ExitedContainer()

    class DockerClient:
        containers = Containers()

    class UnexpectedRequests:
        @staticmethod
        def get(*_args, **_kwargs):
            raise AssertionError(
                "status must not be called for an exited container"
            )

    runtime._docker_client = DockerClient()
    errors = runtime.wait_for_agents_ready(
        [assignment],
        timeout_seconds=1,
        requests_module=UnexpectedRequests,
    )

    assert errors == [
        {
            "agent_id": "agent_a",
            "error": (
                "Agent container 'ag-o10' exited before readiness "
                "(status=exited, exit_code=1): gateway startup failed"
            ),
        }
    ]
    assert assignment.status == "error"
