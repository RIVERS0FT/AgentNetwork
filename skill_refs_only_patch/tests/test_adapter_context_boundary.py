import json
import os

from agent_network.adapters.base import AgentContext
from agent_network.adapters import claude_code, openclaw
from agent_network.adapters.claude_code import ClaudeCodeAdapter
from agent_network.adapters.openclaw import OpenCLAWAdapter


def _context() -> AgentContext:
    return AgentContext(
        trace_id="trace-test",
        agent_id="agent_a",
        agent_name="Agent A",
        role="planner",
        core_goal="Plan work",
        task="Complete the goal",
        messages=[
            {"from": "user", "content": "first"},
            {"from": "agent_b", "content": "second"},
        ],
        skill_refs=["planning"],
        allowed_tools=["send_message", "write_plan"],
        permissions={"can_send": ["agent_b"]},
        state_snapshot={"version": 1},
        tick=3,
        timeout_seconds=60,
        max_turns=5,
        scene_key="demo_scene",
    )


def test_adapters_do_not_keep_global_client_memory_cache():
    assert not hasattr(claude_code, "_clients")
    assert not hasattr(openclaw, "_clients")


def test_claude_task_payload_contains_full_context_not_latest_message_only():
    payload = json.loads(claude_code._build_task_payload(_context()))

    assert payload["scene_key"] == "demo_scene"
    assert payload["trace_id"] == "trace-test"
    assert payload["agent"]["agent_id"] == "agent_a"
    assert [m["content"] for m in payload["messages"]] == ["first", "second"]
    assert payload["skill_refs"] == ["planning"]
    assert payload["skill_context"] == []
    assert payload["allowed_tools"] == ["send_message", "write_plan"]

    server = claude_code._claude_mcp_server(_context())
    assert "--simulation-seed" in server["args"]


def test_openclaw_task_payload_contains_full_context_not_latest_message_only():
    payload = json.loads(openclaw._build_task_payload(_context()))

    assert payload["scene_key"] == "demo_scene"
    assert [m["content"] for m in payload["messages"]] == ["first", "second"]
    assert payload["skill_refs"] == ["planning"]
    assert payload["skill_context"] == []


def test_mock_claude_adapter_returns_application_event_without_real_llm(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")

    result = ClaudeCodeAdapter().run_agent_task(_context())

    assert result.status == "completed"
    assert result.final_message.startswith("[MOCK_LLM]")
    assert result.application_events
    assert result.application_events[0]["event"] == "agent_run_completed"
    assert result.application_events[0]["actor"]["backend"] == "claude-code"


def test_mock_openclaw_adapter_returns_application_event_without_real_llm(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")

    result = OpenCLAWAdapter().run_agent_task(_context())

    assert result.status == "completed"
    assert result.final_message.startswith("[MOCK_LLM]")
    assert result.application_events
    assert result.application_events[0]["event"] == "agent_run_completed"
    assert result.application_events[0]["actor"]["backend"] == "openclaw"


def test_claude_tool_blocks_become_traceable_application_events():
    context = _context()

    class ToolUseBlock:
        id = "tool-1"
        name = "mcp__agent_tools__write_plan"
        input = {"title": "Plan"}

    class ToolResultBlock:
        tool_use_id = "tool-1"
        content = {"status": "success"}
        is_error = False

    use_message = type("Message", (), {"content": [ToolUseBlock()]})()
    result_message = type("Message", (), {"content": [ToolResultBlock()]})()
    use_event = claude_code._tool_events_from_message(use_message, context)[0]
    result_event = claude_code._tool_events_from_message(result_message, context)[0]

    assert use_event["event"] == "tool_call_requested"
    assert use_event["trace_id"] == "trace-test"
    assert use_event["tool"]["tool_call_id"] == "tool-1"
    assert result_event["event"] == "tool_result_received"
    assert result_event["links"]["tool_call_id"] == "tool-1"


def test_claude_result_message_becomes_llm_runtime_event():
    class ResultMessage:
        duration_ms = 1250
        duration_api_ms = 1100
        num_turns = 2
        total_cost_usd = 0.02
        usage = {"input_tokens": 10, "output_tokens": 5}
        session_id = "sdk-session"
        subtype = "success"
        is_error = False

    event = claude_code._runtime_event_from_message(ResultMessage(), _context())[0]

    assert event["event"] == "llm_runtime_completed"
    assert event["trace_id"] == "trace-test"
    assert event["metrics"]["duration_ms"] == 1250
    assert event["metrics"]["usage"]["input_tokens"] == 10
