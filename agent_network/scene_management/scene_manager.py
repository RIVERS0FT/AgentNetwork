from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Optional

from agent_network.file_management import (
    FileManagerError,
    ResourceNotFoundError,
    ResourceNotReadyError,
    stable_resource_id,
)
from agent_network.scene_management.scene_def import SceneDefinition
from agent_network.scene_management.scene_storage import SceneStorage, get_scene_storage


@dataclass
class SceneBatchItemResult:
    operation: str
    scene_key: str
    success: bool
    status: str
    resource_id: str = ""
    title: str = ""
    error_code: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneBatchResult:
    operation: str
    batch_id: str
    items: list[SceneBatchItemResult]
    archive_resource_id: str = ""
    archive_name: str = ""

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.items if item.success)

    @property
    def failed(self) -> int:
        return self.total - self.succeeded

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "batch_id": self.batch_id,
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "items": [item.to_dict() for item in self.items],
            "archive_resource_id": self.archive_resource_id,
            "archive_name": self.archive_name,
        }


class SceneManager:
    """Scene business facade built on top of single-resource SceneStorage."""

    def __init__(
        self,
        storage: Optional[SceneStorage] = None,
        *,
        occupancy_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.storage = storage or get_scene_storage()
        self.files = self.storage.files
        self._occupancy_checker = occupancy_checker or (lambda _scene_key: False)

    @staticmethod
    def _batch_id(operation: str) -> str:
        return f"scene-{operation}-{uuid.uuid4().hex}"

    @staticmethod
    def _error_result(
        operation: str,
        scene_key: str,
        exc: Exception,
    ) -> SceneBatchItemResult:
        if isinstance(exc, ResourceNotFoundError):
            error_code = "scene_not_found"
        elif isinstance(exc, ResourceNotReadyError):
            error_code = "scene_hidden"
        elif isinstance(exc, FileExistsError):
            error_code = "scene_exists"
        elif isinstance(exc, ValueError):
            error_code = "invalid_scene"
        elif isinstance(exc, (OSError, FileManagerError)):
            error_code = "storage_error"
        else:
            error_code = "scene_operation_failed"
        return SceneBatchItemResult(
            operation=operation,
            scene_key=scene_key,
            success=False,
            status="failed",
            error_code=error_code,
            error=str(exc),
        )

    @staticmethod
    def _definition_payload(definition: SceneDefinition) -> dict[str, Any]:
        agents = []
        for agent in definition.agents:
            value = asdict(agent)
            value["native_capabilities"] = agent.native_capabilities.to_dict()
            agents.append(value)
        return {
            "scene_key": definition.scene_key,
            "title": definition.title,
            "description": definition.description,
            "environment": definition.environment,
            "agents": agents,
            "skills": [asdict(skill) for skill in definition.skills],
            "tools": [asdict(tool) for tool in definition.tools],
            "tasks": [asdict(task) for task in definition.tasks],
            "topology": definition.topology,
            "validation": definition.validation.to_dict(),
        }

    def list_scenes(self) -> list[dict[str, Any]]:
        return self.storage.list_scenes()

    def details(self, scene_key: str) -> dict[str, Any]:
        return self.storage.details(scene_key)

    def build_definition(self, scene_key: str) -> SceneDefinition:
        return self.storage.build_definition(scene_key)

    def upload_one(
        self,
        *,
        filename: str,
        content: bytes,
        scene_key: str = "",
    ) -> dict[str, Any]:
        return self.storage.import_archive(
            filename=filename,
            content=content,
            scene_key=scene_key,
        )

    def create_archive(self, scene_key: str):
        return self.storage.create_archive(scene_key)

    def set_visibility(self, scene_key: str, visible: bool):
        return self.storage.set_visibility(scene_key, visible)

    def is_occupied(self, scene_key: str) -> bool:
        return bool(self._occupancy_checker(scene_key))

    def delete_one(self, scene_key: str):
        scene_key = self.storage.validate_scene_key(scene_key)
        if self.is_occupied(scene_key):
            raise RuntimeError(f"Scene '{scene_key}' is used by an active simulation")
        return self.storage.delete(scene_key)

    def upload_many(self, items: Iterable[dict[str, Any]]) -> SceneBatchResult:
        batch_id = self._batch_id("upload")
        results: list[SceneBatchItemResult] = []
        for item in items:
            scene_key = str(item.get("scene_key") or "").strip()
            filename = str(item.get("filename") or "").strip()
            pre_error = str(item.get("error") or "").strip()
            if pre_error:
                results.append(
                    SceneBatchItemResult(
                        operation="upload",
                        scene_key=scene_key or filename,
                        success=False,
                        status="failed",
                        error_code=str(item.get("error_code") or "invalid_upload"),
                        error=pre_error,
                    )
                )
                continue
            content = item.get("content")
            if not filename:
                results.append(
                    SceneBatchItemResult(
                        operation="upload",
                        scene_key=scene_key,
                        success=False,
                        status="failed",
                        error_code="missing_filename",
                        error="filename is required",
                    )
                )
                continue
            if not isinstance(content, (bytes, bytearray)):
                results.append(
                    SceneBatchItemResult(
                        operation="upload",
                        scene_key=scene_key or filename,
                        success=False,
                        status="failed",
                        error_code="invalid_content",
                        error="scene archive content must be bytes",
                    )
                )
                continue
            try:
                details = self.storage.import_archive(
                    filename=filename,
                    content=bytes(content),
                    scene_key=scene_key,
                )
                results.append(
                    SceneBatchItemResult(
                        operation="upload",
                        scene_key=str(details.get("scene_key") or scene_key),
                        success=True,
                        status="uploaded",
                        resource_id=str(details.get("resource_id") or ""),
                        title=str(details.get("title") or ""),
                        details={"visible": details.get("visible", True)},
                    )
                )
            except Exception as exc:
                results.append(
                    self._error_result("upload", scene_key or filename, exc)
                )
        return SceneBatchResult(
            operation="upload",
            batch_id=batch_id,
            items=results,
        )

    def download_many(self, scene_keys: Iterable[str]) -> SceneBatchResult:
        batch_id = self._batch_id("download")
        results: list[SceneBatchItemResult] = []
        resource_ids: list[str] = []
        archive_names: dict[str, str] = {}
        seen: set[str] = set()

        for raw_key in scene_keys:
            scene_key = str(raw_key or "").strip()
            if scene_key in seen:
                results.append(
                    SceneBatchItemResult(
                        operation="download",
                        scene_key=scene_key,
                        success=False,
                        status="failed",
                        error_code="duplicate_scene",
                        error="duplicate scene_key in batch",
                    )
                )
                continue
            seen.add(scene_key)
            try:
                resource = self.storage.get_resource(scene_key)
                resource_ids.append(resource.resource_id)
                archive_names[resource.resource_id] = scene_key
                results.append(
                    SceneBatchItemResult(
                        operation="download",
                        scene_key=scene_key,
                        success=True,
                        status="included",
                        resource_id=resource.resource_id,
                        title=scene_key,
                    )
                )
            except Exception as exc:
                results.append(self._error_result("download", scene_key, exc))

        archive_resource_id = ""
        archive_name = ""
        if resource_ids:
            archive_name = f"{batch_id}.zip"
            archive = self.files.create_archive(
                resource_ids,
                owner_type="scene_batch",
                owner_id=batch_id,
                root_name="archives",
                relative_path=f"scenes/batches/{archive_name}",
                logical_name=archive_name,
                resource_id=stable_resource_id("scene_batch", batch_id, "archive"),
                archive_names=archive_names,
                overwrite=True,
            )
            archive_resource_id = archive.resource_id

        return SceneBatchResult(
            operation="download",
            batch_id=batch_id,
            items=results,
            archive_resource_id=archive_resource_id,
            archive_name=archive_name,
        )

    def prepare_batch_download(self, resource_id: str):
        resource = self.files.get_resource(resource_id)
        if (
            resource.owner_type != "scene_batch"
            or resource.resource_type != "archive"
        ):
            raise ValueError("resource is not a scene batch archive")
        return self.files.prepare_download(resource_id)

    def delete_many(self, scene_keys: Iterable[str]) -> SceneBatchResult:
        batch_id = self._batch_id("delete")
        results: list[SceneBatchItemResult] = []
        seen: set[str] = set()

        for raw_key in scene_keys:
            scene_key = str(raw_key or "").strip()
            if scene_key in seen:
                results.append(
                    SceneBatchItemResult(
                        operation="delete",
                        scene_key=scene_key,
                        success=False,
                        status="failed",
                        error_code="duplicate_scene",
                        error="duplicate scene_key in batch",
                    )
                )
                continue
            seen.add(scene_key)
            try:
                normalized = self.storage.validate_scene_key(scene_key)
                if self.is_occupied(normalized):
                    results.append(
                        SceneBatchItemResult(
                            operation="delete",
                            scene_key=normalized,
                            success=False,
                            status="blocked",
                            error_code="scene_in_use",
                            error="scene is used by an active simulation",
                        )
                    )
                    continue
                resource = self.storage.delete(normalized)
                results.append(
                    SceneBatchItemResult(
                        operation="delete",
                        scene_key=normalized,
                        success=True,
                        status="deleted",
                        resource_id=resource.resource_id,
                    )
                )
            except Exception as exc:
                results.append(self._error_result("delete", scene_key, exc))

        return SceneBatchResult(
            operation="delete",
            batch_id=batch_id,
            items=results,
        )

    def parse_many(self, scene_keys: Iterable[str]) -> SceneBatchResult:
        batch_id = self._batch_id("parse")
        results: list[SceneBatchItemResult] = []
        seen: set[str] = set()

        for raw_key in scene_keys:
            scene_key = str(raw_key or "").strip()
            if scene_key in seen:
                results.append(
                    SceneBatchItemResult(
                        operation="parse",
                        scene_key=scene_key,
                        success=False,
                        status="failed",
                        error_code="duplicate_scene",
                        error="duplicate scene_key in batch",
                    )
                )
                continue
            seen.add(scene_key)
            try:
                definition = self.storage.build_definition(scene_key)
                resource = self.storage.get_resource(scene_key)
                results.append(
                    SceneBatchItemResult(
                        operation="parse",
                        scene_key=definition.scene_key,
                        success=True,
                        status="parsed",
                        resource_id=resource.resource_id,
                        title=definition.title,
                        details={"definition": self._definition_payload(definition)},
                    )
                )
            except Exception as exc:
                results.append(self._error_result("parse", scene_key, exc))

        return SceneBatchResult(
            operation="parse",
            batch_id=batch_id,
            items=results,
        )


_default_scene_manager: Optional[SceneManager] = None


def get_scene_manager() -> SceneManager:
    global _default_scene_manager
    if _default_scene_manager is None:
        _default_scene_manager = SceneManager()
    return _default_scene_manager
