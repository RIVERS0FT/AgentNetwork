from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


_SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_scenes_v2.py"
_SPEC = importlib.util.spec_from_file_location("migrate_scenes_v2", _SCRIPT)
assert _SPEC and _SPEC.loader
migration = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = migration
_SPEC.loader.exec_module(migration)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _legacy_scene(root: Path) -> Path:
    scene = root / "demo"
    (scene / "skills").mkdir(parents=True)
    _write_json(
        scene / "meta_and_roles.json",
        {
            "scenario_metadata": {
                "title": "演示剧本",
                "global_rules": "所有结果可审计",
                "max_rounds": 8,
            },
            "roles": {
                "PLANNER": {
                    "name": "规划者",
                    "identity": "负责规划",
                    "core_goal": "形成方案",
                    "model_backbone": "openclaw",
                    "primary_interaction_paradigm": "INTERNAL_COLLABORATION",
                },
                "REVIEWER": {
                    "name": "评审者",
                    "identity": "负责评审",
                    "core_goal": "完成评审",
                    "model_backbone": "claude-code",
                },
            },
        },
    )
    _write_json(
        scene / "instances_and_skills.json",
        {
            "container_instances": {
                "PLANNER": {
                    "skill_refs": ["planning"],
                    "tool_refs": ["write_plan"],
                    "tasks": [
                        {
                            "task_id": "draft-plan",
                            "goal": "起草方案",
                            "skill_refs": ["planning"],
                            "tool_refs": ["write_plan"],
                            "depends_on": [],
                        }
                    ],
                },
                "REVIEWER": {"skill_refs": ["review"], "tool_refs": []},
            }
        },
    )
    _write_json(
        scene / "network_topology.json",
        {
            "topology": [
                {
                    "endpoint_a": "planner",
                    "endpoint_b": "reviewer",
                    "channel_id": "c1",
                    "delay_ms": 1,
                    "jitter_ms": 0,
                    "loss_pct": 0,
                    "rate_mbit": 100,
                }
            ]
        },
    )
    (scene / "skills" / "planning.md").write_text("# planning\n", encoding="utf-8")
    (scene / "tools.py").write_text(
        "class Registry:\n"
        "    @classmethod\n"
        "    def register(cls, name, fn):\n"
        "        pass\n\n"
        "def write_plan(**kwargs):\n"
        "    return {'status': 'ok'}\n\n"
        "Registry.register('write_plan', write_plan)\n",
        encoding="utf-8",
    )
    return scene


def test_migrate_legacy_scene_to_v2(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scene = _legacy_scene(scenes)
    report = tmp_path / "report.md"

    results = migration.run(scenes, delete_legacy=True, report=report)

    assert len(results) == 1
    assert not any((scene / name).exists() for name in migration.LEGACY_FILES)
    assert not (scene / "tools.py").exists()
    assert (scene / "tools" / "legacy.py").is_file()
    assert (scene / "skills" / "review.md").is_file()

    agents = json.loads((scene / "Agents.json").read_text(encoding="utf-8"))["agents"]
    assert agents["PLANNER"]["tasks"][0]["task_id"] == "draft-plan"
    assert agents["REVIEWER"]["tasks"][0]["task_id"] == "reviewer-primary"

    env = migration.parse_env(scene / "env.py")
    assert env["environment"]["legacy_metadata"]["max_rounds"] == 8
    assert env["environment"]["agent_legacy_data"]["PLANNER"]
    assert env["scene_tasks"][0]["depends_on"] == [
        "draft-plan",
        "reviewer-primary",
    ]
    assert migration.validate_v2_scene(scene, require_no_legacy=True) == []
    assert "迁移剧本：1" in report.read_text(encoding="utf-8")


def test_migration_is_noop_after_legacy_files_are_removed(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    _legacy_scene(scenes)
    migration.run(scenes, delete_legacy=True, report=None)

    assert migration.run(scenes, delete_legacy=True, report=None) == []
