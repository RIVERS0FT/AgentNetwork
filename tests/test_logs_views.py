import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_network.api.logs import router
from agent_network.log_manager import get_log_manager

app = FastAPI()
app.include_router(router, prefix="/api/logs")
client = TestClient(app)


@pytest.fixture
def mock_logs():
    manager = get_log_manager()
    manager.reset()

    manager.emit_application_event(
        event="agent_message",
        actor={"agent_id": "agent_A"},
        target={"agent_id": "agent_B"},
        conversation={},
        action={"name": "send_message"},
        content={"text": "msg 1"},
    )
    manager.emit_application_event(
        event="reasoning",
        actor={"agent_id": "agent_B"},
        action={"name": "reasoning"},
        decision={"summary": "reasoning 1"},
    )
    manager.emit_application_event(
        event="acting",
        actor={"agent_id": "agent_B"},
        action={"name": "acting"},
    )
    manager.emit_application_event(
        event="policy_check",
        actor={"agent_id": "agent_C"},
        policy={"result": "allowed"},
    )

    yield manager
    manager.reset()


@pytest.mark.not_llm
def test_messages_api_view(mock_logs):
    response = client.get("/api/logs/messages")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["entries"][0]["event"] == "agent_message"


@pytest.mark.not_llm
def test_application_api_view(mock_logs):
    response = client.get("/api/logs/application")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 4
    events = [entry["event"] for entry in data["entries"]]
    assert "reasoning" in events
    assert "acting" in events
    assert "agent_message" in events
    assert "policy_check" in events


@pytest.mark.not_llm
def test_log_type_query_parameter(mock_logs):
    response = client.get("/api/logs/", params={"log_type": "application"})
    assert response.status_code == 200
    assert response.json()["total"] == 4


@pytest.mark.not_llm
def test_agent_view_returns_acting_events(mock_logs):
    response = client.get("/api/logs/agent")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["logs"][0]["event"] == "acting"


@pytest.mark.not_llm
def test_agent_log_ingest_always_emits_traceable_tool_event():
    manager = get_log_manager()
    manager.reset()

    response = client.post(
        "/api/logs/agent",
        json={
            "agent_id": "planner",
            "event": "tool_result",
            "action": "tool_result",
            "action_status": "success",
            "trace_id": "trace-1",
            "details": {
                "tool_name": "write_plan",
                "arguments": {"title": "Plan"},
                "result": {"status": "success"},
                "duration_ms": 12.5,
            },
        },
    )

    assert response.status_code == 200
    record = manager.get_entries(1)[0]
    assert record["event"] == "tool_result"
    assert record["trace"]["trace_id"] == "trace-1"
    assert record["tool"]["name"] == "write_plan"
    assert record["action"]["duration_ms"] == 12.5


@pytest.mark.not_llm
@pytest.mark.parametrize(
    "event",
    [
        "decide",
        "agent_decide",
        "act",
        "agent_action",
        "llm_cli_call",
        "custom_application_event",
    ],
)
def test_agent_log_ingest_rejects_unknown_application_events(event):
    manager = get_log_manager()
    manager.reset()

    response = client.post(
        "/api/logs/agent",
        json={
            "agent_id": "planner",
            "event": event,
            "action": event,
        },
    )

    assert response.status_code == 422
    assert "unknown application event" in response.json()["detail"]


@pytest.mark.not_llm
def test_ingest_rejects_invalid_explicit_log_type():
    response = client.post(
        "/api/logs/ingest",
        json={
            "log_type": "application.jsonl",
            "event": "acting",
            "actor": {"agent_id": "planner"},
            "action": {"name": "plan"},
            "trace": {"trace_id": "trace-1"},
        },
    )

    assert response.status_code == 422
    assert "unknown log type" in response.json()["detail"]
