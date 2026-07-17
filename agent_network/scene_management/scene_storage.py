from __future__ import annotations
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent_network.file_management import FileManager, ResourceNotFoundError, ResourceNotReadyError, get_file_manager, stable_resource_id
from agent_network.network_emulation import normalize_profile
from agent_network.scene_management.scene_def import AgentDef, SceneDefinition
from agent_network.scene_management.models import (
    SceneListItem,
    SceneSummary,
    SceneValidationError,
)
from agent_network.scene_management.validator import SceneValidator
REQUIRED_SCENE_FILES = ('meta_and_roles.json', 'instances_and_skills.json', 'network_topology.json')
_TOPOLOGY_NETWORK_FIELDS = ('delay_ms', 'jitter_ms', 'loss_pct', 'rate_mbit')
_TOPOLOGY_LINK_FIELDS = {'endpoint_a', 'endpoint_b', 'channel_id', *_TOPOLOGY_NETWORK_FIELDS}
_SCENE_KEY_RE = re.compile('^[A-Za-z0-9][A-Za-z0-9_.-]*$')

class SceneStorage:
    """Static scene storage backed by the unified FileManager."""

    def __init__(self, file_manager: Optional[FileManager]=None) -> None:
        self.files = file_manager or get_file_manager()
        self.validator = SceneValidator()

    @staticmethod
    def validate_scene_key(scene_key: str) -> str:
        value = str(scene_key or '').strip()
        if not value or not _SCENE_KEY_RE.fullmatch(value):
            raise ValueError("scene_key must contain only letters, digits, '.', '_' or '-'")
        return value

    @staticmethod
    def resource_id(scene_key: str) -> str:
        return stable_resource_id('scene', scene_key, 'directory')

    def _discover(self) -> None:
        root = self.files.root_path('scenes')
        for directory in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if directory.is_symlink() or not directory.is_dir():
                continue
            if not all(((directory / name).is_file() for name in REQUIRED_SCENE_FILES)):
                continue
            scene_key = directory.name
            existing = self.files.find_resource(owner_type='scene', owner_id=scene_key, resource_type='scene_directory', include_deleted=True)
            if existing and existing.state != 'deleted':
                self.files.refresh(existing.resource_id)
                continue
            self.files.register_existing(owner_type='scene', owner_id=scene_key, resource_type='scene_directory', root_name='scenes', relative_path=scene_key, logical_name=scene_key, media_type='inode/directory', resource_id=self.resource_id(scene_key), upsert=True)

    def get_resource(self, scene_key: str, *, allow_hidden: bool=False):
        scene_key = self.validate_scene_key(scene_key)
        resource = self.files.find_resource(owner_type='scene', owner_id=scene_key, resource_type='scene_directory', include_deleted=False)
        if resource is None:
            path = self.files.resolve_path('scenes', scene_key)
            if not path.is_dir():
                raise ResourceNotFoundError(f"Scene '{scene_key}' not found")
            resource = self.files.register_existing(owner_type='scene', owner_id=scene_key, resource_type='scene_directory', root_name='scenes', relative_path=scene_key, logical_name=scene_key, media_type='inode/directory', resource_id=self.resource_id(scene_key), upsert=True)
        if not allow_hidden and (not resource.visible):
            raise ResourceNotReadyError(f"Scene '{scene_key}' is hidden")
        return resource

    def list_scenes(self) -> List[Dict[str, Any]]:
        self._discover()
        result = []
        resources = self.files.list_resources(
            owner_type='scene',
            resource_type='scene_directory',
            include_hidden=False,
        )
        for resource in sorted(resources, key=lambda item: item.created_at):
            try:
                meta = self.files.read_child_json(resource.resource_id, 'meta_and_roles.json', allow_hidden=True)
            except (OSError, ValueError, ResourceNotFoundError):
                meta = {}
            metadata = meta.get('scenario_metadata', {}) if isinstance(meta, dict) else {}
            result.append(
                SceneListItem(
                    scene_key=resource.owner_id,
                    title=metadata.get('title') or resource.owner_id,
                ).to_dict()
            )
        return result

    def read_json(self, scene_key: str, filename: str) -> Any:
        resource = self.get_resource(scene_key)
        return self.files.read_child_json(resource.resource_id, filename)

    def details(self, scene_key: str) -> Dict[str, Any]:
        definition = self.build_definition(scene_key)
        return SceneSummary(
            scene_key=definition.scene_key,
            title=definition.title,
            description=definition.description,
            agents=[asdict(agent) for agent in definition.agents],
            skills=definition.skills,
            tools=definition.tools,
            tasks=definition.tasks,
            topology=definition.topology,
            validation=definition.validation,
        ).to_dict()

    def build_definition(self, scene_key: str) -> SceneDefinition:
        scene_key = self.validate_scene_key(scene_key)
        meta = self.read_json(scene_key, 'meta_and_roles.json')
        instances = self.read_json(scene_key, 'instances_and_skills.json')
        topology_config = self.read_json(scene_key, 'network_topology.json')
        validated = self.validator.validate(
            scene_key,
            self.files.resolve_path('scenes', scene_key),
            meta,
            instances,
            topology_config,
        )
        if not validated.validation.valid:
            raise SceneValidationError(validated.validation)
        scenario_metadata = meta.get('scenario_metadata', {})
        title = scenario_metadata.get('title', scene_key)
        description = scenario_metadata.get('global_rules', '')
        roles = meta.get('roles', {})
        containers = instances.get('container_instances', {})
        normalized_containers = {
            str(key).lower(): value for key, value in containers.items()
        }
        agents: List[AgentDef] = []
        for role_id, role in roles.items():
            instance = normalized_containers.get(str(role_id).lower(), {})
            skill_refs = list(instance.get('skill_refs') or [])
            if not all((isinstance(item, str) and item for item in skill_refs)):
                raise ValueError(f"Scene '{scene_key}' role '{role_id}' skill_refs must contain non-empty strings.")
            allowed_tools = list(instance.get('tool_refs') or [])
            backend = str(role.get('model_backbone', 'openclaw') or 'openclaw').strip()
            if backend not in {'openclaw', 'claude-code', 'direct_llm'}:
                raise ValueError(f"Scene '{scene_key}' role '{role_id}' uses unsupported backend '{backend}'.")
            identity = role.get('identity', '') or role.get('name', role_id)
            task_values = []
            for task in instance.get('tasks') or []:
                task_values.append(
                    task if isinstance(task, str) else str(task.get('goal') or task.get('name') or '')
                )
            agents.append(AgentDef(agent_id=role_id.lower(), role=identity, name=role.get('name', role_id), background=role.get('background', ''), core_goal=role.get('core_goal', ''), backend=backend, skill_refs=list(dict.fromkeys(skill_refs)), allowed_tools=list(dict.fromkeys(allowed_tools)), tasks=[task for task in task_values if task]))
        raw_topology = topology_config.get('topology')
        if not isinstance(raw_topology, list):
            raise ValueError(f"Scene '{scene_key}' network_topology.json must contain a root-level topology array.")
        agent_ids = {agent.agent_id for agent in agents}
        channel_ids = set()
        topology_edges = []
        for index, edge in enumerate(raw_topology):
            if not isinstance(edge, dict):
                raise ValueError(f"Scene '{scene_key}' topology[{index}] must be an object.")
            unexpected = set(edge) - _TOPOLOGY_LINK_FIELDS
            missing = {'endpoint_a', 'endpoint_b', 'channel_id'} - set(edge)
            if unexpected:
                raise ValueError(f"Scene '{scene_key}' topology[{index}] has unsupported fields: {sorted(unexpected)}")
            if missing:
                raise ValueError(f"Scene '{scene_key}' topology[{index}] is missing fields: {sorted(missing)}")
            endpoint_a = str(edge['endpoint_a']).strip().lower()
            endpoint_b = str(edge['endpoint_b']).strip().lower()
            channel_id = str(edge['channel_id']).strip()
            if not endpoint_a or not endpoint_b or endpoint_a == endpoint_b:
                raise ValueError(f"Scene '{scene_key}' topology[{index}] must connect two distinct endpoints.")
            unknown = {endpoint_a, endpoint_b} - agent_ids
            if unknown:
                raise ValueError(f"Scene '{scene_key}' topology[{index}] references unknown agents: {sorted(unknown)}")
            if not channel_id or channel_id in channel_ids:
                raise ValueError(f"Scene '{scene_key}' contains invalid or duplicate channel_id '{channel_id}'.")
            channel_ids.add(channel_id)
            network = normalize_profile({field: edge.get(field, 0) for field in _TOPOLOGY_NETWORK_FIELDS})
            topology_edges.append({'endpoint_a': endpoint_a, 'endpoint_b': endpoint_b, 'channel_id': channel_id, **network})
        return SceneDefinition(
            scene_key=scene_key,
            title=title,
            description=description,
            agents=agents,
            skills=validated.skills,
            tools=validated.tools,
            tasks=validated.tasks,
            topology=topology_edges,
            validation=validated.validation,
        )

    def create_archive(self, scene_key: str):
        scene = self.get_resource(scene_key)
        archive_id = stable_resource_id('scene', scene_key, 'archive')
        return self.files.create_archive([scene.resource_id], owner_type='scene', owner_id=scene_key, root_name='archives', relative_path=f'scenes/{scene_key}.zip', logical_name=f'{scene_key}.zip', resource_id=archive_id, archive_names={scene.resource_id: scene_key}, overwrite=True)

    def set_visibility(self, scene_key: str, visible: bool):
        resource = self.get_resource(scene_key, allow_hidden=True)
        return self.files.set_visibility([resource.resource_id], visible)[0]

    def delete(self, scene_key: str):
        resource = self.get_resource(scene_key, allow_hidden=True)
        return self.files.delete([resource.resource_id])[0]

    def import_archive(self, *, filename: str, content: bytes, scene_key: str=''):
        if not filename.lower().endswith('.zip'):
            raise ValueError('scene upload must be a ZIP archive')
        upload_id = uuid.uuid4().hex
        upload = self.files.write_bytes(content, owner_type='scene_upload', owner_id=upload_id, resource_type='temporary_upload', root_name='temp', relative_path=f'upload/{upload_id}.zip', logical_name=Path(filename).name, media_type='application/zip')
        extract_relative = f'extract/{upload_id}'
        try:
            extracted = self.files.extract_archive(upload.resource_id, destination_root_name='temp', destination_relative_dir=extract_relative)
            children = [item for item in extracted.iterdir() if item.name != '__MACOSX']
            source = extracted
            if len(children) == 1 and children[0].is_dir():
                source = children[0]
            resolved_key = self.validate_scene_key(scene_key or source.name or Path(filename).stem)
            if not all(((source / name).is_file() for name in REQUIRED_SCENE_FILES)):
                raise ValueError('archive does not contain the required scene definition files')
            source_relative = source.relative_to(self.files.root_path('temp')).as_posix()
            resource = self.files.promote_directory(source_root_name='temp', source_relative_path=source_relative, destination_root_name='scenes', destination_relative_path=resolved_key, owner_type='scene', owner_id=resolved_key, resource_type='scene_directory', logical_name=resolved_key, resource_id=self.resource_id(resolved_key))
            try:
                self.build_definition(resolved_key)
            except Exception:
                self.files.delete([resource.resource_id])
                raise
            return self.details(resolved_key)
        finally:
            try:
                self.files.delete([upload.resource_id])
            except (ResourceNotFoundError, ResourceNotReadyError):
                pass
            self.files.cleanup_path('temp', extract_relative)
_default_scene_storage: Optional[SceneStorage] = None

def get_scene_storage() -> SceneStorage:
    global _default_scene_storage
    if _default_scene_storage is None:
        _default_scene_storage = SceneStorage()
    return _default_scene_storage
