from pathlib import Path
try:
    import yaml
except Exception:  # pragma: no cover - optional dependency guard
    yaml = None
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _to_json_schema(inputs: dict) -> dict:
    properties = {}
    required = []

    for name, spec in (inputs or {}).items():
        properties[name] = {
            "type": spec.get("type", "string"),
            "description": spec.get("description", "")
        }

        if "default" in spec:
            properties[name]["default"] = spec["default"]

        if "enum" in spec:
            properties[name]["enum"] = spec["enum"]

        if spec.get("required", False):
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required
    }


def parse_skill_md(path: str | Path) -> dict:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading skill {path}: {e}")
        return {}

    meta = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                if yaml:
                    meta = yaml.safe_load(parts[1]) or {}
                else:
                    meta = {}
                body = parts[2]
            except Exception as e:
                print(f"Error parsing YAML in {path}: {e}")

    name = meta.get("name") or path.stem

    return {
        "name": name,
        "description": meta.get("description", name),
        "version": str(meta.get("version", "1.0")),
        "category": meta.get("category", ""),
        "input_schema": _to_json_schema(meta.get("inputs", {})),
        "output_schema": _to_json_schema(meta.get("outputs", {})),
        "tools": meta.get("tools", []),
        "sop_content": body.strip(),
        "source": str(path)
    }


def load_skill_dir(skill_dir: str | Path) -> list[dict]:
    skill_dir = Path(skill_dir)
    if not skill_dir.exists():
        return []

    skills = []
    for p in sorted(skill_dir.glob("*.md")):
        skill = parse_skill_md(p)
        if skill:
            skills.append(skill)
    return skills


@dataclass
class SkillSpec:
    name: str
    description: str = ""
    version: str = "1.0"
    category: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    tools: List[str] = field(default_factory=list)
    sop_content: str = ""
    source_path: str = ""


class LocalSkillRegistry:
    """Markdown Skill registry.

    Skill is context/SOP metadata only. It is not an executable function tool.
    Atomic executable Tools must be registered through tools.py / MCP.
    """

    def __init__(self, skill_refs=None):
        self.skill_refs = set(skill_refs or [])
        self._skills: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec):
        if spec.name in self.skill_refs:
            self._skills[spec.name] = spec

    def list_skills(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "tools": s.tools
            }
            for s in self._skills.values()
        ]

    def context_specs(self) -> list[dict]:
        """Return Skill specs for backend context injection only."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "category": s.category,
                "input_schema": s.input_schema,
                "output_schema": s.output_schema,
                "tools": s.tools,
                "sop_content": s.sop_content,
                "source_path": s.source_path,
            }
            for s in self._skills.values()
        ]

    def tool_specs(self) -> list[dict]:
        raise RuntimeError(
            "SkillRegistry.tool_specs has been removed. "
            "Markdown Skill must be injected as context, not exposed as executable tools."
        )

    def get(self, name: str) -> Optional[SkillSpec]:
        return self._skills.get(name)

    def has(self, name: str) -> bool:
        return name in self._skills


def load_scene_skill_registry(
    scene_key: str,
    scenes_root: str = "/app/scenes",
    skill_refs: list[str] | None = None
) -> LocalSkillRegistry:
    registry = LocalSkillRegistry(skill_refs)
    skill_dir = Path(scenes_root) / scene_key / "skills"

    if not skill_dir.exists() or not skill_dir.is_dir():
        return registry

    for p in sorted(skill_dir.glob("*.md")):
        parsed = parse_skill_md(p)
        if not parsed:
            continue

        spec = SkillSpec(
            name=parsed["name"],
            description=parsed["description"],
            version=parsed["version"],
            category=parsed["category"],
            input_schema=parsed["input_schema"],
            output_schema=parsed["output_schema"],
            tools=parsed["tools"],
            sop_content=parsed["sop_content"],
            source_path=parsed["source"]
        )
        registry.register(spec)

    return registry
