import io
import zipfile

import pytest

from agent_network.file_management import (
    FileManager,
    ResourceNotReadyError,
    UnsafePathError,
)


@pytest.fixture
def manager(tmp_path):
    roots = {
        "logs": tmp_path / "logs",
        "archives": tmp_path / "archives",
        "temp": tmp_path / "temp",
    }
    return FileManager(roots, catalog_path=tmp_path / "file_registry.json")


@pytest.mark.not_llm
def test_write_read_visibility_download_and_delete(manager):
    resource = manager.write_text(
        "hello\n",
        owner_type="log_session",
        owner_id="session-1",
        resource_type="application_log",
        root_name="logs",
        relative_path="session-1/application.jsonl",
        logical_name="application.jsonl",
        media_type="application/x-ndjson",
    )
    assert manager.read_text(resource.resource_id) == "hello\n"

    manager.append_text(resource.resource_id, "world\n")
    assert manager.read_text(resource.resource_id) == "hello\nworld\n"

    manager.set_visibility([resource.resource_id], False)
    assert manager.list_resources() == []
    assert manager.list_resources(include_hidden=True)[0].visible is False
    with pytest.raises(ResourceNotReadyError):
        manager.prepare_download(resource.resource_id)

    manager.set_visibility([resource.resource_id], True)
    descriptor = manager.prepare_download(resource.resource_id)
    assert descriptor.logical_name == "application.jsonl"
    assert descriptor.sha256

    manager.delete([resource.resource_id])
    assert manager.list_resources(include_hidden=True) == []
    deleted = manager.list_resources(
        include_hidden=True,
        include_deleted=True,
    )[0]
    assert deleted.state == "deleted"


@pytest.mark.not_llm
def test_create_and_extract_archive(manager):
    first = manager.write_text(
        "one",
        owner_type="log_session",
        owner_id="session-1",
        resource_type="application_log",
        root_name="logs",
        relative_path="session-1/application.jsonl",
        logical_name="application.jsonl",
    )
    second = manager.write_bytes(
        b"pcap",
        owner_type="capture_session",
        owner_id="session-1",
        resource_type="pcap",
        root_name="logs",
        relative_path="session-1/a1.pcap",
        logical_name="a1.pcap",
    )
    archive = manager.create_archive(
        [first.resource_id, second.resource_id],
        owner_type="simulation",
        owner_id="session-1",
        root_name="archives",
        relative_path="session-1.zip",
    )
    extracted = manager.extract_archive(
        archive.resource_id,
        destination_root_name="temp",
        destination_relative_dir="session-1",
    )
    assert (extracted / "application.jsonl").read_text() == "one"
    assert (extracted / "a1.pcap").read_bytes() == b"pcap"


@pytest.mark.not_llm
def test_extract_rejects_path_traversal(manager, tmp_path):
    archive_path = tmp_path / "archives" / "unsafe.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    archive_path.write_bytes(payload.getvalue())
    resource = manager.register_existing(
        owner_type="system",
        owner_id="test",
        resource_type="archive",
        root_name="archives",
        relative_path="unsafe.zip",
    )
    with pytest.raises(UnsafePathError):
        manager.extract_archive(
            resource.resource_id,
            destination_root_name="temp",
            destination_relative_dir="unsafe",
        )
