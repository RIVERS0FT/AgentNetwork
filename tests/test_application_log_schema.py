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
    "links",
    "trace",
    "actor",
    "event_id",
    "parent_event_id",
}
APPLICATION_IDENTITY_FIELDS = {
    "timestamp",
    "event",
    "trace_id",
    "agent_id",
}
SYSTEM_ONLY_FIELDS = {"level", "source", "debug"}
NETWORK_TOP_LEVEL_FIELDS = {
    "timestamp",
    "log_id",
    "context",
    "network",
    "raw",
}
UNKNOWN_APPLICATION_EVENTS = {
    "decide",
    "agent_decide",
    "act",
    "agent_action",
    "llm_cli_call",
    "custom_application_event",
}
NETWORK_CONTEXT = {
    "trace_id": "trace-network",
    "capture_id": "capture-dev-fe-001",
    "packet_index": 42,
    "observer_agent_id": "dev_fe",
    "runtime_container": "agent-dev-fe",
    "interface": "any",
    "captured_length": 128,
    "original_length": 128,
    "truncated": False,
}
NETWORK_LAYERS = {
    "ip": {
        "ip.version": "4",
        "ip.hdr_len": "20",
        "ip.len": "128",
        "ip.ttl": "64",
        "ip.proto": "6",
        "ip.src": "172.18.0.2",
        "ip.dst": "172.18.0.3",
    },
    "tcp": {
        "tcp.srcport": "49152",
        "tcp.dstport": "8000",
        "tcp.seq": "10001",
        "tcp.ack": "20001",
        "tcp.flags": "0x0018",
    },
}
NETWORK_RAW = {
    "format": "pcap",
    "encoding": "base64",
    "data": "1MOyoQIABAAAAAAAAAAAAP//AAABAAAA...",
    "byte_length": 168,
    "packet_count": 1,
    "sha256": "9d41c7...",
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

    assert application_log_schema["schema_version"] == "application.v11"
    assert set(application_log_schema["type_fields"]) == APPLICATION_IDENTITY_FIELDS
    assert application_log_schema["type_fields"]["trace_id"]["generator"] == "trace_id"
    assert application_log_schema["type_fields"]["agent_id"] == {
        "type": "string",
        "required": True,
    }
    assert "actor" not in application_log_schema["type_fields"]
    assert "actor" not in application_log_schema.get("field_library", {})
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
        assert "links" not in event_schema["fields"]
        assert "actor" not in event_schema["fields"]


@pytest.mark.not_llm
def test_application_schema_field_boundary(manager):
    record = manager.emit_application_event(
        event="acting",
        agent_id="test_agent",
        action={"name": "test_action"},
        trace_id="trace-test",
    )

    assert record["event"] == "acting"
    assert record["agent_id"] == "test_agent"
    assert record["trace_id"] == "trace-test"
    assert APPLICATION_IDENTITY_FIELDS <= set(record)
    assert not (REMOVED_FIELDS & set(record))
    assert not (SYSTEM_ONLY_FIELDS & set(record))


@pytest.mark.not_llm
def test_application_agent_id_is_required(manager):
    with pytest.raises(TypeError):
        manager.emit_application_event(
            event="acting",
            action={"name": "test_action"},
        )

    with pytest.raises(ValueError, match="agent_id"):
        manager.ingest(
            {
                "event": "acting",
                "trace_id": "trace-test",
                "action": {"name": "test_action"},
            },
            log_type="application",
        )


@pytest.mark.not_llm
def test_application_trace_id_is_generated(manager):
    record = manager.emit_application_event(
        event="acting",
        agent_id="test_agent",
        action={"name": "test_action"},
    )

    assert record["trace_id"].startswith("trace_")
    assert "trace" not in record
    assert "actor" not in record


@pytest.mark.not_llm
def test_network_schema_field_boundary(manager):
    assert network_log_schema["schema_version"] == "network.v4"
    assert set(network_log_schema["type_fields"]) == NETWORK_TOP_LEVEL_FIELDS

    record = manager.emit_network_event(
        context=NETWORK_CONTEXT,
        network=NETWORK_LAYERS,
        raw=NETWORK_RAW,
        timestamp="2026-07-08T12:30:15.123456Z",
        log_id="net_01JZ123456",
    )

    assert set(record) == NETWORK_TOP_LEVEL_FIELDS
    assert record["timestamp"] == "2026-07-08T12:30:15.123456Z"
    assert record["log_id"] == "net_01JZ123456"
    assert record["context"] == NETWORK_CONTEXT
    assert record["network"] == NETWORK_LAYERS
    assert record["raw"] == NETWORK_RAW
    assert not ({"event", "actor", "agent_id", "trace_id"} & set(record))
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
    assert not ({"event_id", "parent_event_id", "actor", "agent_id", "trace", "trace_id"} & set(record))
    assert not (REMOVED_FIELDS & set(record))


@pytest.mark.not_llm
def test_persisted_jsonl_has_no_removed_application_fields(manager):
    session_id = manager.start_session("schema_test")
    manager.emit_application_event(
        event="acting",
        agent_id="a1",
        action={"name": "move"},
        trace_id="trace-application",
    )
    manager.emit_network_event(
        context=NETWORK_CONTEXT,
        network=NETWORK_LAYERS,
        raw=NETWORK_RAW,
    )
    manager._close_file_handles()

    application_path = os.path.join(manager._log_dir, session_id, "application.jsonl")
    with open(application_path, "r", encoding="utf-8") as stream:
        record = json.loads(next(stream))
    assert record["trace_id"] == "trace-application"
    assert record["agent_id"] == "a1"
    assert not (REMOVED_FIELDS & set(record))

    network_path = os.path.join(manager._log_dir, session_id, "network.jsonl")
    with open(network_path, "r", encoding="utf-8") as stream:
        network_record = json.loads(next(stream))
    assert set(network_record) == NETWORK_TOP_LEVEL_FIELDS


@pytest.mark.not_llm
def test_agent_message_uses_application_fields_only(manager):
    record = manager.agent_message(
        from_id="a1",
        to="a2",
        content="hello",
        latency_ms=12.5,
        payload_len=100,
        talk="trace-message",
    )

    assert record["agent_id"] == "a1"
    assert record["action"]["duration_ms"] == 12.5
    assert record["content"]["size_bytes"] == 100
    assert record["trace_id"] == "trace-message"
    assert "network" not in record
    assert not (REMOVED_FIELDS & set(record))
    assert not (SYSTEM_ONLY_FIELDS & set(record))


@pytest.mark.not_llm
def test_reasoning_and_acting_helpers(manager):
    action = manager.acting("a1", "move", {"status": "ok"}, extra="data")
    reasoning = manager.reasoning("a1", "prompt", {"choice": "A"})

    assert action["event"] == "acting"
    assert action["agent_id"] == "a1"
    assert action["content"]["kw"] == {"extra": "data"}
    assert reasoning["event"] == "reasoning"
    assert reasoning["agent_id"] == "a1"
    assert reasoning["content"]["text"] == "prompt"
    assert reasoning["result"] == {"choice": "A"}
    assert "decision" not in reasoning
    assert "actor" not in reasoning
    assert not hasattr(manager, "agent_action")
    assert not hasattr(manager, "agent_decide")


@pytest.mark.not_llm
def test_policy_check_uses_result_field(manager):
    record = manager.emit_application_event(
        event="policy_check",
        agent_id="a1",
        action={"name": "communication_check"},
        result={"status": "allowed", "rule": "communication_matrix"},
    )

    assert record["event"] == "policy_check"
    assert record["agent_id"] == "a1"
    assert record["result"]["status"] == "allowed"
    assert "policy" not in record
    assert "actor" not in record


@pytest.mark.not_llm
@pytest.mark.parametrize("event", sorted(UNKNOWN_APPLICATION_EVENTS))
def test_unknown_application_events_are_rejected(manager, event):
    with pytest.raises(ValueError, match="unknown application event"):
        manager.emit_application_event(
            event=event,
            agent_id="a1",
            action={"name": event},
        )
