import pytest

from agent_network.agent_model import Agent, AgentRegistry, Message


def test_agent_execute_task_is_removed_from_control_plane_model():
    agent = Agent(agent_id="agent_a", role="planner", name="Agent A")
    message = Message(source="user", target="agent_a", payload={"action": "do work"})

    with pytest.raises(RuntimeError) as exc:
        agent.execute_task(message)

    assert "BackendAdapter and /run" in str(exc.value)


def test_agent_call_tool_is_removed_from_control_plane_model():
    agent = Agent(agent_id="agent_a")

    with pytest.raises(RuntimeError) as exc:
        agent.call_tool("some_tool")

    assert "backend-native MCP tool calling" in str(exc.value)


def test_agent_status_and_registry_use_skill_refs_only():
    AgentRegistry.reset()
    agent = Agent(
        agent_id="agent_a",
        role="planner",
        name="Agent A",
        core_goal="Plan work",
        backend="openclaw",
        skill_refs=["planning"],
        allowed_tools=["write_plan"],
    )
    AgentRegistry.register(agent)

    status = agent.get_status()

    assert status["agent_id"] == "agent_a"
    assert status["role"] == "planner"
    assert status["core_goal"] == "Plan work"
    assert status["backend"] == "openclaw"
    assert status["skill_refs"] == ["planning"]
    assert status["allowed_tools"] == ["write_plan"]
    assert "extra_meta" not in status
    assert not hasattr(agent, "extra_meta")
    assert "skills" not in status
    assert "tags" not in status
    assert AgentRegistry.get("agent_a") is agent
    assert AgentRegistry.find_agent(skill_ref="planning") == [agent]

    AgentRegistry.reset()
    assert AgentRegistry.get("agent_a") is None
