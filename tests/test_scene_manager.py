import io
import json
import zipfile

import pytest

from agent_network.file_management import FileManager, ResourceNotFoundError
from agent_network.scene_management import SceneManager, SceneStorage


def _scene_zip(title: str, role_id: str = "planner") -> bytes:
    payload = io.BytesIO()
    meta = {
        "scenario_metadata": {
            "title": title,
            "global_rules": f"{title} rules",
        },
        "roles": {
            role_id: {
                "name": role_id.title(),
                "identity": f"{role_id} identity",
                "model_backbone": "openclaw",
            }
        },
    }
    instances = {
        "container_instances": {
            role_id: {
                "skill_refs": [],
                "tool_refs": [],
                "tasks": ["execute task"],
            }
        }
    }
    topology = {"topology": []}
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("meta_and_roles.json", json.dumps(meta))
        archive.writestr("instances_and_skills.json", json.dumps(instances))
        archive.writestr("network_topology.json", json.dumps(topology))
    return payload.getvalue()


@pytest.fixture
def manager(tmp_path):
    files = FileManager(
        {
            "scenes": tmp_path / "scenes",
            "archives": tmp_path / "archives",
            "temp": tmp_path / "temp",
        },
        catalog_path=tmp_path / "registry.json",
    )
    occupied = {"busy"}
    return SceneManager(
        SceneStorage(files),
        occupancy_checker=lambda scene_key: scene_key in occupied,
    )


def test_batch_upload_isolates_invalid_items(manager):
    result = manager.upload_many(
        [
            {
                "filename": "alpha.zip",
                "scene_key": "alpha",
                "content": _scene_zip("Alpha"),
            },
            {
                "filename": "broken.zip",
                "scene_key": "broken",
                "content": b"not-a-zip",
            },
        ]
    )

    assert result.total == 2
    assert result.succeeded == 1
    assert result.failed == 1
    assert result.items[0].status == "uploaded"
    assert result.items[1].success is False
    assert manager.details("alpha")["title"] == "Alpha"


def test_batch_parse_returns_each_definition(manager):
    manager.upload_one(
        filename="alpha.zip",
        scene_key="alpha",
        content=_scene_zip("Alpha"),
    )

    result = manager.parse_many(["alpha", "missing"])

    assert result.succeeded == 1
    assert result.failed == 1
    definition = result.items[0].details["definition"]
    assert definition["scene_key"] == "alpha"
    assert definition["agents"][0]["agent_id"] == "planner"
    assert result.items[1].error_code == "scene_not_found"


def test_batch_download_creates_one_managed_archive(manager):
    manager.upload_one(
        filename="alpha.zip",
        scene_key="alpha",
        content=_scene_zip("Alpha"),
    )
    manager.upload_one(
        filename="beta.zip",
        scene_key="beta",
        content=_scene_zip("Beta", role_id="operator"),
    )

    result = manager.download_many(["alpha", "beta", "missing"])

    assert result.succeeded == 2
    assert result.failed == 1
    assert result.archive_resource_id
    descriptor = manager.prepare_batch_download(result.archive_resource_id)
    with zipfile.ZipFile(descriptor.internal_path) as archive:
        names = set(archive.namelist())
    assert "alpha/meta_and_roles.json" in names
    assert "beta/meta_and_roles.json" in names


def test_batch_delete_checks_occupancy_per_item(manager):
    manager.upload_one(
        filename="busy.zip",
        scene_key="busy",
        content=_scene_zip("Busy"),
    )
    manager.upload_one(
        filename="free.zip",
        scene_key="free",
        content=_scene_zip("Free"),
    )

    result = manager.delete_many(["busy", "free"])

    assert result.succeeded == 1
    assert result.failed == 1
    assert result.items[0].status == "blocked"
    assert result.items[0].error_code == "scene_in_use"
    assert manager.details("busy")["scene_key"] == "busy"
    with pytest.raises(ResourceNotFoundError):
        manager.storage.get_resource("free")
