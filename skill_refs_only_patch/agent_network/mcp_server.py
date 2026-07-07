import os
import argparse
import json
import time
import random
import asyncio
import importlib.util
from pathlib import Path
import requests

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from agent_network.comm import DirectBus

mcp = FastMCP("agent-network-mcp")

_SCENE_KEY = ""
_AGENT_ID = ""
_AGENT_NAME = ""
_ALLOWED_TOOLS = set()
_SCENES_ROOT = Path("/app/scenes")
_TOOL_REGISTRY = None
_SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000")
_AGENT_DIRECTORY = {}
_COMM_MATRIX = {}
_COMM = DirectBus()
_TRACE_ID = ""

ATOMIC_TOOL_NAMES = {"send_message", "broadcast"}


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="milliseconds")


def _safe_post_json(url: str, json_data: dict, timeout: float = 3) -> bool:
    try:
        requests.post(url, json=json_data, timeout=timeout)
        return True
    except Exception:
        return False


def _log_agent(event: str, detail: str, **kw):
    effective_id = kw.pop("from_id", _AGENT_ID)
    effective_name = kw.pop("from_name", _AGENT_NAME)
    action_type = kw.get("action_type", event)
    target = kw.get("target", kw.get("to", ""))
    _safe_post_json(f"{_SERVER_URL}/api/logs/agent", {
        "agent_id": effective_id,
        "agent_name": effective_name,
        "event": event,
        "detail": detail,
        "timestamp": _now_iso(),
        "from_agent": effective_id,
        "to_agent": target if action_type in ("send_message", "broadcast") else "",
        "action": target if action_type == "skill" else action_type,
        "action_status": kw.get("status", "success"),
        "trace_id": _TRACE_ID,
        "details": {k: v for k, v in kw.items() if k != "action_type"},
    }, timeout=2)


def setup_runtime(
    scene_key: str,
    agent_id: str,
    agent_name: str,
    allowed_tools: list,
    scenes_root: str,
    agent_directory: dict = None,
    comm_matrix: dict = None,
    trace_id: str = "",
    simulation_seed: int = 0,
):
    global _SCENE_KEY, _AGENT_ID, _AGENT_NAME, _ALLOWED_TOOLS
    global _SCENES_ROOT, _TOOL_REGISTRY, _AGENT_DIRECTORY, _COMM_MATRIX, _COMM, _TRACE_ID

    _SCENE_KEY = scene_key
    _AGENT_ID = agent_id.lower()
    _AGENT_NAME = agent_name
    _ALLOWED_TOOLS = set(allowed_tools or [])
    _SCENES_ROOT = Path(scenes_root)
    _TOOL_REGISTRY = None
    _AGENT_DIRECTORY = {str(k).lower(): v for k, v in (agent_directory or {}).items() if v}
    _COMM_MATRIX = {
        str(k).lower(): [str(item).lower() for item in (v or [])]
        for k, v in (comm_matrix or {}).items()
    }
    _COMM = DirectBus(agent_directory=_AGENT_DIRECTORY, comm_matrix=_COMM_MATRIX)
    _TRACE_ID = trace_id
    random.seed(f"{simulation_seed}:{_AGENT_ID}")



def _tool_allowed(tool_name: str) -> bool:
    return not _ALLOWED_TOOLS or tool_name in _ALLOWED_TOOLS or tool_name in ATOMIC_TOOL_NAMES


def _register_atomic_tools():
    @mcp.tool()
    def send_message(
        target: str = Field(description="Target agent_id"),
        content: str = Field(description="Message content")
    ) -> str:
        start_time = time.time()
        _log_agent("tool_call", "Tool call start: send_message", tool_name="send_message", arguments={"target": target, "content": content}, status="running")
        ok = asyncio.run(asyncio.to_thread(_COMM.send, _AGENT_ID, _AGENT_NAME, target, content, "", _TRACE_ID))
        status = "success" if ok else "failed"
        _log_agent(
            "tool_result",
            f"send_message -> {target}",
            action_type="tool_result",
            tool_name="send_message",
            target=target,
            content=content,
            status=status,
            duration_ms=round((time.time() - start_time) * 1000, 1),
        )
        return json.dumps({"status": status, "target": target, "mode": "direct"}, ensure_ascii=False)

    @mcp.tool()
    def broadcast(content: str = Field(description="Message content to broadcast")) -> str:
        start_time = time.time()
        _log_agent("tool_call", "Tool call start: broadcast", tool_name="broadcast", arguments={"content": content}, status="running")
        ok = asyncio.run(asyncio.to_thread(_COMM.broadcast, _AGENT_ID, _AGENT_NAME, content, set(), "", _TRACE_ID))
        status = "success" if ok else "failed"
        _log_agent(
            "tool_result",
            "broadcast",
            action_type="tool_result",
            tool_name="broadcast",
            target="broadcast",
            content=content,
            status=status,
            duration_ms=round((time.time() - start_time) * 1000, 1),
        )
        return json.dumps({"status": status, "target": "broadcast", "mode": "direct"}, ensure_ascii=False)


