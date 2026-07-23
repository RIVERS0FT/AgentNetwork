import asyncio
import json

import pytest

from agent_network.api import simulations
from agent_network.file_management import FileManager, ResourceNotFoundError
from agent_network.scene_management import SceneManager, SceneStorage
from agent_network.scene_management.models import ValidationResult


def _storage(tmp_path):
    return SceneStorage(
        FileManager(
            {
                "scenes": tmp_path / "scenes",
                "archives": tmp_path / "archives",
                "temp": tmp_path / "temp",
            },
            catalog_path=tmp_path / "registry.json",
        )
    )


def _write_v2_scene(root, name="demo"):
    scene = root / name
    scene.mkdir(parents=True)
    (scene / "Agents.json").write_text(
        json.dumps(
            {
                "agents": {
                    "planner": {
                        "name": "Planner",
                        "role": "Planner",
                        "background": "",
                        "core_goal": "Create a plan",
                        "backend": "openclaw",
                        "skill_refs": [],
                        "tool_refs": [],
                        "tasks": [
                            {
                                "task_id": "plan",
                                "goal": "Create a plan",
                                "input": {},
                                "depends_on": [],
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (scene / "topology.json").write_text(
        json.dumps({"topology": []}), encoding="utf-8"
    )
    (scene / "env.py").write_text(
        """ENV = {
    'metadata': {'title': 'Demo v2', 'description': 'New format'},
    'environment': {'global_rules': ['v2 only'], 'initial_state': {'ready': True}, 'shared_data': {}},
    'scene_tasks': [{'task_id': 'done', 'goal': 'Finish', 'input': {}, 'depends_on': ['plan']}],
}
""",
        encoding="utf-8",
    )
    return scene


def _write_legacy_scene(root, name="legacy"):
    scene = root / name
    scene.mkdir(parents=True)
    (scene / "meta_and_roles.json").write_text("{}", encoding="utf-8")
    (scene / "instances_and_skills.json").write_text("{}", encoding="utf-8")
    (scene / "network_topology.json").write_text("{}", encoding="utf-8")
    return scene


def test_storage_discovers_and_reads_only_v2_scenes(tmp_path):
    storage = _storage(tmp_path)
    _write_v2_scene(tmp_path / "scenes")
    _write_legacy_scene(tmp_path / "scenes")

    assert storage.list_scenes() == [{"scene_key": "demo", "title": "Demo v2"}]
    details = storage.details("demo")
    assert details["description"] == "New format"
    assert details["environment"]["initial_state"] == {"ready": True}
    assert details["validation"]["schema_version"] == "agentnetwork-scene.v2"
    assert [(task["task_id"], task["scope"]) for task in details["tasks"]] == [
        ("plan", "agent"),
        ("done", "scene"),
    ]
    with pytest.raises(ResourceNotFoundError):
        storage.get_resource("legacy")


def test_scene_manager_batch_parse_includes_environment(tmp_path):
    storage = _storage(tmp_path)
    _write_v2_scene(tmp_path / "scenes")
    manager = SceneManager(storage)

    result = manager.parse_many(["demo"])

    assert result.succeeded == 1
    definition = result.items[0].details["definition"]
    assert definition["environment"]["global_rules"] == ["v2 only"]
    assert definition["validation"]["schema_version"] == "agentnetwork-scene.v2"


def test_execution_helpers_delegate_scene_reads_to_storage(monkeypatch):
    class FakeStorage:
        def list_scenes(self):
            return [{"scene_key": "demo", "title": "Demo v2"}]

        def details(self, scene_key):
            assert scene_key == "demo"
            return {
                "scene_key": "demo",
                "title": "Demo v2",
                "environment": {"global_rules": ["v2 only"]},
            }

    monkeypatch.setattr(simulations, "get_scene_storage", lambda: FakeStorage())

    assert asyncio.run(simulations.list_scenes()) == {
        "scenes": [{"scene_key": "demo", "title": "Demo v2"}]
    }
    assert asyncio.run(simulations.read_scene("demo"))["environment"] == {
        "global_rules": ["v2 only"]
    }


def test_validation_result_defaults_to_v2_schema():
    assert ValidationResult("demo").schema_version == "agentnetwork-scene.v2"
