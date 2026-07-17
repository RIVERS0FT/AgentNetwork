import os
import argparse
import json
import time
import random
import asyncio
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Dict
import requests

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from agent_network.comm_management import CommManager
from agent_network.file_management import FileManager, get_file_manager
from agent_network.scene_management import SceneStorage
from agent_network.task_management import TaskManager

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
_COMM = CommManager()
_TRACE_ID = ""
_SKILL_REFS = set()
_SKILL_SOURCE_MODE = False

MAX_SKILL_FILE_BYTES = 512 * 1024
_TEST_MANAGERS: Dict[str, FileManager] = {}

ATOMIC_TOOL_NAMES = {"send_message", "delegate_task"}


@dataclass(frozen=True)
class SceneSkillSource:
    name: str
    kind: str
    root: Path
    entrypoint: Path
    scene_resource_id: str = ""
    root_relative: str = ""

    @property
    def entrypoint_relative(self) -> str:
        if self.kind == "package":
            return self.entrypoint.relative_to(self.root).as_posix()
        return self.entrypoint.name


def _validate_skill_name(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{field_name} is required")
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."}:
        raise ValueError(f"Invalid {field_name}: {value}")
    return value


def _manager_for_skill_root(scenes_root: str) -> FileManager:
    requested = Path(scenes_root).resolve()
    default = get_file_manager()
    if requested == default.root_path("scenes"):
        return default
    key = str(requested)
    manager = _TEST_MANAGERS.get(key)
    if manager is None:
        manager = FileManager(
            {"scenes": requested},
            catalog_path=requested.parent / ".skill_file_registry.json",
        )
        _TEST_MANAGERS[key] = manager
    return manager


def resolve_scene_skill(
    scene_key: str,
    skill_ref: str,
    scenes_root: str = "/app/scenes",
) -> SceneSkillSource:
    scene_key = _validate_skill_name(scene_key, "scene_key")
    skill_ref = _validate_skill_name(skill_ref, "skill_ref")
    manager = _manager_for_skill_root(scenes_root)
    scene = SceneStorage(manager).get_resource(scene_key, allow_hidden=True)
    package_relative = f"skills/{skill_ref}"
    if manager.child_kind(
        scene.resource_id,
        package_relative,
        allow_hidden=True,
    ) == "directory":
        entrypoint = manager.resolve_child_path(
            scene.resource_id,
            f"{package_relative}/SKILL.md",
            allow_hidden=True,
            expected_kind="file",
        )
        root = manager.resolve_child_path(
            scene.resource_id,
            package_relative,
            allow_hidden=True,
            expected_kind="directory",
        )
        return SceneSkillSource(
            name=skill_ref,
            kind="package",
            root=root,
            entrypoint=entrypoint,
            scene_resource_id=scene.resource_id,
            root_relative=package_relative,
        )
    single_relative = f"skills/{skill_ref}.md"
    if manager.child_kind(
        scene.resource_id,
        single_relative,
        allow_hidden=True,
    ) == "file":
        entrypoint = manager.resolve_child_path(
            scene.resource_id,
            single_relative,
            allow_hidden=True,
            expected_kind="file",
        )
        root = manager.resolve_child_path(
            scene.resource_id,
            "skills",
            allow_hidden=True,
            expected_kind="directory",
        )
        return SceneSkillSource(
            name=skill_ref,
            kind="file",
            root=root,
            entrypoint=entrypoint,
            scene_resource_id=scene.resource_id,
            root_relative="skills",
        )
    raise FileNotFoundError(
        f"Skill '{skill_ref}' was not found in scene '{scene_key}'"
    )


def describe_scene_skill(
    scene_key: str,
    skill_ref: str,
    scenes_root: str = "/app/scenes",
) -> dict:
    source = resolve_scene_skill(scene_key, skill_ref, scenes_root)
    return {
        "name": source.name,
        "kind": source.kind,
        "entrypoint": source.entrypoint_relative,
    }


def list_scene_skill_files(
    scene_key: str,
    skill_ref: str,
    scenes_root: str = "/app/scenes",
) -> list[str]:
    manager = _manager_for_skill_root(scenes_root)
    source = resolve_scene_skill(scene_key, skill_ref, scenes_root)
    if source.kind == "file":
        return [source.entrypoint.name]
    paths = manager.list_children(
        source.scene_resource_id,
        source.root_relative,
        recursive=True,
        files_only=True,
        allow_hidden=True,
    )
    prefix = source.root_relative.rstrip("/") + "/"
    relative_paths = [path.removeprefix(prefix) for path in paths]
    return sorted(relative_paths, key=lambda path: (path != "SKILL.md", path))


def read_scene_skill_file(
    scene_key: str,
    skill_ref: str,
    relative_path: str = "SKILL.md",
    scenes_root: str = "/app/scenes",
    max_bytes: int = MAX_SKILL_FILE_BYTES,
) -> str:
    manager = _manager_for_skill_root(scenes_root)
    source = resolve_scene_skill(scene_key, skill_ref, scenes_root)
    requested = str(relative_path or "SKILL.md").strip()
    requested_path = Path(requested)
    if requested_path.is_absolute() or ".." in requested_path.parts:
        raise PermissionError("Skill file path must stay inside the Skill")
    if source.kind == "file":
        if requested not in {"SKILL.md", source.entrypoint.name, ""}:
            raise FileNotFoundError(
                f"Single-file Skill '{skill_ref}' only exposes its entrypoint"
            )
        child = f"skills/{skill_ref}.md"
    else:
        child = f"{source.root_relative}/{requested_path.as_posix()}"
    data = manager.read_child_bytes(
        source.scene_resource_id,
        child,
        allow_hidden=True,
    )
    if len(data) > max_bytes:
        raise ValueError(
            f"Skill file is too large: {len(data)} bytes (limit {max_bytes})"
        )
    return data.decode("utf-8")


def _task_db_path(agent_id: str) -> str:
    storage_agent_id = os.environ.get("AGENT_ID", agent_id).lower()
    return os.environ.get(
        "TASK_DB_PATH",
        os.path.join(
            os.environ.get("DATA_DIR", "/app/data" if os.path.isdir("/app") else "data"),
            "tasks",
            f"{storage_agent_id}.db",
        ),
    )


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
        "to_agent": target if action_type == "send_message" else "",
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
    skill_refs: list | None = None,
    skill_source_mode: bool = False,
):
    global _SCENE_KEY, _AGENT_ID, _AGENT_NAME, _ALLOWED_TOOLS
    global _SCENES_ROOT, _TOOL_REGISTRY, _AGENT_DIRECTORY, _COMM_MATRIX, _COMM, _TRACE_ID
    global _SKILL_REFS, _SKILL_SOURCE_MODE

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
    _COMM = CommManager(
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
        agent_directory=_AGENT_DIRECTORY,
        comm_matrix=_COMM_MATRIX,
        task_manager=TaskManager(_task_db_path(_AGENT_ID)),
    )
    _TRACE_ID = trace_id
    _SKILL_REFS = set(skill_refs or [])
    _SKILL_SOURCE_MODE = bool(skill_source_mode)
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
        result = asyncio.run(
            asyncio.to_thread(
                _COMM.send_message,
                _AGENT_ID,
                _AGENT_NAME,
                target,
                content,
                "",
                _TRACE_ID,
            )
        )
        status = result.status
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
        return json.dumps(
            {**result.to_dict(), "mode": "a2a"},
            ensure_ascii=False,
        )

    @mcp.tool()
    def delegate_task(
        target: str = Field(description="Exact target agent_id"),
        goal: str = Field(description="Task goal"),
        input_data: str = Field(
            description="Optional JSON object passed to the task", default="{}"
        ),
    ) -> str:
        start_time = time.time()
        try:
            parsed_input = json.loads(input_data) if input_data else {}
        except Exception:
            parsed_input = {}
        _log_agent(
            "tool_call",
            "Tool call start: delegate_task",
            tool_name="delegate_task",
            arguments={"target": target, "goal": goal, "input": parsed_input},
            status="running",
        )
        result = asyncio.run(
            asyncio.to_thread(
                _COMM.delegate_task,
                _AGENT_ID,
                _AGENT_NAME,
                target,
                goal,
                parsed_input,
                "",
                _TRACE_ID,
            )
        )
        _log_agent(
            "tool_result",
            f"delegate_task -> {target}",
            action_type="tool_result",
            tool_name="delegate_task",
            target=target,
            content=goal,
            status=result.status,
            duration_ms=round((time.time() - start_time) * 1000, 1),
            a2a_task_id=result.task_id,
        )
        return json.dumps(
            {**result.to_dict(), "mode": "a2a", "operation": "delegate_task"},
            ensure_ascii=False,
        )


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
            "application_error",
            f"Failed to load tools.py: {e}",
            action_type="tool_registry_load",
            status="failed",
            error=str(e),
        )


