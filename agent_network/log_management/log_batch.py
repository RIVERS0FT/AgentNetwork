from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from agent_network.file_management import (
    FileManagerError,
    ResourceNotFoundError,
    ResourceNotReadyError,
    stable_resource_id,
)


@dataclass
class LogBatchItemResult:
    operation: str
    session_id: str
    log_type: str
    success: bool
    status: str
    resource_id: str = ""
    error_code: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogBatchResult:
    operation: str
    batch_id: str
    items: list[LogBatchItemResult]
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


class LogBatchMixin:
    """Batch resource management mixed into the FileManager-backed LogManager."""

    @staticmethod
    def _log_batch_id(operation: str) -> str:
        return f"log-{operation}-{uuid.uuid4().hex}"

    @staticmethod
    def _log_batch_error(
        operation: str,
        session_id: str,
        log_type: str,
        exc: Exception,
    ) -> LogBatchItemResult:
        if isinstance(exc, ResourceNotFoundError) or isinstance(exc, FileNotFoundError):
            error_code = "log_not_found"
        elif isinstance(exc, ResourceNotReadyError):
            error_code = "log_not_ready"
        elif isinstance(exc, ValueError):
            error_code = "invalid_log_reference"
        elif isinstance(exc, (OSError, FileManagerError)):
            error_code = "storage_error"
        elif isinstance(exc, RuntimeError):
            error_code = "session_active"
        else:
            error_code = "log_operation_failed"
        return LogBatchItemResult(
            operation=operation,
            session_id=session_id,
            log_type=log_type,
            success=False,
            status="failed",
            error_code=error_code,
            error=str(exc),
        )

    @staticmethod
    def _normalize_ref(item: dict[str, Any]) -> tuple[str, str]:
        from agent_network.log_management import log_manager as legacy

        session_id = str(item.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        log_type = legacy.normalize_log_type(str(item.get("log_type") or ""))
        return session_id, log_type

    def _session_directory_resource(self, session_id: str):
        manager = self._ensure_file_manager()
        directory = manager.find_resource(
            owner_type="log_session",
            owner_id=session_id,
            resource_type="log_session_directory",
            include_deleted=False,
        )
        if directory is None:
            self._resolve_session_dir(session_id)
            directory = manager.ensure_directory(
                owner_type="log_session",
                owner_id=session_id,
                resource_type="log_session_directory",
                root_name="logs",
                relative_path=session_id,
                logical_name=session_id,
                resource_id=self._session_resource_id(session_id),
            )
        return directory

    def _assert_log_readable(
        self,
        session_id: str,
        log_type: str,
        *,
        allow_hidden: bool = False,
    ):
        directory = self._session_directory_resource(session_id)
        resource = self._ensure_log_resource(session_id, log_type)
        if not allow_hidden and not directory.visible:
            raise ResourceNotReadyError(
                f"log session '{session_id}' is hidden"
            )
        if not allow_hidden and not resource.visible:
            raise ResourceNotReadyError(
                f"log '{session_id}/{log_type}' is hidden"
            )
        return resource

    def _write_file(self, log_type: str, record: dict[str, Any]):
        """Persist JSONL while inheriting the parent session visibility."""
        if not self._session_active:
            return
        from agent_network.log_management import log_manager as legacy

        manager = self._ensure_file_manager()
        filename = legacy.LOG_TYPE_TO_FILENAME[log_type]
        try:
            directory = self._session_directory_resource(self._session_id)
            manager.append_or_create_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                owner_type="log_session",
                owner_id=self._session_id,
                resource_type=f"{log_type}_log",
                root_name="logs",
                relative_path=f"{self._session_id}/{filename}",
                logical_name=filename,
                media_type="application/x-ndjson",
                visible=directory.visible,
                resource_id=self._log_resource_id(
                    self._session_id, log_type
                ),
            )
        except Exception as exc:
            print(
                f"[LogManager] managed write failed "
                f"{self._session_id}/{filename}: {exc}",
                file=sys.stderr,
            )

    def get_download_descriptor(self, session_id: str, log_type: str):
        from agent_network.log_management import log_manager as legacy

        normalized_type = legacy.normalize_log_type(log_type)
        resource = self._assert_log_readable(
            session_id, normalized_type, allow_hidden=False
        )
        return self._ensure_file_manager().prepare_download(
            resource.resource_id
        )

    def list_log_files(self, include_hidden: bool = False):
        from agent_network.log_management import log_manager as legacy

        manager = self._ensure_file_manager()
        resources = manager.list_resources(
            owner_type="log_session",
            include_hidden=True,
        )
        reverse_types = {
            f"{log_type}_log": log_type
            for log_type in legacy.LOG_TYPE_TO_FILENAME
        }
        session_visibility = {
            resource.owner_id: resource.visible
            for resource in resources
            if resource.resource_type == "log_session_directory"
        }
        sessions: dict[str, list[dict[str, Any]]] = {}
        for resource in resources:
            log_type = reverse_types.get(resource.resource_type)
            if not log_type:
                continue
            session_visible = session_visibility.get(
                resource.owner_id, True
            )
            sessions.setdefault(resource.owner_id, []).append(
                {
                    "type": log_type,
                    "name": resource.logical_name,
                    "size_bytes": resource.size_bytes,
                    "updated_at": resource.updated_at,
                    "visible": resource.visible,
                    "effective_visible": bool(
                        session_visible and resource.visible
                    ),
                    "resource_id": resource.resource_id,
                }
            )

        result = []
        for session_id, files in sorted(sessions.items(), reverse=True):
            session_visible = session_visibility.get(session_id, True)
            selected = (
                files
                if include_hidden
                else [
                    item
                    for item in files
                    if session_visible and item["visible"]
                ]
            )
            if not selected:
                continue
            result.append(
                {
                    "session": session_id,
                    "visible": session_visible,
                    "files": sorted(selected, key=lambda item: item["type"]),
                }
            )
        return result

    def batch_download_logs(self, items: Iterable[dict[str, Any]]) -> LogBatchResult:
        batch_id = self._log_batch_id("download")
        results: list[LogBatchItemResult] = []
        resource_ids: list[str] = []
        archive_names: dict[str, str] = {}
        seen: set[tuple[str, str]] = set()

        for raw in items:
            session_id = str(raw.get("session_id") or "").strip()
            log_type = str(raw.get("log_type") or "").strip()
            try:
                session_id, log_type = self._normalize_ref(raw)
                key = (session_id, log_type)
                if key in seen:
                    raise ValueError(f"duplicate log reference: {session_id}/{log_type}")
                seen.add(key)
                resource = self._assert_log_readable(
                    session_id, log_type, allow_hidden=False
                )
                resource_ids.append(resource.resource_id)
                archive_names[resource.resource_id] = (
                    f"{session_id}/{resource.logical_name}"
                )
                results.append(
                    LogBatchItemResult(
                        operation="download",
                        session_id=session_id,
                        log_type=log_type,
                        success=True,
                        status="included",
                        resource_id=resource.resource_id,
                        details={
                            "filename": resource.logical_name,
                            "size_bytes": resource.size_bytes,
                            "sha256": resource.sha256,
                        },
                    )
                )
            except Exception as exc:
                results.append(
                    self._log_batch_error(
                        "download", session_id, log_type, exc
                    )
                )

        archive_resource_id = ""
        archive_name = ""
        if resource_ids:
            archive_name = f"{batch_id}.zip"
            manifest = {
                "schema_version": "log-batch.v1",
                "operation": "download",
                "batch_id": batch_id,
                "items": [item.to_dict() for item in results],
            }
            archive = self._ensure_file_manager().create_archive(
                resource_ids,
                owner_type="log_batch",
                owner_id=batch_id,
                root_name="archives",
                relative_path=f"logs/{archive_name}",
                logical_name=archive_name,
                resource_id=stable_resource_id(
                    "log_batch", batch_id, "archive"
                ),
                archive_names=archive_names,
                virtual_files={
                    "LOG_BATCH_MANIFEST.json": json.dumps(
                        manifest, ensure_ascii=False, indent=2
                    )
                },
                overwrite=True,
            )
            archive_resource_id = archive.resource_id

        return LogBatchResult(
            operation="download",
            batch_id=batch_id,
            items=results,
            archive_resource_id=archive_resource_id,
            archive_name=archive_name,
        )

    def prepare_log_batch_download(self, resource_id: str):
        resource = self._ensure_file_manager().get_resource(resource_id)
        if resource.owner_type != "log_batch" or resource.resource_type != "archive":
            raise ValueError("resource is not a managed log batch archive")
        return self._ensure_file_manager().prepare_download(resource_id)

    def batch_delete_logs(self, items: Iterable[dict[str, Any]]) -> LogBatchResult:
        batch_id = self._log_batch_id("delete")
        results: list[LogBatchItemResult] = []
        seen: set[tuple[str, str]] = set()

        for raw in items:
            session_id = str(raw.get("session_id") or "").strip()
            log_type = str(raw.get("log_type") or "").strip()
            try:
                session_id, log_type = self._normalize_ref(raw)
                key = (session_id, log_type)
                if key in seen:
                    raise ValueError(f"duplicate log reference: {session_id}/{log_type}")
                seen.add(key)
                if self._session_active and self._session_id == session_id:
                    raise RuntimeError(
                        f"log session '{session_id}' is active"
                    )
                resource = self._ensure_log_resource(session_id, log_type)
                deleted = self._ensure_file_manager().delete(
                    [resource.resource_id]
                )[0]
                results.append(
                    LogBatchItemResult(
                        operation="delete",
                        session_id=session_id,
                        log_type=log_type,
                        success=True,
                        status="deleted",
                        resource_id=deleted.resource_id,
                        details={"filename": deleted.logical_name},
                    )
                )
            except Exception as exc:
                results.append(
                    self._log_batch_error("delete", session_id, log_type, exc)
                )

        return LogBatchResult(
            operation="delete", batch_id=batch_id, items=results
        )

    def batch_parse_logs(
        self,
        items: Iterable[dict[str, Any]],
        *,
        allow_hidden: bool = False,
        max_errors_per_file: int = 100,
    ) -> LogBatchResult:
        from agent_network.log_management import log_manager as legacy

        batch_id = self._log_batch_id("parse")
        results: list[LogBatchItemResult] = []
        seen: set[tuple[str, str]] = set()

        for raw in items:
            session_id = str(raw.get("session_id") or "").strip()
            log_type = str(raw.get("log_type") or "").strip()
            try:
                session_id, log_type = self._normalize_ref(raw)
                key = (session_id, log_type)
                if key in seen:
                    raise ValueError(f"duplicate log reference: {session_id}/{log_type}")
                seen.add(key)
                resource = self._assert_log_readable(
                    session_id, log_type, allow_hidden=allow_hidden
                )
                text = self._ensure_file_manager().read_text(
                    resource.resource_id,
                    allow_hidden=allow_hidden,
                )
                valid_records = 0
                invalid_records = 0
                errors: list[dict[str, Any]] = []
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise ValueError("log record must be a JSON object")
                        inferred = legacy.infer_log_type(record)
                        if inferred != log_type:
                            raise ValueError(
                                f"record type '{inferred}' does not match '{log_type}'"
                            )
                        event = str(
                            record.get("event") or f"{log_type}_event"
                        )
                        source = dict(record)
                        if log_type == "network":
                            source.pop("event", None)
                            source["timestamp"] = legacy.normalize_packet_timestamp(
                                source.get("timestamp", "")
                            )
                            source["log_id"] = str(
                                source.get("log_id")
                                or f"net_{uuid.uuid4().hex[:12]}"
                            )
                        else:
                            source["timestamp"] = legacy.normalize_log_timestamp(
                                source.get("timestamp", "")
                            )
                        legacy._normalize_record_with_schema(
                            source,
                            legacy.LOG_SCHEMAS[log_type],
                            event,
                        )
                        valid_records += 1
                    except Exception as exc:
                        invalid_records += 1
                        if len(errors) < max_errors_per_file:
                            errors.append(
                                {
                                    "line": line_number,
                                    "error": str(exc),
                                }
                            )
                results.append(
                    LogBatchItemResult(
                        operation="parse",
                        session_id=session_id,
                        log_type=log_type,
                        success=invalid_records == 0,
                        status=(
                            "parsed"
                            if invalid_records == 0
                            else "parsed_with_errors"
                        ),
                        resource_id=resource.resource_id,
                        error_code=(
                            ""
                            if invalid_records == 0
                            else "invalid_log_records"
                        ),
                        error=(
                            ""
                            if invalid_records == 0
                            else f"{invalid_records} invalid records"
                        ),
                        details={
                            "valid_records": valid_records,
                            "invalid_records": invalid_records,
                            "errors": errors,
                        },
                    )
                )
            except Exception as exc:
                results.append(
                    self._log_batch_error("parse", session_id, log_type, exc)
                )

        return LogBatchResult(
            operation="parse", batch_id=batch_id, items=results
        )

    def batch_set_log_visibility(
        self,
        items: Iterable[dict[str, Any]],
        visible: bool,
    ) -> LogBatchResult:
        operation = "show" if visible else "hide"
        batch_id = self._log_batch_id(operation)
        results: list[LogBatchItemResult] = []
        seen: set[tuple[str, str]] = set()

        for raw in items:
            session_id = str(raw.get("session_id") or "").strip()
            log_type = str(raw.get("log_type") or "").strip()
            try:
                session_id, log_type = self._normalize_ref(raw)
                key = (session_id, log_type)
                if key in seen:
                    raise ValueError(f"duplicate log reference: {session_id}/{log_type}")
                seen.add(key)
                updated = self.set_log_visibility(
                    session_id, log_type, visible
                )
                results.append(
                    LogBatchItemResult(
                        operation=operation,
                        session_id=session_id,
                        log_type=log_type,
                        success=True,
                        status="visible" if visible else "hidden",
                        resource_id=str(updated.get("resource_id") or ""),
                        details={
                            "filename": updated.get("filename", ""),
                            "visible": bool(updated.get("visible")),
                        },
                    )
                )
            except Exception as exc:
                results.append(
                    self._log_batch_error(
                        operation, session_id, log_type, exc
                    )
                )

        return LogBatchResult(
            operation=operation, batch_id=batch_id, items=results
        )

    def set_session_log_visibility(
        self,
        session_id: str,
        visible: bool,
    ) -> dict[str, Any]:
        from agent_network.log_management import log_manager as legacy

        session_id = str(session_id or "").strip()
        self._resolve_session_dir(session_id)
        manager = self._ensure_file_manager()

        resources = []
        for log_type in legacy.LOG_TYPE_TO_FILENAME:
            try:
                resources.append(
                    self._ensure_log_resource(session_id, log_type)
                )
            except FileNotFoundError:
                continue

        directory = self._session_directory_resource(session_id)
        resources.append(directory)

        updated = manager.set_visibility(
            [resource.resource_id for resource in resources], visible
        )
        files = [
            {
                "resource_id": resource.resource_id,
                "filename": resource.logical_name,
                "visible": resource.visible,
            }
            for resource in updated
            if resource.resource_type.endswith("_log")
        ]
        return {
            "session_id": session_id,
            "visible": bool(visible),
            "files": files,
        }

    def batch_set_session_log_visibility(
        self,
        session_ids: Iterable[str],
        visible: bool,
    ) -> LogBatchResult:
        operation = "show_session" if visible else "hide_session"
        batch_id = self._log_batch_id(operation)
        results: list[LogBatchItemResult] = []
        seen: set[str] = set()

        for raw_session_id in session_ids:
            session_id = str(raw_session_id or "").strip()
            try:
                if not session_id:
                    raise ValueError("session_id is required")
                if session_id in seen:
                    raise ValueError(f"duplicate session_id: {session_id}")
                seen.add(session_id)
                value = self.set_session_log_visibility(
                    session_id, visible
                )
                results.append(
                    LogBatchItemResult(
                        operation=operation,
                        session_id=session_id,
                        log_type="*",
                        success=True,
                        status="visible" if visible else "hidden",
                        details=value,
                    )
                )
            except Exception as exc:
                results.append(
                    self._log_batch_error(
                        operation, session_id, "*", exc
                    )
                )

        return LogBatchResult(
            operation=operation, batch_id=batch_id, items=results
        )

    def batch_delete_log_sessions(
        self,
        session_ids: Iterable[str],
    ) -> LogBatchResult:
        operation = "delete_session"
        batch_id = self._log_batch_id(operation)
        results: list[LogBatchItemResult] = []
        seen: set[str] = set()

        for raw_session_id in session_ids:
            session_id = str(raw_session_id or "").strip()
            try:
                if not session_id:
                    raise ValueError("session_id is required")
                if session_id in seen:
                    raise ValueError(f"duplicate session_id: {session_id}")
                seen.add(session_id)
                if self._session_active and self._session_id == session_id:
                    raise RuntimeError(
                        f"log session '{session_id}' is active"
                    )
                value = self.delete_session_logs(session_id)
                results.append(
                    LogBatchItemResult(
                        operation=operation,
                        session_id=session_id,
                        log_type="*",
                        success=True,
                        status="deleted",
                        details=value,
                    )
                )
            except Exception as exc:
                results.append(
                    self._log_batch_error(
                        operation, session_id, "*", exc
                    )
                )

        return LogBatchResult(
            operation=operation, batch_id=batch_id, items=results
        )


_LOG_BATCH_METHODS = (
    "_log_batch_id",
    "_log_batch_error",
    "_normalize_ref",
    "_session_directory_resource",
    "_assert_log_readable",
    "_write_file",
    "get_download_descriptor",
    "list_log_files",
    "batch_download_logs",
    "prepare_log_batch_download",
    "batch_delete_logs",
    "batch_parse_logs",
    "batch_set_log_visibility",
    "set_session_log_visibility",
    "batch_set_session_log_visibility",
    "batch_delete_log_sessions",
)


def install_log_batch_manager() -> None:
    """Attach batch resource management to the active unified LogManager class."""
    from agent_network.log_management import log_manager as legacy

    if getattr(legacy, "_LOG_BATCH_MANAGER_INSTALLED", False):
        return
    for name in _LOG_BATCH_METHODS:
        setattr(legacy.LogManager, name, getattr(LogBatchMixin, name))
    active = getattr(legacy, "_log_manager", None)
    if active is not None and not isinstance(active, legacy.LogManager):
        for name in _LOG_BATCH_METHODS:
            setattr(type(active), name, getattr(LogBatchMixin, name))
    legacy._LOG_BATCH_MANAGER_INSTALLED = True
