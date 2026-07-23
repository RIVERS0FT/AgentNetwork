"""Strict validation for the AgentNetwork v2 scene file contract."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Callable

from agent_network.native_capabilities import NativeCapabilityPolicy

from .models import (
    SkillDefinition,
    TaskDefinition,
    ToolDefinition,
    ValidatedScene,
    ValidationIssue,
    ValidationResult,
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
_AGENT_FIELDS = {
    "name", "role", "background", "core_goal", "backend", "skill_refs",
    "tool_refs", "native_capabilities", "tasks",
}
_AGENT_TASK_FIELDS = {
    "task_id", "name", "goal", "input", "skill_refs", "tool_refs", "depends_on",
}
_SCENE_TASK_FIELDS = {"task_id", "name", "goal", "input", "depends_on"}
_TOPOLOGY_FIELDS = {
    "endpoint_a", "endpoint_b", "channel_id", "delay_ms", "jitter_ms",
    "loss_pct", "rate_mbit",
}
AddIssue = Callable[[str, str, str, str, str, str], None]


class SceneValidatorV2:
    def validate(self, scene_key: str, root: Path, agents_cfg: Any, env: Any, topology_cfg: Any) -> ValidatedScene:
        result = ValidationResult(scene_key, schema_version="agentnetwork-scene.v2")
        add = self._adder(result)
        agents_cfg = self._object(agents_cfg, "Agents.json", "$", add)
        env = self._object(env, "env.py", "$.ENV", add)
        topology_cfg = self._object(topology_cfg, "topology.json", "$", add)
        self._unknown(agents_cfg, {"agents"}, "Agents.json", "$", add)
        self._unknown(env, {"metadata", "environment", "scene_tasks"}, "env.py", "$.ENV", add)
        self._unknown(topology_cfg, {"topology"}, "topology.json", "$", add)
        self._validate_metadata(env, add)
        if not isinstance(env.get("environment", {}), dict):
            add("ENVIRONMENT_INVALID", "env.py", "$.ENV.environment", "environment", "", "environment must be an object")

        tools = self._tools(root.resolve(), result)
        skills: dict[str, SkillDefinition] = {}
        tasks: list[TaskDefinition] = []
        task_paths: dict[str, tuple[str, str]] = {}
        agents = agents_cfg.get("agents")
        if not isinstance(agents, dict) or not agents:
            add("AGENT_SET_EMPTY", "Agents.json", "$.agents", "agent", "", "agents must contain at least one Agent")
            agents = {}
        normalized_agents: set[str] = set()
        for raw_id, value in agents.items():
            agent_id = str(raw_id)
            normalized = agent_id.lower()
            path = f"$.agents.{agent_id}"
            if not isinstance(raw_id, str) or not _ID.fullmatch(agent_id):
                add("AGENT_ID_INVALID", "Agents.json", path, "agent", agent_id, "Agent ID has an invalid format")
            if normalized in normalized_agents:
                add("AGENT_ID_DUPLICATE", "Agents.json", path, "agent", agent_id, "Agent ID duplicates another ID after case normalization")
            normalized_agents.add(normalized)
            if not isinstance(value, dict):
                add("SCHEMA_TYPE", "Agents.json", path, "agent", agent_id, "Agent definition must be an object")
                continue
            self._unknown(value, _AGENT_FIELDS, "Agents.json", path, add)
            for field in ("name", "role", "core_goal"):
                if not isinstance(value.get(field), str) or not value.get(field, "").strip():
                    add("AGENT_FIELD_REQUIRED", "Agents.json", f"{path}.{field}", "agent", agent_id, f"{field} must be a non-empty string")
            backend = str(value.get("backend", "openclaw") or "openclaw")
            if backend not in {"openclaw", "claude-code", "direct_llm"}:
                add("AGENT_BACKEND_UNSUPPORTED", "Agents.json", f"{path}.backend", "agent", agent_id, f"unsupported backend '{backend}'")
            try:
                NativeCapabilityPolicy.from_dict(value.get("native_capabilities"), backend=backend)
            except (TypeError, ValueError) as exc:
                add("NATIVE_CAPABILITY_INVALID", "Agents.json", f"{path}.native_capabilities", "agent", agent_id, str(exc))
            skill_refs = self._refs(value.get("skill_refs", []), "Agents.json", f"{path}.skill_refs", agent_id, add)
            tool_refs = self._refs(value.get("tool_refs", []), "Agents.json", f"{path}.tool_refs", agent_id, add)
            for ref in skill_refs:
                definition = self._skill(root, ref, add)
                if definition:
                    skills[ref.lower()] = definition
            for ref in tool_refs:
                if ref not in tools:
                    add("TOOL_NOT_FOUND", "Agents.json", f"{path}.tool_refs", "tool", ref, "referenced Tool is not registered under tools/")
            raw_tasks = value.get("tasks", [])
            if not isinstance(raw_tasks, list):
                add("TASK_LIST_INVALID", "Agents.json", f"{path}.tasks", "task", agent_id, "tasks must be an array")
                raw_tasks = []
            for index, raw_task in enumerate(raw_tasks):
                task_path = f"{path}.tasks[{index}]"
                task = self._agent_task(normalized, index, raw_task, skill_refs, tool_refs, task_path, add)
                if task:
                    self._append_task(task, "Agents.json", task_path, tasks, task_paths, add)
                    self._check_agent_task_refs(task, skill_refs, tool_refs, skills, tools, task_path, add)

        raw_scene_tasks = env.get("scene_tasks", [])
        if not isinstance(raw_scene_tasks, list):
            add("SCENE_TASK_LIST_INVALID", "env.py", "$.ENV.scene_tasks", "scene_task", "", "scene_tasks must be an array")
            raw_scene_tasks = []
        for index, raw_task in enumerate(raw_scene_tasks):
            path = f"$.ENV.scene_tasks[{index}]"
            task = self._scene_task(raw_task, path, add)
            if task:
                self._append_task(task, "env.py", path, tasks, task_paths, add)
        self._dependencies(tasks, task_paths, add)
        self._topology(topology_cfg, normalized_agents, add)
        result.finalize()
        return ValidatedScene(result, sorted(skills.values(), key=lambda item: item.skill_id), sorted(tools.values(), key=lambda item: item.tool_id), tasks)

    @staticmethod
    def _adder(result: ValidationResult) -> AddIssue:
        def add(code: str, file: str, path: str, entity_type: str, entity_id: str, message: str) -> None:
            result.issues.append(ValidationIssue(code, "error", file, path, entity_type, entity_id, message))
        return add

    @staticmethod
    def _object(value: Any, file: str, path: str, add: AddIssue) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        add("SCHEMA_TYPE", file, path, "file", "", "value must be an object")
        return {}

    @staticmethod
    def _unknown(value: Any, allowed: set[str], file: str, path: str, add: AddIssue) -> None:
        if isinstance(value, dict):
            for field in sorted(set(value) - allowed):
                add("SCHEMA_UNKNOWN_FIELD", file, f"{path}.{field}", "field", field, "unknown field is not allowed by agentnetwork-scene.v2")

    def _validate_metadata(self, env: dict[str, Any], add: AddIssue) -> None:
        metadata = env.get("metadata")
        if not isinstance(metadata, dict):
            add("SCHEMA_REQUIRED", "env.py", "$.ENV.metadata", "metadata", "", "metadata must be an object")
            return
        self._unknown(metadata, {"title", "description"}, "env.py", "$.ENV.metadata", add)
        if not isinstance(metadata.get("title"), str) or not metadata.get("title", "").strip():
            add("SCHEMA_REQUIRED", "env.py", "$.ENV.metadata.title", "metadata", "", "title must be a non-empty string")
        if "description" in metadata and not isinstance(metadata["description"], str):
            add("SCHEMA_TYPE", "env.py", "$.ENV.metadata.description", "metadata", "", "description must be a string")

    @staticmethod
    def _refs(value: Any, file: str, path: str, entity_id: str, add: AddIssue) -> list[str]:
        if not isinstance(value, list):
            add("SCHEMA_TYPE", file, path, "reference", entity_id, "value must be an array of non-empty strings")
            return []
        output: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item:
                add("SCHEMA_TYPE", file, f"{path}[{index}]", "reference", entity_id, "reference must be a non-empty string")
            elif item in output:
                add("REFERENCE_DUPLICATE", file, f"{path}[{index}]", "reference", item, "duplicate reference")
            else:
                output.append(item)
        return output

    @staticmethod
    def _skill(root: Path, ref: str, add: AddIssue) -> SkillDefinition | None:
        if not _REF.fullmatch(ref) or ".." in Path(ref).parts or Path(ref).is_absolute():
            add("SKILL_PATH_INVALID", "Agents.json", "$.agents", "skill", ref, "Skill reference is not a safe relative path")
            return None
        skills_root = (root / "skills").resolve()
        for candidate in (skills_root / f"{ref}.md", skills_root / ref / "SKILL.md"):
            resolved = candidate.resolve()
            try:
                resolved.relative_to(skills_root)
            except ValueError:
                continue
            if resolved.is_file():
                try:
                    content = resolved.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    add("SKILL_SOURCE_INVALID", resolved.name, "$", "skill", ref, str(exc))
                    return None
                if not content.strip():
                    add("SKILL_SOURCE_EMPTY", resolved.name, "$", "skill", ref, "Skill entrypoint must not be empty")
                    return None
                return SkillDefinition(ref, resolved.parent.relative_to(root).as_posix(), resolved.relative_to(root).as_posix())
        add("SKILL_NOT_FOUND", "Agents.json", "$.agents", "skill", ref, "referenced Skill entrypoint does not exist")
        return None

    @staticmethod
    def _tools(root: Path, result: ValidationResult) -> dict[str, ToolDefinition]:
        definitions: dict[str, ToolDefinition] = {}
        tools_root = root / "tools"
        paths = sorted(tools_root.rglob("*.py")) if tools_root.is_dir() else []
        for path in paths:
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                result.issues.append(ValidationIssue("TOOL_SOURCE_INVALID", "error", relative, "$", "tool", "", str(exc)))
                continue
            functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args or not isinstance(node.func, ast.Attribute) or node.func.attr != "register":
                    continue
                first = node.args[0]
                if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                    continue
                name = first.value
                if name in definitions:
                    result.issues.append(ValidationIssue("TOOL_ID_DUPLICATE", "error", relative, f"line:{node.lineno}", "tool", name, "Tool ID is registered more than once in the scene"))
                if not _ID.fullmatch(name):
                    result.issues.append(ValidationIssue("TOOL_ID_INVALID", "error", relative, f"line:{node.lineno}", "tool", name, "registered Tool ID has an invalid format"))
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Name) or node.args[1].id not in functions:
                    result.issues.append(ValidationIssue("TOOL_CALLABLE_INVALID", "error", relative, f"line:{node.lineno}", "tool", name, "Tool registration must reference a function defined in the same file"))
                definitions[name] = ToolDefinition(name, relative)
        return definitions

    def _agent_task(self, agent_id: str, index: int, value: Any, skills: list[str], tools: list[str], path: str, add: AddIssue) -> TaskDefinition | None:
        if isinstance(value, str):
            if value.strip():
                return TaskDefinition(f"{agent_id}-{index + 1}", agent_id, value.strip(), scope="agent")
            add("TASK_GOAL_REQUIRED", "Agents.json", path, "task", "", "task goal must be non-empty")
            return None
        return self._task_object(value, "Agents.json", path, _AGENT_TASK_FIELDS, agent_id, "agent", skills, tools, add)

    def _scene_task(self, value: Any, path: str, add: AddIssue) -> TaskDefinition | None:
        return self._task_object(value, "env.py", path, _SCENE_TASK_FIELDS, "", "scene", [], [], add)

    def _task_object(self, value: Any, file: str, path: str, allowed: set[str], agent_id: str, scope: str, default_skills: list[str], default_tools: list[str], add: AddIssue) -> TaskDefinition | None:
        if not isinstance(value, dict):
            add("TASK_STRUCTURE_INVALID", file, path, "task", "", "task must be an object")
            return None
        self._unknown(value, allowed, file, path, add)
        task_id = str(value.get("task_id") or "").strip()
        goal = str(value.get("goal") or value.get("name") or "").strip()
        if not task_id or not _ID.fullmatch(task_id):
            add("TASK_ID_INVALID", file, f"{path}.task_id", "task", task_id, "task_id must be a non-empty identifier")
            return None
        if not goal:
            add("TASK_GOAL_REQUIRED", file, f"{path}.goal", "task", task_id, "task goal must be non-empty")
        input_value = value.get("input", {})
        if not isinstance(input_value, dict):
            add("TASK_INPUT_INVALID", file, f"{path}.input", "task", task_id, "task input must be an object")
            input_value = {}
        skill_refs = value.get("skill_refs", default_skills) if scope == "agent" else []
        tool_refs = value.get("tool_refs", default_tools) if scope == "agent" else []
        depends_on = value.get("depends_on", [])
        for field, refs in (("skill_refs", skill_refs), ("tool_refs", tool_refs), ("depends_on", depends_on)):
            if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
                add("TASK_REFERENCE_INVALID", file, f"{path}.{field}", "task", task_id, f"{field} must be an array of non-empty strings")
        return TaskDefinition(task_id, agent_id, goal, input_value, list(skill_refs) if isinstance(skill_refs, list) else [], list(tool_refs) if isinstance(tool_refs, list) else [], list(depends_on) if isinstance(depends_on, list) else [], scope)

    @staticmethod
    def _append_task(task: TaskDefinition, file: str, path: str, tasks: list[TaskDefinition], task_paths: dict[str, tuple[str, str]], add: AddIssue) -> None:
        if task.task_id in task_paths:
            add("TASK_ID_DUPLICATE", file, path, "task", task.task_id, "task_id must be unique across Agent and scene tasks")
        task_paths[task.task_id] = (file, path)
        tasks.append(task)

    @staticmethod
    def _check_agent_task_refs(task: TaskDefinition, allowed_skills: list[str], allowed_tools: list[str], skills: dict[str, SkillDefinition], tools: dict[str, ToolDefinition], path: str, add: AddIssue) -> None:
        for ref in task.skill_refs:
            if ref not in allowed_skills:
                add("TASK_SKILL_NOT_ALLOWED", "Agents.json", f"{path}.skill_refs", "task", task.task_id, f"Skill '{ref}' is not assigned to Agent '{task.agent_id}'")
            if ref.lower() not in skills:
                add("TASK_SKILL_MISSING", "Agents.json", f"{path}.skill_refs", "task", task.task_id, f"unknown Skill '{ref}'")
        for ref in task.tool_refs:
            if ref not in allowed_tools:
                add("TASK_TOOL_NOT_ALLOWED", "Agents.json", f"{path}.tool_refs", "task", task.task_id, f"Tool '{ref}' is not assigned to Agent '{task.agent_id}'")
            if ref not in tools:
                add("TASK_TOOL_MISSING", "Agents.json", f"{path}.tool_refs", "task", task.task_id, f"unknown Tool '{ref}'")

    @staticmethod
    def _dependencies(tasks: list[TaskDefinition], paths: dict[str, tuple[str, str]], add: AddIssue) -> None:
        graph = {task.task_id: list(task.depends_on) for task in tasks}
        for task in tasks:
            file, path = paths[task.task_id]
            for dependency in task.depends_on:
                if dependency == task.task_id:
                    add("TASK_DEPENDENCY_SELF", file, path, "task", task.task_id, "task cannot depend on itself")
                elif dependency not in graph:
                    add("TASK_DEPENDENCY_MISSING", file, path, "task", task.task_id, f"unknown task dependency '{dependency}'")
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(task_id: str, chain: list[str]) -> None:
            if task_id in visiting:
                file, path = paths[task_id]
                add("TASK_DEPENDENCY_CYCLE", file, path, "task", task_id, "dependency cycle: " + " -> ".join(chain[chain.index(task_id):]))
                return
            if task_id in visited or task_id not in graph:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency, [*chain, dependency])
            visiting.remove(task_id)
            visited.add(task_id)
        for task_id in graph:
            visit(task_id, [task_id])

    def _topology(self, config: dict[str, Any], agents: set[str], add: AddIssue) -> None:
        topology = config.get("topology")
        if not isinstance(topology, list):
            add("TOPOLOGY_REQUIRED", "topology.json", "$.topology", "topology", "", "topology must be an array")
            return
        channels: set[str] = set()
        for index, edge in enumerate(topology):
            path = f"$.topology[{index}]"
            if not isinstance(edge, dict):
                add("SCHEMA_TYPE", "topology.json", path, "topology_edge", "", "topology edge must be an object")
                continue
            self._unknown(edge, _TOPOLOGY_FIELDS, "topology.json", path, add)
            a = str(edge.get("endpoint_a", "")).strip().lower()
            b = str(edge.get("endpoint_b", "")).strip().lower()
            channel = str(edge.get("channel_id", "")).strip()
            if not a or not b or a == b:
                add("TOPOLOGY_ENDPOINT_INVALID", "topology.json", path, "topology_edge", channel, "topology must connect two distinct endpoints")
            for endpoint in (a, b):
                if endpoint and endpoint not in agents:
                    add("TOPOLOGY_AGENT_MISSING", "topology.json", path, "topology_edge", endpoint, "topology references an unknown Agent")
            if not channel or channel in channels:
                add("TOPOLOGY_CHANNEL_DUPLICATE", "topology.json", f"{path}.channel_id", "topology_edge", channel, "channel_id must be non-empty and unique")
            channels.add(channel)
            for field in ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit"):
                value = edge.get(field, 0)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    add("TOPOLOGY_NETWORK_INVALID", "topology.json", f"{path}.{field}", "topology_edge", channel, f"{field} must be a non-negative number")
            loss = edge.get("loss_pct", 0)
            if isinstance(loss, (int, float)) and not isinstance(loss, bool) and loss > 100:
                add("TOPOLOGY_NETWORK_INVALID", "topology.json", f"{path}.loss_pct", "topology_edge", channel, "loss_pct must not exceed 100")
