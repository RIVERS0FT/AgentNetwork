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
        json.dumps({"topology": []}, ensure_ascii=False),
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

    assert scene_def.scene_key == "demo_scene"
    assert scene_def.title == "Demo Scene"
    assert scene_def.description == "Global rules"
    assert agent.agent_id == "ceo"
    assert agent.role == "Leader"
    assert agent.core_goal == "Coordinate the team"
    assert agent.backend == "openclaw"
    assert agent.tasks == []
    assert agent.skill_refs == ["planning", "reporting"]
    assert agent.allowed_tools == ["write_plan"]
    assert not hasattr(agent, "extra_meta")


def test_scene_building_rejects_removed_claudecode_backend(tmp_path, monkeypatch):
    _write_scene(tmp_path, backend="claudecode")
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    with pytest.raises(ValueError) as exc:
        simulations._build_scene_from_folder("demo_scene")

    assert "removed backend alias 'claudecode'" in str(exc.value)


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
