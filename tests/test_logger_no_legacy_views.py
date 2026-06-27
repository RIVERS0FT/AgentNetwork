import pytest
import os
import shutil
from agent_network.logger import SimulationLogger, is_agent_message_record, is_behavior_record

@pytest.fixture
def temp_logger(tmp_path):
    logger = SimulationLogger(log_dir=str(tmp_path))
    # Reset internal state to avoid bleeding between tests
    logger._file_path = ""
    logger._session_application_path = ""
    logger._session_network_path = ""
    logger._session_active = False
    logger._log_dir = str(tmp_path)
    
    yield logger
    
    logger._close_file_handles()
    if os.path.exists(str(tmp_path)):
        shutil.rmtree(str(tmp_path))

@pytest.mark.not_llm
def test_no_legacy_log_files_generated(temp_logger):
    # Start a session
    temp_logger.start_session("test_scene")
    
    # Assert session dir was created
    assert os.path.exists(temp_logger._session_dir)
    
    # Write some mock records that would previously go to communication and behavior
    comm_record = {
        "event": "agent_message",
        "category": "agent_application",
        "actor": {"id": "agent_A"},
        "payload": {"content": "Hello"}
    }
    
    behavior_record = {
        "event": "decide",
        "category": "agent_application",
        "actor": {"id": "agent_B"},
        "payload": {"reasoning": "I decided to wait"}
    }
    
    # Make sure filters work
    assert is_agent_message_record(comm_record) is True
    assert is_behavior_record(behavior_record) is True
    
    temp_logger.emit(comm_record)
    temp_logger.emit(behavior_record)
    
    # Close handles so files are flushed
    temp_logger._close_file_handles()
    
    # Verify new standard files exist
    assert os.path.exists(os.path.join(temp_logger._session_dir, "global.jsonl"))
    assert os.path.exists(os.path.join(temp_logger._session_dir, "application.jsonl"))
    
    # Verify legacy files DO NOT exist
    assert not os.path.exists(os.path.join(temp_logger._session_dir, "communication.jsonl"))
    assert not os.path.exists(os.path.join(temp_logger._session_dir, "behavior.jsonl"))
