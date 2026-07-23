#!/usr/bin/env python3
"""Migrate AgentNetwork v1 scene directories to the v2 file contract."""

from __future__ import annotations

import argparse
import ast
import json
import pprint
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

LEGACY_FILES = (
    "meta_and_roles.json",
    "instances_and_skills.json",
    "network_topology.json",
)
V2_FILES = ("Agents.json", "topology.json", "env.py")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_NON_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
AGENT_TASK_FIELDS = {
    "task_id",
    "name",
    "goal",
    "input",
    "skill_refs",
    "tool_refs",
    "depends_on",
}


@dataclass
class MigrationResult:
    scene: str
    agents: int
    agent_tasks: int
    scene_tasks: int
    skills_created: list[str] = field(default_factory=list)
    tool_source: str = ""
    warnings: list[str] = field(default_factory=list)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_env(path: Path, value: dict[str, Any]) -> None:
    rendered = pprint.pformat(value, width=100, sort_dicts=False)
    path.write_text(
        '"""AgentNetwork v2 scene environment data."""\n\nENV = ' + rendered + "\n",
        encoding="utf-8",
    )


def unique_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in output:
            output.append(item)
    return output


def safe_id(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if _ID_RE.fullmatch(text):
        return text
    text = _NON_ID_RE.sub("-", text).strip("-._")
    if text and _ID_RE.fullmatch(text):
        return text
    fallback = _NON_ID_RE.sub("-", fallback).strip("-._") or "task"
    return fallback


def ensure_unique_id(candidate: str, used: set[str]) -> str:
    result = candidate
    suffix = 2
    while result in used:
        result = f"{candidate}-{suffix}"
        suffix += 1
    used.add(result)
    return result


def normalize_task(
    raw: Any,
    *,
    agent_id: str,
    index: int,
    assigned_skills: list[str],
    assigned_tools: list[str],
    used_ids: set[str],
) -> dict[str, Any] | None:
    fallback_id = f"{safe_id(agent_id.lower(), 'agent')}-task-{index + 1}"
    if isinstance(raw, str):
        goal = raw.strip()
        if not goal:
            return None
        return {
            "task_id": ensure_unique_id(fallback_id, used_ids),
            "goal": goal,
            "input": {},
            "depends_on": [],
        }
    if not isinstance(raw, dict):
        return None

    goal = str(raw.get("goal") or raw.get("name") or "").strip()
    if not goal:
        return None
    task_id = ensure_unique_id(
        safe_id(raw.get("task_id"), fallback_id),
        used_ids,
    )
    output: dict[str, Any] = {
        "task_id": task_id,
        "goal": goal,
        "input": raw.get("input") if isinstance(raw.get("input"), dict) else {},
        "depends_on": unique_strings(raw.get("depends_on", [])),
    }
    if isinstance(raw.get("name"), str) and raw["name"].strip():
        output["name"] = raw["name"].strip()
    if "skill_refs" in raw:
        output["skill_refs"] = [
            ref for ref in unique_strings(raw.get("skill_refs")) if ref in assigned_skills
        ]
    if "tool_refs" in raw:
        output["tool_refs"] = [
            ref for ref in unique_strings(raw.get("tool_refs")) if ref in assigned_tools
        ]
    extras = {key: value for key, value in raw.items() if key not in AGENT_TASK_FIELDS}
    if extras:
        output["input"] = dict(output["input"])
        output["input"]["legacy_fields"] = extras
    return output


def skill_entry_exists(scene: Path, ref: str) -> bool:
    return (scene / "skills" / f"{ref}.md").is_file() or (
        scene / "skills" / ref / "SKILL.md"
    ).is_file()


def create_skill_stub(scene: Path, ref: str, agent_ids: Iterable[str]) -> str:
    path = scene / "skills" / f"{ref}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    agents = "、".join(sorted(set(agent_ids)))
    path.write_text(
        f"# {ref}\n\n"
        "此 Skill 入口由旧剧本迁移生成，用于保留原有 Skill 引用。\n\n"
        f"关联 Agent：{agents or '未指定'}。\n",
        encoding="utf-8",
    )
    return path.relative_to(scene).as_posix()


def registered_tools(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    output: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            output.add(node.args[0].value)
    return output


def migrate_tools(scene: Path, required: set[str], delete_legacy: bool) -> str:
    if not required:
        source = scene / "tools.py"
        if delete_legacy and source.is_file():
            target = scene / "tools" / "legacy.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            source.unlink()
            return target.relative_to(scene).as_posix()
        return ""

    candidates = [scene / "tools.py", scene / "skills.py"]
    chosen: Path | None = None
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        names = registered_tools(candidate)
        seen.update(names)
        if required.issubset(names):
            chosen = candidate
            break
    if chosen is None:
        missing = sorted(required - seen)
        raise ValueError(f"{scene}: missing Tool registrations: {missing}")

    tools_dir = scene / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    target_name = "legacy.py" if chosen.name == "tools.py" else "legacy_skills.py"
    target = tools_dir / target_name
    content = chosen.read_text(encoding="utf-8")
    if target.exists() and target.read_text(encoding="utf-8") != content:
        raise ValueError(
            f"{scene}: target Tool file already exists with different content: {target}"
        )
    target.write_text(content, encoding="utf-8")
    if delete_legacy and chosen.name == "tools.py":
        chosen.unlink()
    return target.relative_to(scene).as_posix()


def optional_environment_data(scene: Path) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for filename, key in (
        ("business_topology.json", "business_topology"),
        ("scale_config.json", "scale_config"),
    ):
        path = scene / filename
        if path.is_file():
            output[key] = read_json(path)
    return output


def migrate_scene(scene: Path, *, root: Path, delete_legacy: bool) -> MigrationResult:
    meta = read_json(scene / "meta_and_roles.json")
    instances = read_json(scene / "instances_and_skills.json")
    topology = read_json(scene / "network_topology.json")
    if not isinstance(meta, dict) or not isinstance(instances, dict) or not isinstance(topology, dict):
        raise ValueError(f"{scene}: legacy core files must contain objects")

    scenario_metadata = meta.get("scenario_metadata") or {}
    roles = meta.get("roles") or {}
    containers = instances.get("container_instances") or {}
    if not isinstance(scenario_metadata, dict) or not isinstance(roles, dict) or not roles:
        raise ValueError(f"{scene}: invalid legacy metadata or roles")
    if not isinstance(containers, dict):
        raise ValueError(f"{scene}: container_instances must be an object")

    title = str(scenario_metadata.get("title") or scene.name)
    description = str(
        scenario_metadata.get("description")
        or scenario_metadata.get("global_rules")
        or ""
    )
    normalized_containers = {str(key).lower(): value for key, value in containers.items()}
    agents: dict[str, Any] = {}
    used_task_ids: set[str] = set()
    agent_task_ids: list[str] = []
    skill_users: dict[str, list[str]] = {}
    required_tools: set[str] = set()
    agent_legacy_data: dict[str, Any] = {}
    instance_legacy_data: dict[str, Any] = {}

    mapped_role_fields = {
        "name",
        "identity",
        "role",
        "background",
        "core_goal",
        "model_backbone",
        "backend",
    }
    mapped_instance_fields = {
        "skill_refs",
        "skills",
        "tool_refs",
        "tools",
        "tasks",
        "native_capabilities",
    }

    for raw_agent_id, raw_role in roles.items():
        agent_id = str(raw_agent_id)
        role = raw_role if isinstance(raw_role, dict) else {}
        instance_value = normalized_containers.get(agent_id.lower(), {})
        instance = instance_value if isinstance(instance_value, dict) else {}
        name = str(role.get("name") or agent_id)
        identity = str(role.get("identity") or role.get("role") or name)
        core_goal = str(
            role.get("core_goal") or f"完成{name}在《{title}》中的职责"
        )
        backend = str(role.get("model_backbone") or role.get("backend") or "openclaw")
        if backend == "brain" or backend not in {
            "openclaw",
            "claude-code",
            "direct_llm",
        }:
            backend = "openclaw"
        skill_refs = unique_strings(
            instance.get("skill_refs", instance.get("skills", []))
        )
        tool_refs = unique_strings(
            instance.get("tool_refs", instance.get("tools", []))
        )
        required_tools.update(tool_refs)
        for ref in skill_refs:
            skill_users.setdefault(ref, []).append(agent_id)

        tasks: list[dict[str, Any]] = []
        raw_tasks = instance.get("tasks", [])
        if isinstance(raw_tasks, list):
            for index, raw_task in enumerate(raw_tasks):
                task = normalize_task(
                    raw_task,
                    agent_id=agent_id,
                    index=index,
                    assigned_skills=skill_refs,
                    assigned_tools=tool_refs,
                    used_ids=used_task_ids,
                )
                if task:
                    tasks.append(task)
        if not tasks:
            task_id = ensure_unique_id(
                f"{safe_id(agent_id.lower(), 'agent')}-primary",
                used_task_ids,
            )
            tasks.append(
                {
                    "task_id": task_id,
                    "name": f"完成{name}核心目标",
                    "goal": core_goal,
                    "input": {},
                    "depends_on": [],
                }
            )
        agent_task_ids.extend(task["task_id"] for task in tasks)

        agent: dict[str, Any] = {
            "name": name,
            "role": identity,
            "background": str(role.get("background") or ""),
            "core_goal": core_goal,
            "backend": backend,
            "skill_refs": skill_refs,
            "tool_refs": tool_refs,
            "tasks": tasks,
        }
        if "native_capabilities" in instance:
            agent["native_capabilities"] = instance.get("native_capabilities")
        agents[agent_id] = agent

        role_extras = {
            key: value for key, value in role.items() if key not in mapped_role_fields
        }
        if role_extras:
            agent_legacy_data[agent_id] = role_extras
        instance_extras = {
            key: value
            for key, value in instance.items()
            if key not in mapped_instance_fields
        }
        if instance_extras:
            instance_legacy_data[agent_id] = instance_extras

    scene_task_id = ensure_unique_id("scene-complete", used_task_ids)
    env_environment: dict[str, Any] = {
        "global_rules": scenario_metadata.get("global_rules", ""),
        "initial_state": {},
        "shared_data": {},
    }
    metadata_extras = {
        key: value
        for key, value in scenario_metadata.items()
        if key not in {"title", "description", "global_rules"}
    }
    if metadata_extras:
        env_environment["legacy_metadata"] = metadata_extras
    if agent_legacy_data:
        env_environment["agent_legacy_data"] = agent_legacy_data
    if instance_legacy_data:
        env_environment["agent_instance_legacy_data"] = instance_legacy_data
    env_environment.update(optional_environment_data(scene))

    agents_config = {"agents": agents}
    env = {
        "metadata": {"title": title, "description": description},
        "environment": env_environment,
        "scene_tasks": [
            {
                "task_id": scene_task_id,
                "name": "完成剧本级验收",
                "goal": f"完成《{title}》剧本目标，并满足全部 Agent 核心目标与全局规则。",
                "input": {"legacy_scene": scene.relative_to(root).as_posix()},
                "depends_on": agent_task_ids,
            }
        ],
    }

    skills_created: list[str] = []
    for ref, users in sorted(skill_users.items()):
        if not skill_entry_exists(scene, ref):
            skills_created.append(create_skill_stub(scene, ref, users))
    tool_source = migrate_tools(scene, required_tools, delete_legacy)

    write_json(scene / "Agents.json", agents_config)
    write_json(scene / "topology.json", {"topology": topology.get("topology", [])})
    write_env(scene / "env.py", env)
    if delete_legacy:
        for filename in LEGACY_FILES:
            (scene / filename).unlink()

    return MigrationResult(
        scene=scene.relative_to(root).as_posix(),
        agents=len(agents),
        agent_tasks=len(agent_task_ids),
        scene_tasks=1,
        skills_created=skills_created,
        tool_source=tool_source,
    )


def parse_env(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    if len(assignments) != 1:
        raise ValueError(f"{path}: expected one ENV assignment")
    node = assignments[0]
    if isinstance(node, ast.Assign):
        if (
            len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
            or node.targets[0].id != "ENV"
        ):
            raise ValueError(f"{path}: invalid ENV assignment")
        value_node = node.value
    else:
        if (
            not isinstance(node.target, ast.Name)
            or node.target.id != "ENV"
            or node.value is None
        ):
            raise ValueError(f"{path}: invalid ENV assignment")
        value_node = node.value
    value = ast.literal_eval(value_node)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: ENV must be a dictionary")
    return value


def validate_v2_scene(scene: Path, *, require_no_legacy: bool) -> list[str]:
    errors: list[str] = []
    for filename in V2_FILES:
        if not (scene / filename).is_file():
            errors.append(f"missing {filename}")
    if errors:
        return errors
    try:
        agents_cfg = read_json(scene / "Agents.json")
        topology_cfg = read_json(scene / "topology.json")
        env = parse_env(scene / "env.py")
    except Exception as exc:
        return [str(exc)]

    agents = agents_cfg.get("agents") if isinstance(agents_cfg, dict) else None
    if not isinstance(agents, dict) or not agents:
        errors.append("Agents.json must contain non-empty agents")
        agents = {}
    agent_ids = {str(agent_id).lower() for agent_id in agents}
    task_ids: set[str] = set()
    dependencies: list[tuple[str, str]] = []
    tool_refs: set[str] = set()
    for agent_id, agent in agents.items():
        if not isinstance(agent, dict):
            errors.append(f"Agent {agent_id} must be an object")
            continue
        tasks = agent.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            errors.append(f"Agent {agent_id} must contain tasks")
            tasks = []
        for ref in unique_strings(agent.get("skill_refs", [])):
            if not skill_entry_exists(scene, ref):
                errors.append(f"Agent {agent_id} references missing Skill {ref}")
        tool_refs.update(unique_strings(agent.get("tool_refs", [])))
        for task in tasks:
            if not isinstance(task, dict):
                errors.append(f"Agent {agent_id} contains invalid task")
                continue
            task_id = str(task.get("task_id") or "")
            if not _ID_RE.fullmatch(task_id) or task_id in task_ids:
                errors.append(f"invalid or duplicate task_id {task_id}")
            task_ids.add(task_id)
            dependencies.extend(
                (task_id, dep) for dep in unique_strings(task.get("depends_on", []))
            )

    scene_tasks = env.get("scene_tasks") if isinstance(env, dict) else None
    if not isinstance(scene_tasks, list) or not scene_tasks:
        errors.append("env.py must contain scene_tasks")
        scene_tasks = []
    for task in scene_tasks:
        if not isinstance(task, dict):
            errors.append("scene_tasks contains invalid task")
            continue
        task_id = str(task.get("task_id") or "")
        if not _ID_RE.fullmatch(task_id) or task_id in task_ids:
            errors.append(f"invalid or duplicate scene task_id {task_id}")
        task_ids.add(task_id)
        dependencies.extend(
            (task_id, dep) for dep in unique_strings(task.get("depends_on", []))
        )
    for task_id, dependency in dependencies:
        if dependency not in task_ids:
            errors.append(f"task {task_id} depends on missing task {dependency}")

    registrations: set[str] = set()
    tools_dir = scene / "tools"
    if tools_dir.is_dir():
        for path in tools_dir.rglob("*.py"):
            try:
                registrations.update(registered_tools(path))
            except Exception as exc:
                errors.append(str(exc))
    missing_tools = sorted(tool_refs - registrations)
    if missing_tools:
        errors.append(f"missing Tool registrations: {missing_tools}")

    topology = topology_cfg.get("topology") if isinstance(topology_cfg, dict) else None
    if not isinstance(topology, list):
        errors.append("topology.json must contain topology array")
    else:
        for edge in topology:
            if not isinstance(edge, dict):
                errors.append("topology edge must be an object")
                continue
            a = str(edge.get("endpoint_a") or "").lower()
            b = str(edge.get("endpoint_b") or "").lower()
            if a not in agent_ids or b not in agent_ids:
                errors.append(f"topology references unknown Agent: {a}<->{b}")
    if require_no_legacy:
        for filename in LEGACY_FILES:
            if (scene / filename).exists():
                errors.append(f"legacy file still exists: {filename}")
        if (scene / "tools.py").exists():
            errors.append("legacy root tools.py still exists")
    return errors


def legacy_scene_dirs(root: Path) -> list[Path]:
    scenes: list[Path] = []
    for marker in root.rglob(LEGACY_FILES[0]):
        scene = marker.parent
        if all((scene / filename).is_file() for filename in LEGACY_FILES):
            scenes.append(scene)
    return sorted(set(scenes))


def v2_scene_dirs(root: Path) -> list[Path]:
    scenes: list[Path] = []
    for marker in root.rglob(V2_FILES[0]):
        scene = marker.parent
        if all((scene / filename).is_file() for filename in V2_FILES):
            scenes.append(scene)
    return sorted(set(scenes))


def write_report(path: Path, results: list[MigrationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total_agents = sum(item.agents for item in results)
    total_agent_tasks = sum(item.agent_tasks for item in results)
    lines = [
        "# 旧剧本迁移到 v2 实施记录",
        "",
        f"- 迁移剧本：{len(results)}",
        f"- Agent：{total_agents}",
        f"- Agent 任务：{total_agent_tasks}",
        f"- 剧本级任务：{sum(item.scene_tasks for item in results)}",
        "",
        "| 剧本 | Agent | Agent任务 | Skill补全 | Tool迁移 |",
        "|---|---:|---:|---:|---|",
    ]
    for item in results:
        lines.append(
            f"| `{item.scene}` | {item.agents} | {item.agent_tasks} | "
            f"{len(item.skills_created)} | `{item.tool_source or '-'}` |"
        )
    lines.extend(
        [
            "",
            "## 迁移规则",
            "",
            "- 原 `core_goal` 在缺少显式任务时生成 Agent 主任务。",
            "- 原显式任务保留任务 ID、目标、输入、Skill、Tool 与依赖。",
            "- `env.py` 保存元数据、全局规则、旧扩展字段、可选业务拓扑与剧本级完成任务。",
            "- 原根目录 `tools.py` 移至 `tools/legacy.py`。",
            "- 缺失的 Skill 入口生成带迁移说明的 Markdown 占位文档。",
            "- 三份旧核心 JSON 在校验成功后删除。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(root: Path, *, delete_legacy: bool, report: Path | None) -> list[MigrationResult]:
    results = [
        migrate_scene(scene, root=root, delete_legacy=delete_legacy)
        for scene in legacy_scene_dirs(root)
    ]
    failures: dict[str, list[str]] = {}
    for scene in v2_scene_dirs(root):
        errors = validate_v2_scene(scene, require_no_legacy=delete_legacy)
        if errors:
            failures[scene.relative_to(root).as_posix()] = errors
    if failures:
        rendered = "\n".join(
            f"- {scene}: {'; '.join(errors)}" for scene, errors in failures.items()
        )
        raise ValueError("v2 scene validation failed:\n" + rendered)
    if report is not None:
        write_report(report, results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("scenes"))
    parser.add_argument("--delete-legacy", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    results = run(root, delete_legacy=args.delete_legacy, report=args.report)
    counts = Counter({"scenes": len(results)})
    counts["agents"] = sum(item.agents for item in results)
    counts["agent_tasks"] = sum(item.agent_tasks for item in results)
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
