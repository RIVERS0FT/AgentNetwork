import json
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
        native_capabilities={
            "enabled": True,
            "tools": {
                "allow": ["fs.read", "fs.search", "agent.spawn"],
                "deny": [
                    "fs.write", "process.exec", "web.fetch", "web.search",
                    "browser.control", "agent.message", "automation.schedule",
                    "channel.send", "device.control", "media.generate",
                ],
            },
        },
        permissions={"can_send": ["agent_b"]},
        state_snapshot={"version": 1},
        simulation_id="sim-test",
        event_id="evt-test",
        event_sequence=3,
        event_type="agent_ready",
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
    assert payload["simulation_id"] == "sim-test"
    assert payload["event_id"] == "evt-test"
    assert payload["event_sequence"] == 3
    assert payload["event_type"] == "agent_ready"
    assert payload["agent"]["agent_id"] == "agent_a"
    assert [m["content"] for m in payload["messages"]] == ["first", "second"]
    assert payload["skill_refs"] == ["planning"]
    assert "skill_context" not in payload
    assert payload["allowed_tools"] == ["send_message", "write_plan"]
    assert payload["native_capabilities"]["enabled"] is True

    server = claude_code._claude_mcp_server(_context())
    assert server["args"][1] == "agent_network.mcp_server"
    assert "--skill-source-mode" in server["args"]
    assert "--skill-refs" in server["args"]
    assert "--simulation-seed" in server["args"]


def test_openclaw_task_payload_contains_full_context_not_latest_message_only():
    payload = json.loads(openclaw._build_task_payload(_context()))

    assert payload["scene_key"] == "demo_scene"
    assert [m["content"] for m in payload["messages"]] == ["first", "second"]
    assert payload["skill_refs"] == ["planning"]
    assert payload["native_capabilities"]["enabled"] is True
    assert "skill_context" not in payload


def test_mock_claude_adapter_returns_application_event_without_real_llm(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")

    result = ClaudeCodeAdapter().run_agent_task(_context())

    event = result.application_events[0]
    assert result.status == "completed"
    assert result.final_message.startswith("[MOCK_LLM]")
    assert event["event"] == "agent_run_completed"
    assert event["agent_id"] == "agent_a"
    assert event["metrics"]["backend"] == "claude-code"
    assert "actor" not in event


def test_claude_backend_never_falls_back_when_sdk_is_missing(monkeypatch):
    monkeypatch.delenv("MOCK_LLM", raising=False)
    monkeypatch.delenv("AGENT_STRICT_BACKEND_SDK", raising=False)
    monkeypatch.setattr(claude_code, "query", None)
    monkeypatch.setattr(claude_code, "ClaudeAgentOptions", None)
    result = ClaudeCodeAdapter().run_agent_task(_context())
    assert result.status == "error"
    assert result.error == "claude-agent-sdk is not installed."


def test_mock_openclaw_adapter_returns_application_event_without_real_llm(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")

    result = OpenCLAWAdapter().run_agent_task(_context())

    event = result.application_events[0]
    assert result.status == "completed"
    assert result.final_message.startswith("[MOCK_LLM]")
    assert event["event"] == "agent_run_completed"
    assert event["agent_id"] == "agent_a"
    assert event["metrics"]["backend"] == "openclaw"
    assert "actor" not in event


def test_openclaw_adapter_awaits_sdk_connect_factory(monkeypatch):
    monkeypatch.delenv("MOCK_LLM", raising=False)

    class FakeResult:
        content = "openclaw result"

    class FakeAgent:
        async def execute(self, prompt):
            assert "AgentNetwork task payload" in prompt
            return FakeResult()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get_agent(self, agent_id, session_name="main"):
            assert agent_id == "agent_a"
            assert session_name
            return FakeAgent()

    class FakeClientFactory:
        @classmethod
        async def connect(cls):
            return FakeClient()

    monkeypatch.setattr(openclaw, "OpenClawClient", FakeClientFactory)
    result = OpenCLAWAdapter().run_agent_task(_context())
    assert result.status == "completed"
    assert result.final_message == "openclaw result"


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
    assert use_event["agent_id"] == "agent_a"
    assert use_event["tool"]["tool_call_id"] == "tool-1"
    assert result_event["event"] == "tool_result_received"
    assert result_event["trace_id"] == "trace-test"
    assert result_event["agent_id"] == "agent_a"
    assert result_event["tool"]["tool_call_id"] == "tool-1"
    assert "links" not in use_event
    assert "links" not in result_event
    assert "actor" not in use_event
    assert "actor" not in result_event


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
    assert event["agent_id"] == "agent_a"
    assert event["metrics"]["duration_ms"] == 1250
    assert event["metrics"]["usage"]["input_tokens"] == 10
    assert "actor" not in event


def test_claude_native_policy_builds_tools_agents_and_hooks(monkeypatch):
    class FakeMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    monkeypatch.setattr(claude_code, "HookMatcher", FakeMatcher)
    monkeypatch.setattr(
        claude_code,
        "AgentDefinition",
        lambda **kwargs: kwargs,
    )

    allowed = claude_code._claude_allowed_tools(_context())
    denied = claude_code._claude_denied_tools(_context())
    agents = claude_code._claude_agents(_context())
    hooks = claude_code._claude_hooks(_context())

    assert {"Read", "Glob", "Grep", "Agent"} <= set(allowed)
    assert {"Write", "Edit", "Bash"} <= set(denied)
    assert "agentnetwork-worker" in agents
    assert "Agent" not in agents["agentnetwork-worker"]["tools"]
    assert {
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "SubagentStart",
        "SubagentStop",
    } <= set(hooks)
