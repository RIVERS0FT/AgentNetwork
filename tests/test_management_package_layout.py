from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agent_network"


def test_log_modules_live_only_in_log_management_package():
    expected = {
        "__init__.py",
        "log_manager.py",
        "log_batch.py",
        "log_batch_install.py",
        "llm_metrics.py",
    }
    package = PACKAGE / "log_management"

    assert expected <= {item.name for item in package.iterdir() if item.is_file()}
    for name in expected - {"__init__.py"}:
        assert not (PACKAGE / name).exists()


def test_scene_modules_live_only_in_scene_management_package():
    expected = {
        "__init__.py",
        "scene_def.py",
        "scene_storage.py",
        "scene_manager.py",
    }
    package = PACKAGE / "scene_management"

    assert expected <= {item.name for item in package.iterdir() if item.is_file()}
    for name in expected - {"__init__.py"}:
        assert not (PACKAGE / name).exists()


def test_management_packages_export_domain_entry_points():
    from agent_network.log_management import LogManager, get_log_manager
    from agent_network.scene_management import (
        SceneDefinition,
        SceneManager,
        SceneStorage,
    )

    assert isinstance(get_log_manager(), LogManager)
    assert SceneDefinition and SceneManager and SceneStorage


def test_removed_legacy_entry_points_stay_removed():
    removed = {
        "comm.py",
        "config.py",
        "event_bus.py",
        "full_packet_capture.py",
        "network_emulation.py",
        "real_packet_store.py",
        "skill_mcp_server.py",
        "skill_md_loader.py",
        "skill_source.py",
        "state.py",
        "tool_runtime.py",
    }
    assert not any((PACKAGE / name).exists() for name in removed)

    assert (PACKAGE / "capture_management/http_adapter.py").is_file()
    assert (PACKAGE / "capture_management/packet_store.py").is_file()
    assert (PACKAGE / "comm_management/network_emulation.py").is_file()
    assert (PACKAGE / "simulation_management/state.py").is_file()
