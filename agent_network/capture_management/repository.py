from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from agent_network.file_management import FileManager, FileResource, get_file_manager, stable_resource_id


class CaptureRepository:
    """Resource-oriented persistence for capture artifacts.

    Physical file operations remain owned by FileManager. This repository only
    applies capture-domain resource identifiers and lookup rules.
    """

    def __init__(self, files: Optional[FileManager] = None) -> None:
        self.files = files or get_file_manager()

    @staticmethod
    def session_resource_id(capture_id: str) -> str:
        return stable_resource_id("capture", capture_id, "directory")

    @staticmethod
    def pcap_resource_id(capture_id: str, agent_id: str) -> str:
        return stable_resource_id("capture", capture_id, agent_id, "pcap")

    @staticmethod
    def manifest_resource_id(capture_id: str, agent_id: str) -> str:
        return stable_resource_id("capture", capture_id, agent_id, "manifest")

    @staticmethod
    def session_manifest_resource_id(capture_id: str) -> str:
        return stable_resource_id("capture", capture_id, "session_manifest")

    def ensure_session(self, capture_id: str, session_id: str) -> FileResource:
        return self.files.ensure_directory(
            owner_type="capture_session",
            owner_id=capture_id,
            resource_type="capture_session_directory",
            root_name="pcap",
            relative_path=session_id,
            logical_name=session_id,
            resource_id=self.session_resource_id(capture_id),
        )

    def register_pcap(self, capture_id: str, session_id: str, agent_id: str) -> FileResource:
        return self.files.register_existing(
            owner_type="capture_session",
            owner_id=capture_id,
            resource_type="pcap",
            root_name="pcap",
            relative_path=f"{session_id}/{agent_id}.pcap",
            logical_name=f"{agent_id}.pcap",
            media_type="application/vnd.tcpdump.pcap",
            resource_id=self.pcap_resource_id(capture_id, agent_id),
            upsert=True,
        )

    def write_target_manifest(self, capture_id: str, session_id: str, agent_id: str, value: dict) -> FileResource:
        return self.files.write_json(
            value,
            owner_type="capture_session",
            owner_id=capture_id,
            resource_type="capture_manifest",
            root_name="pcap",
            relative_path=f"{session_id}/{agent_id}.manifest.json",
            logical_name=f"{agent_id}.manifest.json",
            resource_id=self.manifest_resource_id(capture_id, agent_id),
            overwrite=True,
        )

    def write_session_manifest(self, capture_id: str, session_id: str, value: dict) -> FileResource:
        return self.files.write_json(
            value,
            owner_type="capture_session",
            owner_id=capture_id,
            resource_type="capture_session_manifest",
            root_name="pcap",
            relative_path=f"{session_id}/capture.session.json",
            logical_name="capture.session.json",
            resource_id=self.session_manifest_resource_id(capture_id),
            overwrite=True,
        )

    def list_resources(self, capture_id: str, *, include_hidden: bool = True) -> list[FileResource]:
        return self.files.list_resources(
            owner_type="capture_session",
            owner_id=capture_id,
            include_hidden=include_hidden,
        )

    def get_pcap(self, capture_id: str, agent_id: str) -> Optional[FileResource]:
        return self.files.find_resource(
            owner_type="capture_session",
            owner_id=capture_id,
            resource_type="pcap",
            logical_name=f"{agent_id}.pcap",
        )

    def refresh_pcaps(self, capture_id: str, *, compute_sha256: bool = True) -> list[FileResource]:
        refreshed = []
        for resource in self.list_resources(capture_id):
            if resource.resource_type == "pcap":
                refreshed.append(self.files.refresh(resource.resource_id, compute_sha256=compute_sha256))
        return refreshed

    def prepare_download(self, resource_id: str):
        return self.files.prepare_download(resource_id)

    def delete(self, resource_ids: Iterable[str]):
        return self.files.delete(resource_ids)

    def internal_path(self, resource_id: str) -> Path:
        return self.files.resolve_resource_path(resource_id, allow_hidden=True, allow_directory=False)
