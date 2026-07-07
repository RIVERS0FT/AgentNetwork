import json
import asyncio

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


def test_scene_building_uses_identity_role_and_skill_refs(tmp_path, monkeypatch):
    _write_scene(tmp_path)
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    scene_def = simulations._build_scene_from_folder("demo_scene")
    agent = scene_def.agents[0]

    assert agent.agent_id == "ceo"
    assert agent.role == "Leader"
    assert agent.tasks == ["Coordinate the team"]
    assert agent.skill_refs == ["planning", "reporting"]
    assert agent.extra_meta["allowed_tools"] == ["write_plan"]
    assert "identity" not in agent.extra_meta
    assert "allowed_skills" not in agent.extra_meta
    assert "skills_list" not in agent.extra_meta
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


def test_setup_records_explicit_simulation_seed(tmp_path, monkeypatch):
    _write_scene(tmp_path)
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    result = asyncio.run(simulations.setup_simulation(simulations.SimulationRunRequest(scene="demo_scene", seed=1234)))

    assert result["seed"] == 1234
    assert simulations._pending_seed == 1234
