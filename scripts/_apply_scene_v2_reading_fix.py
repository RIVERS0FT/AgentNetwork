from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_block(text: str, start: str, end: str, replacement: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[:start_index] + replacement + text[end_index:]


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.write_text(content, encoding="utf-8")
    print(f"updated {path}")


def patch_scene_storage() -> None:
    path = ROOT / "agent_network/scene_management/scene_storage.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from agent_network.scene_management.validator import SceneValidator\n",
        "",
    )
    legacy_constant = '''LEGACY_SCENE_FILES = (
    "meta_and_roles.json",
    "instances_and_skills.json",
    "network_topology.json",
)
'''
    text = text.replace(legacy_constant, "")
    text = text.replace("        self.validator = SceneValidator()\n", "")

    text = replace_block(
        text,
        "    @classmethod\n    def _scene_format",
        "    def _discover",
        '''    @classmethod
    def _scene_format(cls, root: Path) -> str:
        return "v2" if cls._contains(root, REQUIRED_SCENE_FILES) else ""

''',
    )

    old_get_resource = '''        if resource is None:
            path = self.files.resolve_path("scenes", scene_key)
            if not path.is_dir():
                raise ResourceNotFoundError(f"Scene '{scene_key}' not found")
            resource = self.files.register_existing(
'''
    new_get_resource = '''        if resource is None:
            path = self.files.resolve_path("scenes", scene_key)
            if not path.is_dir() or not self._contains(path, REQUIRED_SCENE_FILES):
                raise ResourceNotFoundError(f"Scene '{scene_key}' not found")
            resource = self.files.register_existing(
'''
    if old_get_resource not in text:
        raise RuntimeError("SceneStorage.get_resource marker not found")
    text = text.replace(old_get_resource, new_get_resource)
    visibility_marker = '''        if not allow_hidden and not resource.visible:
            raise ResourceNotReadyError(f"Scene '{scene_key}' is hidden")
'''
    visibility_replacement = '''        root = self.files.resolve_resource_path(resource.resource_id)
        if not self._contains(root, REQUIRED_SCENE_FILES):
            raise ResourceNotFoundError(f"Scene '{scene_key}' not found")
        if not allow_hidden and not resource.visible:
            raise ResourceNotReadyError(f"Scene '{scene_key}' is hidden")
'''
    if visibility_marker not in text:
        raise RuntimeError("SceneStorage visibility marker not found")
    text = text.replace(visibility_marker, visibility_replacement, 1)

    text = replace_block(
        text,
        "    def list_scenes",
        "    def read_json",
        '''    def list_scenes(self) -> List[Dict[str, Any]]:
        self._discover()
        result = []
        resources = self.files.list_resources(
            owner_type="scene",
            resource_type="scene_directory",
            include_hidden=False,
        )
        for resource in sorted(resources, key=lambda item: item.created_at):
            root = self.files.resolve_resource_path(resource.resource_id)
            if not self._contains(root, REQUIRED_SCENE_FILES):
                continue
            title = resource.owner_id
            try:
                env = self._read_env(resource.owner_id, root)
                metadata = env.get("metadata", {}) if isinstance(env, dict) else {}
                title = metadata.get("title") or title
            except (OSError, ValueError, ResourceNotFoundError, SceneValidationError):
                pass
            result.append(
                SceneListItem(scene_key=resource.owner_id, title=title).to_dict()
            )
        return result

''',
    )

    text = replace_block(
        text,
        "    def details",
        "    def build_definition",
        '''    def details(self, scene_key: str) -> Dict[str, Any]:
        definition = self.build_definition(scene_key)
        agents = []
        for agent in definition.agents:
            value = asdict(agent)
            value["native_capabilities"] = agent.native_capabilities.to_dict()
            agents.append(value)
        return SceneSummary(
            scene_key=definition.scene_key,
            title=definition.title,
            description=definition.description,
            environment=definition.environment,
            agents=agents,
            skills=definition.skills,
            tools=definition.tools,
            tasks=definition.tasks,
            topology=definition.topology,
            validation=definition.validation,
        ).to_dict()

''',
    )

    text = replace_block(
        text,
        "    def build_definition",
        "    def _build_v2",
        '''    def build_definition(self, scene_key: str) -> SceneDefinition:
        scene_key = self.validate_scene_key(scene_key)
        resource = self.get_resource(scene_key)
        root = self.files.resolve_resource_path(resource.resource_id)
        missing = [
            filename
            for filename in REQUIRED_SCENE_FILES
            if not (root / filename).is_file()
        ]
        if missing:
            raise ValueError(
                f"Scene '{scene_key}' is missing required v2 files: {', '.join(missing)}"
            )
        return self._build_v2(scene_key, resource.resource_id, root)

''',
    )

    text = replace_block(
        text,
        "    def _build_v1",
        "    @staticmethod\n    def _build_topology",
        "",
    )
    write("agent_network/scene_management/scene_storage.py", text)


