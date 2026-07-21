import json

import pytest

from agent_network.file_management import FileManager
from agent_network.scene_management import SceneStorage, SceneValidationError


def _write_scene(root, scene_name="demo_scene", backend="openclaw"):
    folder = root / scene_name
    folder.mkdir()
    (folder / "meta_and_roles.json").write_text(
        json.dumps(
            {
                "scenario_metadata": {
                    "title": "Demo Scene",
                    "global_rules": "Global rules",
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
            }
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
                        "tasks": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (folder / "network_topology.json").write_text(
        json.dumps({"topology": []}), encoding="utf-8"
    )
    skills_dir = folder / "skills"
    skills_dir.mkdir()
    (skills_dir / "planning.md").write_text("Planning SOP.\n", encoding="utf-8")
    (skills_dir / "reporting.md").write_text("Reporting SOP.\n", encoding="utf-8")
    (folder / "tools.py").write_text(
        'def write_plan(**kwargs):\n    return kwargs\n\nToolRegistry.register("write_plan", write_plan)\n',
        encoding="utf-8",
    )
    return folder


def _storage(tmp_path):
    return SceneStorage(
        FileManager(
            {
                "scenes": tmp_path,
                "archives": tmp_path / ".archives",
                "temp": tmp_path / ".temp",
            },
            catalog_path=tmp_path / ".registry.json",
        )
    )


def test_scene_building_uses_unified_validated_domain_model(tmp_path):
    _write_scene(tmp_path)

    scene_def = _storage(tmp_path).build_definition("demo_scene")
    agent = scene_def.agents[0]

    assert scene_def.scene_key == "demo_scene"
    assert scene_def.title == "Demo Scene"
    assert scene_def.description == "Global rules"
    assert agent.agent_id == "ceo"
    assert agent.role == "Leader"
    assert agent.core_goal == "Coordinate the team"
    assert agent.backend == "openclaw"
    assert agent.skill_refs == ["planning", "reporting"]
    assert agent.allowed_tools == ["write_plan"]
    assert agent.native_capabilities.enabled is True
    assert agent.native_capabilities.allows("agent.spawn") is True
    assert agent.native_capabilities.allows("fs.write") is False
    assert [item.skill_id for item in scene_def.skills] == ["planning", "reporting"]
    assert [item.tool_id for item in scene_def.tools] == ["write_plan"]
    assert scene_def.validation.validation_status == "fully_validated"
    details = _storage(tmp_path).details("demo_scene")
    json.dumps(details)
    assert details["agents"][0]["native_capabilities"]["tools"]["allow"]


@pytest.mark.parametrize("backend", ["claude-code", "direct_llm"])
def test_scene_building_accepts_supported_backends(tmp_path, backend):
    _write_scene(tmp_path, backend=backend)
    assert _storage(tmp_path).build_definition("demo_scene").agents[0].backend == backend


@pytest.mark.parametrize("backend", ["brain", "unknown-backend"])
def test_scene_building_reports_unsupported_backend(tmp_path, backend):
    _write_scene(tmp_path, backend=backend)

    with pytest.raises(SceneValidationError) as exc:
        _storage(tmp_path).build_definition("demo_scene")

    assert "unsupported backend" in str(exc.value)


def test_scene_validation_reports_all_detected_issues(tmp_path):
    folder = _write_scene(tmp_path)
    meta_path = folder / "meta_and_roles.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["scenario_metadata"]["max_rounds"] = 2
    meta["roles"]["CEO"]["model_backbone"] = "brain"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SceneValidationError) as exc:
        _storage(tmp_path).build_definition("demo_scene")

    codes = {issue.code for issue in exc.value.result.issues}
    assert "SCHEMA_UNKNOWN_FIELD" in codes
    assert "AGENT_BACKEND_UNSUPPORTED" in codes
    assert exc.value.result.error_count >= 2


def test_scene_validation_rejects_task_dependency_cycle(tmp_path):
    folder = _write_scene(tmp_path)
    instances_path = folder / "instances_and_skills.json"
    instances = json.loads(instances_path.read_text(encoding="utf-8"))
    instances["container_instances"]["CEO"]["tasks"] = [
        {"task_id": "plan", "goal": "Plan", "depends_on": ["report"]},
        {"task_id": "report", "goal": "Report", "depends_on": ["plan"]},
    ]
    instances_path.write_text(json.dumps(instances), encoding="utf-8")

    with pytest.raises(SceneValidationError) as exc:
        _storage(tmp_path).build_definition("demo_scene")

    assert "TASK_DEPENDENCY_CYCLE" in {
        issue.code for issue in exc.value.result.issues
    }


def test_scene_validation_rejects_invalid_native_capability_fields(tmp_path):
    folder = _write_scene(tmp_path)
    instances_path = folder / "instances_and_skills.json"
    instances = json.loads(instances_path.read_text(encoding="utf-8"))
    instances["container_instances"]["CEO"]["native_capabilities"] = {
        "enabled": True,
        "unexpected": True,
    }
    instances_path.write_text(json.dumps(instances), encoding="utf-8")

    with pytest.raises(SceneValidationError) as exc:
        _storage(tmp_path).build_definition("demo_scene")

    assert "NATIVE_CAPABILITY_INVALID" in {
        issue.code for issue in exc.value.result.issues
    }
