from fastapi.testclient import TestClient

from agent_network.adapters.base import AgentRunResult
from services import agent_server


def _configure(client):
    response = client.post(
        "/communication/configure",
        json={
            "agent_id": "agent-b",
            "agent_name": "Agent B",
            "agent_role": "planner",
            "agent_directory": {
                "agent-a": "http://agent-a:8000",
                "agent-b": "http://agent-b:8000",
            },
            "comm_matrix": {"agent-a": ["agent-b"]},
        },
    )
    assert response.status_code == 200


def test_agent_card_and_a2a_delivery_task(monkeypatch):
    monkeypatch.setattr(agent_server, "_safe_post_json", lambda *args, **kwargs: True)
    agent_server._clear_inbox()
    agent_server.comm.clear_tasks()
    client = TestClient(agent_server.app)
    _configure(client)

    card_response = client.get("/.well-known/agent-card.json")
    assert card_response.status_code == 200
    interface = card_response.json()["supportedInterfaces"][0]
    assert interface["protocolBinding"] == "HTTP+JSON"
    assert interface["protocolVersion"] == "1.0"

    request_body = {
        "message": {
            "messageId": "message-1",
            "contextId": "context-1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello", "mediaType": "text/plain"}],
            "metadata": {
                "fromAgentId": "agent-a",
                "targetAgentId": "agent-b",
                "traceId": "trace-1",
            },
        },
        "configuration": {"returnImmediately": True},
    }
    missing_version = client.post("/a2a/v1/message:send", json=request_body)
    assert missing_version.status_code == 400

    response = client.post(
        "/a2a/v1/message:send",
        json=request_body,
        headers={"A2A-Version": "1.0"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/a2a+json")
    task = response.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"

    task_response = client.get(
        f"/a2a/v1/tasks/{task['id']}",
        headers={"A2A-Version": "1.0"},
    )
    assert task_response.status_code == 200
    assert task_response.json()["id"] == task["id"]

    list_response = client.get(
        "/a2a/v1/tasks",
        headers={"A2A-Version": "1.0"},
    )
    assert list_response.status_code == 200
    assert list_response.json()["totalSize"] == 1

    cancel_response = client.post(
        f"/a2a/v1/tasks/{task['id']}:cancel",
        headers={"A2A-Version": "1.0"},
    )
    assert cancel_response.status_code == 409
    assert cancel_response.json()["error"]["status"] == "TASK_NOT_CANCELABLE"

    inbox, _ = agent_server._snapshot_inbox()
    assert inbox == [
        {
            "from": "agent-a",
            "content": "hello",
            "type": "a2a",
            "channel_id": "context-1",
            "trace_id": "trace-1",
        }
    ]


def test_delegated_task_runs_and_emits_authenticated_callbacks(monkeypatch):
    class CallbackResponse:
        def raise_for_status(self):
            return None

    class CallbackSession:
        def __init__(self):
            self.calls = []

        def post(self, url, json, headers, timeout):
            self.calls.append((url, json, headers, timeout))
            return CallbackResponse()

    class Adapter:
        def run_agent_task(self, context):
            assert context.task == "inspect target"
            return AgentRunResult(
                trace_id=context.trace_id,
                agent_id=context.agent_id,
                status="completed",
                final_message="inspection complete",
            )

    monkeypatch.setattr(agent_server, "_safe_post_json", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "agent_network.native_audit._post_server", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(agent_server, "_make_adapter", lambda: Adapter())
    callback_session = CallbackSession()
    monkeypatch.setattr(agent_server.callback_dispatcher, "session", callback_session)
    agent_server.comm.clear_tasks()
    client = TestClient(agent_server.app)
    _configure(client)
    request_body = {
        "message": {
            "messageId": "delegation-1",
            "contextId": "context-task-1",
            "role": "ROLE_USER",
            "parts": [
                {"text": "inspect target", "mediaType": "text/plain"},
                {"data": {"input": {}}, "mediaType": "application/json"},
            ],
            "metadata": {
                "operation": "agent.task.assign",
                "fromAgentId": "srv",
                "targetAgentId": "agent-b",
                "traceId": "trace-task-1",
            },
        },
        "configuration": {
            "taskPushNotificationConfig": {
                "id": "callback-1",
                "url": "http://srv:8000/api/simulations/sim-1/task-events",
                "token": "callback-secret",
            }
        },
    }

    accepted = client.post(
        "/a2a/v1/message:send",
        json=request_body,
        headers={"A2A-Version": "1.0"},
    )
    assert accepted.status_code == 200
    task_id = accepted.json()["task"]["id"]
    assert accepted.json()["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert client.get("/status").json()["pending_tasks"] == 1

    completed = client.post("/run", json={"agent_id": "agent-b"})
    assert completed.status_code == 200
    task = agent_server.task_manager.get_task(task_id)
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "inspection complete"
    assert len(callback_session.calls) == 3
    assert all(
        call[2]["X-A2A-Notification-Token"] == "callback-secret"
        for call in callback_session.calls
    )
    assert callback_session.calls[0][1]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
    assert "artifactUpdate" in callback_session.calls[1][1]
    assert callback_session.calls[2][1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_task_callback_rejects_wrong_token_and_updates_outbound_task():
    agent_server.comm.clear_tasks()
    task = {
        "id": "outbound-task-1",
        "contextId": "context-1",
        "status": {"state": "TASK_STATE_SUBMITTED"},
        "metadata": {"traceId": "trace-1"},
    }
    agent_server.task_manager.save_outbound(
        task,
        "agent-b",
        "agent-a",
        "delegated work",
        callback_config={
            "id": "callback-1",
            "url": "http://agent-b:8000/a2a/v1/task-events",
            "token": "expected-token",
        },
    )
    payload = {
        "statusUpdate": {
            "taskId": "outbound-task-1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {"sequence": 1},
        }
    }
    client = TestClient(agent_server.app)

    rejected = client.post(
        "/a2a/v1/task-events",
        json=payload,
        headers={
            "A2A-Version": "1.0",
            "X-A2A-Notification-Token": "wrong-token",
        },
    )
    assert rejected.status_code == 401

    accepted = client.post(
        "/a2a/v1/task-events",
        json=payload,
        headers={
            "A2A-Version": "1.0",
            "X-A2A-Notification-Token": "expected-token",
        },
    )
    assert accepted.status_code == 200
    assert agent_server.task_manager.get_task("outbound-task-1")["status"]["state"] == "TASK_STATE_COMPLETED"
