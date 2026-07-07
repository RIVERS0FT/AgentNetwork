import importlib


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func
        return decorator

    def add_tool(self, func):
        self.tools[func.__name__] = func
        return func


def test_markdown_skill_is_not_wrapped_as_dynamic_tool():
    import agent_network.mcp_server as mcp_server

    assert not hasattr(mcp_server, "_create_tool_function")


def test_atomic_mcp_tools_exclude_wait(monkeypatch):
    import agent_network.mcp_server as mcp_server

    fake_mcp = FakeMCP()
    monkeypatch.setattr(mcp_server, "mcp", fake_mcp)

    mcp_server._register_atomic_tools()

    assert set(fake_mcp.tools.keys()) == {"send_message", "broadcast"}
    assert "wait" not in fake_mcp.tools
    assert mcp_server.ATOMIC_TOOL_NAMES == {"send_message", "broadcast"}


def test_scene_tool_registration_exposes_toolregistry_entries_as_atomic_tools(monkeypatch):
    import agent_network.mcp_server as mcp_server

    fake_mcp = FakeMCP()

    class FakeToolRegistry:
        @staticmethod
        def list_tools():
            return ["write_plan"]

        @staticmethod
        def execute(name, **kwargs):
            return {"name": name, "kwargs": kwargs}

    monkeypatch.setattr(mcp_server, "mcp", fake_mcp)
    monkeypatch.setattr(mcp_server, "_TOOL_REGISTRY", FakeToolRegistry)
    monkeypatch.setattr(mcp_server, "_ALLOWED_TOOLS", {"write_plan"})
    monkeypatch.setattr(mcp_server, "_log_agent", lambda *args, **kwargs: None)

    mcp_server._register_scene_tools()

    assert set(fake_mcp.tools.keys()) == {"write_plan"}
    assert "wait" not in fake_mcp.tools
    result = fake_mcp.tools["write_plan"]('{"x": 1}')
    assert '"status": "success"' in result
    assert '"tool": "write_plan"' in result


def test_mcp_server_does_not_cache_or_parse_skill_content(tmp_path):
    import agent_network.mcp_server as mcp_server

    mcp_server.setup_runtime(
        scene_key="demo",
        agent_id="agent_a",
        agent_name="Agent A",
        allowed_tools=[],
        scenes_root=str(tmp_path),
    )

    assert not hasattr(mcp_server, "_SKILLS_CACHE")
    assert not hasattr(mcp_server, "_ALLOWED_SKILLS")
