from agent_network.agent_management import Agent, AgentRegistry
from agent_network.native_audit import NativeAuditState, _post_server
from fastapi.testclient import TestClient


def test_native_audit_state_registers_child_and_terminal_status(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "agent_network.native_audit.emit_native_event",
        lambda record: emitted.append(record) or record,
    )
    AgentRegistry.reset()
    parent = Agent(agent_id="parent", backend="openclaw")
    AgentRegistry.register(parent)
    state = NativeAuditState()
    state.configure("parent", "openclaw", None, trace_id="trace-1")

    decision = state.check_tool(
        agent_id="parent",
        backend="openclaw",
        tool_name="sessions_spawn",
        tool_input={"task": "research"},
        tool_call_id="spawn-1",
    )
    assert decision["allowed"] is True

    state.subagent_lifecycle(
        parent_agent_id="parent",
        child_agent_id="child-1",
        backend="openclaw",
        status="running",
        session_id="agent:parent:subagent:1",
    )
    state.subagent_lifecycle(
        parent_agent_id="parent",
        child_agent_id="child-1",
        backend="openclaw",
        status="completed",
        session_id="agent:parent:subagent:1",
    )

    child = AgentRegistry.get("child-1")
    assert child is not None
    assert child.parent_agent_id == "parent"
    assert child.runtime_kind == "openclaw_native"
    assert child.status == "completed"
    assert [event["event"] for event in emitted].count("subagent_lifecycle") == 2
    state.reset()
    assert AgentRegistry.get("child-1") is None
    assert AgentRegistry.get("parent") is parent
    AgentRegistry.reset()


def test_native_audit_state_denies_unknown_tool(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "agent_network.native_audit.emit_native_event",
        lambda record: emitted.append(record) or record,
    )
    state = NativeAuditState()
    state.configure("agent-a", "claude-code", None, trace_id="trace-2")
    decision = state.check_tool(
        agent_id="agent-a",
        backend="claude-code",
        tool_name="UnregisteredNativeTool",
        tool_input={},
        tool_call_id="unknown-1",
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "unknown native tool"
    assert any(
        event["event"] == "policy_check"
        and event["result"].get("decision") == "denied"
        for event in emitted
    )


def test_native_session_resume_expires_at_policy_ttl(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(
        "agent_network.native_audit.emit_native_event", lambda record: record
    )
    monkeypatch.setattr("agent_network.native_audit.time.monotonic", lambda: clock[0])
    state = NativeAuditState()
    state.configure(
        "agent-a",
        "claude-code",
        {"session": {"persistent": True, "ttl_seconds": 10}},
    )
    state.set_session_id("agent-a", "session-1")
    assert state.session_id("agent-a") == "session-1"
    clock[0] = 111.0
    assert state.session_id("agent-a") == ""


def test_native_audit_delivery_failure_is_fail_closed(monkeypatch):
    monkeypatch.setattr(
        "agent_network.native_audit.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    try:
        _post_server({"event": "policy_check"})
    except RuntimeError as exc:
        assert "native audit delivery" in str(exc)
    else:
        raise AssertionError("native audit delivery failure must raise")


def test_native_audit_reset_emits_terminal_event_for_running_children(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "agent_network.native_audit.emit_native_event",
        lambda record: emitted.append(record) or record,
    )
    state = NativeAuditState()
    state.configure("parent", "openclaw", None, trace_id="trace-reset")
    state.subagent_lifecycle(
        parent_agent_id="parent",
        child_agent_id="child-reset",
        backend="openclaw",
        status="running",
        session_id="child-session",
    )
    state.reset()
    assert any(
        event["event"] == "subagent_lifecycle"
        and event["target"]["agent_id"] == "child-reset"
        and event["action"]["status"] == "reset"
        for event in emitted
    )


def test_agent_server_native_audit_endpoint_requires_token(monkeypatch):
    from services import agent_server

    monkeypatch.setenv("NATIVE_AUDIT_TOKEN", "test-native-token")
    monkeypatch.setattr(
        agent_server.native_audit_state,
        "check_tool",
        lambda **kwargs: {"allowed": True, "reason": "test"},
    )
    client = TestClient(agent_server.app)
    body = {
        "agent_id": "a1",
        "backend": "openclaw",
        "tool_name": "read",
        "tool_input": {"path": "/app/demo.txt"},
    }

    assert client.post("/internal/native/policy/check", json=body).status_code == 401
    response = client.post(
        "/internal/native/policy/check",
        json=body,
        headers={"Authorization": "Bearer test-native-token"},
    )
    assert response.status_code == 200
    assert response.json()["allowed"] is True


def test_agent_server_native_audit_endpoint_fails_closed_without_configured_token(
    monkeypatch,
):
    from services import agent_server

    monkeypatch.delenv("NATIVE_AUDIT_TOKEN", raising=False)
    client = TestClient(agent_server.app)
    response = client.post(
        "/internal/native/policy/check",
        json={
            "agent_id": "a1",
            "backend": "openclaw",
            "tool_name": "read",
            "tool_input": {"path": "/app/demo.txt"},
        },
    )
    assert response.status_code == 401
