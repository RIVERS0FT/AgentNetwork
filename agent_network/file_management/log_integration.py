from __future__ import annotations
import json
import os
import sys
import threading
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, IO, Optional
from .defaults import get_file_manager
from .file_manager import FileManager, stable_resource_id

def _build_manager_for_log_dir(log_dir: str) -> FileManager:
    default = get_file_manager()
    requested = Path(log_dir).resolve()
    if default.root_path('logs') == requested:
        return default
    data_root = requested.parent
    roots = {'scenes': os.environ.get('SCENE_DIR', './scenes'), 'logs': requested, 'pcap': os.environ.get('PCAP_DIR', str(data_root / 'pcap')), 'archives': os.environ.get('ARCHIVE_DIR', str(data_root / 'archives')), 'temp': os.environ.get('FILE_TEMP_DIR', str(data_root / 'tmp'))}
    return FileManager(roots, catalog_path=requested / '.file_registry.json')

def install_log_manager() -> None:
    """Replace the legacy file-owning LogManager with a FileManager-backed class."""
    from agent_network.log_management import log_manager as legacy
    if getattr(legacy, '_UNIFIED_FILE_MANAGER_INSTALLED', False):
        return
    BaseLogManager = legacy.LogManager

    class UnifiedLogManager(BaseLogManager):
        _instance = None
        _instance_lock = threading.Lock()

        def __init__(self, name='', max_entries=2000, log_dir=''):
            super().__init__(name=name, max_entries=max_entries, log_dir=log_dir)
            self._managed_log_root = ''
            self._file_manager: Optional[FileManager] = None
            self._ensure_file_manager()

        def _ensure_file_manager(self) -> FileManager:
            resolved = str(Path(self._log_dir).resolve())
            if self._file_manager is None or self._managed_log_root != resolved:
                self._file_manager = _build_manager_for_log_dir(self._log_dir)
                self._managed_log_root = resolved
            return self._file_manager

        @staticmethod
        def _log_resource_id(session_id: str, log_type: str) -> str:
            return stable_resource_id('log', session_id, log_type)

        @staticmethod
        def _session_resource_id(session_id: str) -> str:
            return stable_resource_id('log_session', session_id, 'directory')

        def start_session(self, scene_name: str) -> str:
            self._close_file_handles()
            safe = scene_name.replace('/', '_').replace('\\', '_').replace(' ', '_')
            self._session_id = f"{safe}_{datetime.now(legacy._LOG_TZ).strftime('%Y%m%d_%H%M%S_%f')}"
            manager = self._ensure_file_manager()
            manager.ensure_directory(owner_type='log_session', owner_id=self._session_id, resource_type='log_session_directory', root_name='logs', relative_path=self._session_id, logical_name=self._session_id, resource_id=self._session_resource_id(self._session_id))
            self._session_dir = str(manager.resolve_path('logs', self._session_id))
            self._set_session_paths()
            self._session_active = True
            self.emit_system_event('session_start', f'Session started: {scene_name}', kind='lifecycle', payload={'scene_name': scene_name, 'session_id': self._session_id})
            return self._session_id

        def set_session_dir(self, session_dir: str):
            manager = self._ensure_file_manager()
            resolved = Path(session_dir).resolve()
            root = manager.root_path('logs')
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError('session directory must be inside log_dir') from exc
            if not relative or '/' in relative:
                raise ValueError('session directory must be a direct child of log_dir')
            manager.ensure_directory(owner_type='log_session', owner_id=relative, resource_type='log_session_directory', root_name='logs', relative_path=relative, logical_name=relative, resource_id=self._session_resource_id(relative))
            self._session_dir = str(resolved)
            self._session_id = relative
            self._set_session_paths()
            self._session_active = True

        def _get_file_handle(self, path: str) -> IO:
            raise RuntimeError('direct log file handles are disabled; use FileManager')

        def _close_file_handle(self, path: str):
            self._file_handles.pop(path, None)

        def _close_file_handles(self):
            self._file_handles.clear()

        def _write_file(self, log_type: str, record: Dict[str, Any]):
            if not self._session_active:
                return
            manager = self._ensure_file_manager()
            filename = legacy.LOG_TYPE_TO_FILENAME[log_type]
            try:
                manager.append_or_create_text(json.dumps(record, ensure_ascii=False) + '\n', owner_type='log_session', owner_id=self._session_id, resource_type=f'{log_type}_log', root_name='logs', relative_path=f'{self._session_id}/{filename}', logical_name=filename, media_type='application/x-ndjson', resource_id=self._log_resource_id(self._session_id, log_type))
            except Exception as exc:
                print(f'[LogManager] managed write failed {self._session_id}/{filename}: {exc}', file=sys.stderr)

        def export_file(self, filepath, fmt='jsonl', limit=0, log_type=None):
            manager = self._ensure_file_manager()
            path = Path(filepath)
            suffix = path.suffix.lstrip('.') or fmt
            resource = manager.write_text(self.export(fmt, limit, log_type=log_type), owner_type='log_export', owner_id=self._session_id or 'adhoc', resource_type='log_export', root_name='temp', relative_path=f'exports/{uuid.uuid4().hex}/{path.name}', logical_name=path.name, media_type={'jsonl': 'application/x-ndjson', 'json': 'application/json', 'csv': 'text/csv'}.get(suffix, 'text/plain'))
            return str(manager.resolve_resource_path(resource.resource_id))

        def _resolve_session_dir(self, session_id, require_exists=True):
            if not session_id or session_id in {'.', '..'} or Path(session_id).name != session_id:
                raise ValueError('invalid session_id')
            manager = self._ensure_file_manager()
            session = manager.resolve_path('logs', session_id)
            if require_exists and not session.is_dir():
                raise FileNotFoundError(f"log session '{session_id}' not found")
            return session

        def _ensure_log_resource(self, session_id: str, log_type: str):
            manager = self._ensure_file_manager()
            filename = legacy.LOG_TYPE_TO_FILENAME[log_type]
            resource_id = self._log_resource_id(session_id, log_type)
            resource = manager.find_resource(include_deleted=False, root_name='logs', relative_path=f'{session_id}/{filename}')
            if resource is None:
                path = manager.resolve_path('logs', f'{session_id}/{filename}')
                if not path.is_file():
                    raise FileNotFoundError(f"{filename} not found in session '{session_id}'")
                resource = manager.register_existing(owner_type='log_session', owner_id=session_id, resource_type=f'{log_type}_log', root_name='logs', relative_path=f'{session_id}/{filename}', logical_name=filename, media_type='application/x-ndjson', resource_id=resource_id, upsert=True)
            return resource

        def resolve_log_path(self, session_id, log_type, require_exists=True):
            normalized_type = legacy.normalize_log_type(log_type)
            self._resolve_session_dir(session_id, require_exists=require_exists)
            if not require_exists:
                return str(self._ensure_file_manager().resolve_path('logs', f'{session_id}/{legacy.LOG_TYPE_TO_FILENAME[normalized_type]}'))
            resource = self._ensure_log_resource(session_id, normalized_type)
            return str(self._ensure_file_manager().resolve_resource_path(resource.resource_id, allow_hidden=True, allow_directory=False))

        def get_download_descriptor(self, session_id: str, log_type: str):
            normalized_type = legacy.normalize_log_type(log_type)
            resource = self._ensure_log_resource(session_id, normalized_type)
            return self._ensure_file_manager().prepare_download(resource.resource_id)

        def get_download_path(self, session_id, log_type):
            return self.get_download_descriptor(session_id, log_type).internal_path

        def set_log_visibility(self, session_id, log_type, visible):
            normalized_type = legacy.normalize_log_type(log_type)
            resource = self._ensure_log_resource(session_id, normalized_type)
            updated = self._ensure_file_manager().set_visibility([resource.resource_id], bool(visible))[0]
            return {'session': session_id, 'log_type': normalized_type, 'filename': updated.logical_name, 'visible': updated.visible, 'resource_id': updated.resource_id}

        def hide_log(self, session_id, log_type):
            return self.set_log_visibility(session_id, log_type, False)

        def show_log(self, session_id, log_type):
            return self.set_log_visibility(session_id, log_type, True)

        def list_log_files(self, include_hidden=False):
            manager = self._ensure_file_manager()
            resources = manager.list_resources(owner_type='log_session', include_hidden=include_hidden)
            sessions: Dict[str, list] = {}
            reverse_types = {f'{log_type}_log': log_type for log_type in legacy.LOG_TYPE_TO_FILENAME}
            for resource in resources:
                log_type = reverse_types.get(resource.resource_type)
                if not log_type:
                    continue
                sessions.setdefault(resource.owner_id, []).append({'type': log_type, 'name': resource.logical_name, 'size_bytes': resource.size_bytes, 'updated_at': resource.updated_at, 'visible': resource.visible, 'resource_id': resource.resource_id})
            return [{'session': session_id, 'files': sorted(files, key=lambda item: item['type'])} for session_id, files in sorted(sessions.items(), reverse=True) if files]

        def read_session_records(self, session_id: str, log_type: str) -> list[dict]:
            resource = self._ensure_log_resource(session_id, legacy.normalize_log_type(log_type))
            text = self._ensure_file_manager().read_text(resource.resource_id, allow_hidden=True)
            records = []
            for line in text.splitlines():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
            return records

        def delete_log(self, session_id, log_type):
            normalized_type = legacy.normalize_log_type(log_type)
            resource = self._ensure_log_resource(session_id, normalized_type)
            self._ensure_file_manager().delete([resource.resource_id])
            if session_id == self._session_id:
                with self._entry_lock:
                    self._entries = deque((entry for entry in self._entries if entry.get(legacy._INTERNAL_LOG_TYPE) != normalized_type), maxlen=self._max)
                    self._rebuild_stats()
            return {'session': session_id, 'log_type': normalized_type, 'filename': legacy.LOG_TYPE_TO_FILENAME[normalized_type], 'resource_id': resource.resource_id, 'deleted': True}

        def delete_session_logs(self, session_id):
            self._resolve_session_dir(session_id)
            manager = self._ensure_file_manager()
            resources = [item for item in manager.list_resources(owner_type='log_session', owner_id=session_id, include_hidden=True) if item.resource_type.endswith('_log')]
            deleted_names = [item.logical_name for item in resources]
            if resources:
                manager.delete([item.resource_id for item in resources])
            directory = manager.find_resource(owner_type='log_session', owner_id=session_id, resource_type='log_session_directory', include_deleted=False)
            if directory:
                manager.delete([directory.resource_id])
            if session_id == self._session_id:
                with self._entry_lock:
                    self._entries.clear()
                    self._stats = self._new_stats()
            return {'session': session_id, 'deleted_files': deleted_names, 'deleted': bool(deleted_names)}

        def reset(self):
            result = super().reset()
            self._file_manager = None
            self._managed_log_root = ''
            return result
    legacy.LogManager = UnifiedLogManager
    legacy._log_manager = UnifiedLogManager('AgentNetwork')
    legacy._UNIFIED_FILE_MANAGER_INSTALLED = True
