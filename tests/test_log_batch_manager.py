import zipfile

import pytest

from agent_network.file_management import reset_file_manager
from agent_network.log_manager import get_log_manager


@pytest.fixture
def managed_logs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SCENE_DIR", str(tmp_path / "scenes"))
    monkeypatch.setenv("LOG_DIR", str(data / "logs"))
    monkeypatch.setenv("PCAP_DIR", str(data / "pcap"))
    monkeypatch.setenv("ARCHIVE_DIR", str(data / "archives"))
    monkeypatch.setenv("FILE_TEMP_DIR", str(data / "tmp"))
    monkeypatch.setenv(
        "FILE_REGISTRY_PATH",
        str(data / "pcap" / ".file_registry.json"),
    )
    reset_file_manager()
    manager = get_log_manager()
    manager.reset()
    manager._log_dir = str(data / "logs")
    manager._file_manager = None
    manager._managed_log_root = ""
    yield manager
    manager.reset()
    reset_file_manager()


def test_batch_download_parse_and_partial_failure(managed_logs):
    manager = managed_logs
    first = manager.start_session("alpha")
    manager.emit_application_event(
        event="acting",
        agent_id="planner",
        action={"name": "plan", "status": "success"},
    )
    second = manager.start_session("beta")
    manager.emit_system_event("system_error", result={"status": "failed"})

    refs = [
        {"session_id": first, "log_type": "application"},
        {"session_id": second, "log_type": "system"},
        {"session_id": "missing", "log_type": "network"},
    ]
    downloaded = manager.batch_download_logs(refs)

    assert downloaded.succeeded == 2
    assert downloaded.failed == 1
    descriptor = manager.prepare_log_batch_download(
        downloaded.archive_resource_id
    )
    with zipfile.ZipFile(descriptor.internal_path) as archive:
        names = set(archive.namelist())
    assert f"{first}/application.jsonl" in names
    assert f"{second}/system.jsonl" in names
    assert "LOG_BATCH_MANIFEST.json" in names

    parsed = manager.batch_parse_logs(refs)
    assert parsed.succeeded == 2
    assert parsed.failed == 1
    assert parsed.items[0].details["valid_records"] == 1


def test_session_visibility_is_parent_gate_and_new_files_inherit_it(managed_logs):
    manager = managed_logs
    session_id = manager.start_session("hidden")

    hidden = manager.set_session_log_visibility(session_id, False)
    assert hidden["visible"] is False

    manager.emit_application_event(
        event="acting",
        agent_id="planner",
        action={"name": "plan", "status": "success"},
    )
    files = manager.list_log_files(include_hidden=True)
    session = next(item for item in files if item["session"] == session_id)
    assert session["visible"] is False
    assert all(not item["effective_visible"] for item in session["files"])

    refs = [{"session_id": session_id, "log_type": "application"}]
    blocked_download = manager.batch_download_logs(refs)
    assert blocked_download.failed == 1
    blocked_parse = manager.batch_parse_logs(refs)
    assert blocked_parse.failed == 1

    internal_parse = manager.batch_parse_logs(refs, allow_hidden=True)
    assert internal_parse.succeeded == 1

    manager.set_session_log_visibility(session_id, True)
    available = manager.batch_download_logs(refs)
    assert available.succeeded == 1


def test_batch_delete_protects_active_session_and_isolates_results(managed_logs):
    manager = managed_logs
    inactive = manager.start_session("inactive")
    active = manager.start_session("active")

    result = manager.batch_delete_logs(
        [
            {"session_id": inactive, "log_type": "system"},
            {"session_id": active, "log_type": "system"},
        ]
    )
    assert result.succeeded == 1
    assert result.failed == 1
    assert result.items[1].error_code == "session_active"

    visibility = manager.batch_set_session_log_visibility(
        [inactive, active],
        False,
    )
    assert visibility.succeeded == 2
    assert visibility.failed == 0

    sessions_deleted = manager.batch_delete_log_sessions(
        [inactive, active]
    )
    assert sessions_deleted.succeeded == 1
    assert sessions_deleted.failed == 1
    assert sessions_deleted.items[1].error_code == "session_active"
