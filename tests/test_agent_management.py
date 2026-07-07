from agent_network.agent_management import (
    Agent,
    AgentManagement,
    AgentRegistry,
    ContainerAgent,
    ContainerRuntime,
)


def _runtime(monkeypatch):
    monkeypatch.setattr(
        ContainerRuntime,
        "_init_docker",
        lambda self: setattr(self, "_docker_client", None),
    )
    return ContainerRuntime()


def test_management_creates_registers_and_unregisters_agent(monkeypatch):
    AgentRegistry.reset()
    management = AgentManagement(_runtime(monkeypatch))

    agent = management.create_agent(
        agent_id="planner",
        role="planner",
        name="Planner",
        skill_refs=["planning"],
    )

    assert AgentRegistry.get("planner") is agent
    assert management.get_stats()["total_agents"] == 1
    assert management.unregister_agent("planner") is True
    assert AgentRegistry.get("planner") is None


def test_runtime_status_updates_registered_agent(monkeypatch):
    AgentRegistry.reset()
    runtime = _runtime(monkeypatch)
    management = AgentManagement(runtime)
    agent = management.create_agent(agent_id="planner", name="Planner")
    assignment = ContainerAgent(
        agent_id="planner",
        name="Planner",
        role="generic",
        container_id="container-1",
        url="http://planner:8000",
    )

    runtime._set_status(assignment, "acting")

    assert agent.status == "acting"
    assert agent.container_id == "container-1"
    assert agent.container_url == "http://planner:8000"


def test_agent_status_and_registry_use_current_fields_only():
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
    assert "completed_tasks" not in status
    assert "skills" not in status
    assert "tags" not in status
    assert AgentRegistry.get("agent_a") is agent
    assert AgentRegistry.find_agent(skill_ref="planning") == [agent]

    AgentRegistry.reset()
