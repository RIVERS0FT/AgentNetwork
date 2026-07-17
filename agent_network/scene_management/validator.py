"""Strict, multi-issue validator for the three core scene JSON files."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .models import (
    SkillDefinition,
    TaskDefinition,
    ToolDefinition,
    ValidatedScene,
    ValidationIssue,
    ValidationResult,
)


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
_META_FIELDS = {"scenario_metadata", "roles"}
_SCENARIO_FIELDS = {"title", "global_rules", "factions", "execution_order"}
_ROLE_FIELDS = {
    "name",
    "identity",
    "background",
    "core_goal",
    "model_backbone",
    "primary_interaction_paradigm",
    "faction_id",
}
_INSTANCE_FIELDS = {"skill_refs", "tool_refs", "tasks"}
_TASK_FIELDS = {
    "task_id",
    "name",
    "goal",
    "input",
    "skill_refs",
    "tool_refs",
    "depends_on",
}
_TOPOLOGY_FIELDS = {
    "endpoint_a",
    "endpoint_b",
    "channel_id",
    "delay_ms",
    "jitter_ms",
    "loss_pct",
    "rate_mbit",
}


class SceneValidator:
    def validate(
        self,
        scene_key: str,
        root: Path,
        meta: Any,
        instances: Any,
        topology_config: Any,
    ) -> ValidatedScene:
        root = root.resolve()
        result = ValidationResult(scene_key=scene_key)
        add = self._issue_adder(result)

        if not isinstance(meta, dict):
            add("SCHEMA_TYPE", "meta_and_roles.json", "$", "file", "", "root must be an object")
            meta = {}
        if not isinstance(instances, dict):
            add("SCHEMA_TYPE", "instances_and_skills.json", "$", "file", "", "root must be an object")
            instances = {}
        if not isinstance(topology_config, dict):
            add("SCHEMA_TYPE", "network_topology.json", "$", "file", "", "root must be an object")
            topology_config = {}

        self._unknown_fields(meta, _META_FIELDS, "meta_and_roles.json", "$", add)
        self._unknown_fields(instances, {"container_instances"}, "instances_and_skills.json", "$", add)
        self._unknown_fields(topology_config, {"topology"}, "network_topology.json", "$", add)

        scenario = meta.get("scenario_metadata")
        if not isinstance(scenario, dict):
            add("SCHEMA_REQUIRED", "meta_and_roles.json", "$.scenario_metadata", "metadata", "", "scenario_metadata must be an object")
            scenario = {}
        else:
            self._unknown_fields(scenario, _SCENARIO_FIELDS, "meta_and_roles.json", "$.scenario_metadata", add)
            if not isinstance(scenario.get("title"), str) or not scenario.get("title", "").strip():
                add("SCHEMA_REQUIRED", "meta_and_roles.json", "$.scenario_metadata.title", "metadata", "", "title must be a non-empty string")
            if "global_rules" in scenario and not isinstance(scenario["global_rules"], str):
                add("SCHEMA_TYPE", "meta_and_roles.json", "$.scenario_metadata.global_rules", "metadata", "", "global_rules must be a string")

        roles = meta.get("roles")
        if not isinstance(roles, dict) or not roles:
            add("AGENT_SET_EMPTY", "meta_and_roles.json", "$.roles", "agent", "", "roles must contain at least one Agent")
            roles = {}
        containers = instances.get("container_instances")
        if not isinstance(containers, dict):
            add("SCHEMA_REQUIRED", "instances_and_skills.json", "$.container_instances", "agent_instance", "", "container_instances must be an object")
            containers = {}

        normalized_roles: dict[str, str] = {}
        for role_id, role in roles.items():
            path = f"$.roles.{role_id}"
            normalized = str(role_id).lower()
            if not isinstance(role_id, str) or not _ID_RE.fullmatch(role_id):
                add("AGENT_ID_INVALID", "meta_and_roles.json", path, "agent", str(role_id), "Agent ID has an invalid format")
            if normalized in normalized_roles:
                add("AGENT_ID_DUPLICATE", "meta_and_roles.json", path, "agent", str(role_id), "Agent ID duplicates another ID after case normalization")
            normalized_roles[normalized] = str(role_id)
            if not isinstance(role, dict):
                add("SCHEMA_TYPE", "meta_and_roles.json", path, "agent", str(role_id), "Agent role must be an object")
                continue
            self._unknown_fields(role, _ROLE_FIELDS, "meta_and_roles.json", path, add)
            for field in ("name", "identity", "core_goal"):
                if not isinstance(role.get(field), str) or not role.get(field, "").strip():
                    add("AGENT_FIELD_REQUIRED", "meta_and_roles.json", f"{path}.{field}", "agent", str(role_id), f"{field} must be a non-empty string")
            backend = role.get("model_backbone", "openclaw")
            if backend not in {"openclaw", "claude-code", "direct_llm"}:
                add("AGENT_BACKEND_UNSUPPORTED", "meta_and_roles.json", f"{path}.model_backbone", "agent", str(role_id), f"unsupported backend '{backend}'")

        normalized_instances = {str(key).lower(): str(key) for key in containers}
        for missing in sorted(set(normalized_roles) - set(normalized_instances)):
            add("AGENT_INSTANCE_MISSING", "instances_and_skills.json", "$.container_instances", "agent", normalized_roles[missing], "role has no matching container instance")
        for extra in sorted(set(normalized_instances) - set(normalized_roles)):
            add("AGENT_ROLE_MISSING", "instances_and_skills.json", f"$.container_instances.{normalized_instances[extra]}", "agent_instance", normalized_instances[extra], "container instance has no matching role")

        tool_ids = self._tool_ids(root, result)
        skill_defs: dict[str, SkillDefinition] = {}
        task_defs: list[TaskDefinition] = []
        task_ids: set[str] = set()
        pending_dependencies: list[tuple[TaskDefinition, str]] = []

        for instance_id, instance in containers.items():
            agent_id = str(instance_id).lower()
            path = f"$.container_instances.{instance_id}"
            if not isinstance(instance, dict):
                add("SCHEMA_TYPE", "instances_and_skills.json", path, "agent_instance", str(instance_id), "container instance must be an object")
                continue
            self._unknown_fields(instance, _INSTANCE_FIELDS, "instances_and_skills.json", path, add)
            skill_refs = self._string_list(instance.get("skill_refs", []), "instances_and_skills.json", f"{path}.skill_refs", "skill", str(instance_id), add)
            tool_refs = self._string_list(instance.get("tool_refs", []), "instances_and_skills.json", f"{path}.tool_refs", "tool", str(instance_id), add)
            for ref in skill_refs:
                definition = self._skill_definition(root, ref, add)
                if definition:
                    existing = skill_defs.get(ref.lower())
                    if existing and existing.relative_path != definition.relative_path:
                        add("SKILL_ID_DUPLICATE", "instances_and_skills.json", f"{path}.skill_refs", "skill", ref, "Skill ID resolves to more than one package")
                    skill_defs[ref.lower()] = definition
            for ref in tool_refs:
                if ref not in tool_ids:
                    add("TOOL_NOT_FOUND", "instances_and_skills.json", f"{path}.tool_refs", "tool", ref, "referenced Tool is not registered in tools.py")
            tasks = instance.get("tasks", [])
            if not isinstance(tasks, list):
                add("TASK_LIST_INVALID", "instances_and_skills.json", f"{path}.tasks", "task", agent_id, "tasks must be an array")
                tasks = []
            for index, raw_task in enumerate(tasks):
                task = self._task_definition(agent_id, index, raw_task, skill_refs, tool_refs, add)
                if not task:
                    continue
                if task.task_id in task_ids:
                    add("TASK_ID_DUPLICATE", "instances_and_skills.json", f"{path}.tasks[{index}]", "task", task.task_id, "task_id must be unique within the scene")
                task_ids.add(task.task_id)
                task_defs.append(task)
                for ref in task.skill_refs:
                    if ref not in skill_refs:
                        add("TASK_SKILL_NOT_ALLOWED", "instances_and_skills.json", f"{path}.tasks[{index}].skill_refs", "task", task.task_id, f"Skill '{ref}' is not assigned to Agent '{agent_id}'")
                for ref in task.tool_refs:
                    if ref not in tool_refs:
                        add("TASK_TOOL_NOT_ALLOWED", "instances_and_skills.json", f"{path}.tasks[{index}].tool_refs", "task", task.task_id, f"Tool '{ref}' is not assigned to Agent '{agent_id}'")
                for dependency in task.depends_on:
                    pending_dependencies.append((task, dependency))

        for task, dependency in pending_dependencies:
            if dependency == task.task_id:
                add("TASK_DEPENDENCY_SELF", "instances_and_skills.json", "$.container_instances", "task", task.task_id, "task cannot depend on itself")
            elif dependency not in task_ids:
                add("TASK_DEPENDENCY_MISSING", "instances_and_skills.json", "$.container_instances", "task", task.task_id, f"unknown task dependency '{dependency}'")
        for task in task_defs:
            for ref in task.skill_refs:
                if ref.lower() not in skill_defs:
                    add("TASK_SKILL_MISSING", "instances_and_skills.json", "$.container_instances", "task", task.task_id, f"unknown Skill '{ref}'")
            for ref in task.tool_refs:
                if ref not in tool_ids:
                    add("TASK_TOOL_MISSING", "instances_and_skills.json", "$.container_instances", "task", task.task_id, f"unknown Tool '{ref}'")
        self._dependency_cycles(task_defs, add)

        topology = topology_config.get("topology")
        if not isinstance(topology, list):
            add("TOPOLOGY_REQUIRED", "network_topology.json", "$.topology", "topology", "", "topology must be an array")
            topology = []
        channel_ids: set[str] = set()
        for index, edge in enumerate(topology):
            path = f"$.topology[{index}]"
            if not isinstance(edge, dict):
                add("SCHEMA_TYPE", "network_topology.json", path, "topology_edge", "", "topology edge must be an object")
                continue
            self._unknown_fields(edge, _TOPOLOGY_FIELDS, "network_topology.json", path, add)
            for field in ("endpoint_a", "endpoint_b", "channel_id"):
                if not isinstance(edge.get(field), str) or not edge.get(field, "").strip():
                    add("TOPOLOGY_FIELD_REQUIRED", "network_topology.json", f"{path}.{field}", "topology_edge", "", f"{field} must be a non-empty string")
            endpoint_a = str(edge.get("endpoint_a", "")).lower()
            endpoint_b = str(edge.get("endpoint_b", "")).lower()
            if endpoint_a == endpoint_b and endpoint_a:
                add("TOPOLOGY_SELF_LINK", "network_topology.json", path, "topology_edge", "", "topology endpoints must be distinct")
            for endpoint in (endpoint_a, endpoint_b):
                if endpoint and endpoint not in normalized_roles:
                    add("TOPOLOGY_AGENT_MISSING", "network_topology.json", path, "topology_edge", endpoint, "topology references an unknown Agent")
            channel_id = str(edge.get("channel_id", ""))
            if channel_id in channel_ids and channel_id:
                add("TOPOLOGY_CHANNEL_DUPLICATE", "network_topology.json", f"{path}.channel_id", "topology_edge", channel_id, "channel_id must be unique")
            channel_ids.add(channel_id)
            for field in ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit"):
                if field in edge and (isinstance(edge[field], bool) or not isinstance(edge[field], (int, float)) or edge[field] < 0):
                    add("TOPOLOGY_NETWORK_INVALID", "network_topology.json", f"{path}.{field}", "topology_edge", channel_id, f"{field} must be a non-negative number")
            if isinstance(edge.get("loss_pct"), (int, float)) and not isinstance(edge.get("loss_pct"), bool) and edge["loss_pct"] > 100:
                add("TOPOLOGY_NETWORK_INVALID", "network_topology.json", f"{path}.loss_pct", "topology_edge", channel_id, "loss_pct must not exceed 100")

        result.finalize()
        return ValidatedScene(
            validation=result,
            skills=sorted(skill_defs.values(), key=lambda item: item.skill_id),
            tools=[ToolDefinition(tool_id=item) for item in sorted(tool_ids)],
            tasks=task_defs,
        )

    @staticmethod
    def _issue_adder(result: ValidationResult):
        def add(code, file, path, entity_type, entity_id, message):
            result.issues.append(ValidationIssue(code, "error", file, path, entity_type, entity_id, message))
        return add

    @staticmethod
    def _unknown_fields(value, allowed, file, path, add):
        if isinstance(value, dict):
            for field in sorted(set(value) - set(allowed)):
                add("SCHEMA_UNKNOWN_FIELD", file, f"{path}.{field}", "field", field, "unknown field is not allowed by agentnetwork-scene.v1")

    @staticmethod
    def _string_list(value, file, path, entity_type, entity_id, add):
        if not isinstance(value, list):
            add("SCHEMA_TYPE", file, path, entity_type, entity_id, "value must be an array of non-empty strings")
            return []
        result = []
        seen = set()
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                add("SCHEMA_TYPE", file, f"{path}[{index}]", entity_type, entity_id, "reference must be a non-empty string")
                continue
            if item in seen:
                add("REFERENCE_DUPLICATE", file, f"{path}[{index}]", entity_type, item, "duplicate reference")
                continue
            seen.add(item)
            result.append(item)
        return result

    @staticmethod
    def _skill_definition(root: Path, ref: str, add) -> SkillDefinition | None:
        if not _REF_RE.fullmatch(ref) or ".." in Path(ref).parts or Path(ref).is_absolute():
            add("SKILL_PATH_INVALID", "instances_and_skills.json", "$.container_instances", "skill", ref, "Skill reference is not a safe relative path")
            return None
        skills_root = (root / "skills").resolve()
        candidates = [skills_root / f"{ref}.md", skills_root / ref / "SKILL.md"]
        for candidate in candidates:
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
        add("SKILL_NOT_FOUND", "instances_and_skills.json", "$.container_instances", "skill", ref, "referenced Skill entrypoint does not exist")
        return None

    @staticmethod
    def _tool_ids(root: Path, result: ValidationResult) -> set[str]:
        path = root / "tools.py"
        if not path.is_file():
            return set()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            result.issues.append(
                ValidationIssue(
                    "TOOL_SOURCE_INVALID",
                    "error",
                    "tools.py",
                    "$",
                    "tool",
                    "",
                    str(exc),
                )
            )
            return set()
        names = set()
        functions = {
            node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "register":
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    name = first.value
                    if not _ID_RE.fullmatch(name):
                        result.issues.append(ValidationIssue("TOOL_ID_INVALID", "error", "tools.py", f"line:{node.lineno}", "tool", name, "registered Tool ID has an invalid format"))
                    elif name in names:
                        result.issues.append(ValidationIssue("TOOL_ID_DUPLICATE", "error", "tools.py", f"line:{node.lineno}", "tool", name, "Tool ID is registered more than once"))
                    if len(node.args) < 2 or not isinstance(node.args[1], ast.Name) or node.args[1].id not in functions:
                        result.issues.append(ValidationIssue("TOOL_CALLABLE_INVALID", "error", "tools.py", f"line:{node.lineno}", "tool", name, "Tool registration must reference a function defined in tools.py"))
                    names.add(name)
        return names

    @staticmethod
    def _dependency_cycles(tasks: list[TaskDefinition], add) -> None:
        graph = {task.task_id: list(task.depends_on) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str, path: list[str]) -> None:
            if task_id in visiting:
                cycle = path[path.index(task_id):]
                add("TASK_DEPENDENCY_CYCLE", "instances_and_skills.json", "$.container_instances", "task", task_id, "dependency cycle: " + " -> ".join(cycle))
                return
            if task_id in visited or task_id not in graph:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency, [*path, dependency])
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id, [task_id])

    @staticmethod
    def _task_definition(agent_id, index, value, agent_skills, agent_tools, add):
        if isinstance(value, str):
            if not value.strip():
                add("TASK_GOAL_REQUIRED", "instances_and_skills.json", "$.container_instances", "task", "", "task goal must be non-empty")
                return None
            return TaskDefinition(f"{agent_id}-{index + 1}", agent_id, value.strip())
        if not isinstance(value, dict):
            add("TASK_STRUCTURE_INVALID", "instances_and_skills.json", "$.container_instances", "task", "", "task must be a string or object")
            return None
        unknown = set(value) - _TASK_FIELDS
        for field in sorted(unknown):
            add("SCHEMA_UNKNOWN_FIELD", "instances_and_skills.json", f"$.container_instances.tasks[{index}].{field}", "task", "", "unknown task field")
        task_id = str(value.get("task_id") or "").strip()
        goal = str(value.get("goal") or value.get("name") or "").strip()
        if not task_id or not _ID_RE.fullmatch(task_id):
            add("TASK_ID_INVALID", "instances_and_skills.json", "$.container_instances", "task", task_id, "task_id must be a non-empty identifier")
            return None
        if not goal:
            add("TASK_GOAL_REQUIRED", "instances_and_skills.json", "$.container_instances", "task", task_id, "task goal must be non-empty")
        input_value = value.get("input", {})
        if not isinstance(input_value, dict):
            add("TASK_INPUT_INVALID", "instances_and_skills.json", "$.container_instances", "task", task_id, "task input must be an object")
            input_value = {}
        skill_refs = value.get("skill_refs", agent_skills)
        tool_refs = value.get("tool_refs", agent_tools)
        depends_on = value.get("depends_on", [])
        for field_name, field_value in (("skill_refs", skill_refs), ("tool_refs", tool_refs), ("depends_on", depends_on)):
            if not isinstance(field_value, list) or not all(isinstance(item, str) and item for item in field_value):
                add("TASK_REFERENCE_INVALID", "instances_and_skills.json", "$.container_instances", "task", task_id, f"{field_name} must be an array of non-empty strings")
        return TaskDefinition(
            task_id,
            agent_id,
            goal,
            input_value,
            list(skill_refs) if isinstance(skill_refs, list) else [],
            list(tool_refs) if isinstance(tool_refs, list) else [],
            list(depends_on) if isinstance(depends_on, list) else [],
        )
