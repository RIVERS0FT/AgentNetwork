import os
import argparse
import json
import time
import asyncio
import importlib.util
from pathlib import Path
import requests

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from agent_network.skill_md_loader import parse_skill_md

mcp = FastMCP("agent-network-mcp")

# Runtime state. This process is launched by the backend-native MCP integration.
_SCENE_KEY = ""
_AGENT_ID = ""
_AGENT_NAME = ""
_ALLOWED_SKILLS = set()
_ALLOWED_TOOLS = set()
_SCENES_ROOT = Path("/app/scenes")
_SKILLS_CACHE = {}
_TOOL_REGISTRY = None
_SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000")

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
    """Send application-layer telemetry to the server.

    This is observability only. It does not implement ReAct, memory, or tool
    selection. Backend frameworks decide which MCP tool to call.
    """
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
        "details": {k: v for k, v in kw.items() if k not in ("action_type", "target")},
    }, timeout=2)


def setup_runtime(scene_key: str, agent_id: str, agent_name: str, allowed_skills: list, allowed_tools: list, scenes_root: str):
    """Prepare context-only Skill metadata and Tool allowlists.

    Markdown Skill is not registered as an executable MCP tool. Skill metadata is
    injected into the backend as context by the adapter. MCP only exposes atomic
    executable tools.
    """
    global _SCENE_KEY, _AGENT_ID, _AGENT_NAME, _ALLOWED_SKILLS, _ALLOWED_TOOLS, _SCENES_ROOT, _SKILLS_CACHE, _TOOL_REGISTRY

    _SCENE_KEY = scene_key
    _AGENT_ID = agent_id
    _AGENT_NAME = agent_name
    _ALLOWED_SKILLS = set(allowed_skills or [])
    _ALLOWED_TOOLS = set(allowed_tools or [])
    _SCENES_ROOT = Path(scenes_root)
    _SKILLS_CACHE = {}
    _TOOL_REGISTRY = None

    skill_dir = _SCENES_ROOT / _SCENE_KEY / "skills"
    if skill_dir.exists() and skill_dir.is_dir():
        for p in sorted(skill_dir.glob("*.md")):
            parsed = parse_skill_md(p)
            if not parsed:
                continue

            s_name = parsed["name"]
            if _ALLOWED_SKILLS and s_name not in _ALLOWED_SKILLS:
                continue

            _SKILLS_CACHE[s_name] = parsed


def _tool_allowed(tool_name: str) -> bool:
    # Empty allowlist means allow all registered tools for local development.
    return not _ALLOWED_TOOLS or tool_name in _ALLOWED_TOOLS or tool_name in ATOMIC_TOOL_NAMES


def _register_atomic_tools():
    """Register AgentNetwork-provided atomic MCP tools.

    These are small system actions. The backend framework decides whether and
    when to call them through native MCP/tool calling. Idle/no-op behavior is
    represented by the backend not calling a tool, not by a wait tool.
    """

    @mcp.tool()
    def send_message(
        target: str = Field(description="Target agent_id"),
        content: str = Field(description="Message content")
    ) -> str:
        """Send a message to another agent."""
        from agent_network.api.system import comm

        ok = asyncio.run(asyncio.to_thread(comm.send, _AGENT_ID, _AGENT_NAME, target, content, "", ""))
        status = "success" if ok else "failed"
        _log_agent(
            "agent_action",
            f"send_message -> {target}",
            action_type="send_message",
            target=target,
            content=content,
            status=status,
        )
        return json.dumps({"status": status, "target": target}, ensure_ascii=False)

    @mcp.tool()
    def broadcast(content: str = Field(description="Message content to broadcast")) -> str:
        """Broadcast a message to all allowed agents."""
        from agent_network.api.system import comm

        ok = asyncio.run(asyncio.to_thread(comm.broadcast, _AGENT_ID, _AGENT_NAME, content, set(), "", ""))
        status = "success" if ok else "failed"
        _log_agent(
            "agent_action",
            "broadcast",
            action_type="broadcast",
            target="broadcast",
            content=content,
            status=status,
        )
        return json.dumps({"status": status, "target": "broadcast"}, ensure_ascii=False)


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
        _log_agent(
            "tool_registry_load_failed",
            f"Failed to load tools.py: {e}",
            status="failed",
            error=str(e),
            action_type="tool_registry_load",
        )


def _list_scene_tools() -> list[str]:
    if not _TOOL_REGISTRY:
        return []

    try:
        raw_tools = _TOOL_REGISTRY.list_tools()
    except Exception as e:
        _log_agent(
            "tool_registry_list_failed",
            f"ToolRegistry.list_tools failed: {e}",
            status="failed",
            error=str(e),
            action_type="tool_registry",
        )
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
    """Expose tools.py ToolRegistry entries as atomic MCP tools.

    The MCP server does not chain tools and does not execute Skill.tools. It only
    exposes each allowed Tool as one callable action.
    """
    for tool_name in _list_scene_tools():
        if not _tool_allowed(tool_name):
            continue

        def make_tool(name: str):
            def scene_tool(
                arguments: str = Field(
                    description="JSON string arguments for this tool",
                    default="{}"
                )
            ) -> str:
                start_time = time.time()
                try:
                    args_dict = json.loads(arguments) if arguments else {}
                except Exception:
                    args_dict = {}

                _log_agent(
                    "tool_call",
                    f"Tool call start: {name}",
                    tool_name=name,
                    arguments=args_dict,
                    action_type="tool_call",
                    status="running",
                )

                try:
                    result = _TOOL_REGISTRY.execute(name, **args_dict)
                    status = "success"
                    payload = {"status": status, "tool": name, "result": result}
                except Exception as e:
                    status = "failed"
                    payload = {"status": status, "tool": name, "error": str(e)}

                duration_ms = round((time.time() - start_time) * 1000, 1)
                _log_agent(
                    "tool_result",
                    f"Tool call finished: {name}",
                    tool_name=name,
                    arguments=args_dict,
                    result=payload,
                    duration_ms=duration_ms,
                    action_type="tool_result",
                    status=status,
                )
                return json.dumps(payload, ensure_ascii=False)

            scene_tool.__name__ = name
            scene_tool.__doc__ = f"Scene tool: {name}"
            return scene_tool

        mcp.add_tool(make_tool(tool_name))


def load_tools():
    _load_tool_registry()
    _register_atomic_tools()
    _register_scene_tools()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--agent-name", default="")
    parser.add_argument("--allowed-skills", default="")
    parser.add_argument("--allowed-tools", default="")
    parser.add_argument("--scenes-root", default="/app/scenes")

    args = parser.parse_args()
    allowed_skills = args.allowed_skills.split(",") if args.allowed_skills else []
    allowed_tools = args.allowed_tools.split(",") if args.allowed_tools else []

    setup_runtime(
        scene_key=args.scene,
        agent_id=args.agent_id,
        agent_name=args.agent_name or args.agent_id,
        allowed_skills=allowed_skills,
        allowed_tools=allowed_tools,
        scenes_root=args.scenes_root,
    )
    load_tools()

    mcp.run()


if __name__ == "__main__":
    main()
