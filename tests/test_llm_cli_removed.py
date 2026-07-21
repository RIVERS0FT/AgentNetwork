import pytest

from agent_network.log_management import llm_metrics
from agent_network.log_management.log_manager import (
    APPLICATION_EVENTS,
    LogManager,
    application_log_schema,
)


@pytest.mark.not_llm
def test_llm_cli_event_and_logger_are_removed(tmp_path):
    assert "llm_cli_call" not in APPLICATION_EVENTS
    assert "llm_cli_call" not in application_log_schema["event_schemas"]
    assert application_log_schema["schema_version"] == "application.v12"
    assert not hasattr(llm_metrics, "log_llm_cli")

    manager = LogManager(log_dir=str(tmp_path))
    manager.reset()
    manager._log_dir = str(tmp_path)

    with pytest.raises(ValueError, match="unknown application event"):
        manager.emit_application_event(
            event="llm_cli_call",
            agent_id="a1",
            action={"name": "CLI"},
        )

    manager.reset()


@pytest.mark.not_llm
def test_llm_api_logging_requires_current_explicit_flag(monkeypatch):
    monkeypatch.delenv("LOG_LLM_API", raising=False)
    monkeypatch.setenv("LOG_TRAFFIC", "1")
    assert llm_metrics.llm_api_enabled() is False

    monkeypatch.setenv("LOG_LLM_API", "1")
    assert llm_metrics.llm_api_enabled() is True


@pytest.mark.not_llm
def test_token_usage_ignores_non_api_llm_events():
    from agent_network.simulation_management import state

    state.reset_token_usage_state("test")
    assert state.append_token_usage_record({
        "event": "llm_runtime_completed",
        "trace_id": "trace-runtime-1",
        "agent_id": "agent-a",
        "payload": {"input_tokens": 10, "output_tokens": 5},
    }) is False
    assert state.get_token_usage_snapshot()["totals"]["events"] == 0


@pytest.mark.not_llm
def test_token_usage_records_top_level_agent_id():
    from agent_network.simulation_management import state

    state.reset_token_usage_state("test")
    record = {
        "timestamp": "2026-07-09T12:00:00.000",
        "event": "llm_api_call",
        "trace_id": "trace-api-1",
        "agent_id": "agent-a",
        "target": {"provider": "openai", "model": "demo"},
        "payload": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "estimated": False,
        },
    }

    assert state.append_token_usage_record(record) is True
    point = state.get_token_usage_snapshot()["last_event"]
    assert point["agent_id"] == "agent-a"
    assert "actor" not in point
