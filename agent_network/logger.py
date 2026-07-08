"""统一结构化日志。

application.jsonl 的字段、类型、默认值和附加字段策略统一定义在
``agent_network.scene_def.SceneDefinition.application_log_schema`` 中；本模块
只负责产生日志、调用该 schema 的规范化函数并按日志层路由持久化。
"""

import json
import os
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, IO, List, Optional

from .scene_def import normalize_application_record


class LogLevel(Enum):
    INFO = 0
    WARN = 1
    ERROR = 2


AGENT_APPLICATION_LAYER = "agent_application"
AGENT_NETWORK_LAYER = "agent_network"

APPLICATION_EVENTS = {
    "agent_run_started",
    "agent_run_completed",
    "agent_run_failed",
    "agent_message",
    "agent_message_received",
    "decide",
    "act",
    "agent_action",
    "agent_decide",
    "skill_use",
    "tool_call",
    "tool_result",
    "state_change",
    "policy_check",
    "application_error",
    "llm_api_call",
    "llm_cli_call",
}

NETWORK_EVENTS = {
    "docker_http_inbound",
    "docker_http_outbound",
    "llm_api_packet",
    "tcpdump_packet",
}

APPLICATION_CATEGORIES = {
    AGENT_APPLICATION_LAYER,
    "agent_behavior",
    "llm_api",
    "communication",
}
NETWORK_CATEGORIES = {AGENT_NETWORK_LAYER, "network_capture"}


def infer_log_layer(record: Dict) -> str:
    """Infer the two-layer Agent log model."""

    layer = record.get("layer")
    if layer:
        return layer
    category = record.get("category", "")
    event = record.get("event", "")
    if event in NETWORK_EVENTS or category in NETWORK_CATEGORIES:
        return AGENT_NETWORK_LAYER
    if event in APPLICATION_EVENTS or category in APPLICATION_CATEGORIES:
        return AGENT_APPLICATION_LAYER
    return ""


def is_agent_application_record(record: Dict) -> bool:
    return infer_log_layer(record) == AGENT_APPLICATION_LAYER


def is_agent_network_record(record: Dict) -> bool:
    return infer_log_layer(record) == AGENT_NETWORK_LAYER


def is_agent_message_record(record: Dict) -> bool:
    return record.get("event") == "agent_message"


def is_behavior_record(record: Dict) -> bool:
    return (
        record.get("event") in {"decide", "act", "agent_action", "agent_decide"}
        or record.get("category") == "agent_behavior"
    )


_LOG_TZ = timezone(timedelta(hours=8))


def _format_log_time(dt: datetime, timespec: str = "milliseconds") -> str:
    """Format timestamps as Beijing time without a timezone suffix."""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOG_TZ)
    dt = dt.astimezone(_LOG_TZ)
    if timespec == "seconds":
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def current_log_timestamp(timespec: str = "milliseconds") -> str:
    return _format_log_time(datetime.now(_LOG_TZ), timespec=timespec)


def normalize_log_timestamp(value: Any = "", timespec: str = "milliseconds") -> str:
    """Normalize incoming timestamps to Beijing-local log time."""

    if not value:
        return current_log_timestamp(timespec=timespec)
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except Exception:
            return current_log_timestamp(timespec=timespec)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOG_TZ)
    return _format_log_time(dt, timespec=timespec)


def _base_record(
    level: str,
    source: str,
    component: str,
    category: str,
    event: str,
    message: str = "",
) -> Dict:
    """构造非 application 专用日志记录骨架。"""

    return {
        "timestamp": current_log_timestamp(),
        "seq": 0,
        "session_id": "",
        "level": level,
        "source": source,
        "component": component,
        "category": category,
        "layer": "",
        "event": event,
        "actor": {},
        "target": {},
        "action": {},
        "message": message,
        "payload": {},
        "network": {},
        "trace": {},
    }


