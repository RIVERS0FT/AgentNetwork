import importlib
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _load_system_module(monkeypatch):
    # Ensure module-level ENABLE_DEBUG_TOOL_EXECUTE checks see the current env.
    sys.modules.pop("agent_network.api.system", None)
    import agent_network.api.system as system
    return importlib.reload(system)


def test_direct_tool_execute_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_DEBUG_TOOL_EXECUTE", raising=False)
    system = _load_system_module(monkeypatch)

    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    client = TestClient(app)

    response = client.post("/api/tools/execute", json={"tool_name": "demo", "params": {}})

    assert response.status_code == 403
    assert "backend-native MCP tool calling" in response.json()["detail"]


def test_direct_tool_execute_can_be_enabled_for_debug(monkeypatch):
    monkeypatch.setenv("ENABLE_DEBUG_TOOL_EXECUTE", "1")
    system = _load_system_module(monkeypatch)

    class FakeToolRegistry:
        @staticmethod
        def list_tools():
            return ["demo"]

        @staticmethod
        def execute(tool_name, **kwargs):
            return {"tool_name": tool_name, "kwargs": kwargs}

    system.state.active_tools_module = types.SimpleNamespace(ToolRegistry=FakeToolRegistry)

    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    client = TestClient(app)

    response = client.post("/api/tools/execute", json={"tool_name": "demo", "params": {"x": 1}})

    assert response.status_code == 200
    assert response.json() == {"tool": "demo", "result": {"tool_name": "demo", "kwargs": {"x": 1}}}


def test_tools_list_remains_available(monkeypatch):
    system = _load_system_module(monkeypatch)

    class FakeToolRegistry:
        @staticmethod
        def list_tools():
            return ["demo"]

    system.state.active_tools_module = types.SimpleNamespace(ToolRegistry=FakeToolRegistry)

    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    client = TestClient(app)

    response = client.get("/api/tools")

    assert response.status_code == 200
    assert response.json() == {"tools": ["demo"]}
