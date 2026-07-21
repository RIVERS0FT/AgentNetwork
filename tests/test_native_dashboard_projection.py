from agent_network.agent_management import Agent, AgentRegistry
from agent_network.api.logs import _project_native_subagent


def test_control_plane_projects_native_child_from_application_event():
    AgentRegistry.reset()
    parent = Agent(agent_id="parent", backend="openclaw")
    parent.container_id = "container-parent"
    AgentRegistry.register(parent)

    _project_native_subagent(
        {
            "event": "subagent_lifecycle",
            "agent_id": "parent",
            "target": {"agent_id": "child", "role": "worker"},
            "action": {"status": "running"},
            "result": {"status": "running"},
            "payload": {
                "backend": "openclaw",
                "child_session_id": "child-session",
            },
        }
    )

    child = AgentRegistry.get("child")
    assert child is not None
    assert child.parent_agent_id == "parent"
    assert child.container_id == "container-parent"
    assert child.runtime_session_id == "child-session"

    _project_native_subagent(
        {
            "event": "subagent_lifecycle",
            "agent_id": "parent",
            "target": {"agent_id": "child"},
            "action": {"status": "reset"},
            "result": {"status": "reset"},
            "payload": {"backend": "openclaw"},
        }
    )
    assert AgentRegistry.get("child") is None
    AgentRegistry.reset()