class SimulationLogger:
    """全局单例日志：线程安全环形缓冲 + JSONL 持久化。"""

    _instance: Optional["SimulationLogger"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, name: str = "", max_entries: int = 2000, log_dir: str = ""):
        if self._initialized:
            return
        self.name = name or "AgentNetwork"
        self._max = max_entries
        self._entries: deque[Dict] = deque(maxlen=max_entries)
        self._stats = {
            "total": 0,
            "by_level": {},
            "by_event": {},
            "by_agent": {},
            "start_time": current_log_timestamp(timespec="seconds"),
        }
        self._seq = 0
        self._session_id = ""
        self._log_dir = log_dir or os.environ.get("LOG_DIR", "./data/logs")
        self._file_path = ""
        self._session_dir = ""
        self._session_application_path = ""
        self._session_network_path = ""
        self._session_active = False
        self._file_lock = threading.Lock()
        self._file_handles: Dict[str, IO] = {}
        self._init_file()
        self._initialized = True

    def _init_file(self):
        if self._log_dir:
            os.makedirs(self._log_dir, exist_ok=True)

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def start_session(self, scene_name: str):
        """开始新的仿真会话，创建场景会话目录。"""

        self._close_file_handles()
        ts = datetime.now(_LOG_TZ).strftime("%Y%m%d_%H%M%S_%f")
        safe_name = scene_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        self._session_id = f"{safe_name}_{ts}"
        self._session_dir = os.path.join(self._log_dir, self._session_id)
        os.makedirs(self._session_dir, exist_ok=True)
        self._set_session_paths()
        self._session_active = True
        with self._lock:
            self._seq = 0

        record = _base_record(
            "INFO",
            "backend",
            "srv",
            "lifecycle",
            "session_start",
            f"Session started: {scene_name}",
        )
        record["session_id"] = self._session_id
        record["seq"] = self._next_seq()
        record["payload"] = {
            "scene_name": scene_name,
            "session_dir": self._session_dir,
        }
        self._write_file(record)
        self._append_memory(record)

    def set_session_dir(self, session_dir: str):
        """复用已有 session 文件夹。"""

        self._session_dir = session_dir
        self._session_id = os.path.basename(session_dir)
        self._set_session_paths()
        self._session_active = True

    def _set_session_paths(self):
        self._file_path = os.path.join(self._session_dir, "global.jsonl")
        self._session_application_path = os.path.join(
            self._session_dir, "application.jsonl"
        )
        self._session_network_path = os.path.join(self._session_dir, "network.jsonl")

    def _get_file_handle(self, filepath: str) -> IO:
        handle = self._file_handles.get(filepath)
        if handle is None or handle.closed:
            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            handle = open(filepath, "a", encoding="utf-8")
            self._file_handles[filepath] = handle
        return handle

    def _close_file_handles(self):
        for handle in self._file_handles.values():
            try:
                handle.close()
            except Exception:
                pass
        self._file_handles.clear()

    def _append_file(self, filepath: str, entry: Dict):
        if not filepath:
            return
        try:
            with self._file_lock:
                handle = self._get_file_handle(filepath)
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
        except Exception as exc:
            print(f"[Logger] write failed {filepath}: {exc}", file=sys.stderr)

    def _write_file(self, record: Dict):
        if not self._file_path:
            return
        self._append_file(self._file_path, record)
        if is_agent_application_record(record) and self._session_application_path:
            self._append_file(self._session_application_path, record)
        if is_agent_network_record(record) and self._session_network_path:
            self._append_file(self._session_network_path, record)

    def _normalize_record(self, record: Dict) -> Dict:
        layer = infer_log_layer(record)
        if layer == AGENT_APPLICATION_LAYER:
            return normalize_application_record(record)

        if layer:
            record["layer"] = layer
        else:
            record.setdefault("layer", "")
        record.setdefault("actor", {})
        record.setdefault("target", {})
        record.setdefault("action", {})
        record.setdefault("payload", {})
        record.setdefault("network", {})
        record.setdefault("trace", {})
        return record

    def emit(self, record: Dict):
        """写入一条日志到内存缓冲区和持久化文件。"""

        record["seq"] = self._next_seq()
        record["session_id"] = self._session_id
        if not record.get("timestamp"):
            record["timestamp"] = current_log_timestamp()
        record = self._normalize_record(record)
        self._append_memory(record)
        self._write_file(record)
        return record

    def ingest(self, record: Dict):
        """接收外部日志，并应用对应日志层的规范化规则。"""

        record.setdefault("source", "external")
        record.setdefault("component", "unknown")
        record.setdefault("category", "system")
        record.setdefault("level", "INFO")
        record["seq"] = self._next_seq()
        record["session_id"] = self._session_id
        record["timestamp"] = normalize_log_timestamp(record.get("timestamp", ""))
        record = self._normalize_record(record)
        self._append_memory(record)
        self._write_file(record)
        return record

    def _append_memory(self, record: Dict):
        with self._lock:
            self._entries.append(record)
            self._stats["total"] += 1
            level = record.get("level", "INFO")
            event = record.get("event", "")
            actor = record.get("actor") or {}
            agent_id = actor.get("agent_id") or actor.get("id") or ""
            self._stats["by_level"][level] = (
                self._stats["by_level"].get(level, 0) + 1
            )
            self._stats["by_event"][event] = (
                self._stats["by_event"].get(event, 0) + 1
            )
            if agent_id:
                self._stats["by_agent"][agent_id] = (
                    self._stats["by_agent"].get(agent_id, 0) + 1
                )

    def system(
        self,
        event: str,
        message: str = "",
        level: LogLevel = LogLevel.INFO,
        agent_id: str = "",
        details: Dict = None,
        **kwargs,
    ):
        rec = _base_record(level.name, "backend", "srv", "system", event, message)
        if agent_id:
            rec["actor"] = {"id": agent_id}
        if details or kwargs:
            rec["payload"] = {**(details or {}), **kwargs}
        return self.emit(rec)

    def emit_application_event(
        self,
        event: str,
        actor: dict,
        target: dict | None = None,
        task: dict | None = None,
        conversation: dict | None = None,
        action: dict | None = None,
        content: dict | None = None,
        decision: dict | None = None,
        skill: dict | None = None,
        tool: dict | None = None,
        state_change: dict | None = None,
        policy: dict | None = None,
        result: dict | None = None,
        metrics: dict | None = None,
        links: dict | None = None,
        trace_id: str = "",
        parent_event_id: str = "",
        tick: int | None = None,
        level: str = "INFO",
        component: str = "",
        source: str = "agent",
        debug: dict | None = None,
    ) -> dict:
        """构造并写入严格遵循 scene_def schema 的应用层事件。"""

        record = {
            "timestamp": current_log_timestamp(),
            "level": level,
            "source": source,
            "component": component or actor.get("agent_id", "unknown"),
            "event": event,
            "actor": actor,
            "target": target or {},
            "task": task or {},
            "conversation": conversation or {},
            "action": action or {},
            "content": content or {},
            "decision": decision or {},
            "skill": skill or {},
            "tool": tool or {},
            "state_change": state_change or {},
            "policy": policy or {},
            "result": result or {},
            "metrics": metrics or {},
            "links": links or {},
            "trace_id": trace_id,
            "parent_event_id": parent_event_id,
            "tick": tick if tick is not None else 0,
            "debug": debug or {},
            "seq": 0,
            "session_id": self._session_id,
        }
        record = normalize_application_record(record)
        return self.emit(record)

    def agent_action(self, agent_id: str, action: str, result: Dict = None, **kwargs):
        return self.emit_application_event(
            event="act",
            actor={"agent_id": agent_id},
            action={"name": action, "status": "success"},
            content={"kw": kwargs},
            result=result or {},
        )

    def agent_decide(self, agent_id: str, prompt_snippet: str, decision: Dict = None):
        return self.emit_application_event(
            event="decide",
            actor={"agent_id": agent_id},
            action={"name": "decide", "status": "decided"},
            decision={
                "decision_summary": str(decision) if decision else "",
                "inputs_used": ["prompt_snippet"],
                "raw_model_output_ref": prompt_snippet,
            },
        )

    def agent_message(
        self,
        from_id: str,
        to: str,
        content: str,
        reasoning: str = "",
        latency_ms: float = 0,
        status: str = "success",
        src_ip: str = "",
        src_port: int = 0,
        dst_ip: str = "",
        dst_port: int = 0,
        protocol: str = "TCP/HTTP",
        packet_len: int = 0,
        header_len: int = 0,
        payload_len: int = 0,
        tcp_flags: str = "",
        channel_id: str = "",
        message_type: str = "relay",
        talk: str = "",
    ):
        """记录 Agent 应用层业务消息；网络参数不会进入 application.jsonl。"""

        normalized_status = "success"
        if status and ("failed" in status.lower() or "error" in status.lower()):
            normalized_status = "failed"

        return self.emit_application_event(
            event="agent_message",
            actor={"agent_id": from_id},
            target={"agent_id": to},
            conversation={
                "conversation_id": talk or "",
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
                "size_bytes": payload_len or len((content or "").encode("utf-8")),
                "redacted": False,
            },
            decision={
                "decision_summary": reasoning[:200] if reasoning else "",
                "reasoning_visible": reasoning[:500] if reasoning else "",
            },
            policy={
                "checked": True,
                "result": "allowed",
                "rule": "communication_matrix",
                "reason": "",
            },
            result={
                "status": normalized_status,
                "message": status,
                "error_code": "",
                "error_message": "" if normalized_status == "success" else status,
                "retryable": False,
            },
            trace_id=talk or "",
            debug={
                "schema_version": "application.v1",
                "emitter": "SimulationLogger.agent_message",
                "duration_source": "message_bus_relay_timer",
                "duration_scope": "bus_receive_to_target_message_response",
            },
        )

    def container_event(self, agent_id: str, event: str, message: str = "", **kwargs):
        rec = _base_record(
            "INFO",
            "backend",
            "srv",
            "lifecycle",
            f"container_{event}",
            f"[{agent_id}] {message or event}",
        )
        rec["actor"] = {"id": agent_id}
        if kwargs:
            rec["payload"] = kwargs
        return self.emit(rec)

    def event_trigger(self, turn: int, event_name: str, impact: str):
        rec = _base_record(
            "INFO",
            "backend",
            "srv",
            "system",
            "event_trigger",
            f"Round {turn}: {event_name} — {impact}",
        )
        rec["payload"] = {
            "turn": turn,
            "event_name": event_name,
            "impact": impact,
        }
        rec["trace"]["round"] = turn
        return self.emit(rec)

    def dag_step(
        self,
        step_id: str,
        agent_id: str,
        action: str,
        round_num: int,
        status: str = "started",
    ):
        rec = _base_record(
            "INFO",
            "backend",
            "srv",
            "system",
            "dag_step",
            f"Round {round_num}, Step {step_id}: [{agent_id}] {action} ({status})",
        )
        rec["actor"] = {"id": agent_id}
        rec["action"] = {"name": action, "status": status}
        rec["payload"] = {"step_id": step_id}
        rec["trace"]["round"] = round_num
        return self.emit(rec)

    def error(self, event: str, message: str = "", agent_id: str = "", **kwargs):
        rec = _base_record("ERROR", "backend", "srv", "system", event, message)
        if agent_id:
            rec["actor"] = {"id": agent_id}
        if kwargs:
            rec["payload"] = kwargs
        return self.emit(rec)

    def get_entries(self, limit: int = 100) -> List[Dict]:
        with self._lock:
            return list(self._entries)[-limit:]

    def query(
        self,
        agent_id: str = None,
        event: str = None,
        level: str = None,
        keyword: str = None,
        layer: str = None,
        category: str = None,
        trace_id: str = None,
        task_id: str = None,
        limit: int = 50,
    ) -> List[Dict]:
        with self._lock:
            results = list(self._entries)

        if agent_id:
            results = [
                entry
                for entry in results
                if (
                    (entry.get("actor") or {}).get("agent_id") == agent_id
                    or (entry.get("actor") or {}).get("id") == agent_id
                    or (entry.get("target") or {}).get("agent_id") == agent_id
                    or (entry.get("target") or {}).get("id") == agent_id
                )
            ]
        if event:
            results = [entry for entry in results if entry.get("event") == event]
        if layer:
            results = [
                entry for entry in results if infer_log_layer(entry) == layer
            ]
        if category:
            results = [
                entry for entry in results if entry.get("category") == category
            ]
        if trace_id:
            results = [
                entry
                for entry in results
                if entry.get("trace_id") == trace_id
                or (entry.get("trace") or {}).get("trace_id") == trace_id
            ]
        if task_id:
            results = [
                entry
                for entry in results
                if (entry.get("task") or {}).get("task_id") == task_id
            ]
        if level:
            results = [
                entry for entry in results if entry.get("level") == level.upper()
            ]
        if keyword:
            lowered = keyword.lower()
            results = [
                entry
                for entry in results
                if lowered in (entry.get("message") or "").lower()
                or lowered
                in json.dumps(entry.get("content", {}), ensure_ascii=False).lower()
                or lowered
                in json.dumps(entry.get("payload", {}), ensure_ascii=False).lower()
                or lowered
                in json.dumps(entry.get("result", {}), ensure_ascii=False).lower()
            ]
        return results[-limit:]

    def get_index_stats(self) -> Dict:
        with self._lock:
            return dict(self._stats)

    def get_agent_timeline(self, agent_id: str, limit: int = 50) -> List[Dict]:
        return self.query(agent_id=agent_id, limit=limit)

    def get_message_log(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            results = [
                entry for entry in self._entries if is_agent_message_record(entry)
            ]
        return results[-limit:]

    def export(self, fmt: str = "jsonl", limit: int = 0) -> str:
        entries = list(self._entries)[-limit:] if limit > 0 else list(self._entries)
        if fmt == "json":
            return json.dumps(entries, ensure_ascii=False, indent=2)
        if fmt == "csv":
            import csv
            import io

            buffer = io.StringIO()
            fieldnames = [
                "timestamp",
                "seq",
                "session_id",
                "level",
                "source",
                "component",
                "category",
                "layer",
                "event",
                "message",
            ]
            writer = csv.DictWriter(
                buffer,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            for entry in entries:
                writer.writerow({name: entry.get(name, "") for name in fieldnames})
            return buffer.getvalue()
        return "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries)

    def export_file(self, filepath: str, fmt: str = "jsonl", limit: int = 0):
        content = self.export(fmt=fmt, limit=limit)
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as stream:
            stream.write(content)
        return filepath

    def list_log_files(self) -> List[Dict]:
        if not self._log_dir or not os.path.isdir(self._log_dir):
            return []
        sessions = []
        for entry in sorted(os.listdir(self._log_dir), reverse=True):
            entry_path = os.path.join(self._log_dir, entry)
            if os.path.isdir(entry_path):
                files = []
                for filename in sorted(os.listdir(entry_path)):
                    if filename.endswith(".jsonl"):
                        filepath = os.path.join(entry_path, filename)
                        files.append(
                            {
                                "name": filename,
                                "size_bytes": os.path.getsize(filepath),
                                "path": filepath,
                            }
                        )
                if files:
                    sessions.append(
                        {"session": entry, "path": entry_path, "files": files}
                    )
            elif entry.endswith(".jsonl"):
                sessions.append(
                    {
                        "session": None,
                        "path": None,
                        "files": [
                            {
                                "name": entry,
                                "size_bytes": os.path.getsize(entry_path),
                                "path": entry_path,
                            }
                        ],
                    }
                )
        return sessions

    def reset(self):
        self._close_file_handles()
        with self._lock:
            self._entries.clear()
            self._stats = {
                "total": 0,
                "by_level": {},
                "by_event": {},
                "by_agent": {},
                "start_time": current_log_timestamp(timespec="seconds"),
            }
            self._seq = 0
        self._session_id = ""
        return self


_logger = SimulationLogger("AgentNetwork")
system_log = _logger.system
agent_log = _logger.agent_action
message_log = _logger.agent_message
get_logger = lambda: _logger