def _load_tool_registry():
    global _TOOL_REGISTRY
    tools_path = _SCENES_ROOT / _SCENE_KEY / "tools.py"
    if not tools_path.exists():
        return
    try:
        spec = importlib.util.spec_from_file_location(f"tools_{_SCENE_KEY}_{_AGENT_ID}", tools_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "ToolRegistry"):
            _TOOL_REGISTRY = mod.ToolRegistry
    except Exception as e:
        _TOOL_REGISTRY = None
        _log_agent("tool_registry_load_failed", f"Failed to load tools.py: {e}", status="failed", error=str(e))


def _list_scene_tools() -> list[str]:
    if not _TOOL_REGISTRY:
        return []
    try:
        raw_tools = _TOOL_REGISTRY.list_tools()
    except Exception as e:
        _log_agent("tool_registry_list_failed", f"ToolRegistry.list_tools failed: {e}", status="failed", error=str(e))
        return []
    names = []
    for item in raw_tools or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name") or item.get("tool_name") or ""
        else:
            name = getattr(item, "name", "")
        if name:
            names.append(name)
    return names


def _register_scene_tools():
    for tool_name in _list_scene_tools():
        if not _tool_allowed(tool_name):
            continue

        def make_tool(name: str):
            def scene_tool(arguments: str = Field(description="JSON string arguments for this tool", default="{}")) -> str:
                start_time = time.time()
                try:
                    args_dict = json.loads(arguments) if arguments else {}
                except Exception:
                    args_dict = {}
                _log_agent("tool_call", f"Tool call start: {name}", tool_name=name, arguments=args_dict, status="running")
                try:
                    result = _TOOL_REGISTRY.execute(name, **args_dict)
                    status = "success"
                    payload = {"status": status, "tool": name, "result": result}
                except Exception as e:
                    status = "failed"
                    payload = {"status": status, "tool": name, "error": str(e)}
                duration_ms = round((time.time() - start_time) * 1000, 1)
                _log_agent("tool_result", f"Tool call finished: {name}", tool_name=name, arguments=args_dict, result=payload, duration_ms=duration_ms, status=status)
                return json.dumps(payload, ensure_ascii=False)
            scene_tool.__name__ = name
            scene_tool.__doc__ = f"Scene tool: {name}"
            return scene_tool

        mcp.add_tool(make_tool(tool_name))


def load_tools():
    _load_tool_registry()
    _register_atomic_tools()
    _register_scene_tools()


def _json_arg(value: str) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--agent-name", default="")
    parser.add_argument("--allowed-tools", default="")
    parser.add_argument("--scenes-root", default="/app/scenes")
    parser.add_argument("--agent-directory-json", default=os.environ.get("AGENT_DIRECTORY_JSON", "{}"))
    parser.add_argument("--comm-matrix-json", default=os.environ.get("COMM_MATRIX_JSON", "{}"))
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--simulation-seed", type=int, default=0)
    args = parser.parse_args()

    setup_runtime(
        scene_key=args.scene,
        agent_id=args.agent_id,
        agent_name=args.agent_name or args.agent_id,
        allowed_tools=args.allowed_tools.split(",") if args.allowed_tools else [],
        scenes_root=args.scenes_root,
        agent_directory=_json_arg(args.agent_directory_json),
        comm_matrix=_json_arg(args.comm_matrix_json),
        trace_id=args.trace_id,
        simulation_seed=args.simulation_seed,
    )
    load_tools()
    mcp.run()


if __name__ == "__main__":
    main()
