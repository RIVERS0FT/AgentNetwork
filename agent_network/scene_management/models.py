"""Scene management domain models and validation result contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    relative_path: str
    entrypoint: str


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    source_file: str = "tools.py"


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    agent_id: str
    goal: str
    input: dict[str, Any] = field(default_factory=dict)
    skill_refs: list[str] = field(default_factory=list)
    tool_refs: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    file: str
    json_path: str
    entity_type: str
    entity_id: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    scene_key: str
    validation_status: str = "fully_validated"
    schema_version: str = "agentnetwork-scene.v1"
    validated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def valid(self) -> bool:
        return self.error_count == 0

    def finalize(self) -> "ValidationResult":
        self.validation_status = "fully_validated" if self.valid else "invalid"
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_key": self.scene_key,
            "validation_status": self.validation_status,
            "schema_version": self.schema_version,
            "validated_at": self.validated_at,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class SceneValidationError(ValueError):
    def __init__(self, result: ValidationResult):
        self.result = result.finalize()
        summary = "; ".join(
            f"{issue.file}{issue.json_path}: {issue.message}"
            for issue in self.result.issues[:10]
        )
        if len(self.result.issues) > 10:
            summary += f"; and {len(self.result.issues) - 10} more issue(s)"
        super().__init__(summary or "scene validation failed")


@dataclass(frozen=True)
class SceneListItem:
    scene_key: str
    title: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class SceneSummary:
    scene_key: str
    title: str
    description: str
    agents: list[dict[str, Any]]
    skills: list[SkillDefinition]
    tools: list[ToolDefinition]
    tasks: list[TaskDefinition]
    topology: list[dict[str, Any]]
    validation: ValidationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_key": self.scene_key,
            "title": self.title,
            "description": self.description,
            "agents": self.agents,
            "skills": [asdict(item) for item in self.skills],
            "tools": [asdict(item) for item in self.tools],
            "tasks": [asdict(item) for item in self.tasks],
            "topology": self.topology,
            "validation": self.validation.to_dict(),
        }


@dataclass
class ValidatedScene:
    validation: ValidationResult
    skills: list[SkillDefinition] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    tasks: list[TaskDefinition] = field(default_factory=list)
