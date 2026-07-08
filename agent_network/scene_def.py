"""
场景与 Agent 结构定义、application.jsonl 格式与 API 配置模块。

提供:
- AgentDef 与 SceneDefinition 数据结构
- application.jsonl 的唯一结构定义
- 自动检测可用的 LLM API Key (get_api_config)
"""

import copy
import os
import uuid

from .config import DEFAULT_LLM_MODEL
from typing import Dict, Any, List, ClassVar
from dataclasses import dataclass, field


APPLICATION_JSONL_SCHEMA: Dict[str, Any] = {
    "name": "application.jsonl",
    "format": "jsonl",
    "schema_version": "application.v1",
    "additional_properties": False,
    "fields": {
        "timestamp": {"type": "string", "required": True},
        "seq": {"type": "integer", "required": True, "default": 0},
        "session_id": {"type": "string", "required": True, "default": ""},
        "level": {"type": "string", "required": True, "default": "INFO"},
        "source": {"type": "string", "required": True, "default": "agent"},
        "component": {"type": "string", "required": True, "default": ""},
        "category": {
            "type": "string",
            "required": True,
            "const": "agent_application",
        },
        "layer": {
            "type": "string",
            "required": True,
            "const": "agent_application",
        },
        "event": {"type": "string", "required": True},
        "event_id": {
            "type": "string",
            "required": True,
            "generator": "application_event_id",
        },
        "parent_event_id": {
            "type": "string",
            "required": True,
            "default": "",
        },
        "tick": {"type": "integer", "required": True, "default": 0},
        "actor": {
            "type": "object",
            "required": True,
            "default": {},
            "properties": {
                "agent_id": {"type": "string"},
                "name": {"type": "string"},
                "role": {"type": "string"},
                "backend": {"type": "string"},
            },
        },
        "target": {
            "type": "object",
            "required": True,
            "default": {},
            "properties": {
                "agent_id": {"type": "string"},
                "name": {"type": "string"},
                "role": {"type": "string"},
            },
        },
        "task": {"type": "object", "required": True, "default": {}},
        "conversation": {"type": "object", "required": True, "default": {}},
        "action": {"type": "object", "required": True, "default": {}},
        "content": {"type": "object", "required": True, "default": {}},
        "decision": {"type": "object", "required": True, "default": {}},
        "skill": {"type": "object", "required": True, "default": {}},
        "tool": {"type": "object", "required": True, "default": {}},
        "state_change": {"type": "object", "required": True, "default": {}},
        "policy": {"type": "object", "required": True, "default": {}},
        "result": {"type": "object", "required": True, "default": {}},
        "metrics": {"type": "object", "required": True, "default": {}},
        "links": {
            "type": "object",
            "required": True,
            "default": {
                "network_event_ids": [],
                "audit_event_ids": [],
                "tool_event_ids": [],
                "state_event_ids": [],
                "related_trace_ids": [],
            },
        },
        "trace": {
            "type": "object",
            "required": True,
            "default": {},
            "required_properties": ["trace_id"],
        },
        "message": {
            "type": "string",
            "required": True,
            "generator": "human_summary",
        },
        "debug": {
            "type": "object",
            "required": True,
            "default": {
                "schema_version": "application.v1",
                "emitter": "SimulationLogger",
            },
        },
    },
}


@dataclass
class AgentDef:
    """场景配置中的单个 Agent 定义。"""

    agent_id: str
    role: str  # 直接保存角色 identity 内容
    name: str
    background: str = ""  # 角色经历、组织环境与业务背景
    core_goal: str = ""
    backend: str = "openclaw"
    skill_refs: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)


@dataclass
class SceneDefinition:
    """完整场景定义。application.jsonl 只使用这里声明的结构。"""

    application_log_schema: ClassVar[Dict[str, Any]] = APPLICATION_JSONL_SCHEMA
    scene_key: str = ""
    title: str = ""
    description: str = ""
    agents: List[AgentDef] = field(default_factory=list)
    topology: List[Dict[str, Any]] = field(default_factory=list)


