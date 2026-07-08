import pytest

from agent_network import llm_metrics
from agent_network.log_manager import (
    APPLICATION_EVENTS,
    LogManager,
    application_log_schema,
)


@pytest.mark.not_llm
def test_llm_cli_event_and_logger_are_removed(tmp_path):
    assert "llm_cli_call" not in APPLICATION_EVENTS
    assert "llm_cli_call" not in application_log_schema["event_schemas"]
    assert application_log_schema["schema_version"] == "application.v8"
    assert not hasattr(llm_metrics, "log_llm_cli")

    manager = LogManager(log_dir=str(tmp_path))
    manager.reset()
    manager._log_dir = str(tmp_path)

    with pytest.raises(ValueError, match="unknown application event"):
        manager.emit_application_event(
            event="llm_cli_call",
            actor={"agent_id": "a1"},
            action={"name": "CLI"},
        )

    manager.reset()


@pytest.mark.not_llm
def test_token_usage_ignores_non_api_llm_events():
    from agent_network import state

    state.reset_token_usage_state("test")
    assert state.append_token_usage_record({
        "event": "llm_runtime_completed",
        "event_id": "runtime_1",
        "payload": {"input_tokens": 10, "output_tokens": 5},
    }) is False
    assert state.get_token_usage_snapshot()["totals"]["events"] == 0
