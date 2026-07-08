import json
import os

import pytest

from agent_network.log_manager import (
    APPLICATION_EVENTS,
    LogManager,
    application_log_schema,
    network_log_schema,
    system_log_schema,
)


REMOVED_FIELDS = {
    "seq",
    "session_id",
    "tick",
    "layer",
    "message",
    "category",
    "component",
    "policy",
    "decision",
}
SYSTEM_ONLY_FIELDS = {"level", "source", "debug"}
EVENT_IDENTITY_FIELDS = {
    "event",
    "event_id",
    "parent_event_id",
    "actor",
    "trace",
}
UNKNOWN_APPLICATION_EVENTS = {
    "decide",
    "agent_decide",
    "act",
    "agent_action",
    "llm_cli_call",
    "custom_application_event",
}


@pytest.fixture
def manager(tmp_path):
    instance = LogManager(log_dir=str(tmp_path))
    instance.reset()
    instance._log_dir = str(tmp_path)
    os.makedirs(instance._log_dir, exist_ok=True)
    yield instance
    instance.reset()


@pytest.mark.not_llm
def test_each_schema_owns_timestamp_field():
    schemas = (
        application_log_schema,
        network_log_schema,
        system_log_schema,
    )
    for schema in schemas:
        assert "common_fields" not in schema
        assert schema["type_fields"]["timestamp"] == {
            "type": "string",
            "required": True,
        }

    assert application_log_schema["type_fields"] is not network_log_schema["type_fields"]
    assert application_log_schema["type_fields"] is not system_log_schema["type_fields"]
    assert network_log_schema["type_fields"] is not system_log_schema["type_fields"]


@pytest.mark.not_llm
def test_application_schema_is_strict_and_uses_one_event_source():
    schemas = application_log_schema["event_schemas"]

    assert application_log_schema["schema_version"] == "application.v9"
    assert "*" not in schemas
    assert set(schemas) == set(APPLICATION_EVENTS)
    assert "reasoning" in schemas
    assert "acting" in schemas
    assert not (UNKNOWN_APPLICATION_EVENTS & set(schemas))
    assert schemas["reasoning"]["required_fields"] == ["action"]
    assert schemas["policy_check"]["required_fields"] == ["result"]
    assert schemas["acting"]["required_fields"] == ["action"]

    for event_schema in schemas.values():
        assert "policy" not in event_schema["fields"]
        assert "decision" not in event_schema["fields"]


@pytest.mark.not_llm
def test_application_schema_field_boundary(manager):
    record = manager.emit_application_event(
        event="acting",
        actor={"agent_id": "test_agent"},
        action={"name": "test_action"},
    )

    assert record["event"] == "acting"
    assert record["actor"]["agent_id"] == "test_agent"
    assert EVENT_IDENTITY_FIELDS <= set(record)
    assert not (REMOVED_FIELDS & set(record))
    assert not (SYSTEM_ONLY_FIELDS & set(record))
    assert "trace_id" in record["trace"]
    assert "trace_id" not in record
    assert "timestamp" in record


@pytest.mark.not_llm
def test_network_schema_field_boundary(manager):
    record = manager.emit_network_event(
        event="docker_http_outbound",
        actor={"agent_id": "agent_a"},
        network={"direction": "outbound"},
    )

    assert EVENT_IDENTITY_FIELDS <= set(record)
    assert record["network"]["direction"] == "outbound"
    assert not (REMOVED_FIELDS & set(record))
    assert not (SYSTEM_ONLY_FIELDS & set(record))


@pytest.mark.not_llm
def test_system_schema_uses_final_source_and_kind(manager):
    record = manager.emit_system_event(
        event="debug_snapshot",
        message="snapshot ready",
        kind="debug",
        source="backend.srv",
        debug={"request_id": "r1"},
    )

    assert record["source"] == "backend.srv"
    assert record["level"] == "INFO"
    assert record["debug"]["event"] == "debug_snapshot"
    assert record["debug"]["kind"] == "debug"
    assert record["payload"]["message"] == "snapshot ready"
    assert not (EVENT_IDENTITY_FIELDS & set(record))
    assert not (REMOVED_FIELDS & set(record))


@pytest.mark.not_llm
def test_persisted_jsonl_has_no_removed_fields(manager):
    session_id = manager.start_session("schema_test")
    manager.emit_application_event(
        event="acting",
        actor={"agent_id": "a1"},
        action={"name": "move"},
    )
    manager.emit_network_event(
        event="docker_http_outbound",
        network={"direction": "outbound"},
    )
    manager._close_file_handles()

    for filename in ("application.jsonl", "network.jsonl", "system.jsonl"):
        path = os.path.join(manager._log_dir, session_id, filename)
        with open(path, "r", encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                assert not (REMOVED_FIELDS & set(record))
                assert "timestamp" in record


@pytest.mark.not_llm
def test_agent_message_uses_application_fields_only(manager):
    record = manager.agent_message(
        from_id="a1",
        to="a2",
        content="hello",
        latency_ms=12.5,
        payload_len=100,
    )

    assert record["action"]["duration_ms"] == 12.5
    assert record["content"]["size_bytes"] == 100
    assert "network" not in record
    assert "policy" not in record
    assert "decision" not in record
    assert not (SYSTEM_ONLY_FIELDS & set(record))


@pytest.mark.not_llm
def test_reasoning_and_acting_helpers(manager):
    action = manager.acting("a1", "move", {"status": "ok"}, extra="data")
    reasoning = manager.reasoning("a1", "prompt", {"choice": "A"})

    assert action["event"] == "acting"
    assert action["content"]["kw"] == {"extra": "data"}
    assert reasoning["event"] == "reasoning"
    assert reasoning["content"]["text"] == "prompt"
    assert reasoning["result"] == {"choice": "A"}
    assert "decision" not in reasoning
    assert not hasattr(manager, "agent_action")
    assert not hasattr(manager, "agent_decide")


@pytest.mark.not_llm
def test_policy_check_uses_result_field(manager):
    record = manager.emit_application_event(
        event="policy_check",
        actor={"agent_id": "a1"},
        action={"name": "communication_check"},
        result={"status": "allowed", "rule": "communication_matrix"},
    )

    assert record["event"] == "policy_check"
    assert record["result"]["status"] == "allowed"
    assert "policy" not in record


@pytest.mark.not_llm
@pytest.mark.parametrize("event", sorted(UNKNOWN_APPLICATION_EVENTS))
def test_unknown_application_events_are_rejected(manager, event):
    with pytest.raises(ValueError, match="unknown application event"):
        manager.emit_application_event(
            event=event,
            actor={"agent_id": "a1"},
            action={"name": event},
        )
