from __future__ import annotations

import ast
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_network.comm_management import normalize_profile
from agent_network.file_management import (
    FileManager,
    ResourceNotFoundError,
    ResourceNotReadyError,
    get_file_manager,
    stable_resource_id,
)
from agent_network.native_capabilities import NativeCapabilityPolicy
from agent_network.scene_management.models import (
    SceneListItem,
    SceneSummary,
    SceneValidationError,
    ValidationIssue,
    ValidationResult,
)
from agent_network.scene_management.scene_def import AgentDef, SceneDefinition
from agent_network.scene_management.validator import SceneValidator
from agent_network.scene_management.validator_v2 import SceneValidatorV2

REQUIRED_SCENE_FILES = ("Agents.json", "topology.json", "env.py")
LEGACY_SCENE_FILES = (
    "meta_and_roles.json",
    "instances_and_skills.json",
    "network_topology.json",
)
_TOPOLOGY_NETWORK_FIELDS = ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit")
_TOPOLOGY_LINK_FIELDS = {
    "endpoint_a",
    "endpoint_b",
    "channel_id",
    *_TOPOLOGY_NETWORK_FIELDS,
}
_SCENE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class SceneStorage:
    """Static scene storage backed by the unified FileManager."""

    def __init__(self, file_manager: Optional[FileManager] = None) -> None:
        self.files = file_manager or get_file_manager()
        self.validator = SceneValidator()
        self.validator_v2 = SceneValidatorV2()

    @staticmethod
    def validate_scene_key(scene_key: str) -> str:
        value = str(scene_key or "").strip()
        if not value or not _SCENE_KEY_RE.fullmatch(value):
            raise ValueError("scene_key must contain only letters, digits, '.', '_' or '-'")
        return value

    @staticmethod
    def resource_id(scene_key: str) -> str:
        return stable_resource_id("scene", scene_key, "directory")

    @staticmethod
    def _contains(root: Path, names: tuple[str, ...]) -> bool:
        return all((root / name).is_file() for name in names)

    @classmethod
    def _scene_format(cls, root: Path) -> str:
        if cls._contains(root, REQUIRED_SCENE_FILES):
            return "v2"
        if cls._contains(root, LEGACY_SCENE_FILES):
            return "v1"
        return ""

    def _discover(self) -> None:
        root = self.files.root_path("scenes")
        for directory in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if directory.is_symlink() or not directory.is_dir():
                continue
            if not self._scene_format(directory):
                continue
            scene_key = directory.name
            existing = self.files.find_resource(
                owner_type="scene",
                owner_id=scene_key,
                resource_type="scene_directory",
                include_deleted=True,
            )
            if existing and existing.state != "deleted":
                self.files.refresh(existing.resource_id)
                continue
            self.files.register_existing(
                owner_type="scene",
                owner_id=scene_key,
                resource_type="scene_directory",
                root_name="scenes",
                relative_path=scene_key,
                logical_name=scene_key,
                media_type="inode/directory",
                resource_id=self.resource_id(scene_key),
                upsert=True,
            )

    def get_resource(self, scene_key: str, *, allow_hidden: bool = False):
        scene_key = self.validate_scene_key(scene_key)
        resource = self.files.find_resource(
            owner_type="scene",
            owner_id=scene_key,
            resource_type="scene_directory",
            include_deleted=False,
        )
        if resource is None:
            path = self.files.resolve_path("scenes", scene_key)
            if not path.is_dir():
                raise ResourceNotFoundError(f"Scene '{scene_key}' not found")
            resource = self.files.register_existing(
                owner_type="scene",
                owner_id=scene_key,
                resource_type="scene_directory",
                root_name="scenes",
                relative_path=scene_key,
                logical_name=scene_key,
                media_type="inode/directory",
                resource_id=self.resource_id(scene_key),
                upsert=True,
            )
        if not allow_hidden and not resource.visible:
            raise ResourceNotReadyError(f"Scene '{scene_key}' is hidden")
        return resource

    @staticmethod
    def _env_error(scene_key: str, message: str) -> SceneValidationError:
        result = ValidationResult(
            scene_key=scene_key,
            schema_version="agentnetwork-scene.v2",
        )
        result.issues.append(
            ValidationIssue(
                "ENV_SOURCE_INVALID",
                "error",
                "env.py",
                "$.ENV",
                "environment",
                "",
                message,
            )
        )
        return SceneValidationError(result)

    @classmethod
    def _read_env(cls, scene_key: str, root: Path) -> dict[str, Any]:
        path = root / "env.py"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise cls._env_error(scene_key, str(exc)) from exc

        env_node: ast.AST | None = None
        for index, node in enumerate(tree.body):
            if (
                index == 0
                and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            if isinstance(node, ast.Assign):
                if (
                    len(node.targets) != 1
                    or not isinstance(node.targets[0], ast.Name)
                    or node.targets[0].id != "ENV"
                    or env_node is not None
                ):
                    raise cls._env_error(
                        scene_key,
                        "env.py only allows one direct top-level ENV assignment",
                    )
                env_node = node.value
                continue
            if isinstance(node, ast.AnnAssign):
                if (
                    not isinstance(node.target, ast.Name)
                    or node.target.id != "ENV"
                    or node.value is None
                    or env_node is not None
                ):
                    raise cls._env_error(
                        scene_key,
                        "env.py only allows one direct top-level ENV assignment",
                    )
                env_node = node.value
                continue
            raise cls._env_error(
                scene_key,
                "env.py must be declarative; imports, calls, functions and classes are forbidden",
            )

        if env_node is None:
            raise cls._env_error(scene_key, "env.py must define ENV")
        try:
            value = ast.literal_eval(env_node)
        except (ValueError, TypeError, SyntaxError) as exc:
            raise cls._env_error(
                scene_key,
                "ENV must contain only Python literal data",
            ) from exc
        if not isinstance(value, dict):
            raise cls._env_error(scene_key, "ENV must be a dictionary")
        return value

    def list_scenes(self) -> List[Dict[str, Any]]:
        self._discover()
        result = []
        resources = self.files.list_resources(
            owner_type="scene",
            resource_type="scene_directory",
            include_hidden=False,
        )
        for resource in sorted(resources, key=lambda item: item.created_at):
            title = resource.owner_id
            root = self.files.resolve_resource_path(resource.resource_id)
            scene_format = self._scene_format(root)
            try:
                if scene_format == "v2":
                    env = self._read_env(resource.owner_id, root)
                    metadata = env.get("metadata", {}) if isinstance(env, dict) else {}
                    title = metadata.get("title") or title
                elif scene_format == "v1":
                    meta = self.files.read_child_json(
                        resource.resource_id,
                        "meta_and_roles.json",
                        allow_hidden=True,
                    )
                    metadata = (
                        meta.get("scenario_metadata", {})
                        if isinstance(meta, dict)
                        else {}
                    )
                    title = metadata.get("title") or title
            except (OSError, ValueError, ResourceNotFoundError, SceneValidationError):
                pass
            result.append(
                SceneListItem(scene_key=resource.owner_id, title=title).to_dict()
            )
        return result

    def read_json(self, scene_key: str, filename: str) -> Any:
        resource = self.get_resource(scene_key)
        return self.files.read_child_json(resource.resource_id, filename)

    def details(self, scene_key: str) -> Dict[str, Any]:
        definition = self.build_definition(scene_key)
        agents = []
        for agent in definition.agents:
            value = asdict(agent)
            value["native_capabilities"] = agent.native_capabilities.to_dict()
            agents.append(value)
        details = SceneSummary(
            scene_key=definition.scene_key,
            title=definition.title,
            description=definition.description,
            agents=agents,
            skills=definition.skills,
            tools=definition.tools,
            tasks=definition.tasks,
            topology=definition.topology,
            validation=definition.validation,
        ).to_dict()
        if definition.validation.schema_version == "agentnetwork-scene.v2":
            details["environment"] = definition.environment
        return details

    def build_definition(self, scene_key: str) -> SceneDefinition:
        scene_key = self.validate_scene_key(scene_key)
        resource = self.get_resource(scene_key)
        root = self.files.resolve_resource_path(resource.resource_id)
        scene_format = self._scene_format(root)
        if scene_format == "v2":
            return self._build_v2(scene_key, resource.resource_id, root)
        if scene_format == "v1":
            return self._build_v1(scene_key, resource.resource_id, root)
        raise ValueError(
            f"Scene '{scene_key}' does not contain required scene definition files"
        )

    def _build_v2(
        self,
        scene_key: str,
        resource_id: str,
        root: Path,
    ) -> SceneDefinition:
        agents_config = self.files.read_child_json(resource_id, "Agents.json")
        topology_config = self.files.read_child_json(resource_id, "topology.json")
        env = self._read_env(scene_key, root)
        validated = self.validator_v2.validate(
            scene_key,
            root,
            agents_config,
            env,
            topology_config,
        )
        if not validated.validation.valid:
            raise SceneValidationError(validated.validation)

        metadata = env.get("metadata", {})
        title = metadata.get("title", scene_key)
        description = metadata.get("description", "")
        environment = dict(env.get("environment") or {})
        raw_agents = agents_config.get("agents", {})
        agents: List[AgentDef] = []
        for raw_agent_id, raw_agent in raw_agents.items():
            agent_id = str(raw_agent_id).lower()
            agent = raw_agent if isinstance(raw_agent, dict) else {}
            backend = str(agent.get("backend", "openclaw") or "openclaw").strip()
            agent_tasks = [
                task.goal
                for task in validated.tasks
                if task.scope == "agent" and task.agent_id == agent_id
            ]
            agents.append(
                AgentDef(
                    agent_id=agent_id,
                    role=agent.get("role", "") or agent.get("name", raw_agent_id),
                    name=agent.get("name", raw_agent_id),
                    background=agent.get("background", ""),
                    core_goal=agent.get("core_goal", ""),
                    backend=backend,
                    skill_refs=list(dict.fromkeys(agent.get("skill_refs") or [])),
                    allowed_tools=list(dict.fromkeys(agent.get("tool_refs") or [])),
                    native_capabilities=NativeCapabilityPolicy.from_dict(
                        agent.get("native_capabilities"),
                        backend=backend,
                    ),
                    tasks=agent_tasks,
                )
            )
        topology_edges = self._build_topology(
            scene_key,
            topology_config,
            agents,
            "topology.json",
        )
        return SceneDefinition(
            scene_key=scene_key,
            title=title,
            description=description,
            environment=environment,
            agents=agents,
            skills=validated.skills,
            tools=validated.tools,
            tasks=validated.tasks,
            topology=topology_edges,
            validation=validated.validation,
        )

    def _build_v1(
        self,
        scene_key: str,
        resource_id: str,
        root: Path,
    ) -> SceneDefinition:
        meta = self.files.read_child_json(resource_id, "meta_and_roles.json")
        instances = self.files.read_child_json(resource_id, "instances_and_skills.json")
        topology_config = self.files.read_child_json(resource_id, "network_topology.json")
        validated = self.validator.validate(
            scene_key,
            root,
            meta,
            instances,
            topology_config,
        )
        if not validated.validation.valid:
            raise SceneValidationError(validated.validation)
        scenario_metadata = meta.get("scenario_metadata", {})
        title = scenario_metadata.get("title", scene_key)
        description = scenario_metadata.get("global_rules", "")
        roles = meta.get("roles", {})
        containers = instances.get("container_instances", {})
        normalized_containers = {
            str(key).lower(): value for key, value in containers.items()
        }
        agents: List[AgentDef] = []
        for role_id, role in roles.items():
            instance = normalized_containers.get(str(role_id).lower(), {})
            skill_refs = list(instance.get("skill_refs") or [])
            if not all(isinstance(item, str) and item for item in skill_refs):
                raise ValueError(
                    f"Scene '{scene_key}' role '{role_id}' skill_refs must contain non-empty strings."
                )
            allowed_tools = list(instance.get("tool_refs") or [])
            backend = str(role.get("model_backbone", "openclaw") or "openclaw").strip()
            if backend not in {"openclaw", "claude-code", "direct_llm"}:
                raise ValueError(
                    f"Scene '{scene_key}' role '{role_id}' uses unsupported backend '{backend}'."
                )
            identity = role.get("identity", "") or role.get("name", role_id)
            task_values = []
            for task in instance.get("tasks") or []:
                task_values.append(
                    task
                    if isinstance(task, str)
                    else str(task.get("goal") or task.get("name") or "")
                )
            native_capabilities = NativeCapabilityPolicy.from_dict(
                instance.get("native_capabilities"),
                backend=backend,
            )
            agents.append(
                AgentDef(
                    agent_id=role_id.lower(),
                    role=identity,
                    name=role.get("name", role_id),
                    background=role.get("background", ""),
                    core_goal=role.get("core_goal", ""),
                    backend=backend,
                    skill_refs=list(dict.fromkeys(skill_refs)),
                    allowed_tools=list(dict.fromkeys(allowed_tools)),
                    native_capabilities=native_capabilities,
                    tasks=[task for task in task_values if task],
                )
            )
        topology_edges = self._build_topology(
            scene_key,
            topology_config,
            agents,
            "network_topology.json",
        )
        return SceneDefinition(
            scene_key=scene_key,
            title=title,
            description=description,
            environment={"global_rules": description},
            agents=agents,
            skills=validated.skills,
            tools=validated.tools,
            tasks=validated.tasks,
            topology=topology_edges,
            validation=validated.validation,
        )

    @staticmethod
    def _build_topology(
        scene_key: str,
        topology_config: dict[str, Any],
        agents: List[AgentDef],
        filename: str,
    ) -> list[dict[str, Any]]:
        raw_topology = topology_config.get("topology")
        if not isinstance(raw_topology, list):
            raise ValueError(
                f"Scene '{scene_key}' {filename} must contain a root-level topology array."
            )
        agent_ids = {agent.agent_id for agent in agents}
        channel_ids = set()
        topology_edges = []
        for index, edge in enumerate(raw_topology):
            if not isinstance(edge, dict):
                raise ValueError(
                    f"Scene '{scene_key}' topology[{index}] must be an object."
                )
            unexpected = set(edge) - _TOPOLOGY_LINK_FIELDS
            missing = {"endpoint_a", "endpoint_b", "channel_id"} - set(edge)
            if unexpected:
                raise ValueError(
                    f"Scene '{scene_key}' topology[{index}] has unsupported fields: {sorted(unexpected)}"
                )
            if missing:
                raise ValueError(
                    f"Scene '{scene_key}' topology[{index}] is missing fields: {sorted(missing)}"
                )
            endpoint_a = str(edge["endpoint_a"]).strip().lower()
            endpoint_b = str(edge["endpoint_b"]).strip().lower()
            channel_id = str(edge["channel_id"]).strip()
            if not endpoint_a or not endpoint_b or endpoint_a == endpoint_b:
                raise ValueError(
                    f"Scene '{scene_key}' topology[{index}] must connect two distinct endpoints."
                )
            unknown = {endpoint_a, endpoint_b} - agent_ids
            if unknown:
                raise ValueError(
                    f"Scene '{scene_key}' topology[{index}] references unknown agents: {sorted(unknown)}"
                )
            if not channel_id or channel_id in channel_ids:
                raise ValueError(
                    f"Scene '{scene_key}' contains invalid or duplicate channel_id '{channel_id}'."
                )
            channel_ids.add(channel_id)
            network = normalize_profile(
                {field: edge.get(field, 0) for field in _TOPOLOGY_NETWORK_FIELDS}
            )
            topology_edges.append(
                {
                    "endpoint_a": endpoint_a,
                    "endpoint_b": endpoint_b,
                    "channel_id": channel_id,
                    **network,
                }
            )
        return topology_edges

    def create_archive(self, scene_key: str):
        scene = self.get_resource(scene_key)
        archive_id = stable_resource_id("scene", scene_key, "archive")
        return self.files.create_archive(
            [scene.resource_id],
            owner_type="scene",
            owner_id=scene_key,
            root_name="archives",
            relative_path=f"scenes/{scene_key}.zip",
            logical_name=f"{scene_key}.zip",
            resource_id=archive_id,
            archive_names={scene.resource_id: scene_key},
            overwrite=True,
        )

    def set_visibility(self, scene_key: str, visible: bool):
        resource = self.get_resource(scene_key, allow_hidden=True)
        return self.files.set_visibility([resource.resource_id], visible)[0]

    def delete(self, scene_key: str):
        resource = self.get_resource(scene_key, allow_hidden=True)
        return self.files.delete([resource.resource_id])[0]

    def import_archive(
        self,
        *,
        filename: str,
        content: bytes,
        scene_key: str = "",
    ):
        if not filename.lower().endswith(".zip"):
            raise ValueError("scene upload must be a ZIP archive")
        upload_id = uuid.uuid4().hex
        upload = self.files.write_bytes(
            content,
            owner_type="scene_upload",
            owner_id=upload_id,
            resource_type="temporary_upload",
            root_name="temp",
            relative_path=f"upload/{upload_id}.zip",
            logical_name=Path(filename).name,
            media_type="application/zip",
        )
        extract_relative = f"extract/{upload_id}"
        try:
            extracted = self.files.extract_archive(
                upload.resource_id,
                destination_root_name="temp",
                destination_relative_dir=extract_relative,
            )
            children = [item for item in extracted.iterdir() if item.name != "__MACOSX"]
            source = extracted
            if len(children) == 1 and children[0].is_dir():
                source = children[0]
            resolved_key = self.validate_scene_key(
                scene_key or source.name or Path(filename).stem
            )
            if not self._scene_format(source):
                raise ValueError(
                    "archive does not contain Agents.json, topology.json and env.py"
                )
            source_relative = source.relative_to(self.files.root_path("temp")).as_posix()
            resource = self.files.promote_directory(
                source_root_name="temp",
                source_relative_path=source_relative,
                destination_root_name="scenes",
                destination_relative_path=resolved_key,
                owner_type="scene",
                owner_id=resolved_key,
                resource_type="scene_directory",
                logical_name=resolved_key,
                resource_id=self.resource_id(resolved_key),
            )
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
            self.files.cleanup_path("temp", extract_relative)


_default_scene_storage: Optional[SceneStorage] = None


def get_scene_storage() -> SceneStorage:
    global _default_scene_storage
    if _default_scene_storage is None:
        _default_scene_storage = SceneStorage()
    return _default_scene_storage
