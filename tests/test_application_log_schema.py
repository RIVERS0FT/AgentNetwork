import pytest
from agent_network.logger import is_agent_message_record, is_behavior_record, SimulationLogger

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

@pytest.mark.not_llm
def test_emit_application_event_schema(temp_logger):
    record = temp_logger.emit_application_event(
        event="test_event",
        actor={"agent_id": "test_agent"}
    )
    
    assert record["event"] == "test_event"
    assert record["actor"]["agent_id"] == "test_agent"
    assert record["layer"] == "agent_application"
    assert record["category"] == "agent_application"
    
    # Check default objects are present
    assert isinstance(record["target"], dict)
    assert isinstance(record["task"], dict)
    assert isinstance(record["action"], dict)
    assert isinstance(record["content"], dict)
    
    # Check auto-generated IDs
    assert "event_id" in record
    assert "trace_id" in record["trace"]
    
    # Should not have raw top-level trace_id to avoid duplication
    assert "trace_id" not in record
    
    assert "timestamp" in record

@pytest.mark.not_llm
def test_agent_message_strips_network_fields(temp_logger):
    temp_logger.agent_message(
        from_id="a1",
        to="a2",
        content="hello",
        latency_ms=12.5,
        src_ip="192.168.1.1",
        tcp_flags="SYN",
        payload_len=100
    )
    
    assert len(temp_logger._entries) > 0
    record = temp_logger._entries[-1]
    
    # Verify mapping
    assert record["action"]["duration_ms"] == 12.5
    assert record["content"]["size_bytes"] == 100
    
    # Verify no network fields
    assert "network" not in record
    assert "src_ip" not in record
    assert "tcp_flags" not in record
    
    assert record["debug"].get("legacy_network_fields_dropped") is True

@pytest.mark.not_llm
def test_agent_action_compatibility(temp_logger):
    temp_logger.agent_action("a1", "move", {"status": "ok"}, extra="data")
    record = temp_logger._entries[-1]
    
    assert record["event"] == "act"
    assert record["actor"]["agent_id"] == "a1"
    assert record["action"]["name"] == "move"
    assert record["content"]["kw"] == {"extra": "data"}

@pytest.mark.not_llm
def test_agent_decide_compatibility(temp_logger):
    temp_logger.agent_decide("a1", "prompt", {"choice": "A"})
    record = temp_logger._entries[-1]
    
    assert record["event"] == "decide"
    assert record["actor"]["agent_id"] == "a1"
    assert record["decision"]["raw_model_output_ref"] == "prompt"