def _is_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _normalize_object(value: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    properties = spec.get("properties")
    if not properties:
        return dict(value)

    normalized = {}
    for name, property_spec in properties.items():
        property_value = value.get(name)
        if _is_type(property_value, property_spec.get("type", "")):
            normalized[name] = property_value
    return normalized


def _application_message(record: Dict[str, Any]) -> str:
    actor_id = (record.get("actor") or {}).get("agent_id", "")
    target_id = (record.get("target") or {}).get("agent_id", "")
    action_name = (record.get("action") or {}).get("name") or record.get("event", "")
    if actor_id and target_id:
        return f"{actor_id} -> {target_id}: {action_name}"
    if actor_id:
        return f"{actor_id}: {action_name}"
    return str(record.get("event", ""))


def normalize_application_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """严格按照 SceneDefinition.application_log_schema 生成应用层记录。"""

    schema = SceneDefinition.application_log_schema
    fields = schema["fields"]
    source = dict(record)

    trace = source.get("trace") if isinstance(source.get("trace"), dict) else {}
    trace = dict(trace)
    trace_id = source.get("trace_id") or trace.get("trace_id")
    if not trace_id:
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"
    trace["trace_id"] = str(trace_id)
    source["trace"] = trace

    actor = source.get("actor") if isinstance(source.get("actor"), dict) else {}
    if "agent_id" not in actor and actor.get("id"):
        actor = {**actor, "agent_id": actor["id"]}
    source["actor"] = actor

    target = source.get("target") if isinstance(source.get("target"), dict) else {}
    if "agent_id" not in target and target.get("id"):
        target = {**target, "agent_id": target["id"]}
    source["target"] = target

    normalized: Dict[str, Any] = {}
    for name, spec in fields.items():
        if "const" in spec:
            normalized[name] = spec["const"]
            continue

        value = source.get(name)
        generator = spec.get("generator")
        if generator == "application_event_id" and not value:
            value = f"app_{uuid.uuid4().hex[:12]}"
        elif generator == "human_summary" and not value:
            value = _application_message(source)

        if value is None and "default" in spec:
            value = copy.deepcopy(spec["default"])

        expected_type = spec.get("type", "")
        if expected_type == "object":
            value = _normalize_object(value, spec)
            defaults = spec.get("default")
            if isinstance(defaults, dict):
                value = {**copy.deepcopy(defaults), **value}
        elif not _is_type(value, expected_type):
            if "default" in spec:
                value = copy.deepcopy(spec["default"])
            elif spec.get("required"):
                raise ValueError(
                    f"application.jsonl field '{name}' must be {expected_type}"
                )
            else:
                continue

        normalized[name] = value

    debug = normalized["debug"]
    debug["schema_version"] = schema["schema_version"]
    normalized["debug"] = debug
    return normalized


def get_api_config() -> Dict[str, str]:
    """获取 LLM API 配置，优先级: 环境变量 > 配置文件"""

    config = {
        "provider": "auto",
        "api_key": "",
        "api_base": "",
        "model": "",
    }

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        config["api_key"] = anthropic_key
        config["provider"] = "anthropic"
        config["model"] = os.environ.get("ANTHROPIC_MODEL", DEFAULT_LLM_MODEL)

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        config["api_key"] = openai_key
        config["provider"] = "openai"
        config["api_base"] = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        config["model"] = os.environ.get("OPENAI_MODEL", "gpt-4o")

    custom_base = os.environ.get("LLM_API_BASE", "")
    if custom_base:
        config["api_base"] = custom_base
        config["provider"] = os.environ.get("LLM_PROVIDER", "openai")

    custom_key = os.environ.get("LLM_API_KEY", "")
    if custom_key:
        config["api_key"] = custom_key

    custom_model = os.environ.get("LLM_MODEL", "")
    if custom_model:
        config["model"] = custom_model

    return config
