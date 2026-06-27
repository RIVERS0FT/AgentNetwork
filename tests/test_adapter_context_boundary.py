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
        skills=[
            {
                "name": "planning",
                "description": "Plan tasks",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "sop_content": "Plan step by step",
                "tools": ["write_plan"],
            }
        ],
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
    assert payload["skills"][0]["name"] == "planning"
    assert payload["skills"][0]["sop_content"] == "Plan step by step"
    assert payload["allowed_tools"] == ["send_message", "write_plan"]


def test_openclaw_task_payload_contains_full_context_not_latest_message_only():
    payload = json.loads(openclaw._build_task_payload(_context()))

    assert payload["scene_key"] == "demo_scene"
    assert [m["content"] for m in payload["messages"]] == ["first", "second"]
    assert payload["skills"][0]["tools"] == ["write_plan"]


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
