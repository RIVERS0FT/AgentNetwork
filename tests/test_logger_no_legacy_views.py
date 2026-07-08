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
        "actor": {"agent_id": "agent_A"},
        "target": {"agent_id": "agent_B"},
        "conversation": {},
        "action": {},
        "content": {"text": "Hello"},
        "trace": {"trace_id": "trace-message"},
    }
    behavior_record = {
        "event": "reasoning",
        "actor": {"agent_id": "agent_B"},
        "action": {},
        "decision": {"reasoning": "I decided to wait"},
        "trace": {"trace_id": "trace-reasoning"},
    }
    network_record = {
        "event": "docker_http_outbound",
        "actor": {},
        "network": {"direction": "outbound"},
        "trace": {"trace_id": "trace-network"},
    }

    assert is_agent_message_record(communication_record) is True
    assert is_behavior_record(behavior_record) is True

    temp_log_manager.emit(communication_record)
    temp_log_manager.emit(behavior_record)
    temp_log_manager.emit(network_record)
    temp_log_manager._close_file_handles()

    session_dir = temp_log_manager._session_dir
    assert os.path.exists(os.path.join(session_dir, "application.jsonl"))
    assert os.path.exists(os.path.join(session_dir, "network.jsonl"))
    assert os.path.exists(os.path.join(session_dir, "system.jsonl"))
    assert not os.path.exists(os.path.join(session_dir, "global.jsonl"))
    assert not os.path.exists(os.path.join(session_dir, "communication.jsonl"))
    assert not os.path.exists(os.path.join(session_dir, "behavior.jsonl"))
