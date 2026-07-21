from __future__ import annotations
import json
import mimetypes
import os
import uuid
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, Iterable, List
from .base import FileManagerError, ResourceNotFoundError, ResourceNotReadyError, UnsafePathError, _sha256_file, _utc_now, stable_resource_id
from .models import DownloadDescriptor, FileResource

class ManagedIOMixin:

    def ensure_directory(self, *, owner_type: str, owner_id: str, resource_type: str, root_name: str, relative_path: str, logical_name: str='', visible: bool=True, resource_id: str='') -> FileResource:
        path = self.resolve_path(root_name, relative_path)
        path.mkdir(parents=True, exist_ok=True)
        return self.register_existing(owner_type=owner_type, owner_id=owner_id, resource_type=resource_type, root_name=root_name, relative_path=relative_path, logical_name=logical_name or path.name, media_type='inode/directory', visible=visible, resource_id=resource_id, upsert=True)

    def write_bytes(self, data: bytes, *, owner_type: str, owner_id: str, resource_type: str, root_name: str, relative_path: str, logical_name: str='', media_type: str='application/octet-stream', visible: bool=True, resource_id: str='', overwrite: bool=False) -> FileResource:
        path = self.resolve_path(root_name, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite:
            raise FileExistsError(str(path))
        temp_path = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        try:
            temp_path.write_bytes(data)
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return self._upsert_written_resource(path=path, owner_type=owner_type, owner_id=owner_id, resource_type=resource_type, root_name=root_name, relative_path=relative_path, logical_name=logical_name, media_type=media_type, visible=visible, resource_id=resource_id)

    def write_text(self, text: str, *, encoding: str='utf-8', **kwargs) -> FileResource:
        return self.write_bytes(text.encode(encoding), **kwargs)

    def write_json(self, value: object, **kwargs) -> FileResource:
        return self.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), media_type='application/json', **kwargs)

    def write_stream(self, stream: BinaryIO, *, chunk_size: int=1024 * 1024, **kwargs) -> FileResource:
        path = self.resolve_path(kwargs['root_name'], kwargs['relative_path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not kwargs.get('overwrite', False):
            raise FileExistsError(str(path))
        temp_path = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        try:
            with temp_path.open('wb') as target:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    target.write(chunk)
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return self._upsert_written_resource(path=path, owner_type=kwargs['owner_type'], owner_id=kwargs['owner_id'], resource_type=kwargs['resource_type'], root_name=kwargs['root_name'], relative_path=kwargs['relative_path'], logical_name=kwargs.get('logical_name', ''), media_type=kwargs.get('media_type', 'application/octet-stream'), visible=kwargs.get('visible', True), resource_id=kwargs.get('resource_id', ''))

    def _upsert_written_resource(self, *, path: Path, owner_type: str, owner_id: str, resource_type: str, root_name: str, relative_path: str, logical_name: str, media_type: str, visible: bool, resource_id: str) -> FileResource:
        resolved_id = resource_id or stable_resource_id(owner_type, owner_id, resource_type, Path(relative_path).as_posix())
        now = _utc_now()
        with self._catalog_guard(write=True):
            existing = self._resources.get(resolved_id)
            resource = FileResource(resource_id=resolved_id, owner_type=owner_type, owner_id=owner_id, resource_type=resource_type, root_name=root_name, relative_path=Path(relative_path).as_posix(), logical_name=logical_name or path.name, media_type=media_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream', visible=bool(visible), state='ready', size_bytes=path.stat().st_size, sha256=_sha256_file(path), created_at=existing.created_at if existing else now, updated_at=now)
            self._resources[resolved_id] = resource
            return resource

    def append_text(self, resource_id: str, text: str, *, encoding: str='utf-8') -> FileResource:
        with self._catalog_guard(write=True):
            resource = self._resource_unlocked(resource_id)
            if resource.state != 'ready':
                raise ResourceNotReadyError(resource_id)
            path = self.resolve_path(resource.root_name, resource.relative_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding=encoding, newline='') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            updated = replace(resource, size_bytes=path.stat().st_size, sha256=_sha256_file(path), updated_at=_utc_now(), error_message='')
            self._resources[resource_id] = updated
            return updated

    def append_or_create_text(self, text: str, *, owner_type: str, owner_id: str, resource_type: str, root_name: str, relative_path: str, logical_name: str='', media_type: str='text/plain', visible: bool=True, resource_id: str='', encoding: str='utf-8') -> FileResource:
        resolved_id = resource_id or stable_resource_id(owner_type, owner_id, resource_type, Path(relative_path).as_posix())
        existing = self.find_resource(include_deleted=True, root_name=root_name, relative_path=relative_path)
        if existing and existing.state != 'deleted':
            return self.append_text(existing.resource_id, text, encoding=encoding)
        path = self.resolve_path(root_name, relative_path)
        if path.exists():
            registered = self.register_existing(owner_type=owner_type, owner_id=owner_id, resource_type=resource_type, root_name=root_name, relative_path=relative_path, logical_name=logical_name, media_type=media_type, visible=visible, resource_id=resolved_id, upsert=True)
            return self.append_text(registered.resource_id, text, encoding=encoding)
        return self.write_text(text, encoding=encoding, owner_type=owner_type, owner_id=owner_id, resource_type=resource_type, root_name=root_name, relative_path=relative_path, logical_name=logical_name, media_type=media_type, visible=visible, resource_id=resolved_id, overwrite=False)

    def resolve_resource_path(self, resource_id: str, *, allow_hidden: bool=False, allow_directory: bool=True, verify_hash: bool=False) -> Path:
        resource = self._assert_readable(resource_id, allow_hidden=allow_hidden)
        path = self.resolve_path(resource.root_name, resource.relative_path)
        if not allow_directory and not path.is_file():
            raise ResourceNotReadyError(f"resource '{resource_id}' is not a file")
        if verify_hash and path.is_file() and resource.sha256 and _sha256_file(path) != resource.sha256:
            raise FileManagerError(f"resource '{resource_id}' failed SHA-256 validation")
        return path

    def read_bytes(self, resource_id: str, *, allow_hidden: bool=False) -> bytes:
        return self.resolve_resource_path(resource_id, allow_hidden=allow_hidden, allow_directory=False).read_bytes()

    def read_text(self, resource_id: str, *, encoding: str='utf-8', allow_hidden: bool=False) -> str:
        return self.read_bytes(resource_id, allow_hidden=allow_hidden).decode(encoding)

    def read_json(self, resource_id: str, *, allow_hidden: bool=False):
        return json.loads(self.read_text(resource_id, allow_hidden=allow_hidden))

    def resolve_child_path(self, directory_resource_id: str, child_relative_path: str, *, allow_hidden: bool=False, expected_kind: str='file') -> Path:
        directory = self.resolve_resource_path(directory_resource_id, allow_hidden=allow_hidden, allow_directory=True)
        if not directory.is_dir():
            raise ResourceNotReadyError('parent resource is not a directory')
        child = Path(child_relative_path)
        if child.is_absolute() or '..' in child.parts:
            raise UnsafePathError('child path is unsafe')
        target = (directory / child).resolve()
        if target != directory and directory not in target.parents:
            raise UnsafePathError('child path escapes directory resource')
        if target.is_symlink():
            raise UnsafePathError('symbolic links are not managed children')
        if expected_kind == 'file' and not target.is_file():
            raise ResourceNotFoundError(f'child file not found: {child_relative_path}')
        if expected_kind == 'directory' and not target.is_dir():
            raise ResourceNotFoundError(f'child directory not found: {child_relative_path}')
        if expected_kind not in {'file', 'directory', 'any'}:
            raise ValueError('expected_kind must be file, directory or any')
        if expected_kind == 'any' and not target.exists():
            raise ResourceNotFoundError(f'child path not found: {child_relative_path}')
        return target

    def read_child_bytes(self, directory_resource_id: str, child_relative_path: str, *, allow_hidden: bool=False) -> bytes:
        return self.resolve_child_path(directory_resource_id, child_relative_path, allow_hidden=allow_hidden, expected_kind='file').read_bytes()

    def read_child_text(self, directory_resource_id: str, child_relative_path: str, *, encoding: str='utf-8', allow_hidden: bool=False) -> str:
        return self.read_child_bytes(directory_resource_id, child_relative_path, allow_hidden=allow_hidden).decode(encoding)

    def read_child_json(self, directory_resource_id: str, child_relative_path: str, *, allow_hidden: bool=False):
        return json.loads(self.read_child_text(directory_resource_id, child_relative_path, allow_hidden=allow_hidden))

    def list_children(self, directory_resource_id: str, relative_dir: str='', *, recursive: bool=True, files_only: bool=True, allow_hidden: bool=False) -> List[str]:
        root = self.resolve_resource_path(directory_resource_id, allow_hidden=allow_hidden, allow_directory=True)
        if not root.is_dir():
            raise ResourceNotReadyError('parent resource is not a directory')
        requested = Path(relative_dir) if relative_dir else Path('.')
        if requested.is_absolute() or '..' in requested.parts:
            raise UnsafePathError('child directory is unsafe')
        base = (root / requested).resolve()
        if base != root and root not in base.parents:
            raise UnsafePathError('child directory escapes directory resource')
        if not base.is_dir():
            raise ResourceNotFoundError(f'child directory not found: {relative_dir}')
        iterator = base.rglob('*') if recursive else base.iterdir()
        result = []
        for child in sorted(iterator):
            if child.is_symlink():
                continue
            if files_only and not child.is_file():
                continue
            result.append(child.relative_to(root).as_posix())
        return result

    def child_kind(self, directory_resource_id: str, child_relative_path: str, *, allow_hidden: bool=False) -> str:
        root = self.resolve_resource_path(directory_resource_id, allow_hidden=allow_hidden, allow_directory=True)
        child = Path(child_relative_path)
        if child.is_absolute() or '..' in child.parts:
            raise UnsafePathError('child path is unsafe')
        target = (root / child).resolve()
        if target != root and root not in target.parents:
            raise UnsafePathError('child path escapes directory resource')
        if target.is_symlink():
            raise UnsafePathError('symbolic links are not managed children')
        if target.is_file():
            return 'file'
        if target.is_dir():
            return 'directory'
        return 'missing'

    def open_stream(self, resource_id: str, *, allow_hidden: bool=False) -> BinaryIO:
        return self.resolve_resource_path(resource_id, allow_hidden=allow_hidden, allow_directory=False).open('rb')

    def _assert_readable(self, resource_id: str, *, allow_hidden: bool) -> FileResource:
        with self._catalog_guard(write=False):
            resource = self._resource_unlocked(resource_id)
        if resource.state != 'ready':
            raise ResourceNotReadyError(f"resource '{resource_id}' is not ready: {resource.state}")
        if not allow_hidden and not resource.visible:
            raise ResourceNotReadyError(f"resource '{resource_id}' is hidden")
        path = self.resolve_path(resource.root_name, resource.relative_path)
        if not path.exists():
            raise ResourceNotFoundError(f'resource file missing: {resource_id}')
        return resource

    def set_visibility(self, resource_ids: Iterable[str], visible: bool) -> List[FileResource]:
        updated = []
        with self._catalog_guard(write=True):
            for resource_id in resource_ids:
                resource = self._resource_unlocked(resource_id)
                if resource.state == 'deleted':
                    raise ResourceNotReadyError(f"resource '{resource_id}' is deleted")
                value = replace(resource, visible=bool(visible), updated_at=_utc_now())
                self._resources[resource_id] = value
                updated.append(value)
        return updated

    def prepare_download(self, resource_id: str, *, allow_hidden: bool=False) -> DownloadDescriptor:
        resource = self._assert_readable(resource_id, allow_hidden=allow_hidden)
        path = self.resolve_path(resource.root_name, resource.relative_path)
        if not path.is_file():
            raise ResourceNotReadyError('directories must be archived before download')
        current_sha = _sha256_file(path)
        if resource.sha256 and current_sha != resource.sha256:
            raise FileManagerError(f"resource '{resource_id}' failed SHA-256 validation")
        return DownloadDescriptor(resource_id=resource.resource_id, logical_name=resource.logical_name, media_type=resource.media_type, size_bytes=path.stat().st_size, sha256=current_sha, internal_path=str(path))