def _list_scene_tools() -> list[str]:
    if not _TOOL_REGISTRY:
        return []
    try:
        raw_tools = _TOOL_REGISTRY.list_tools()
    except Exception as e:
        _log_agent(
            "application_error",
            f"ToolRegistry.list_tools failed: {e}",
            action_type="tool_registry_list",
            status="failed",
            error=str(e),
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


def _skill_allowed(skill_ref: str) -> bool:
    return skill_ref in _SKILL_REFS


def _register_skill_source_tools():
    @mcp.tool()
    def list_available_skills() -> str:
        """List Skill sources that the current Agent is allowed to read."""
        items = []
        for skill_ref in sorted(_SKILL_REFS):
            try:
                items.append(
                    describe_scene_skill(
                        scene_key=_SCENE_KEY,
                        skill_ref=skill_ref,
                        scenes_root=str(_SCENES_ROOT),
                    )
                )
            except Exception as exc:
                items.append(
                    {
                        "name": skill_ref,
                        "available": False,
                        "error": str(exc),
                    }
                )
        return json.dumps(items, ensure_ascii=False)

    @mcp.tool()
    def list_skill_files(
        skill_ref: str = Field(description="Allowed Skill name"),
    ) -> str:
        """List files inside one allowed Skill package."""
        if not _skill_allowed(skill_ref):
            raise PermissionError(f"Skill is not allowed: {skill_ref}")
        files = list_scene_skill_files(
            scene_key=_SCENE_KEY,
            skill_ref=skill_ref,
            scenes_root=str(_SCENES_ROOT),
        )
        return json.dumps(
            {"skill_ref": skill_ref, "files": files},
            ensure_ascii=False,
        )

    @mcp.tool()
    def read_skill_file(
        skill_ref: str = Field(description="Allowed Skill name"),
        relative_path: str = Field(
            default="SKILL.md",
            description="Path relative to the Skill package root",
        ),
    ) -> str:
        """Read one file from an allowed Skill package."""
        if not _skill_allowed(skill_ref):
            raise PermissionError(f"Skill is not allowed: {skill_ref}")
        return read_scene_skill_file(
            scene_key=_SCENE_KEY,
            skill_ref=skill_ref,
            relative_path=relative_path,
            scenes_root=str(_SCENES_ROOT),
        )


def load_tools():
    _load_tool_registry()
    _register_atomic_tools()
    _register_scene_tools()
    if _SKILL_SOURCE_MODE:
        _register_skill_source_tools()


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
    parser.add_argument("--skill-refs", default="")
    parser.add_argument("--skill-source-mode", action="store_true")
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
        skill_refs=args.skill_refs.split(",") if args.skill_refs else [],
        skill_source_mode=args.skill_source_mode,
    )
    load_tools()
    mcp.run()


if __name__ == "__main__":
    main()
