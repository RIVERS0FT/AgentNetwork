import json

import pytest

from agent_network.api import simulations


def _write_scene(root, scene_name="demo_scene", backend="openclaw"):
    folder = root / scene_name
    folder.mkdir()

    (folder / "meta_and_roles.json").write_text(
        json.dumps(
            {
                "scenario_metadata": {
                    "title": "Demo Scene",
                    "global_rules": "Global rules",
                    "max_rounds": 2,
                },
                "roles": {
                    "CEO": {
                        "name": "Chief Executive",
                        "identity": "Leader",
                        "core_goal": "Coordinate the team",
                        "model_backbone": backend,
                        "primary_interaction_paradigm": "INTERNAL_COLLABORATION",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (folder / "instances_and_skills.json").write_text(
        json.dumps(
            {
                "container_instances": {
                    "CEO": {
                        "skill_refs": ["planning", "reporting"],
                        "tool_refs": ["write_plan"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (folder / "network_topology.json").write_text(
        json.dumps({"sub_networks": [{"edges": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    skills_dir = folder / "skills"
    skills_dir.mkdir()
    (skills_dir / "planning.md").write_text(
        """---
name: planning
description: Plan work
tools:
  - write_plan
---
Planning SOP.
""",
        encoding="utf-8",
    )
    (skills_dir / "reporting.md").write_text(
        """---
name: reporting
description: Report work
---
Reporting SOP.
""",
        encoding="utf-8",
    )

    return folder


def test_scene_building_uses_core_goal_as_task_and_skills_as_context(tmp_path, monkeypatch):
    _write_scene(tmp_path)
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    scene_def = simulations._build_scene_from_folder("demo_scene")
    agent = scene_def.agents[0]

    assert agent.agent_id == "ceo"
    assert agent.tasks == ["Coordinate the team"]
    assert agent.skills == ["planning", "reporting"]
    assert agent.extra_meta["skills_list"][0]["name"] == "planning"
    assert agent.extra_meta["allowed_tools"] == ["write_plan"]
    assert agent.extra_meta["action_space"] == ["send_message", "broadcast", "write_plan"]
    assert agent.extra_meta["skill_execution_mode"] == "backend_native_mcp"


def test_scene_building_normalizes_claudecode_backend(tmp_path, monkeypatch):
    _write_scene(tmp_path, backend="claudecode")
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    scene_def = simulations._build_scene_from_folder("demo_scene")

    assert scene_def.agents[0].extra_meta["backend"] == "claude-code"


def test_scene_building_rejects_removed_brain_backend(tmp_path, monkeypatch):
    _write_scene(tmp_path, backend="brain")
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    with pytest.raises(ValueError) as exc:
        simulations._build_scene_from_folder("demo_scene")

    assert "removed backend 'brain'" in str(exc.value)


def test_scene_building_rejects_unknown_backend(tmp_path, monkeypatch):
    _write_scene(tmp_path, backend="unknown-backend")
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    with pytest.raises(ValueError) as exc:
        simulations._build_scene_from_folder("demo_scene")

    assert "unsupported backend" in str(exc.value)
