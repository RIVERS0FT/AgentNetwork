from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict
from agent_network.file_management import FileManager, get_file_manager
from agent_network.scene_management import SceneStorage
MAX_SKILL_FILE_BYTES = 512 * 1024
_TEST_MANAGERS: Dict[str, FileManager] = {}

@dataclass(frozen=True)
class SceneSkillSource:
    name: str
    kind: str
    root: Path
    entrypoint: Path
    scene_resource_id: str = ''
    root_relative: str = ''

    @property
    def entrypoint_relative(self) -> str:
        if self.kind == 'package':
            return self.entrypoint.relative_to(self.root).as_posix()
        return self.entrypoint.name

def _validate_name(value: str, field_name: str) -> str:
    value = str(value or '').strip()
    if not value:
        raise ValueError(f'{field_name} is required')
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {'.', '..'}:
        raise ValueError(f'Invalid {field_name}: {value}')
    return value

def _manager_for_root(scenes_root: str) -> FileManager:
    requested = Path(scenes_root).resolve()
    default = get_file_manager()
    if requested == default.root_path('scenes'):
        return default
    key = str(requested)
    manager = _TEST_MANAGERS.get(key)
    if manager is None:
        manager = FileManager({'scenes': requested}, catalog_path=requested.parent / '.skill_file_registry.json')
        _TEST_MANAGERS[key] = manager
    return manager

def resolve_scene_skill(scene_key: str, skill_ref: str, scenes_root: str='/app/scenes') -> SceneSkillSource:
    scene_key = _validate_name(scene_key, 'scene_key')
    skill_ref = _validate_name(skill_ref, 'skill_ref')
    manager = _manager_for_root(scenes_root)
    scene = SceneStorage(manager).get_resource(scene_key, allow_hidden=True)
    package_relative = f'skills/{skill_ref}'
    package_kind = manager.child_kind(scene.resource_id, package_relative, allow_hidden=True)
    if package_kind == 'directory':
        entry_relative = f'{package_relative}/SKILL.md'
        entrypoint = manager.resolve_child_path(scene.resource_id, entry_relative, allow_hidden=True, expected_kind='file')
        root = manager.resolve_child_path(scene.resource_id, package_relative, allow_hidden=True, expected_kind='directory')
        return SceneSkillSource(name=skill_ref, kind='package', root=root, entrypoint=entrypoint, scene_resource_id=scene.resource_id, root_relative=package_relative)
    single_relative = f'skills/{skill_ref}.md'
    if manager.child_kind(scene.resource_id, single_relative, allow_hidden=True) == 'file':
        entrypoint = manager.resolve_child_path(scene.resource_id, single_relative, allow_hidden=True, expected_kind='file')
        root = manager.resolve_child_path(scene.resource_id, 'skills', allow_hidden=True, expected_kind='directory')
        return SceneSkillSource(name=skill_ref, kind='file', root=root, entrypoint=entrypoint, scene_resource_id=scene.resource_id, root_relative='skills')
    raise FileNotFoundError(f"Skill '{skill_ref}' was not found in scene '{scene_key}'")

def describe_scene_skill(scene_key: str, skill_ref: str, scenes_root: str='/app/scenes') -> dict:
    source = resolve_scene_skill(scene_key, skill_ref, scenes_root)
    return {'name': source.name, 'kind': source.kind, 'entrypoint': source.entrypoint_relative}

def list_scene_skill_files(scene_key: str, skill_ref: str, scenes_root: str='/app/scenes') -> list[str]:
    manager = _manager_for_root(scenes_root)
    source = resolve_scene_skill(scene_key, skill_ref, scenes_root)
    if source.kind == 'file':
        return [source.entrypoint.name]
    paths = manager.list_children(source.scene_resource_id, source.root_relative, recursive=True, files_only=True, allow_hidden=True)
    prefix = source.root_relative.rstrip('/') + '/'
    return [path.removeprefix(prefix) for path in paths]

def resolve_scene_skill_file(scene_key: str, skill_ref: str, relative_path: str='SKILL.md', scenes_root: str='/app/scenes') -> Path:
    manager = _manager_for_root(scenes_root)
    source = resolve_scene_skill(scene_key, skill_ref, scenes_root)
    requested = str(relative_path or 'SKILL.md').strip()
    requested_path = Path(requested)
    if requested_path.is_absolute() or '..' in requested_path.parts:
        raise PermissionError('Skill file path must stay inside the Skill')
    if source.kind == 'file':
        allowed_names = {'SKILL.md', source.entrypoint.name, ''}
        if requested not in allowed_names:
            raise FileNotFoundError(f"Single-file Skill '{skill_ref}' only exposes its entrypoint")
        return source.entrypoint
    return manager.resolve_child_path(source.scene_resource_id, f'{source.root_relative}/{requested_path.as_posix()}', allow_hidden=True, expected_kind='file')

def read_scene_skill_file(scene_key: str, skill_ref: str, relative_path: str='SKILL.md', scenes_root: str='/app/scenes', max_bytes: int=MAX_SKILL_FILE_BYTES) -> str:
    manager = _manager_for_root(scenes_root)
    source = resolve_scene_skill(scene_key, skill_ref, scenes_root)
    requested = str(relative_path or 'SKILL.md').strip()
    requested_path = Path(requested)
    if requested_path.is_absolute() or '..' in requested_path.parts:
        raise PermissionError('Skill file path must stay inside the Skill')
    if source.kind == 'file':
        allowed_names = {'SKILL.md', source.entrypoint.name, ''}
        if requested not in allowed_names:
            raise FileNotFoundError(f"Single-file Skill '{skill_ref}' only exposes its entrypoint")
        child = f'skills/{skill_ref}.md'
    else:
        child = f'{source.root_relative}/{requested_path.as_posix()}'
    data = manager.read_child_bytes(source.scene_resource_id, child, allow_hidden=True)
    if len(data) > max_bytes:
        raise ValueError(f'Skill file is too large: {len(data)} bytes (limit {max_bytes})')
    return data.decode('utf-8')