def patch_scene_manager() -> None:
    path = ROOT / "agent_network/scene_management/scene_manager.py"
    text = path.read_text(encoding="utf-8")
    marker = '''            "description": definition.description,
            "agents": agents,
'''
    replacement = '''            "description": definition.description,
            "environment": definition.environment,
            "agents": agents,
'''
    if marker not in text:
        raise RuntimeError("SceneManager definition payload marker not found")
    write(
        "agent_network/scene_management/scene_manager.py",
        text.replace(marker, replacement, 1),
    )


def patch_package_exports() -> None:
    path = ROOT / "agent_network/scene_management/__init__.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("from .validator import SceneValidator\n", "")
    text = text.replace('    "SceneValidator",\n', "")
    write("agent_network/scene_management/__init__.py", text)


def patch_simulations() -> None:
    path = ROOT / "agent_network/api/simulations.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from agent_network.file_management import ResourceNotFoundError, ResourceNotReadyError\n",
        "",
    )
    insert_after = "from agent_network.experiment_manifest import (\n"
    if "from agent_network.file_management import ResourceNotFoundError" not in text:
        index = text.index(insert_after)
        text = (
            text[:index]
            + "from agent_network.file_management import ResourceNotFoundError, ResourceNotReadyError\n"
            + text[index:]
        )
    text = text.replace("from agent_network.native_capabilities import NativeCapabilityPolicy\n", "")
    text = text.replace("    AgentDef,\n", "")
    if "    SceneValidationError,\n" not in text:
        text = text.replace(
            "    SceneDefinition,\n",
            "    SceneDefinition,\n    SceneValidationError,\n",
            1,
        )
    text = text.replace('_SCENES_DIR = Path("scenes")\n', "")
    topology_fields = '''_TOPOLOGY_LINK_FIELDS = {
    "endpoint_a",
    "endpoint_b",
    "channel_id",
    *_TOPOLOGY_NETWORK_FIELDS,
}
'''
    text = text.replace(topology_fields, "")

    text = replace_block(
        text,
        "def _normalize_backend(",
        "async def setup_simulation(",
        '''def _build_scene_from_folder(scene_name: str) -> SceneDefinition:
    return get_scene_storage().build_definition(scene_name)


async def setup_simulation(''',
    )

    text = replace_block(
        text,
        "async def list_scenes():",
        "async def scene_state_unified():",
        '''async def list_scenes():
    return {"scenes": get_scene_storage().list_scenes()}


async def scene_state_unified():''',
    )

    read_start = text.index("async def read_scene(scene_name: str):")
    text = text[:read_start] + '''async def read_scene(scene_name: str):
    try:
        return get_scene_storage().details(scene_name)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ResourceNotReadyError, SceneValidationError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
'''

    if "json." not in text:
        text = text.replace("import json\n", "")
    if "Path(" not in text:
        text = text.replace("from pathlib import Path\n", "")
    write("agent_network/api/simulations.py", text)


def main() -> None:
    patch_scene_storage()
    patch_scene_manager()
    patch_package_exports()
    patch_simulations()
    legacy_validator = ROOT / "agent_network/scene_management/validator.py"
    if legacy_validator.exists():
        legacy_validator.unlink()
        print("removed agent_network/scene_management/validator.py")


if __name__ == "__main__":
    main()
