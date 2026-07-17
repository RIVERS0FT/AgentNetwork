import importlib

import pytest

from agent_network.comm_management import CommManager, CommunicationError, SendResult


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(("GET", url, timeout))
        return FakeResponse(
            {
                "supportedInterfaces": [
                    {
                        "url": "http://agent-b:8000/a2a/v1",
                        "protocolBinding": "HTTP+JSON",
                        "protocolVersion": "1.0",
                    }
                ]
            }
        )

    def post(self, url, json, headers, timeout):
        self.calls.append(("POST", url, json, headers, timeout))
        return FakeResponse(
            {
                "task": {
                    "id": "task-1",
                    "contextId": json["message"]["contextId"],
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "metadata": json["message"].get("metadata", {}),
                }
            }
        )


def test_legacy_comm_import_is_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agent_network.comm")

    assert not hasattr(CommManager, "broadcast")


def test_send_message_uses_a2a_card_and_rest_binding():
    session = FakeSession()
    manager = CommManager(
        agent_directory={"agent-b": "http://agent-b:8000"},
        comm_matrix={"agent-a": ["agent-b"]},
        session=session,
    )

    result = manager.send_message(
        "agent-a",
        "Agent A",
        "agent-b",
        "hello",
        "context-1",
        "trace-1",
    )

    assert result.ok
    assert result.task_id == "task-1"
    assert session.calls[0][0:2] == (
        "GET",
        "http://agent-b:8000/.well-known/agent-card.json",
    )
    method, url, payload, headers, _ = session.calls[1]
    assert method == "POST"
    assert url == "http://agent-b:8000/a2a/v1/message:send"
    assert headers["A2A-Version"] == "1.0"
    assert headers["Content-Type"] == "application/a2a+json"
    assert payload["message"]["role"] == "ROLE_USER"
    assert payload["message"]["parts"] == [
        {"text": "hello", "mediaType": "text/plain"}
    ]
    assert payload["message"]["metadata"]["fromAgentId"] == "agent-a"
    assert payload["configuration"]["returnImmediately"] is True


def test_permission_denial_does_not_send_http_request():
    session = FakeSession()
    manager = CommManager(
        agent_directory={"agent-b": "http://agent-b:8000"},
        comm_matrix={"agent-a": []},
        session=session,
    )

    result = manager.send_message("agent-a", "Agent A", "agent-b", "hello")

    assert not result.ok
    assert "denied" in result.error
    assert session.calls == []


def test_send_to_many_is_sequential_deduplicated_and_continues_after_failure():
    calls = []

    class RecordingManager(CommManager):
        def send_message(self, from_id, from_name, target, content, channel_id="", trace_id=""):
            calls.append(target)
            return SendResult(
                target=target,
                status="failed" if target == "agent-b" else "success",
                message_id=f"message-{target}",
            )

    result = RecordingManager().send_to_many(
        "agent-a",
        "Agent A",
        ["agent-b", "agent-c", "agent-b", "agent-d"],
        "hello",
    )

    assert calls == ["agent-b", "agent-c", "agent-d"]
    assert result.status == "partial"
    assert [item.target for item in result.results] == calls


def test_receive_message_creates_delivery_task_and_calls_inbox_handler():
    received = []
    manager = CommManager(
        agent_id="agent-b",
        comm_matrix={"agent-a": ["agent-b"]},
        inbox_handler=lambda *args: received.append(args),
    )

    response = manager.receive_message(
        {
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
            }
        }
    )

    task = response["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["contextId"] == "context-1"
    assert manager.get_task(task["id"])["id"] == task["id"]
    listed = manager.list_tasks(context_id="context-1")
    assert listed["totalSize"] == 1
    assert "artifacts" not in listed["tasks"][0]
    assert received == [("agent-a", "hello", "a2a", "context-1", "trace-1")]


def test_receive_message_rejects_disallowed_source():
    manager = CommManager(
        agent_id="agent-b",
        comm_matrix={"agent-c": ["agent-b"]},
    )

    try:
        manager.receive_message(
            {
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                    "metadata": {
                        "fromAgentId": "agent-a",
                        "targetAgentId": "agent-b",
                    },
                }
            }
        )
    except CommunicationError as exc:
        assert exc.code == "COMMUNICATION_DENIED"
        assert exc.status_code == 403
    else:
        raise AssertionError("expected communication denial")


def test_agent_card_declares_only_point_to_point_a2a_http_json():
    manager = CommManager(
        agent_id="agent-b",
        agent_name="Agent B",
        agent_role="planner",
    )

    card = manager.agent_card("http://agent-b:8000", ["planning"])

    assert card["name"] == "Agent B"
    assert card["supportedInterfaces"] == [
        {
            "url": "http://agent-b:8000/a2a/v1",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }
    ]
    assert card["capabilities"]["streaming"] is False
    assert card["capabilities"]["pushNotifications"] is True
    assert card["skills"][0]["id"] == "planning"


def test_delegate_task_creates_persistent_submitted_task_with_callback():
    session = FakeSession()
    manager = CommManager(
        agent_id="agent-a",
        agent_directory={
            "agent-a": "http://agent-a:8000",
            "agent-b": "http://agent-b:8000",
        },
        comm_matrix={"agent-a": ["agent-b"]},
        session=session,
    )

    result = manager.delegate_task(
        "agent-a",
        "Agent A",
        "agent-b",
        "inspect target",
        {"priority": 2},
        simulation_id="sim-1",
        idempotency_key="delegate-1",
    )

    assert result.ok
    _, _, payload, _, _ = session.calls[1]
    assert payload["message"]["metadata"]["operation"] == "agent.task.assign"
    assert payload["message"]["metadata"]["simulationId"] == "sim-1"
    assert payload["message"]["parts"][1]["data"] == {"input": {"priority": 2}}
    callback = payload["configuration"]["taskPushNotificationConfig"]
    assert callback["url"] == "http://agent-a:8000/a2a/v1/task-events"
    assert callback["token"]
    record = manager.task_manager.get_record(result.task_id)
    assert record["direction"] == "outbound"
    assert record["simulation_id"] == "sim-1"


def test_receive_task_assignment_is_durable_and_idempotent():
    manager = CommManager(
        agent_id="agent-b",
        comm_matrix={"agent-a": ["agent-b"]},
    )
    request = {
        "message": {
            "messageId": "message-task-1",
            "contextId": "context-task-1",
            "role": "ROLE_USER",
            "parts": [{"text": "inspect target", "mediaType": "text/plain"}],
            "metadata": {
                "operation": "agent.task.assign",
                "fromAgentId": "agent-a",
                "targetAgentId": "agent-b",
                "idempotencyKey": "delegate-1",
            },
        },
        "configuration": {
            "taskPushNotificationConfig": {
                "id": "callback-1",
                "url": "http://agent-a:8000/a2a/v1/task-events",
                "token": "secret",
            }
        },
    }

    first = manager.receive_message(request)["task"]
    second = manager.receive_message(request)["task"]

    assert first["id"] == second["id"]
    assert first["status"]["state"] == "TASK_STATE_SUBMITTED"
    record = manager.task_manager.claim_next("agent-b")
    assert record["goal"] == "inspect target"
    assert record["task"]["status"]["state"] == "TASK_STATE_WORKING"
