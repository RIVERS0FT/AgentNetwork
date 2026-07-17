import json
import os

import pytest

from agent_network.file_management.log_integration import (
    _build_manager_for_log_dir,
)
from agent_network.log_management import LogManager


NETWORK_CONTEXT = {
    "trace_id": "trace_test",
    "capture_id": "capture_test",
    "packet_index": 1,
    "observer_agent_id": "a1",
    "runtime_container": "agent-a1",
    "interface": "any",
    "captured_length": 64,
    "original_length": 64,
    "truncated": False,
}
NETWORK_LAYERS = {
    "ip": {"ip.version": "4", "ip.proto": "6"},
    "tcp": {"tcp.srcport": "49152", "tcp.dstport": "8000"},
}
NETWORK_RAW = {
    "format": "pcap",
    "encoding": "base64",
    "data": "AAAA",
    "byte_length": 3,
    "packet_count": 1,
    "sha256": "test",
}


@pytest.fixture
def manager(tmp_path):
    instance = LogManager(log_dir=str(tmp_path))
    instance.reset()
    instance._log_dir = str(tmp_path)
    os.makedirs(instance._log_dir, exist_ok=True)
    yield instance
    instance.reset()


@pytest.mark.not_llm
def test_layered_recording_without_global_log(manager):
    session_id = manager.start_session("test_scene")
    manager.emit_application_event(
        event="acting",
        agent_id="a1",
        action={"name": "move"},
    )
    manager.emit_network_event(
        context=NETWORK_CONTEXT,
        network=NETWORK_LAYERS,
        raw=NETWORK_RAW,
    )
    manager.emit_system_event(event="debug_snapshot", payload={"ready": True})
    manager._close_file_handles()

    session_dir = os.path.join(manager._log_dir, session_id)
    application_path = os.path.join(session_dir, "application.jsonl")
    assert os.path.isfile(application_path)
    assert os.path.isfile(os.path.join(session_dir, "network.jsonl"))
    assert os.path.isfile(os.path.join(session_dir, "system.jsonl"))
    assert not os.path.exists(os.path.join(session_dir, "global.jsonl"))

    with open(application_path, "r", encoding="utf-8") as stream:
        application_record = json.loads(next(stream))
    assert application_record["agent_id"] == "a1"
    assert "actor" not in application_record


@pytest.mark.not_llm
def test_hide_show_download_and_delete(manager):
    session_id = manager.start_session("test_scene")
    manager.emit_network_event(
        context=NETWORK_CONTEXT,
        network=NETWORK_LAYERS,
        raw=NETWORK_RAW,
    )

    download_path = manager.get_download_path(session_id, "network")
    assert os.path.isfile(download_path)

    manager.hide_log(session_id, "network")
    visible_files = manager.list_log_files()
    assert all(
        item["type"] != "network"
        for session in visible_files
        for item in session["files"]
    )

    hidden_files = manager.list_log_files(include_hidden=True)
    network_file = next(
        item
        for session in hidden_files
        for item in session["files"]
        if item["type"] == "network"
    )
    assert network_file["visible"] is False

    manager.show_log(session_id, "network")
    assert any(
        item["type"] == "network"
        for session in manager.list_log_files()
        for item in session["files"]
    )

    result = manager.delete_log(session_id, "network")
    assert result["deleted"] is True
    assert not os.path.exists(download_path)


@pytest.mark.not_llm
def test_custom_log_roots_use_isolated_resource_catalogs(tmp_path):
    first = _build_manager_for_log_dir(str(tmp_path / "first"))
    second = _build_manager_for_log_dir(str(tmp_path / "second"))

    first.write_text(
        "{}\n",
        owner_type="log_session",
        owner_id="first_session",
        resource_type="network_log",
        root_name="logs",
        relative_path="first_session/network.jsonl",
        logical_name="network.jsonl",
    )

    assert second.list_resources(owner_type="log_session") == []
