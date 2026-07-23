import json

import pytest

from agent_network.file_management import FileManager
from agent_network.scene_management import SceneStorage, SceneValidationError


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


def _write_scene(root, name="demo"):
    folder = root / name
    folder.mkdir()
    (folder / "Agents.json").write_text(
        json.dumps(
            {
                "agents": {
                    "planner": {
                        "name": "Planner",
                        "role": "Planner",
                        "background": "",
                        "core_goal": "Create a plan",
                        "backend": "openclaw",
                        "skill_refs": ["planning"],
                        "tool_refs": ["write_plan"],
                        "tasks": [
                            {
                                "task_id": "draft-plan",
                                "goal": "Draft the plan",
                                "input": {},
                                "skill_refs": ["planning"],
                                "tool_refs": ["write_plan"],
                                "depends_on": [],
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (folder / "topology.json").write_text(
        json.dumps({"topology": []}),
        encoding="utf-8",
    )
    (folder / "env.py").write_text(
        """ENV = {
    'metadata': {'title': 'Demo', 'description': 'Demo scene'},
    'environment': {
        'global_rules': ['audit everything'],
        'initial_state': {'status': 'ready'},
        'shared_data': {'ticket': 7},
    },
    'scene_tasks': [
        {
            'task_id': 'finish-scene',
            'goal': 'Complete scene acceptance',
            'input': {},
            'depends_on': ['draft-plan'],
        }
    ],
}
""",
        encoding="utf-8",
    )
    skills = folder / "skills"
    skills.mkdir()
    (skills / "planning.md").write_text("Planning SOP", encoding="utf-8")
    tools = folder / "tools"
    tools.mkdir()
    (tools / "planning.py").write_text(
        "def write_plan(**kwargs):\n    return kwargs\n\nToolRegistry.register('write_plan', write_plan)\n",
        encoding="utf-8",
    )
    return folder


def test_v2_scene_separates_agent_and_scene_tasks(tmp_path):
    _write_scene(tmp_path)

    definition = _storage(tmp_path).build_definition("demo")

    assert definition.validation.schema_version == "agentnetwork-scene.v2"
    assert definition.environment["initial_state"] == {"status": "ready"}
    assert definition.agents[0].tasks == ["Draft the plan"]
    assert [(task.task_id, task.scope) for task in definition.tasks] == [
        ("draft-plan", "agent"),
        ("finish-scene", "scene"),
    ]
    details = _storage(tmp_path).details("demo")
    assert details["environment"]["shared_data"] == {"ticket": 7}


def test_env_py_is_parsed_without_execution(tmp_path):
    folder = _write_scene(tmp_path)
    marker = tmp_path / "executed"
    (folder / "env.py").write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('bad')\nENV = {{}}\n",
        encoding="utf-8",
    )

    with pytest.raises(SceneValidationError) as exc:
        _storage(tmp_path).build_definition("demo")

    assert "ENV_SOURCE_INVALID" in {
        issue.code for issue in exc.value.result.issues
    }
    assert not marker.exists()


def test_scene_tasks_share_dependency_graph_with_agent_tasks(tmp_path):
    folder = _write_scene(tmp_path)
    (folder / "env.py").write_text(
        """ENV = {
    'metadata': {'title': 'Demo', 'description': ''},
    'environment': {},
    'scene_tasks': [
        {'task_id': 'finish-scene', 'goal': 'Finish', 'depends_on': ['missing']}
    ],
}
""",
        encoding="utf-8",
    )

    with pytest.raises(SceneValidationError) as exc:
        _storage(tmp_path).build_definition("demo")

    assert "TASK_DEPENDENCY_MISSING" in {
        issue.code for issue in exc.value.result.issues
    }


def test_tools_can_be_split_across_files_but_ids_are_unique(tmp_path):
    folder = _write_scene(tmp_path)
    (folder / "tools" / "duplicate.py").write_text(
        "def duplicate(**kwargs):\n    return kwargs\n\nToolRegistry.register('write_plan', duplicate)\n",
        encoding="utf-8",
    )

    with pytest.raises(SceneValidationError) as exc:
        _storage(tmp_path).build_definition("demo")

    assert "TOOL_ID_DUPLICATE" in {
        issue.code for issue in exc.value.result.issues
    }
