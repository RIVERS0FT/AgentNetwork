"""AgentNetwork 日志记录与文件管理。

持久化日志仅包含 application.jsonl、network.jsonl、system.jsonl。
三类日志不共享持久化字段；日志类型只作为内存索引元数据使用。
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
from pathlib import Path
from typing import Any, Dict, IO, List, Optional


LOG_TYPE_TO_FILENAME = {
    "application": "application.jsonl",
    "network": "network.jsonl",
    "system": "system.jsonl",
}
MANAGED_LOG_FILENAMES = frozenset(LOG_TYPE_TO_FILENAME.values())
VISIBILITY_METADATA_FILENAME = ".log_visibility.json"
_INTERNAL_LOG_TYPE = "__log_type"
_INTERNAL_EVENT = "__event"

NETWORK_EVENTS = {
    "docker_http_inbound",
    "docker_http_outbound",
    "llm_api_packet",
    "tcpdump_packet",
}


def _object(
    *,
    default: Optional[Dict[str, Any]] = None,
    properties: Optional[Dict[str, Any]] = None,
    required_properties: Optional[List[str]] = None,
) -> Dict[str, Any]:
    spec: Dict[str, Any] = {
        "type": "object",
        "required": True,
        "default": copy.deepcopy(default) if default is not None else {},
    }
    if properties:
        spec["properties"] = properties
    if required_properties:
        spec["required_properties"] = required_properties
    return spec


FIELD_LIBRARY: Dict[str, Dict[str, Any]] = {
    "actor": _object(
        properties={
            "agent_id": {"type": "string"},
            "name": {"type": "string"},
            "role": {"type": "string"},
            "backend": {"type": "string"},
        }
    ),
    "target": _object(
        properties={
            "agent_id": {"type": "string"},
            "name": {"type": "string"},
            "role": {"type": "string"},
        }
    ),
    "open_target": _object(),
    "task": _object(),
    "conversation": _object(),
    "action": _object(),
    "content": _object(),
    "skill": _object(),
    "tool": _object(),
    "state_change": _object(),
    "result": _object(),
    "metrics": _object(),
    "payload": _object(),
    "network": _object(),
    "links": _object(
        default={
            "network_event_ids": [],
            "audit_event_ids": [],
            "tool_event_ids": [],
            "state_event_ids": [],
            "related_trace_ids": [],
        }
    ),
    "trace": _object(required_properties=["trace_id"]),
}


def _fields(*names: str, open_target: bool = False) -> Dict[str, Any]:
    result = {name: copy.deepcopy(FIELD_LIBRARY[name]) for name in names}
    if open_target and "target" in result:
        result["target"] = copy.deepcopy(FIELD_LIBRARY["open_target"])
    return result


def _event(
    required: tuple[str, ...],
    allowed: tuple[str, ...],
    *,
    open_target: bool = False,
) -> Dict[str, Any]:
    return {
        "required_fields": list(required),
        "fields": _fields(*allowed, open_target=open_target),
    }


def _application_fields() -> Dict[str, Any]:
    return {
        "timestamp": {"type": "string", "required": True},
        "event": {"type": "string", "required": True},
        "event_id": {
            "type": "string",
            "required": True,
            "generator": "event_id",
        },
        "parent_event_id": {
            "type": "string",
            "required": True,
            "default": "",
        },
        "actor": copy.deepcopy(FIELD_LIBRARY["actor"]),
        "trace": copy.deepcopy(FIELD_LIBRARY["trace"]),
    }


def _network_fields() -> Dict[str, Any]:
    return {
        "timestamp": {"type": "string", "required": True},
        "event": {"type": "string", "required": True},
        "event_id": {
            "type": "string",
            "required": True,
            "generator": "event_id",
        },
        "parent_event_id": {
            "type": "string",
            "required": True,
            "default": "",
        },
        "actor": copy.deepcopy(FIELD_LIBRARY["actor"]),
        "trace": copy.deepcopy(FIELD_LIBRARY["trace"]),
    }


def _system_fields(version: str) -> Dict[str, Any]:
    return {
        "timestamp": {"type": "string", "required": True},
        "level": {"type": "string", "required": True, "default": "INFO"},
        "source": {"type": "string", "required": True, "default": "unknown"},
        "debug": _object(
            default={"schema_version": version, "emitter": "LogManager"}
        ),
    }


APP_LAYOUTS = {
    "agent_run_started": (
        ("task", "action"),
        ("task", "action", "links"),
    ),
    "agent_run_completed": (
        ("task", "action", "result"),
        ("task", "action", "content", "result", "metrics", "links"),
    ),
    "agent_run_failed": (
        ("task", "action", "result"),
        ("task", "action", "result", "links"),
    ),
    "agent_message": (
        ("target", "conversation", "action", "content"),
        (
            "target",
            "conversation",
            "action",
            "content",
            "result",
            "links",
        ),
    ),
    "agent_message_received": (
        ("target", "conversation", "action", "content"),
        ("target", "conversation", "action", "content", "result", "links"),
    ),
    "reasoning": (
        ("action",),
        ("task", "action", "content", "result", "metrics", "links"),
    ),
    "acting": (
        ("action",),
        ("target", "task", "action", "content", "result", "metrics", "links"),
    ),
    "skill_use": (
        ("action", "skill"),
        ("target", "task", "action", "skill", "result", "metrics", "links"),
    ),
    "tool_call": (
        ("action", "tool"),
        ("target", "task", "action", "tool", "result", "links"),
    ),
    "tool_call_requested": (
        ("action", "tool"),
        ("target", "task", "action", "tool", "result", "links"),
    ),
    "tool_result": (
        ("action", "tool", "result"),
        ("target", "task", "action", "tool", "result", "metrics", "links"),
    ),
    "tool_result_received": (
        ("action", "tool", "result"),
        ("target", "task", "action", "tool", "result", "metrics", "links"),
    ),
    "state_change": (
        ("state_change",),
        ("target", "task", "action", "state_change", "result", "links"),
    ),
    "policy_check": (
        ("result",),
        ("target", "task", "action", "result", "links"),
    ),
    "application_error": (
        ("result",),
        ("target", "task", "action", "content", "result", "metrics", "links"),
    ),
    "llm_api_call": (
        ("action",),
        (
            "target",
            "task",
            "action",
            "content",
            "payload",
            "result",
            "metrics",
            "links",
        ),
    ),
    "llm_runtime_completed": (
        ("action", "result", "metrics"),
        ("task", "action", "payload", "result", "metrics", "links"),
    ),
}
APPLICATION_EVENTS = frozenset(APP_LAYOUTS)

application_log_schema: Dict[str, Any] = {
    "name": "application.jsonl",
    "format": "jsonl",
    "log_type": "application",
    "schema_version": "application.v9",
    "additional_properties": False,
    "type_fields": _application_fields(),
    "event_schemas": {
        name: _event(
            *layout,
            open_target=name == "llm_api_call",
        )
        for name, layout in APP_LAYOUTS.items()
    },
}

NETWORK_ALLOWED = (
    "target",
    "action",
    "payload",
    "network",
    "result",
    "metrics",
    "links",
)
network_log_schema: Dict[str, Any] = {
    "name": "network.jsonl",
    "format": "jsonl",
    "log_type": "network",
    "schema_version": "network.v3",
    "additional_properties": False,
    "type_fields": _network_fields(),
    "event_schemas": {
        "*": _event(("network",), NETWORK_ALLOWED, open_target=True),
        **{
            name: _event(("network",), NETWORK_ALLOWED, open_target=True)
            for name in NETWORK_EVENTS
        },
    },
}

SYSTEM_ALLOWED = ("action", "payload", "result", "metrics")
SYSTEM_LAYOUTS = {
    "session_start": (("payload",), ("payload",)),
    "session_stop": ((), ("payload", "metrics")),
    "event_trigger": (("payload",), ("payload",)),
    "dag_step": (("action", "payload"), ("action", "payload")),
    "system_error": (
        ("result",),
        ("action", "payload", "result", "metrics"),
    ),
}
system_log_schema: Dict[str, Any] = {
    "name": "system.jsonl",
    "format": "jsonl",
    "purpose": "debug",
    "log_type": "system",
    "schema_version": "system.v3",
    "additional_properties": False,
    "type_fields": _system_fields("system.v3"),
    "event_schemas": {
        "*": _event((), SYSTEM_ALLOWED),
        **{name: _event(*layout) for name, layout in SYSTEM_LAYOUTS.items()},
    },
}

LOG_SCHEMAS = {
    "application": application_log_schema,
    "network": network_log_schema,
    "system": system_log_schema,
}


def normalize_log_type(log_type: str) -> str:
    normalized = str(log_type or "").strip().lower()
    if normalized not in LOG_SCHEMAS:
        raise ValueError(
            f"unknown log type {log_type!r}; expected application, network or system"
        )
    return normalized


def infer_log_type(record: Dict[str, Any]) -> str:
    if record.get("log_type"):
        return normalize_log_type(str(record["log_type"]))
    if record.get(_INTERNAL_LOG_TYPE):
        return normalize_log_type(str(record[_INTERNAL_LOG_TYPE]))

    event = str(record.get("event", ""))
    if event in NETWORK_EVENTS:
        return "network"
    if event in APPLICATION_EVENTS:
        return "application"
    return "system"


def is_agent_application_record(record: Dict[str, Any]) -> bool:
    return infer_log_type(record) == "application"


def is_agent_network_record(record: Dict[str, Any]) -> bool:
    return infer_log_type(record) == "network"


def is_system_record(record: Dict[str, Any]) -> bool:
    return infer_log_type(record) == "system"


def is_agent_message_record(record: Dict[str, Any]) -> bool:
    return (
        record.get("event") == "agent_message"
        or record.get(_INTERNAL_EVENT) == "agent_message"
    )


def is_behavior_record(record: Dict[str, Any]) -> bool:
    event = record.get("event") or record.get(_INTERNAL_EVENT)
    return event in {"reasoning", "acting"}


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


def _normalize_object(
    value: Any,
    spec: Dict[str, Any],
    field_name: str,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        if "default" in spec:
            value = copy.deepcopy(spec["default"])
        elif spec.get("required"):
            raise ValueError(f"log field '{field_name}' must be object")
        else:
            return {}

    properties = spec.get("properties")
    if properties:
        value = {
            name: value[name]
            for name, property_spec in properties.items()
            if name in value
            and _is_type(value[name], property_spec.get("type", ""))
        }
    else:
        value = dict(value)

    defaults = spec.get("default")
    if isinstance(defaults, dict):
        value = {**copy.deepcopy(defaults), **value}

    for name in spec.get("required_properties", []):
        if name not in value:
            raise ValueError(f"log field '{field_name}.{name}' is required")
    return value


def _normalize_record_with_schema(
    record: Dict[str, Any],
    schema: Dict[str, Any],
    event: str,
) -> Dict[str, Any]:
    source = dict(record)
    event_schema = schema["event_schemas"].get(event)
    if event_schema is None:
        if schema["log_type"] == "application":
            raise ValueError(f"unknown application event: {event}")
        event_schema = schema["event_schemas"]["*"]

    fields = {
        **schema["type_fields"],
        **event_schema.get("fields", {}),
    }
    required_fields = set(event_schema.get("required_fields", []))

    if "trace" in fields:
        trace = (
            dict(source.get("trace") or {})
            if isinstance(source.get("trace"), dict)
            else {}
        )
        trace["trace_id"] = str(
            trace.get("trace_id") or f"trace_{uuid.uuid4().hex[:12]}"
        )
        source["trace"] = trace

    normalized: Dict[str, Any] = {}
    for name, raw_spec in fields.items():
        spec = copy.deepcopy(raw_spec)
        if name in required_fields:
            spec["required"] = True
            if name not in source:
                raise ValueError(
                    f"{schema['name']} field '{name}' is required "
                    f"for event '{event}'"
                )

        value = source.get(name)
        if spec.get("generator") == "event_id" and not value:
            value = f"{schema['log_type']}_{uuid.uuid4().hex[:12]}"
        if value is None and "default" in spec:
            value = copy.deepcopy(spec["default"])

        expected = spec.get("type", "")
        if expected == "object":
            value = _normalize_object(value, spec, name)
        elif not _is_type(value, expected):
            if "default" in spec:
                value = copy.deepcopy(spec["default"])
            elif spec.get("required"):
                raise ValueError(
                    f"{schema['name']} field '{name}' must be {expected}"
                )
            else:
                continue
        normalized[name] = value

    if "debug" in normalized:
        debug = normalized["debug"]
        debug.update(
            {
                "schema_version": schema["schema_version"],
                "event": event,
                "event_schema": (
                    event if event in schema["event_schemas"] else "*"
                ),
            }
        )
        normalized["debug"] = debug
    return normalized


_LOG_TZ = timezone(timedelta(hours=8))


def current_log_timestamp(timespec: str = "milliseconds") -> str:
    now = datetime.now(_LOG_TZ)
    if timespec == "seconds":
        return now.strftime("%Y-%m-%dT%H:%M:%S")
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def normalize_log_timestamp(
    value: Any = "",
    timespec: str = "milliseconds",
) -> str:
    if not value:
        return current_log_timestamp(timespec)
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except Exception:
            return current_log_timestamp(timespec)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOG_TZ)
    dt = dt.astimezone(_LOG_TZ)
    if timespec == "seconds":
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _trace(trace_id: str = "") -> Dict[str, str]:
    return {"trace_id": str(trace_id or f"trace_{uuid.uuid4().hex[:12]}")}


def _public_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in entry.items()
        if not key.startswith("__")
    }


class LogManager:
    """线程安全的日志记录、查询和文件管理器。"""

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
        if self._initialized:
            return
        self.name = name or "AgentNetwork"
        self._max = max_entries
        self._entries: deque[Dict[str, Any]] = deque(maxlen=max_entries)
        self._entry_lock = threading.RLock()
        self._file_lock = threading.RLock()
        self._management_lock = threading.RLock()
        self._stats = self._new_stats()
        self._session_id = ""
        self._log_dir = log_dir or os.environ.get("LOG_DIR", "./data/logs")
        self._session_dir = ""
        self._session_application_path = ""
        self._session_network_path = ""
        self._session_system_path = ""
        self._session_active = False
        self._file_handles: Dict[str, IO] = {}
        os.makedirs(self._log_dir, exist_ok=True)
        self._initialized = True

    @staticmethod
    def _new_stats() -> Dict[str, Any]:
        return {
            "total": 0,
            "by_level": {},
            "by_event": {},
            "by_agent": {},
            "by_log_type": {},
            "start_time": current_log_timestamp("seconds"),
        }

    def start_session(self, scene_name: str) -> str:
        self._close_file_handles()
        safe = (
            scene_name.replace("/", "_")
            .replace("\\", "_")
            .replace(" ", "_")
        )
        self._session_id = (
            f"{safe}_{datetime.now(_LOG_TZ).strftime('%Y%m%d_%H%M%S_%f')}"
        )
        self._session_dir = os.path.join(self._log_dir, self._session_id)
        os.makedirs(self._session_dir, exist_ok=True)
        self._set_session_paths()
        self._session_active = True
        self.emit_system_event(
            "session_start",
            f"Session started: {scene_name}",
            kind="lifecycle",
            payload={
                "scene_name": scene_name,
                "session_dir": self._session_dir,
            },
        )
        return self._session_id

    def set_session_dir(self, session_dir: str):
        resolved = os.path.realpath(session_dir)
        root = os.path.realpath(self._log_dir)
        if resolved != root and not resolved.startswith(root + os.sep):
            raise ValueError("session directory must be inside log_dir")
        os.makedirs(resolved, exist_ok=True)
        self._session_dir = resolved
        self._session_id = os.path.basename(resolved)
        self._set_session_paths()
        self._session_active = True

    def _set_session_paths(self):
        self._session_application_path = os.path.join(
            self._session_dir,
            "application.jsonl",
        )
        self._session_network_path = os.path.join(
            self._session_dir,
            "network.jsonl",
        )
        self._session_system_path = os.path.join(
            self._session_dir,
            "system.jsonl",
        )

    def _path_for_log_type(self, log_type: str) -> str:
        return {
            "application": self._session_application_path,
            "network": self._session_network_path,
            "system": self._session_system_path,
        }.get(log_type, "")

    def _get_file_handle(self, path: str) -> IO:
        handle = self._file_handles.get(path)
        if handle is None or handle.closed:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handle = open(path, "a", encoding="utf-8")
            self._file_handles[path] = handle
        return handle

    def _close_file_handle(self, path: str):
        handle = self._file_handles.pop(path, None)
        if handle:
            try:
                handle.close()
            except Exception:
                pass

    def _close_file_handles(self):
        with self._file_lock:
            for path in list(self._file_handles):
                self._close_file_handle(path)

    def _write_file(self, log_type: str, record: Dict[str, Any]):
        if not self._session_active:
            return
        path = self._path_for_log_type(log_type)
        try:
            with self._file_lock:
                handle = self._get_file_handle(path)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
        except Exception as exc:
            print(f"[LogManager] write failed {path}: {exc}", file=sys.stderr)

    def emit(
        self,
        record: Dict[str, Any],
        log_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_record = dict(record)
        resolved_type = (
            normalize_log_type(log_type)
            if log_type
            else infer_log_type(source_record)
        )
        event = str(source_record.get("event") or f"{resolved_type}_event")
        source_record["timestamp"] = normalize_log_timestamp(
            source_record.get("timestamp", "")
        )

        normalized = _normalize_record_with_schema(
            source_record,
            LOG_SCHEMAS[resolved_type],
            event,
        )
        self._append_memory(normalized, resolved_type, event)
        self._write_file(resolved_type, normalized)
        return copy.deepcopy(normalized)

    def ingest(
        self,
        record: Dict[str, Any],
        log_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_record = dict(record)
        resolved_type = (
            normalize_log_type(log_type)
            if log_type
            else infer_log_type(source_record)
        )
        source_record.setdefault("event", f"{resolved_type}_event")
        if resolved_type == "system":
            source_record.setdefault("level", "INFO")
            source_record.setdefault("source", "external")
            source_record.setdefault("debug", {})
        return self.emit(source_record, log_type=resolved_type)

    def _append_memory(
        self,
        record: Dict[str, Any],
        log_type: str,
        event: str,
    ):
        internal = copy.deepcopy(record)
        internal[_INTERNAL_LOG_TYPE] = log_type
        internal[_INTERNAL_EVENT] = event
        with self._entry_lock:
            self._entries.append(internal)
            self._increment_stats(internal)

    def _increment_stats(self, entry: Dict[str, Any]):
        self._stats["total"] += 1
        log_type = entry.get(_INTERNAL_LOG_TYPE, "system")
        event = entry.get("event") or entry.get(_INTERNAL_EVENT, "")
        self._stats["by_log_type"][log_type] = (
            self._stats["by_log_type"].get(log_type, 0) + 1
        )
        if event:
            self._stats["by_event"][event] = (
                self._stats["by_event"].get(event, 0) + 1
            )
        level = entry.get("level")
        if level:
            self._stats["by_level"][level] = (
                self._stats["by_level"].get(level, 0) + 1
            )
        agent = (entry.get("actor") or {}).get("agent_id")
        if agent:
            self._stats["by_agent"][agent] = (
                self._stats["by_agent"].get(agent, 0) + 1
            )

    def emit_application_event(
        self,
        event,
        actor,
        target=None,
        task=None,
        conversation=None,
        action=None,
        content=None,
        skill=None,
        tool=None,
        state_change=None,
        result=None,
        metrics=None,
        payload=None,
        links=None,
        trace_id="",
        parent_event_id="",
    ):
        return self.emit(
            {
                "event": event,
                "actor": actor,
                "target": target or {},
                "task": task or {},
                "conversation": conversation or {},
                "action": action or {},
                "content": content or {},
                "skill": skill or {},
                "tool": tool or {},
                "state_change": state_change or {},
                "result": result or {},
                "metrics": metrics or {},
                "payload": payload or {},
                "links": links or {},
                "trace": _trace(trace_id),
                "parent_event_id": parent_event_id,
            },
            log_type="application",
        )

    def emit_network_event(
        self,
        event,
        network,
        actor=None,
        target=None,
        action=None,
        payload=None,
        result=None,
        metrics=None,
        links=None,
        trace_id="",
        parent_event_id="",
    ):
        return self.emit(
            {
                "event": event,
                "actor": actor or {},
                "target": target or {},
                "action": action or {},
                "payload": payload or {},
                "network": network,
                "result": result or {},
                "metrics": metrics or {},
                "links": links or {},
                "trace": _trace(trace_id),
                "parent_event_id": parent_event_id,
            },
            log_type="network",
        )

    def emit_system_event(
        self,
        event,
        message="",
        kind="system",
        actor=None,
        target=None,
        action=None,
        payload=None,
        result=None,
        metrics=None,
        trace_id="",
        level="INFO",
        source="backend.srv",
        debug=None,
    ):
        system_payload = dict(payload or {})
        if message:
            system_payload.setdefault("message", message)

        system_debug = dict(debug or {})
        if kind and kind != "system":
            system_debug.setdefault("kind", kind)
        context: Dict[str, Any] = {}
        if actor:
            context["actor"] = actor
        if target:
            context["target"] = target
        if trace_id:
            context["trace_id"] = trace_id
        if context:
            system_debug.setdefault("context", context)

        return self.emit(
            {
                "event": event,
                "level": str(level).upper(),
                "source": source,
                "debug": system_debug,
                "action": action or {},
                "payload": system_payload,
                "result": result or {},
                "metrics": metrics or {},
            },
            log_type="system",
        )

    def system(
        self,
        event,
        message="",
        level="INFO",
        agent_id="",
        details=None,
        **kwargs,
    ):
        return self.emit_system_event(
            event,
            message,
            actor={"agent_id": agent_id} if agent_id else {},
            payload={**(details or {}), **kwargs},
            level=str(level).upper(),
        )

    def acting(self, agent_id, action, result=None, **kwargs):
        return self.emit_application_event(
            "acting",
            {"agent_id": agent_id},
            action={"name": action, "status": "success"},
            content={"kw": kwargs},
            result=result or {},
        )

    def reasoning(self, agent_id, prompt_snippet, result=None):
        return self.emit_application_event(
            "reasoning",
            {"agent_id": agent_id},
            action={"name": "reasoning", "status": "completed"},
            content={
                "content_type": "reasoning",
                "text": prompt_snippet,
                "summary": str(result) if result else "",
            },
            result=result or {},
        )

    def agent_message(
        self,
        from_id,
        to,
        content,
        latency_ms=0,
        status="success",
        payload_len=0,
        channel_id="",
        message_type="relay",
        talk="",
    ):
        normalized_status = (
            "failed"
            if status
            and any(marker in status.lower() for marker in ("failed", "error"))
            else "success"
        )
        return self.emit_application_event(
            "agent_message",
            {"agent_id": from_id},
            target={"agent_id": to},
            conversation={
                "conversation_id": talk,
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "message_type": message_type,
                "channel_id": channel_id,
                "broadcast": message_type == "broadcast",
            },
            action={
                "type": "send_message",
                "name": message_type,
                "status": normalized_status,
                "duration_ms": round(latency_ms, 1),
            },
            content={
                "content_type": "message",
                "text": content,
                "summary": content[:120],
                "size_bytes": payload_len
                or len((content or "").encode("utf-8")),
                "redacted": False,
            },
            result={
                "status": normalized_status,
                "message": status,
                "error_code": "",
                "error_message": (
                    "" if normalized_status == "success" else status
                ),
                "retryable": False,
            },
            trace_id=talk,
        )

    def container_event(self, agent_id, event, message="", **kwargs):
        return self.emit_system_event(
            f"container_{event}",
            f"[{agent_id}] {message or event}",
            kind="lifecycle",
            actor={"agent_id": agent_id},
            payload=kwargs,
        )

    def event_trigger(self, turn, event_name, impact):
        return self.emit_system_event(
            "event_trigger",
            f"Round {turn}: {event_name} — {impact}",
            payload={
                "turn": turn,
                "event_name": event_name,
                "impact": impact,
            },
        )

    def dag_step(
        self,
        step_id,
        agent_id,
        action,
        round_num,
        status="started",
    ):
        return self.emit_system_event(
            "dag_step",
            f"Round {round_num}, Step {step_id}: "
            f"[{agent_id}] {action} ({status})",
            kind="debug",
            actor={"agent_id": agent_id},
            action={"name": action, "status": status},
            payload={"step_id": step_id, "round": round_num},
        )

    def error(self, event, message="", agent_id="", **kwargs):
        return self.emit_system_event(
            event or "system_error",
            message,
            kind="debug",
            actor={"agent_id": agent_id} if agent_id else {},
            payload=kwargs,
            result={"status": "failed", "error_message": message},
            level="ERROR",
        )

    def get_entries(self, limit=100) -> List[Dict[str, Any]]:
        with self._entry_lock:
            return [_public_entry(entry) for entry in list(self._entries)[-limit:]]

    def query(
        self,
        agent_id=None,
        event=None,
        level=None,
        keyword=None,
        log_type=None,
        trace_id=None,
        task_id=None,
        limit=50,
    ) -> List[Dict[str, Any]]:
        normalized_type = normalize_log_type(log_type) if log_type else None
        with self._entry_lock:
            results = list(self._entries)

        if normalized_type:
            results = [
                entry
                for entry in results
                if entry.get(_INTERNAL_LOG_TYPE) == normalized_type
            ]
        if agent_id:
            results = [
                entry
                for entry in results
                if agent_id
                in {
                    (entry.get("actor") or {}).get("agent_id"),
                    (entry.get("target") or {}).get("agent_id"),
                }
            ]
        if event:
            results = [
                entry
                for entry in results
                if (entry.get("event") or entry.get(_INTERNAL_EVENT)) == event
            ]
        if trace_id:
            results = [
                entry
                for entry in results
                if (entry.get("trace") or {}).get("trace_id") == trace_id
                or (
                    (entry.get("debug") or {}).get("context") or {}
                ).get("trace_id")
                == trace_id
            ]
        if task_id:
            results = [
                entry
                for entry in results
                if (entry.get("task") or {}).get("task_id") == task_id
            ]
        if level:
            results = [
                entry
                for entry in results
                if entry.get("level") == str(level).upper()
            ]
        if keyword:
            word = keyword.lower()
            results = [
                entry
                for entry in results
                if word
                in json.dumps(
                    _public_entry(entry),
                    ensure_ascii=False,
                ).lower()
            ]
        return [_public_entry(entry) for entry in results[-limit:]]

    def get_index_stats(self) -> Dict[str, Any]:
        with self._entry_lock:
            return copy.deepcopy(self._stats)

    def get_agent_timeline(self, agent_id, limit=50):
        return self.query(agent_id=agent_id, limit=limit)

    def get_message_log(self, limit=50):
        return self.query(
            log_type="application",
            event="agent_message",
            limit=limit,
        )

    def export(self, fmt="jsonl", limit=0, log_type=None):
        entries = (
            self.query(
                log_type=log_type,
                limit=limit or self._max,
            )
            if log_type
            else self.get_entries(limit or self._max)
        )
        if fmt == "json":
            return json.dumps(entries, ensure_ascii=False, indent=2)
        if fmt == "csv":
            output = io.StringIO()
            fieldnames: List[str] = []
            for entry in entries:
                for name in entry:
                    if name not in fieldnames:
                        fieldnames.append(name)
            writer = csv.DictWriter(
                output,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            for entry in entries:
                writer.writerow(
                    {
                        name: (
                            json.dumps(value, ensure_ascii=False)
                            if isinstance(value, (dict, list))
                            else value
                        )
                        for name, value in entry.items()
                    }
                )
            return output.getvalue()
        return "\n".join(
            json.dumps(entry, ensure_ascii=False) for entry in entries
        )

    def export_file(
        self,
        filepath,
        fmt="jsonl",
        limit=0,
        log_type=None,
    ):
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as stream:
            stream.write(
                self.export(
                    fmt,
                    limit,
                    log_type=log_type,
                )
            )
        return filepath

    def _resolve_session_dir(self, session_id, require_exists=True):
        if (
            not session_id
            or session_id in {".", ".."}
            or Path(session_id).name != session_id
        ):
            raise ValueError("invalid session_id")
        root = Path(self._log_dir).resolve()
        session = (Path(self._log_dir) / session_id).resolve()
        if session.parent != root:
            raise ValueError("session path escapes log_dir")
        if require_exists and not session.is_dir():
            raise FileNotFoundError(f"log session '{session_id}' not found")
        return session

    def resolve_log_path(
        self,
        session_id,
        log_type,
        require_exists=True,
    ):
        normalized_type = normalize_log_type(log_type)
        session = self._resolve_session_dir(session_id, require_exists)
        path = session / LOG_TYPE_TO_FILENAME[normalized_type]
        if require_exists and not path.is_file():
            raise FileNotFoundError(
                f"{path.name} not found in session '{session_id}'"
            )
        return str(path)

    def get_download_path(self, session_id, log_type):
        return self.resolve_log_path(session_id, log_type)

    def _visibility_path(self, session):
        return session / VISIBILITY_METADATA_FILENAME

    def _read_visibility(self, session):
        values = {name: True for name in MANAGED_LOG_FILENAMES}
        path = self._visibility_path(session)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                values.update(
                    {
                        name: bool(data[name])
                        for name in MANAGED_LOG_FILENAMES
                        if name in data
                    }
                )
            except (OSError, ValueError, TypeError):
                pass
        return values

    def _write_visibility(self, session, values):
        path = self._visibility_path(session)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(
                {
                    name: bool(values.get(name, True))
                    for name in sorted(MANAGED_LOG_FILENAMES)
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temp, path)

    def set_log_visibility(self, session_id, log_type, visible):
        normalized_type = normalize_log_type(log_type)
        session = self._resolve_session_dir(session_id)
        filename = LOG_TYPE_TO_FILENAME[normalized_type]
        with self._management_lock:
            values = self._read_visibility(session)
            values[filename] = bool(visible)
            self._write_visibility(session, values)
        return {
            "session": session_id,
            "log_type": normalized_type,
            "filename": filename,
            "visible": bool(visible),
        }

    def hide_log(self, session_id, log_type):
        return self.set_log_visibility(session_id, log_type, False)

    def show_log(self, session_id, log_type):
        return self.set_log_visibility(session_id, log_type, True)

    def list_log_files(self, include_hidden=False):
        root = Path(self._log_dir)
        if not root.is_dir():
            return []
        sessions = []
        for session in sorted(
            (path for path in root.iterdir() if path.is_dir()),
            reverse=True,
        ):
            visibility = self._read_visibility(session)
            files = []
            for log_type, filename in LOG_TYPE_TO_FILENAME.items():
                path = session / filename
                if not path.is_file():
                    continue
                visible = visibility.get(filename, True)
                if not visible and not include_hidden:
                    continue
                stat = path.stat()
                files.append(
                    {
                        "type": log_type,
                        "name": filename,
                        "size_bytes": stat.st_size,
                        "updated_at": datetime.fromtimestamp(
                            stat.st_mtime,
                            _LOG_TZ,
                        ).isoformat(),
                        "visible": visible,
                        "path": str(path),
                    }
                )
            if files:
                sessions.append(
                    {
                        "session": session.name,
                        "path": str(session),
                        "files": files,
                    }
                )
        return sessions

    def delete_log(self, session_id, log_type):
        normalized_type = normalize_log_type(log_type)
        path = self.resolve_log_path(session_id, normalized_type)
        with self._management_lock:
            with self._file_lock:
                self._close_file_handle(path)
            os.remove(path)
            session = self._resolve_session_dir(session_id)
            values = self._read_visibility(session)
            values[LOG_TYPE_TO_FILENAME[normalized_type]] = True
            self._write_visibility(session, values)

        if session_id == self._session_id:
            with self._entry_lock:
                self._entries = deque(
                    (
                        entry
                        for entry in self._entries
                        if entry.get(_INTERNAL_LOG_TYPE) != normalized_type
                    ),
                    maxlen=self._max,
                )
                self._rebuild_stats()
        return {
            "session": session_id,
            "log_type": normalized_type,
            "filename": LOG_TYPE_TO_FILENAME[normalized_type],
            "deleted": True,
        }

    def delete_session_logs(self, session_id):
        session = self._resolve_session_dir(session_id)
        deleted = []
        with self._management_lock:
            for filename in LOG_TYPE_TO_FILENAME.values():
                path = session / filename
                if path.is_file():
                    with self._file_lock:
                        self._close_file_handle(str(path))
                    path.unlink()
                    deleted.append(filename)
            metadata = self._visibility_path(session)
            if metadata.is_file():
                metadata.unlink()

        if session_id == self._session_id:
            with self._entry_lock:
                self._entries.clear()
                self._stats = self._new_stats()
        return {
            "session": session_id,
            "deleted_files": deleted,
            "deleted": bool(deleted),
        }

    def _rebuild_stats(self):
        entries = list(self._entries)
        self._stats = self._new_stats()
        for entry in entries:
            self._increment_stats(entry)

    def reset(self):
        self._close_file_handles()
        with self._entry_lock:
            self._entries.clear()
            self._stats = self._new_stats()
        self._session_id = ""
        self._session_dir = ""
        self._session_application_path = ""
        self._session_network_path = ""
        self._session_system_path = ""
        self._session_active = False
        return self


_log_manager = LogManager("AgentNetwork")


def get_log_manager() -> LogManager:
    return _log_manager
