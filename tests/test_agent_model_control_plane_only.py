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


def test_agent_status_and_registry_remain_control_plane_features():
    AgentRegistry.reset()
    agent = Agent(agent_id="agent_a", role="planner", name="Agent A", skills=["planning"], tags=["demo"])
    AgentRegistry.register(agent)

    status = agent.get_status()

    assert status["agent_id"] == "agent_a"
    assert status["role"] == "planner"
    assert status["skills"] == ["planning"]
    assert AgentRegistry.get("agent_a") is agent
    assert AgentRegistry.find_agent(skill="planning") == [agent]

    AgentRegistry.reset()
    assert AgentRegistry.get("agent_a") is None
