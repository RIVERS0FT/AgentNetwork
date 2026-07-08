"""AgentNetwork 分层日志记录与文件管理。

日志只写入 application.jsonl、network.jsonl、system.jsonl。每层日志均由
公共字段 Schema 与 event 专属 Schema 共同规范化；system.jsonl 用于系统
生命周期、异常与调试信息。LogManager 负责记录、查询、下载路径解析、隐藏、
显示和删除。
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, IO, List, Optional


class LogLevel(Enum):
    INFO = 0
    WARN = 1
    ERROR = 2


AGENT_APPLICATION_LAYER = "agent_application"
AGENT_NETWORK_LAYER = "agent_network"
SYSTEM_LAYER = "system"

LOG_TYPE_TO_LAYER = {
    "application": AGENT_APPLICATION_LAYER,
    "network": AGENT_NETWORK_LAYER,
    "system": SYSTEM_LAYER,
}
LAYER_TO_LOG_TYPE = {value: key for key, value in LOG_TYPE_TO_LAYER.items()}
LOG_TYPE_TO_FILENAME = {
    "application": "application.jsonl",
    "network": "network.jsonl",
    "system": "system.jsonl",
}
MANAGED_LOG_FILENAMES = frozenset(LOG_TYPE_TO_FILENAME.values())
VISIBILITY_METADATA_FILENAME = ".log_visibility.json"

APPLICATION_EVENTS = {
    "agent_run_started", "agent_run_completed", "agent_run_failed",
    "agent_message", "agent_message_received", "decide", "act",
    "agent_action", "agent_decide", "skill_use", "tool_call",
    "tool_result", "tool_call_requested", "tool_result_received",
    "state_change", "policy_check", "application_error", "llm_api_call",
    "llm_cli_call", "llm_runtime_completed",
}
NETWORK_EVENTS = {
    "docker_http_inbound", "docker_http_outbound", "llm_api_packet",
    "tcpdump_packet",
}
APPLICATION_CATEGORIES = {
    AGENT_APPLICATION_LAYER, "agent_behavior", "llm_api", "communication",
}
NETWORK_CATEGORIES = {AGENT_NETWORK_LAYER, "network_capture"}


def _object(
    *,
    default: Optional[Dict[str, Any]] = None,
    properties: Optional[Dict[str, Any]] = None,
    required_properties: Optional[List[str]] = None,
) -> Dict[str, Any]:
    spec: Dict[str, Any] = {"type": "object", "required": True, "default": default or {}}
    if properties:
        spec["properties"] = properties
    if required_properties:
        spec["required_properties"] = required_properties
    return spec


FIELD_LIBRARY: Dict[str, Dict[str, Any]] = {
    "actor": _object(properties={
        "agent_id": {"type": "string"}, "name": {"type": "string"},
        "role": {"type": "string"}, "backend": {"type": "string"},
    }),
    "target": _object(properties={
        "agent_id": {"type": "string"}, "name": {"type": "string"},
        "role": {"type": "string"},
    }),
    "network_target": _object(),
    "task": _object(), "conversation": _object(), "action": _object(),
    "content": _object(), "decision": _object(), "skill": _object(),
    "tool": _object(), "state_change": _object(), "policy": _object(),
    "result": _object(), "metrics": _object(), "payload": _object(),
    "network": _object(),
    "links": _object(default={
        "network_event_ids": [], "audit_event_ids": [], "tool_event_ids": [],
        "state_event_ids": [], "related_trace_ids": [],
    }),
    "trace": _object(required_properties=["trace_id"]),
}


def _fields(*names: str, network_target: bool = False) -> Dict[str, Any]:
    result = {name: FIELD_LIBRARY[name] for name in names}
    if network_target and "target" in result:
        result["target"] = FIELD_LIBRARY["network_target"]
    return result


def _event(required: tuple[str, ...], allowed: tuple[str, ...], *, network_target=False):
    return {
        "required_fields": list(required),
        "fields": _fields(*allowed, network_target=network_target),
    }


def _common_fields(layer: str, version: str) -> Dict[str, Any]:
    category = (
        {"type": "string", "required": True, "default": "system"}
        if layer == SYSTEM_LAYER
        else {"type": "string", "required": True, "const": layer}
    )
    return {
        "timestamp": {"type": "string", "required": True},
        "seq": {"type": "integer", "required": True, "default": 0},
        "session_id": {"type": "string", "required": True, "default": ""},
        "level": {"type": "string", "required": True, "default": "INFO"},
        "source": {"type": "string", "required": True, "default": "unknown"},
        "component": {"type": "string", "required": True, "default": ""},
        "category": category,
        "layer": {"type": "string", "required": True, "const": layer},
        "event": {"type": "string", "required": True},
        "event_id": {"type": "string", "required": True, "generator": "event_id"},
        "parent_event_id": {"type": "string", "required": True, "default": ""},
        "tick": {"type": "integer", "required": True, "default": 0},
        "actor": FIELD_LIBRARY["actor"],
        "trace": FIELD_LIBRARY["trace"],
        "message": {"type": "string", "required": True, "generator": "human_summary"},
        "debug": _object(default={"schema_version": version, "emitter": "LogManager"}),
    }


APP_FALLBACK = (
    "target", "task", "conversation", "action", "content", "decision",
    "skill", "tool", "state_change", "policy", "result", "metrics", "links",
)
APP_LAYOUTS = {
    "agent_run_started": (("task", "action"), ("task", "action", "links")),
    "agent_run_completed": (("task", "action", "result"), ("task", "action", "content", "result", "metrics", "links")),
    "agent_run_failed": (("task", "action", "result"), ("task", "action", "result", "links")),
    "agent_message": (("target", "conversation", "action", "content"), ("target", "conversation", "action", "content", "decision", "policy", "result", "links")),
    "agent_message_received": (("target", "conversation", "action", "content"), ("target", "conversation", "action", "content", "result", "links")),
    "decide": (("action", "decision"), ("task", "action", "decision", "result", "metrics", "links")),
    "agent_decide": (("action", "decision"), ("task", "action", "decision", "result", "metrics", "links")),
    "act": (("action",), ("target", "task", "action", "content", "result", "metrics", "links")),
    "agent_action": (("action",), ("target", "task", "action", "content", "result", "metrics", "links")),
    "skill_use": (("action", "skill"), ("target", "task", "action", "skill", "result", "metrics", "links")),
    "tool_call": (("action", "tool"), ("target", "task", "action", "tool", "policy", "result", "links")),
    "tool_call_requested": (("action", "tool"), ("target", "task", "action", "tool", "policy", "result", "links")),
    "tool_result": (("action", "tool", "result"), ("target", "task", "action", "tool", "result", "metrics", "links")),
    "tool_result_received": (("action", "tool", "result"), ("target", "task", "action", "tool", "result", "metrics", "links")),
    "state_change": (("state_change",), ("target", "task", "action", "state_change", "result", "links")),
    "policy_check": (("policy",), ("target", "task", "action", "policy", "result", "links")),
    "application_error": (("result",), ("target", "task", "action", "content", "result", "metrics", "links")),
    "llm_api_call": (("action", "result"), ("task", "action", "content", "result", "metrics", "links")),
    "llm_cli_call": (("action", "result"), ("task", "action", "content", "result", "metrics", "links")),
    "llm_runtime_completed": (("action", "result", "metrics"), ("task", "action", "result", "metrics", "links")),
}

application_log_schema: Dict[str, Any] = {
    "name": "application.jsonl", "format": "jsonl",
    "schema_version": "application.v3", "additional_properties": False,
    "common_fields": _common_fields(AGENT_APPLICATION_LAYER, "application.v3"),
    "event_schemas": {
        "*": _event((), APP_FALLBACK),
        **{name: _event(*layout) for name, layout in APP_LAYOUTS.items()},
    },
}

NETWORK_ALLOWED = ("target", "action", "payload", "network", "result", "metrics", "links")
network_log_schema: Dict[str, Any] = {
    "name": "network.jsonl", "format": "jsonl",
    "schema_version": "network.v1", "additional_properties": False,
    "common_fields": _common_fields(AGENT_NETWORK_LAYER, "network.v1"),
    "event_schemas": {
        "*": _event(("network",), NETWORK_ALLOWED, network_target=True),
        **{
            name: _event(("network",), NETWORK_ALLOWED, network_target=True)
            for name in NETWORK_EVENTS
        },
    },
}

SYSTEM_ALLOWED = ("target", "action", "payload", "result", "metrics")
SYSTEM_LAYOUTS = {
    "session_start": (("payload",), ("payload",)),
    "session_stop": ((), ("payload", "metrics")),
    "event_trigger": (("payload",), ("payload",)),
    "dag_step": (("action", "payload"), ("action", "payload")),
    "system_error": (("result",), ("action", "payload", "result", "metrics")),
}
system_log_schema: Dict[str, Any] = {
    "name": "system.jsonl", "format": "jsonl", "purpose": "debug",
    "schema_version": "system.v1", "additional_properties": False,
    "common_fields": _common_fields(SYSTEM_LAYER, "system.v1"),
    "event_schemas": {
        "*": _event((), SYSTEM_ALLOWED),
        **{name: _event(*layout) for name, layout in SYSTEM_LAYOUTS.items()},
    },
}

LOG_SCHEMAS = {
    AGENT_APPLICATION_LAYER: application_log_schema,
    AGENT_NETWORK_LAYER: network_log_schema,
    SYSTEM_LAYER: system_log_schema,
}


def _normalize_layer(value: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "application": AGENT_APPLICATION_LAYER,
        "application.jsonl": AGENT_APPLICATION_LAYER,
        AGENT_APPLICATION_LAYER: AGENT_APPLICATION_LAYER,
        "network": AGENT_NETWORK_LAYER,
        "network.jsonl": AGENT_NETWORK_LAYER,
        AGENT_NETWORK_LAYER: AGENT_NETWORK_LAYER,
        "system": SYSTEM_LAYER, "system.jsonl": SYSTEM_LAYER,
    }
    return aliases.get(raw, raw)


def normalize_log_type(log_type: str) -> str:
    layer = _normalize_layer(log_type)
    if layer not in LAYER_TO_LOG_TYPE:
        raise ValueError(f"unknown log type {log_type!r}; expected application, network or system")
    return LAYER_TO_LOG_TYPE[layer]


def infer_log_layer(record: Dict[str, Any]) -> str:
    explicit = _normalize_layer(record.get("layer", ""))
    if explicit in LOG_SCHEMAS:
        return explicit
    category, event = str(record.get("category", "")), str(record.get("event", ""))
    if event in NETWORK_EVENTS or category in NETWORK_CATEGORIES:
        return AGENT_NETWORK_LAYER
    if event in APPLICATION_EVENTS or category in APPLICATION_CATEGORIES:
        return AGENT_APPLICATION_LAYER
    return SYSTEM_LAYER


def is_agent_application_record(record: Dict[str, Any]) -> bool:
    return infer_log_layer(record) == AGENT_APPLICATION_LAYER


def is_agent_network_record(record: Dict[str, Any]) -> bool:
    return infer_log_layer(record) == AGENT_NETWORK_LAYER


def is_system_record(record: Dict[str, Any]) -> bool:
    return infer_log_layer(record) == SYSTEM_LAYER


def is_agent_message_record(record: Dict[str, Any]) -> bool:
    return record.get("event") == "agent_message"


def is_behavior_record(record: Dict[str, Any]) -> bool:
    return record.get("event") in {"decide", "act", "agent_action", "agent_decide"} or record.get("category") == "agent_behavior"


def _is_type(value: Any, expected: str) -> bool:
    if expected == "string": return isinstance(value, str)
    if expected == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number": return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean": return isinstance(value, bool)
    if expected == "array": return isinstance(value, list)
    if expected == "object": return isinstance(value, dict)
    return True


def _normalize_object(value: Any, spec: Dict[str, Any], field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        if "default" in spec: value = copy.deepcopy(spec["default"])
        elif spec.get("required"): raise ValueError(f"log field '{field_name}' must be object")
        else: return {}
    properties = spec.get("properties")
    if properties:
        value = {
            name: value[name]
            for name, property_spec in properties.items()
            if name in value and _is_type(value[name], property_spec.get("type", ""))
        }
    else:
        value = dict(value)
    defaults = spec.get("default")
    if isinstance(defaults, dict): value = {**copy.deepcopy(defaults), **value}
    for name in spec.get("required_properties", []):
        if name not in value: raise ValueError(f"log field '{field_name}.{name}' is required")
    return value


def _human_summary(record: Dict[str, Any]) -> str:
    actor = (record.get("actor") or {}).get("agent_id", "")
    target = (record.get("target") or {}).get("agent_id", "")
    action = (record.get("action") or {}).get("name") or record.get("event", "")
    if actor and target: return f"{actor} -> {target}: {action}"
    if actor: return f"{actor}: {action}"
    return str(record.get("event", ""))


def _normalize_record_with_schema(record: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    source, event = dict(record), str(record.get("event") or "")
    event_schema = schema["event_schemas"].get(event, schema["event_schemas"]["*"])
    fields = {**schema["common_fields"], **event_schema.get("fields", {})}
    required_fields = set(event_schema.get("required_fields", []))

    trace = dict(source.get("trace") or {}) if isinstance(source.get("trace"), dict) else {}
    trace["trace_id"] = str(source.get("trace_id") or trace.get("trace_id") or f"trace_{uuid.uuid4().hex[:12]}")
    source["trace"] = trace
    for party in ("actor", "target"):
        value = dict(source.get(party) or {}) if isinstance(source.get(party), dict) else {}
        if "agent_id" not in value and value.get("id"): value["agent_id"] = value["id"]
        source[party] = value

    normalized: Dict[str, Any] = {}
    for name, raw_spec in fields.items():
        spec = dict(raw_spec)
        if name in required_fields:
            spec["required"] = True
            if name not in source:
                raise ValueError(f"{schema['name']} field '{name}' is required for event '{event}'")
        if "const" in spec:
            normalized[name] = spec["const"]
            continue
        value = source.get(name)
        if spec.get("generator") == "event_id" and not value:
            prefix = LAYER_TO_LOG_TYPE[schema["common_fields"]["layer"]["const"]]
            value = f"{prefix}_{uuid.uuid4().hex[:12]}"
        elif spec.get("generator") == "human_summary" and not value:
            value = _human_summary(source)
        if value is None and "default" in spec: value = copy.deepcopy(spec["default"])
        expected = spec.get("type", "")
        if expected == "object":
            value = _normalize_object(value, spec, name)
        elif not _is_type(value, expected):
            if "default" in spec: value = copy.deepcopy(spec["default"])
            elif spec.get("required"): raise ValueError(f"{schema['name']} field '{name}' must be {expected}")
            else: continue
        normalized[name] = value

    debug = normalized["debug"]
    debug.update({
        "schema_version": schema["schema_version"],
        "event_schema": event if event in schema["event_schemas"] else "*",
    })
    if event == "agent_message": debug.setdefault("legacy_network_fields_dropped", True)
    normalized["debug"] = debug
    return normalized


def normalize_application_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_record_with_schema(record, application_log_schema)


def normalize_network_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_record_with_schema(record, network_log_schema)


def normalize_system_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_record_with_schema(record, system_log_schema)


_LOG_TZ = timezone(timedelta(hours=8))


def current_log_timestamp(timespec: str = "milliseconds") -> str:
    now = datetime.now(_LOG_TZ)
    if timespec == "seconds": return now.strftime("%Y-%m-%dT%H:%M:%S")
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def normalize_log_timestamp(value: Any = "", timespec: str = "milliseconds") -> str:
    if not value: return current_log_timestamp(timespec)
    if isinstance(value, datetime): dt = value
    else:
        try: dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except Exception: return current_log_timestamp(timespec)
    if dt.tzinfo is None: dt = dt.replace(tzinfo=_LOG_TZ)
    dt = dt.astimezone(_LOG_TZ)
    if timespec == "seconds": return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


class LogManager:
    """线程安全的分层日志记录、查询和文件管理器。"""
    _instance: Optional["LogManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, name="", max_entries=2000, log_dir=""):
        if self._initialized: return
        self.name, self._max = name or "AgentNetwork", max_entries
        self._entries: deque[Dict[str, Any]] = deque(maxlen=max_entries)
        self._entry_lock, self._file_lock = threading.RLock(), threading.RLock()
        self._management_lock = threading.RLock()
        self._stats, self._seq, self._session_id = self._new_stats(), 0, ""
        self._log_dir = log_dir or os.environ.get("LOG_DIR", "./data/logs")
        self._session_dir = ""
        self._session_application_path = self._session_network_path = self._session_system_path = ""
        self._session_active, self._file_handles = False, {}
        os.makedirs(self._log_dir, exist_ok=True)
        self._initialized = True

    @staticmethod
    def _new_stats():
        return {"total": 0, "by_level": {}, "by_event": {}, "by_agent": {}, "by_layer": {}, "start_time": current_log_timestamp("seconds")}

    def _next_seq(self):
        with self._entry_lock:
            self._seq += 1
            return self._seq

    def start_session(self, scene_name: str) -> str:
        self._close_file_handles()
        safe = scene_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        self._session_id = f"{safe}_{datetime.now(_LOG_TZ).strftime('%Y%m%d_%H%M%S_%f')}"
        self._session_dir = os.path.join(self._log_dir, self._session_id)
        os.makedirs(self._session_dir, exist_ok=True)
        self._set_session_paths(); self._session_active = True; self._seq = 0
        self.emit_system_event("session_start", f"Session started: {scene_name}", "lifecycle", payload={"scene_name": scene_name, "session_dir": self._session_dir})
        return self._session_id

    def set_session_dir(self, session_dir: str):
        resolved, root = os.path.realpath(session_dir), os.path.realpath(self._log_dir)
        if resolved != root and not resolved.startswith(root + os.sep): raise ValueError("session directory must be inside log_dir")
        os.makedirs(resolved, exist_ok=True)
        self._session_dir, self._session_id = resolved, os.path.basename(resolved)
        self._set_session_paths(); self._session_active = True

    def _set_session_paths(self):
        self._session_application_path = os.path.join(self._session_dir, "application.jsonl")
        self._session_network_path = os.path.join(self._session_dir, "network.jsonl")
        self._session_system_path = os.path.join(self._session_dir, "system.jsonl")

    def _path_for_layer(self, layer):
        return {AGENT_APPLICATION_LAYER: self._session_application_path, AGENT_NETWORK_LAYER: self._session_network_path, SYSTEM_LAYER: self._session_system_path}.get(layer, "")

    def _get_file_handle(self, path):
        handle = self._file_handles.get(path)
        if handle is None or handle.closed:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handle = open(path, "a", encoding="utf-8"); self._file_handles[path] = handle
        return handle

    def _close_file_handle(self, path):
        handle = self._file_handles.pop(path, None)
        if handle:
            try: handle.close()
            except Exception: pass

    def _close_file_handles(self):
        with self._file_lock:
            for path in list(self._file_handles): self._close_file_handle(path)

    def _write_file(self, record):
        if not self._session_active: return
        path = self._path_for_layer(record["layer"])
        try:
            with self._file_lock:
                handle = self._get_file_handle(path)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n"); handle.flush()
        except Exception as exc:
            print(f"[LogManager] write failed {path}: {exc}", file=sys.stderr)

    def record(self, record): return self.emit(record)

    def emit(self, record):
        record = dict(record)
        record.update({"seq": self._next_seq(), "session_id": self._session_id, "timestamp": normalize_log_timestamp(record.get("timestamp", ""))})
        layer = infer_log_layer(record); record["layer"] = layer
        normalized = _normalize_record_with_schema(record, LOG_SCHEMAS[layer])
        self._append_memory(normalized); self._write_file(normalized)
        return normalized

    def ingest(self, record):
        record = dict(record)
        record.setdefault("source", "external"); record.setdefault("component", "unknown")
        record.setdefault("category", "system"); record.setdefault("level", "INFO")
        record.setdefault("event", "log")
        return self.emit(record)

    def _append_memory(self, record):
        with self._entry_lock:
            self._entries.append(record); self._stats["total"] += 1
            for group, value in (("by_level", record.get("level", "INFO")), ("by_event", record.get("event", "")), ("by_layer", record.get("layer", SYSTEM_LAYER))):
                self._stats[group][value] = self._stats[group].get(value, 0) + 1
            agent = (record.get("actor") or {}).get("agent_id") or (record.get("actor") or {}).get("id")
            if agent: self._stats["by_agent"][agent] = self._stats["by_agent"].get(agent, 0) + 1

    def emit_application_event(self, event, actor, target=None, task=None, conversation=None, action=None, content=None, decision=None, skill=None, tool=None, state_change=None, policy=None, result=None, metrics=None, links=None, trace_id="", parent_event_id="", tick=None, level="INFO", component="", source="agent", debug=None):
        return self.emit({"level": level, "source": source, "component": component or actor.get("agent_id", "unknown"), "category": AGENT_APPLICATION_LAYER, "layer": AGENT_APPLICATION_LAYER, "event": event, "actor": actor, "target": target or {}, "task": task or {}, "conversation": conversation or {}, "action": action or {}, "content": content or {}, "decision": decision or {}, "skill": skill or {}, "tool": tool or {}, "state_change": state_change or {}, "policy": policy or {}, "result": result or {}, "metrics": metrics or {}, "links": links or {}, "trace_id": trace_id, "parent_event_id": parent_event_id, "tick": tick or 0, "debug": debug or {}})

    def emit_network_event(self, event, network, actor=None, target=None, action=None, payload=None, result=None, metrics=None, links=None, trace_id="", parent_event_id="", tick=None, level="INFO", component="network", source="network", debug=None):
        return self.emit({"level": level, "source": source, "component": component, "category": AGENT_NETWORK_LAYER, "layer": AGENT_NETWORK_LAYER, "event": event, "actor": actor or {}, "target": target or {}, "action": action or {}, "payload": payload or {}, "network": network, "result": result or {}, "metrics": metrics or {}, "links": links or {}, "trace_id": trace_id, "parent_event_id": parent_event_id, "tick": tick or 0, "debug": debug or {}})

    def emit_system_event(self, event, message="", category="system", actor=None, target=None, action=None, payload=None, result=None, metrics=None, trace_id="", parent_event_id="", tick=None, level="INFO", component="srv", source="backend", debug=None):
        return self.emit({"level": level, "source": source, "component": component, "category": category, "layer": SYSTEM_LAYER, "event": event, "actor": actor or {}, "target": target or {}, "action": action or {}, "payload": payload or {}, "result": result or {}, "metrics": metrics or {}, "trace_id": trace_id, "parent_event_id": parent_event_id, "tick": tick or 0, "message": message, "debug": debug or {}})

    def system(self, event, message="", level=LogLevel.INFO, agent_id="", details=None, **kwargs):
        return self.emit_system_event(event, message, actor={"agent_id": agent_id} if agent_id else {}, payload={**(details or {}), **kwargs}, level=level.name)

    def agent_action(self, agent_id, action, result=None, **kwargs):
        return self.emit_application_event("act", {"agent_id": agent_id}, action={"name": action, "status": "success"}, content={"kw": kwargs}, result=result or {})

    def agent_decide(self, agent_id, prompt_snippet, decision=None):
        return self.emit_application_event("decide", {"agent_id": agent_id}, action={"name": "decide", "status": "decided"}, decision={"decision_summary": str(decision) if decision else "", "inputs_used": ["prompt_snippet"], "raw_model_output_ref": prompt_snippet})

    def agent_message(self, from_id, to, content, reasoning="", latency_ms=0, status="success", src_ip="", src_port=0, dst_ip="", dst_port=0, protocol="TCP/HTTP", packet_len=0, header_len=0, payload_len=0, tcp_flags="", channel_id="", message_type="relay", talk=""):
        normalized_status = "failed" if status and any(x in status.lower() for x in ("failed", "error")) else "success"
        return self.emit_application_event("agent_message", {"agent_id": from_id}, target={"agent_id": to}, conversation={"conversation_id": talk, "message_id": f"msg_{uuid.uuid4().hex[:12]}", "message_type": message_type, "channel_id": channel_id, "broadcast": message_type == "broadcast"}, action={"type": "send_message", "name": message_type, "status": normalized_status, "duration_ms": round(latency_ms, 1)}, content={"content_type": "message", "text": content, "summary": content[:120], "size_bytes": payload_len or len((content or "").encode()), "redacted": False}, decision={"decision_summary": reasoning[:200], "reasoning_visible": reasoning[:500]}, policy={"checked": True, "result": "allowed", "rule": "communication_matrix", "reason": ""}, result={"status": normalized_status, "message": status, "error_code": "", "error_message": "" if normalized_status == "success" else status, "retryable": False}, trace_id=talk, debug={"emitter": "LogManager.agent_message", "duration_source": "message_bus_relay_timer", "duration_scope": "bus_receive_to_target_message_response"})

    def container_event(self, agent_id, event, message="", **kwargs):
        return self.emit_system_event(f"container_{event}", f"[{agent_id}] {message or event}", "lifecycle", actor={"agent_id": agent_id}, payload=kwargs)

    def event_trigger(self, turn, event_name, impact):
        return self.emit_system_event("event_trigger", f"Round {turn}: {event_name} — {impact}", payload={"turn": turn, "event_name": event_name, "impact": impact}, tick=turn)

    def dag_step(self, step_id, agent_id, action, round_num, status="started"):
        return self.emit_system_event("dag_step", f"Round {round_num}, Step {step_id}: [{agent_id}] {action} ({status})", "debug", actor={"agent_id": agent_id}, action={"name": action, "status": status}, payload={"step_id": step_id}, tick=round_num)

    def error(self, event, message="", agent_id="", **kwargs):
        return self.emit_system_event(event or "system_error", message, "debug", actor={"agent_id": agent_id} if agent_id else {}, payload=kwargs, result={"status": "failed", "error_message": message}, level="ERROR")

    def get_entries(self, limit=100):
        with self._entry_lock: return list(self._entries)[-limit:]

    def query(self, agent_id=None, event=None, level=None, keyword=None, layer=None, category=None, trace_id=None, task_id=None, limit=50):
        with self._entry_lock: results = list(self._entries)
        if agent_id: results = [e for e in results if agent_id in {(e.get("actor") or {}).get("agent_id"), (e.get("actor") or {}).get("id"), (e.get("target") or {}).get("agent_id"), (e.get("target") or {}).get("id")}]
        if event: results = [e for e in results if e.get("event") == event]
        if layer: results = [e for e in results if infer_log_layer(e) == _normalize_layer(layer)]
        if category: results = [e for e in results if e.get("category") == category]
        if trace_id: results = [e for e in results if e.get("trace_id") == trace_id or (e.get("trace") or {}).get("trace_id") == trace_id]
        if task_id: results = [e for e in results if (e.get("task") or {}).get("task_id") == task_id]
        if level: results = [e for e in results if e.get("level") == level.upper()]
        if keyword:
            word = keyword.lower()
            results = [e for e in results if word in str(e.get("message") or "").lower() or any(word in json.dumps(e.get(name, {}), ensure_ascii=False).lower() for name in ("content", "payload", "result", "network", "tool"))]
        return results[-limit:]

    def get_index_stats(self):
        with self._entry_lock: return copy.deepcopy(self._stats)
    def get_agent_timeline(self, agent_id, limit=50): return self.query(agent_id=agent_id, limit=limit)
    def get_message_log(self, limit=50): return self.query(layer=AGENT_APPLICATION_LAYER, event="agent_message", limit=limit)

    def export(self, fmt="jsonl", limit=0, layer=None):
        entries = self.query(layer=layer, limit=limit or self._max) if layer else self.get_entries(limit or self._max)
        if fmt == "json": return json.dumps(entries, ensure_ascii=False, indent=2)
        if fmt == "csv":
            out, fields = io.StringIO(), ["timestamp", "seq", "session_id", "level", "source", "component", "category", "layer", "event", "message"]
            writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore"); writer.writeheader()
            for entry in entries: writer.writerow({name: entry.get(name, "") for name in fields})
            return out.getvalue()
        return "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries)

    def export_file(self, filepath, fmt="jsonl", limit=0, layer=None):
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as stream: stream.write(self.export(fmt, limit, layer))
        return filepath

    def _resolve_session_dir(self, session_id, require_exists=True):
        if not session_id or session_id in {".", ".."} or Path(session_id).name != session_id: raise ValueError("invalid session_id")
        root, session = Path(self._log_dir).resolve(), (Path(self._log_dir) / session_id).resolve()
        if session.parent != root: raise ValueError("session path escapes log_dir")
        if require_exists and not session.is_dir(): raise FileNotFoundError(f"log session '{session_id}' not found")
        return session

    def resolve_log_path(self, session_id, log_type, require_exists=True):
        log_type, session = normalize_log_type(log_type), self._resolve_session_dir(session_id, require_exists)
        path = session / LOG_TYPE_TO_FILENAME[log_type]
        if require_exists and not path.is_file(): raise FileNotFoundError(f"{path.name} not found in session '{session_id}'")
        return str(path)
    def get_download_path(self, session_id, log_type): return self.resolve_log_path(session_id, log_type)

    def _visibility_path(self, session): return session / VISIBILITY_METADATA_FILENAME
    def _read_visibility(self, session):
        values = {name: True for name in MANAGED_LOG_FILENAMES}; path = self._visibility_path(session)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                values.update({name: bool(data[name]) for name in MANAGED_LOG_FILENAMES if name in data})
            except (OSError, ValueError, TypeError): pass
        return values
    def _write_visibility(self, session, values):
        path, temp = self._visibility_path(session), self._visibility_path(session).with_suffix(".json.tmp")
        temp.write_text(json.dumps({name: bool(values.get(name, True)) for name in sorted(MANAGED_LOG_FILENAMES)}, indent=2), encoding="utf-8"); os.replace(temp, path)

    def set_log_visibility(self, session_id, log_type, visible):
        log_type, session = normalize_log_type(log_type), self._resolve_session_dir(session_id)
        filename = LOG_TYPE_TO_FILENAME[log_type]
        with self._management_lock:
            values = self._read_visibility(session); values[filename] = bool(visible); self._write_visibility(session, values)
        return {"session": session_id, "log_type": log_type, "filename": filename, "visible": bool(visible)}
    def hide_log(self, session_id, log_type): return self.set_log_visibility(session_id, log_type, False)
    def show_log(self, session_id, log_type): return self.set_log_visibility(session_id, log_type, True)

    def list_log_files(self, include_hidden=False):
        root = Path(self._log_dir)
        if not root.is_dir(): return []
        sessions = []
        for session in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
            visibility, files = self._read_visibility(session), []
            for log_type, filename in LOG_TYPE_TO_FILENAME.items():
                path = session / filename
                if not path.is_file(): continue
                visible = visibility.get(filename, True)
                if not visible and not include_hidden: continue
                stat = path.stat(); files.append({"type": log_type, "name": filename, "size_bytes": stat.st_size, "updated_at": datetime.fromtimestamp(stat.st_mtime, _LOG_TZ).isoformat(), "visible": visible, "path": str(path)})
            if files: sessions.append({"session": session.name, "path": str(session), "files": files})
        return sessions

    def delete_log(self, session_id, log_type):
        log_type, path = normalize_log_type(log_type), self.resolve_log_path(session_id, log_type)
        with self._management_lock:
            with self._file_lock: self._close_file_handle(path)
            os.remove(path)
            session = self._resolve_session_dir(session_id); values = self._read_visibility(session)
            values[LOG_TYPE_TO_FILENAME[log_type]] = True; self._write_visibility(session, values)
        if session_id == self._session_id:
            layer = LOG_TYPE_TO_LAYER[log_type]
            with self._entry_lock:
                self._entries = deque((e for e in self._entries if infer_log_layer(e) != layer), maxlen=self._max); self._rebuild_stats()
        return {"session": session_id, "log_type": log_type, "filename": LOG_TYPE_TO_FILENAME[log_type], "deleted": True}

    def delete_session_logs(self, session_id):
        session, deleted = self._resolve_session_dir(session_id), []
        with self._management_lock:
            for filename in LOG_TYPE_TO_FILENAME.values():
                path = session / filename
                if path.is_file():
                    with self._file_lock: self._close_file_handle(str(path))
                    path.unlink(); deleted.append(filename)
            metadata = self._visibility_path(session)
            if metadata.is_file(): metadata.unlink()
        if session_id == self._session_id:
            with self._entry_lock: self._entries.clear(); self._stats = self._new_stats()
        return {"session": session_id, "deleted_files": deleted, "deleted": bool(deleted)}

    def _rebuild_stats(self):
        entries = list(self._entries); self._stats = self._new_stats()
        for entry in entries:
            self._stats["total"] += 1
            for group, value in (("by_level", entry.get("level", "INFO")), ("by_event", entry.get("event", "")), ("by_layer", entry.get("layer", SYSTEM_LAYER))): self._stats[group][value] = self._stats[group].get(value, 0) + 1
            agent = (entry.get("actor") or {}).get("agent_id")
            if agent: self._stats["by_agent"][agent] = self._stats["by_agent"].get(agent, 0) + 1

    def reset(self):
        self._close_file_handles()
        with self._entry_lock: self._entries.clear(); self._stats = self._new_stats(); self._seq = 0
        self._session_id = self._session_dir = ""
        self._session_application_path = self._session_network_path = self._session_system_path = ""
        self._session_active = False
        return self


SimulationLogger = LogManager
_log_manager = LogManager("AgentNetwork")
get_log_manager = lambda: _log_manager
get_logger = get_log_manager
system_log = _log_manager.system
agent_log = _log_manager.agent_action
message_log = _log_manager.agent_message
