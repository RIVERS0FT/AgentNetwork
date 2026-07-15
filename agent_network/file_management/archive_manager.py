from __future__ import annotations
import hashlib
import os
import shutil
import stat
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence
from .base import ArchiveLimitError, FileManagerError, ResourceNotFoundError, ResourceNotReadyError, UnsafePathError, _sha256_file, _utc_now
from .models import FileResource

class ArchiveMixin:

    def create_archive(self, resource_ids: Sequence[str], *, owner_type: str, owner_id: str, root_name: str, relative_path: str, logical_name: str='', visible: bool=True, resource_id: str='', archive_names: Optional[Mapping[str, str]]=None, virtual_files: Optional[Mapping[str, bytes | str]]=None, overwrite: bool=False) -> FileResource:
        if not resource_ids and not virtual_files:
            raise ValueError('at least one resource or virtual file is required')
        destination = self.resolve_path(root_name, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            raise FileExistsError(str(destination))
        temp_path = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
        archive_names_seen: set[str] = set()
        try:
            with zipfile.ZipFile(temp_path, 'w') as archive:
                for resource_id_item in resource_ids:
                    source_resource = self._assert_readable(resource_id_item, allow_hidden=True)
                    source = self.resolve_path(source_resource.root_name, source_resource.relative_path)
                    base_name = (archive_names or {}).get(resource_id_item) or source_resource.logical_name or source.name
                    if source.is_dir():
                        for child in sorted(source.rglob('*')):
                            if child.is_symlink() or not child.is_file():
                                continue
                            relative = Path(base_name) / child.relative_to(source)
                            archive_name = self._unique_archive_name(relative.as_posix(), archive_names_seen)
                            archive.write(child, archive_name, compress_type=self._compression_for(child))
                    else:
                        archive_name = self._unique_archive_name(base_name, archive_names_seen)
                        archive.write(source, archive_name, compress_type=self._compression_for(source))
                for name, payload in (virtual_files or {}).items():
                    archive_name = self._unique_archive_name(name, archive_names_seen)
                    data = payload.encode('utf-8') if isinstance(payload, str) else payload
                    archive.writestr(archive_name, data, compress_type=zipfile.ZIP_DEFLATED)
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)
        return self._upsert_written_resource(path=destination, owner_type=owner_type, owner_id=owner_id, resource_type='archive', root_name=root_name, relative_path=relative_path, logical_name=logical_name or destination.name, media_type='application/zip', visible=visible, resource_id=resource_id)

    @staticmethod
    def _compression_for(path: Path) -> int:
        return zipfile.ZIP_STORED if path.suffix.lower() == '.pcap' else zipfile.ZIP_DEFLATED

    @staticmethod
    def _unique_archive_name(name: str, used: set[str]) -> str:
        candidate = Path(name).as_posix().lstrip('/')
        if not candidate or candidate in {'.', '..'} or '..' in Path(candidate).parts:
            raise UnsafePathError(f'unsafe archive entry: {name!r}')
        if candidate in used:
            raise FileManagerError(f'duplicate archive entry: {candidate}')
        used.add(candidate)
        return candidate

    def extract_archive(self, archive_resource_id: str, *, destination_root_name: str, destination_relative_dir: str, max_files: int=10000, max_total_bytes: int=2 * 1024 * 1024 * 1024, max_file_bytes: int=512 * 1024 * 1024) -> Path:
        archive_resource = self._assert_readable(archive_resource_id, allow_hidden=True)
        archive_path = self.resolve_path(archive_resource.root_name, archive_resource.relative_path)
        destination = self.resolve_path(destination_root_name, destination_relative_dir)
        if destination.exists():
            raise FileExistsError(str(destination))
        temp_dir = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.extracting')
        total_bytes = 0
        try:
            temp_dir.mkdir(parents=True)
            with zipfile.ZipFile(archive_path, 'r') as archive:
                members = archive.infolist()
                if len(members) > max_files:
                    raise ArchiveLimitError('archive contains too many entries')
                for member in members:
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or '..' in member_path.parts or self._zip_member_is_symlink(member):
                        raise UnsafePathError(f'unsafe archive entry: {member.filename!r}')
                    if member.file_size > max_file_bytes:
                        raise ArchiveLimitError(f'archive entry too large: {member.filename}')
                    total_bytes += member.file_size
                    if total_bytes > max_total_bytes:
                        raise ArchiveLimitError('archive exceeds total extraction limit')
                    target = (temp_dir / member_path).resolve()
                    if target != temp_dir and temp_dir not in target.parents:
                        raise UnsafePathError(f'archive entry escapes destination: {member.filename!r}')
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, 'r') as source, target.open('wb') as sink:
                        shutil.copyfileobj(source, sink)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_dir, destination)
            return destination
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @staticmethod
    def _zip_member_is_symlink(member: zipfile.ZipInfo) -> bool:
        return stat.S_ISLNK(member.external_attr >> 16)

    def promote_directory(self, *, source_root_name: str, source_relative_path: str, destination_root_name: str, destination_relative_path: str, owner_type: str, owner_id: str, resource_type: str, logical_name: str='', visible: bool=True, resource_id: str='') -> FileResource:
        source = self.resolve_path(source_root_name, source_relative_path)
        destination = self.resolve_path(destination_root_name, destination_relative_path)
        if not source.is_dir():
            raise ResourceNotFoundError('source directory does not exist')
        if destination.exists():
            raise FileExistsError(str(destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        return self.register_existing(owner_type=owner_type, owner_id=owner_id, resource_type=resource_type, root_name=destination_root_name, relative_path=destination_relative_path, logical_name=logical_name or destination.name, visible=visible, resource_id=resource_id, upsert=True)

    def tree_manifest(self, directory_resource_id: str) -> dict:
        root = self.resolve_resource_path(directory_resource_id, allow_hidden=True, allow_directory=True)
        if not root.is_dir():
            raise ResourceNotReadyError('resource is not a directory')
        files = []
        digest = hashlib.sha256()
        for path in sorted(item for item in root.rglob('*') if item.is_file() and not item.is_symlink() and '__pycache__' not in item.parts):
            relative = path.relative_to(root).as_posix()
            file_hash = _sha256_file(path)
            files.append({'path': relative, 'sha256': file_hash, 'bytes': path.stat().st_size})
            digest.update(relative.encode('utf-8'))
            digest.update(file_hash.encode('ascii'))
        return {'sha256': digest.hexdigest(), 'files': files}

    def cleanup_path(self, root_name: str, relative_path: str, *, missing_ok: bool=True) -> None:
        path = self.resolve_path(root_name, relative_path)
        if not path.exists() and not path.is_symlink():
            if missing_ok:
                return
            raise FileNotFoundError(str(path))
        if path.is_symlink():
            path.unlink(missing_ok=missing_ok)
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=missing_ok)

    def delete(self, resource_ids: Iterable[str]) -> List[FileResource]:
        deleted = []
        with self._catalog_guard(write=True):
            for resource_id in resource_ids:
                resource = self._resource_unlocked(resource_id)
                path = self.resolve_path(resource.root_name, resource.relative_path)
                if path.is_symlink():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=False)
                else:
                    path.unlink(missing_ok=True)
                updated = replace(resource, state='deleted', size_bytes=0, sha256='', visible=False, updated_at=_utc_now())
                self._resources[resource_id] = updated
                deleted.append(updated)
        return deleted
