import json
import os
import shutil

import pytest

from agent_network.log_manager import (
    LogManager,
    is_agent_message_record,
    is_behavior_record,
)


@pytest.fixture
def temp_log_manager(tmp_path):
    manager = LogManager(log_dir=str(tmp_path))
    manager.reset()
    manager._log_dir = str(tmp_path)

    yield manager

    manager._close_file_handles()
    if os.path.exists(str(tmp_path)):
        shutil.rmtree(str(tmp_path))


@pytest.mark.not_llm
def test_only_current_log_files_are_generated(temp_log_manager):
    temp_log_manager.start_session("test_scene")

    communication_record = {
        "event": "agent_message",
        "trace_id": "trace-message",
        "agent_id": "agent_A",
        "target": {"agent_id": "agent_B"},
        "conversation": {},
        "action": {},
        "content": {"text": "Hello"},
    }
    behavior_record = {
        "event": "reasoning",
        "trace_id": "trace-reasoning",
        "agent_id": "agent_B",
        "action": {},
        "content": {"text": "I decided to wait"},
    }
    network_record = {
        "timestamp": "2026-07-08T12:30:15.123456Z",
        "log_id": "net_01JZ123456",
        "context": {
            "trace_id": "trace-network",
            "capture_id": "capture-agent-a-001",
            "packet_index": 1,
            "observer_agent_id": "agent_A",
            "runtime_container": "agent-a",
            "interface": "any",
            "captured_length": 64,
            "original_length": 64,
            "truncated": False,
        },
        "network": {
            "ip": {"ip.version": "4", "ip.proto": "6"},
            "tcp": {"tcp.srcport": "49152", "tcp.dstport": "8000"},
        },
        "raw": {
            "format": "pcap",
            "encoding": "base64",
            "data": "AAAA",
            "byte_length": 3,
            "packet_count": 1,
            "sha256": "test",
        },
    }

    assert is_agent_message_record(communication_record) is True
    assert is_behavior_record(behavior_record) is True

    application_records = [
        temp_log_manager.emit(communication_record),
        temp_log_manager.emit(behavior_record),
    ]
    temp_log_manager.emit(network_record)
    temp_log_manager._close_file_handles()

    assert all("agent_id" in record for record in application_records)
    assert all("actor" not in record for record in application_records)

    session_dir = temp_log_manager._session_dir
    assert os.path.exists(os.path.join(session_dir, "application.jsonl"))
    assert os.path.exists(os.path.join(session_dir, "network.jsonl"))
    assert os.path.exists(os.path.join(session_dir, "system.jsonl"))
    assert not os.path.exists(os.path.join(session_dir, "global.jsonl"))
    assert not os.path.exists(os.path.join(session_dir, "communication.jsonl"))
    assert not os.path.exists(os.path.join(session_dir, "behavior.jsonl"))

    with open(os.path.join(session_dir, "application.jsonl"), "r", encoding="utf-8") as stream:
        persisted = [json.loads(line) for line in stream]
    assert {record["agent_id"] for record in persisted} == {"agent_A", "agent_B"}
    assert all("actor" not in record for record in persisted)
