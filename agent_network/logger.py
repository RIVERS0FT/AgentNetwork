"""
统一结构化日志 — 所有模块共用

日志条目格式 (v2 统一 schema):
{
  "timestamp": "2026-06-11T19:14:02.501",
  "seq": 1,
  "session_id": "minesweeper_20260611_191402_501302",
  "level": "INFO",
  "source": "backend",          # backend | agent | bus | frontend
  "component": "srv",           # srv | bus | ag-c1 | dashboard
  "category": "agent_application",  # agent_application | agent_network | system | frontend | lifecycle
  "layer": "agent_application",     # agent_application | agent_network
  "event": "agent_message",

  "actor": {"id": "cmdr_01"},
  "target": {"id": "soldier_07"},

  "action": {"name": "send_message", "status": "success"},
  "message": "cmdr_01 → soldier_07",   # 短摘要，仅用于人类快速阅读

  "payload": {                         # 业务数据
    "content": "报告指挥官...",
    "reasoning": "...",
    "skill_params": {},
    "skill_result": {}
  },

  "network": {                         # 通信层数据
    "src_ip": "172.19.0.4", "src_port": 0,
    "dst_ip": "172.19.0.8", "dst_port": 0,
    "protocol": "TCP/HTTP",
    "latency_ms": 8.4,
    "packet_len": 317, "header_len": 200, "payload_len": 117,
    "tcp_flags": "PSH,ACK",
    "channel_id": "",
    "message_type": "relay"
  },

  "trace": {                           # 追踪信息
    "round": 3,
    "talk": "",
    "correlation_id": ""
  }
}

路由规则:
  - global.jsonl: 全部日志
  - application.jsonl: event == agent_application 的隔离视图 (包含所有 Agent 行为/消息)
  - network.jsonl: 网络层原始抓包记录 (供回放与分析)"""

import json
import os
import sys
import time
import threading
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, IO
from collections import deque


class LogLevel(Enum):
    INFO = 0
    WARN = 1
    ERROR = 2


AGENT_APPLICATION_LAYER = "agent_application"
AGENT_NETWORK_LAYER = "agent_network"

APPLICATION_EVENTS = {
    "agent_run_started",
    "agent_run_completed",
    "agent_message",
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

APPLICATION_CATEGORIES = {AGENT_APPLICATION_LAYER, "agent_behavior", "llm_api", "communication"}
NETWORK_CATEGORIES = {AGENT_NETWORK_LAYER, "network_capture"}


def infer_log_layer(record: Dict) -> str:
    """Infer the two-layer Agent log model for new and legacy records."""
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


# ── 统一日志记录 schema ──

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
    """Current Beijing-time timestamp for global logs."""
    return _format_log_time(datetime.now(_LOG_TZ), timespec=timespec)


def normalize_log_timestamp(value: Any = "", timespec: str = "milliseconds") -> str:
    """
    Normalize incoming timestamps to the global log timezone.

    Naive timestamps in this project are already Beijing-local log time. Browser
    clients may send explicit UTC timestamps with a Z suffix; those are converted
    to the same Beijing-local display format.
    """
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

def _base_record(level: str, source: str, component: str, category: str,
                 event: str, message: str = "") -> Dict:
    """构造一条统一 schema 日志记录的骨架"""
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

import uuid

def _ensure_dict(value: Any) -> Dict:
    return value if isinstance(value, dict) else {}


def _actor_id_of(record: Dict) -> str:
    actor = record.get("actor") or {}
    return actor.get("agent_id") or actor.get("id") or ""


def _target_id_of(record: Dict) -> str:
    target = record.get("target") or {}
    return target.get("agent_id") or target.get("id") or ""


def normalize_application_record(record: Dict) -> Dict:
    """
    Normalize an application-layer record.

    application.jsonl 只记录 Agent 应用层语义：
    - Agent 身份
    - 任务上下文
    - 业务动作
    - 消息内容
    - 决策摘要
    - Skill / Tool
    - 状态变更
    - 权限结果
    - 执行结果
    - 应用层耗时

    不记录底层网络字段：
    - IP
    - 端口
    - TCP flags
    - packet length
    - HTTP path/method/status
    """

    record["layer"] = AGENT_APPLICATION_LAYER
    record["category"] = AGENT_APPLICATION_LAYER

    if not record.get("event_id"):
        record["event_id"] = f"app_{uuid.uuid4().hex[:12]}"

    trace = _ensure_dict(record.get("trace"))
    trace_id = record.get("trace_id") or trace.get("trace_id") or f"trace_{uuid.uuid4().hex[:12]}"
    record["trace_id"] = trace_id
    trace["trace_id"] = trace_id
    record["trace"] = trace

    record.setdefault("parent_event_id", "")
    record.setdefault("tick", 0)

    actor = _ensure_dict(record.get("actor"))
    if "id" in actor and "agent_id" not in actor:
        actor["agent_id"] = actor["id"]
    record["actor"] = actor

    target = _ensure_dict(record.get("target"))
    if "id" in target and "agent_id" not in target:
        target["agent_id"] = target["id"]
    record["target"] = target

    record["task"] = _ensure_dict(record.get("task"))
    record["conversation"] = _ensure_dict(record.get("conversation"))
    record["action"] = _ensure_dict(record.get("action"))
    record["content"] = _ensure_dict(record.get("content"))
    record["decision"] = _ensure_dict(record.get("decision"))
    record["skill"] = _ensure_dict(record.get("skill"))
    record["tool"] = _ensure_dict(record.get("tool"))
    record["state_change"] = _ensure_dict(record.get("state_change"))
    record["policy"] = _ensure_dict(record.get("policy"))
    record["result"] = _ensure_dict(record.get("result"))
    record["metrics"] = _ensure_dict(record.get("metrics"))

    links = _ensure_dict(record.get("links"))
    links.setdefault("network_event_ids", [])
    links.setdefault("audit_event_ids", [])
    links.setdefault("tool_event_ids", [])
    links.setdefault("state_event_ids", [])
    links.setdefault("related_trace_ids", [])
    record["links"] = links

    debug = _ensure_dict(record.get("debug"))
    debug.setdefault("schema_version", "application.v1")
    debug.setdefault("emitter", "SimulationLogger")
    record["debug"] = debug

    # 兼容旧字段：payload.content -> content.text
    payload = _ensure_dict(record.get("payload"))
    if payload:
        if not record["content"].get("text") and payload.get("content"):
            record["content"]["text"] = payload.get("content")
        if not record["content"].get("reasoning") and payload.get("reasoning"):
            record["content"]["reasoning"] = payload.get("reasoning")
        if payload.get("skill_params") or payload.get("skill_result"):
            record["skill"].setdefault("input", payload.get("skill_params", {}))
            record["skill"].setdefault("output", payload.get("skill_result", {}))
        debug["legacy_payload_migrated"] = True
        record.pop("payload", None)

    # 应用层禁止直接带 network 对象
    if record.get("network"):
        debug["legacy_network_fields_dropped"] = True
        record.pop("network", None)

    forbidden_network_keys = {
        "src_ip", "dst_ip", "src_port", "dst_port",
        "protocol", "tcp_flags", "packet_len", "header_len", "payload_len",
        "http_method", "http_path", "http_status_code", "tcpdump", "rtt_ms"
    }

    for key in list(record.keys()):
        if key in forbidden_network_keys:
            debug["legacy_network_fields_dropped"] = True
            record.pop(key, None)

    # message 字段作为人类可读摘要保留
    if not record.get("message"):
        actor_id = _actor_id_of(record)
        target_id = _target_id_of(record)
        action_name = (record.get("action") or {}).get("name", record.get("event", ""))
        if actor_id and target_id:
            record["message"] = f"{actor_id} -> {target_id}: {action_name}"
        elif actor_id:
            record["message"] = f"{actor_id}: {action_name}"
        else:
            record["message"] = record.get("event", "")

    return record

class SimulationLogger:
    """全局单例日志 — 线程安全环形缓冲 + JSONL 持久化"""

    _instance: Optional["SimulationLogger"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kw):
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
        # 持久化目录
        self._log_dir = log_dir or os.environ.get("LOG_DIR", "./data/logs")
        self._file_path = ""            # global.jsonl
        self._session_dir = ""
        self._session_application_path = ""  # application.jsonl
        self._session_network_path = ""  # network.jsonl
        self._session_active = False
        self._file_lock = threading.Lock()
        self._file_handles: Dict[str, IO] = {}  # 常驻文件句柄池
        self._init_file()
        self._initialized = True

    # ═══════════════════════════════════════════
    # 文件持久化
    # ═══════════════════════════════════════════

    def _init_file(self):
        if not self._log_dir:
            return
        os.makedirs(self._log_dir, exist_ok=True)

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def start_session(self, scene_name: str):
        """开始新的仿真会话 — 创建 {场景名}_{时间戳}/ 文件夹"""
        self._close_file_handles()  # 关闭上一轮句柄
        ts = datetime.now(_LOG_TZ).strftime("%Y%m%d_%H%M%S_%f")
        safe_name = scene_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        self._session_id = f"{safe_name}_{ts}"
        self._session_dir = os.path.join(self._log_dir, self._session_id)
        os.makedirs(self._session_dir, exist_ok=True)
        self._set_session_paths()
        self._session_active = True
        with self._lock:
            self._seq = 0
        # 写入 session 元信息
        record = _base_record("INFO", "backend", "srv", "lifecycle", "session_start",
                              f"Session started: {scene_name}")
        record["session_id"] = self._session_id
        record["seq"] = self._next_seq()
        record["payload"] = {"scene_name": scene_name, "session_dir": self._session_dir}
        self._write_file(record)
        self._append_memory(record)

    def set_session_dir(self, session_dir: str):
        """复用已有 session 文件夹（供跨容器同步，由 message_bus 调用）"""
        self._session_dir = session_dir
        self._session_id = os.path.basename(session_dir)
        self._set_session_paths()
        self._session_active = True

    def _set_session_paths(self):
        self._file_path = os.path.join(self._session_dir, "global.jsonl")
        self._session_application_path = os.path.join(self._session_dir, "application.jsonl")
        self._session_network_path = os.path.join(self._session_dir, "network.jsonl")

    def _get_file_handle(self, filepath: str) -> IO:
        """获取或创建常驻文件句柄"""
        fh = self._file_handles.get(filepath)
        if fh is None or fh.closed:
            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            fh = open(filepath, "a", encoding="utf-8")
            self._file_handles[filepath] = fh
        return fh

    def _close_file_handles(self):
        """关闭所有常驻文件句柄"""
        for fh in self._file_handles.values():
            try:
                fh.close()
            except Exception:
                pass
        self._file_handles.clear()

    def _append_file(self, filepath: str, entry: Dict):
        if not filepath:
            return
        try:
            with self._file_lock:
                fh = self._get_file_handle(filepath)
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
        except Exception as e:
            print(f"[Logger] write failed {filepath}: {e}", file=sys.stderr)

    def _write_file(self, record: Dict):
        """按 category 路由写入文件"""
        if not self._file_path:
            return
        # global.jsonl: 全部写入
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

    # ═══════════════════════════════════════════
    # 核心 emit 方法 — 唯一写入入口
    # ═══════════════════════════════════════════

    def emit(self, record: Dict):
        """写入一条统一 schema 日志到内存缓冲区 + 持久化文件"""
        record["seq"] = self._next_seq()
        record["session_id"] = self._session_id

        if not record.get("timestamp"):
            record["timestamp"] = current_log_timestamp()

        record = self._normalize_record(record)

        self._append_memory(record)
        self._write_file(record)
        return record

    def ingest(self, record: Dict):
        """接收外部日志（前端、外部服务），直接写入文件不做转换"""
        record.setdefault("source", "external")
        record.setdefault("component", "unknown")
        record.setdefault("category", "system")
        record.setdefault("level", "INFO")
        record["seq"] = self._next_seq()
        record["session_id"] = self._session_id
        record["timestamp"] = normalize_log_timestamp(record.get("timestamp", ""))
        self._normalize_record(record)
        self._append_memory(record)
        self._write_file(record)

    def _append_memory(self, record: Dict):
        with self._lock:
            self._entries.append(record)
            self._stats["total"] += 1

            lvl = record.get("level", "INFO")
            evt = record.get("event", "")
            actor = record.get("actor") or {}
            aid = actor.get("agent_id") or actor.get("id") or ""

            self._stats["by_level"][lvl] = self._stats["by_level"].get(lvl, 0) + 1
            self._stats["by_event"][evt] = self._stats["by_event"].get(evt, 0) + 1

            if aid:
                self._stats["by_agent"][aid] = self._stats["by_agent"].get(aid, 0) + 1

    # ═══════════════════════════════════════════
    # 便捷方法 — 内部构造新 schema 后调用 emit
    # ═══════════════════════════════════════════

    def system(self, event: str, message: str = "", level: LogLevel = LogLevel.INFO,
               agent_id: str = "", details: Dict = None, **kw):
        """系统事件（server / message_bus）"""
        rec = _base_record(level.name, "backend", "srv", "system", event, message)
        if agent_id:
            rec["actor"] = {"id": agent_id}
        if details or kw:
            rec["payload"] = {**(details or {}), **kw}
        self.emit(rec)

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
        """Constructs and emits a strict application-layer event."""
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
        self.emit(record)
        return record

    def agent_action(self, agent_id: str, action: str, result: Dict = None, **kw):
        """Agent 动作执行"""
        self.emit_application_event(
            event="act",
            actor={"agent_id": agent_id},
            action={"name": action, "status": "success"},
            content={"kw": kw},
            result=result or {}
        )

    def agent_decide(self, agent_id: str, prompt_snippet: str, decision: Dict = None):
        """Agent 决策"""
        self.emit_application_event(
            event="decide",
            actor={"agent_id": agent_id},
            action={"name": "decide", "status": "decided"},
            decision={
                "decision_summary": str(decision) if decision else "",
                "inputs_used": ["prompt_snippet"],
                "raw_model_output_ref": prompt_snippet
            }
        )

    def agent_message(self, from_id: str, to: str, content: str, reasoning: str = "",
                  latency_ms: float = 0, status: str = "success",
                  src_ip: str = "", src_port: int = 0,
                  dst_ip: str = "", dst_port: int = 0,
                  protocol: str = "TCP/HTTP",
                  packet_len: int = 0, header_len: int = 0, payload_len: int = 0,
                  tcp_flags: str = "",
                  channel_id: str = "",
                  message_type: str = "relay",
                  talk: str = ""):
        """
        Agent 应用层业务消息。

        注意：
        - 不写 IP / 端口 / TCP flags / packet_len
        - latency_ms 作为应用层动作耗时写入 action.duration_ms
        - 真实网络细节由 network.jsonl 记录
        """

        normalized_status = "success"
        if status and ("failed" in status.lower() or "error" in status.lower()):
            normalized_status = "failed"

        self.emit_application_event(
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
                "legacy_network_fields_dropped": True,
                "duration_source": "message_bus_relay_timer",
                "duration_scope": "bus_receive_to_target_message_response",
            },
        )

    def container_event(self, agent_id: str, event: str, message: str = "", **kw):
        """容器生命周期事件"""
        rec = _base_record("INFO", "backend", "srv", "lifecycle",
                           f"container_{event}", f"[{agent_id}] {message or event}")
        rec["actor"] = {"id": agent_id}
        if kw:
            rec["payload"] = kw
        self.emit(rec)

    def event_trigger(self, turn: int, event_name: str, impact: str):
        """场景事件触发"""
        rec = _base_record("INFO", "backend", "srv", "system",
                           "event_trigger", f"Round {turn}: {event_name} — {impact}")
        rec["payload"] = {"turn": turn, "event_name": event_name, "impact": impact}
        rec["trace"]["round"] = turn
        self.emit(rec)

    def dag_step(self, step_id: str, agent_id: str, action: str, round_num: int, status: str = "started"):
        """DAG 工作流步骤"""
        rec = _base_record("INFO", "backend", "srv", "system",
                           "dag_step", f"Round {round_num}, Step {step_id}: [{agent_id}] {action} ({status})")
        rec["actor"] = {"id": agent_id}
        rec["action"] = {"name": action, "status": status}
        rec["payload"] = {"step_id": step_id}
        rec["trace"]["round"] = round_num
        self.emit(rec)

    def error(self, event: str, message: str = "", agent_id: str = "", **kw):
        """错误事件"""
        rec = _base_record("ERROR", "backend", "srv", "system", event, message)
        if agent_id:
            rec["actor"] = {"id": agent_id}
        if kw:
            rec["payload"] = kw
        self.emit(rec)

    # ═══════════════════════════════════════════
    # 查询 & 导出
    # ═══════════════════════════════════════════

    def get_entries(self, limit: int = 100) -> List[Dict]:
        with self._lock:
            return list(self._entries)[-limit:]

    def query(self, agent_id: str = None, event: str = None, level: str = None,
        keyword: str = None, layer: str = None, category: str = None,
        trace_id: str = None, task_id: str = None,
        limit: int = 50) -> List[Dict]:
        with self._lock:
            results = list(self._entries)

        if agent_id:
            results = [
                e for e in results
                if ((e.get("actor") or {}).get("agent_id") == agent_id
                    or (e.get("actor") or {}).get("id") == agent_id
                    or (e.get("target") or {}).get("agent_id") == agent_id
                    or (e.get("target") or {}).get("id") == agent_id)
            ]

        if event:
            results = [e for e in results if e.get("event") == event]

        if layer:
            results = [e for e in results if infer_log_layer(e) == layer]

        if category:
            results = [e for e in results if e.get("category") == category]

        if trace_id:
            results = [
                e for e in results
                if e.get("trace_id") == trace_id
                or (e.get("trace") or {}).get("trace_id") == trace_id
            ]

        if task_id:
            results = [
                e for e in results
                if (e.get("task") or {}).get("task_id") == task_id
            ]

        if level:
            results = [e for e in results if e.get("level") == level.upper()]

        if keyword:
            k = keyword.lower()
            results = [
                e for e in results
                if k in (e.get("message") or "").lower()
                or k in json.dumps(e.get("content", {}), ensure_ascii=False).lower()
                or k in json.dumps(e.get("payload", {}), ensure_ascii=False).lower()
                or k in json.dumps(e.get("result", {}), ensure_ascii=False).lower()
            ]

        return results[-limit:]

    def get_index_stats(self) -> Dict:
        with self._lock:
            return dict(self._stats)

    def get_agent_timeline(self, agent_id: str, limit: int = 50) -> List[Dict]:
        return self.query(agent_id=agent_id, limit=limit)

    def get_message_log(self, limit: int = 50) -> List[Dict]:
        """获取 Agent 间通信报文（兼容旧 API）"""
        with self._lock:
            results = [e for e in self._entries if is_agent_message_record(e)]
        return results[-limit:]

    def export(self, fmt: str = "jsonl", limit: int = 0) -> str:
        entries = list(self._entries)[-limit:] if limit > 0 else list(self._entries)
        if fmt == "json":
            return json.dumps(entries, ensure_ascii=False, indent=2)
        elif fmt == "csv":
            import io
            import csv
            buf = io.StringIO()
            fieldnames = ["timestamp", "seq", "session_id", "level", "source",
                          "component", "category", "layer", "event", "message"]
            writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for d in entries:
                row = {k: d.get(k, "") for k in fieldnames}
                row["payload"] = json.dumps(d.get("payload", {}), ensure_ascii=False)
                row["network"] = json.dumps(d.get("network", {}), ensure_ascii=False)
                writer.writerow(row)
            return buf.getvalue()
        else:
            return "\n".join(json.dumps(d, ensure_ascii=False) for d in entries)

    def export_file(self, filepath: str, fmt: str = "jsonl", limit: int = 0):
        content = self.export(fmt=fmt, limit=limit)
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def list_log_files(self) -> List[Dict]:
        if not self._log_dir or not os.path.isdir(self._log_dir):
            return []
        sessions = []
        for entry in sorted(os.listdir(self._log_dir), reverse=True):
            entry_path = os.path.join(self._log_dir, entry)
            if os.path.isdir(entry_path):
                files = []
                for f in sorted(os.listdir(entry_path)):
                    if f.endswith(".jsonl"):
                        fp = os.path.join(entry_path, f)
                        files.append({
                            "name": f, "size_bytes": os.path.getsize(fp), "path": fp,
                        })
                if files:
                    sessions.append({
                        "session": entry, "path": entry_path, "files": files,
                    })
            elif entry.endswith(".jsonl"):
                sessions.append({
                    "session": None, "path": None,
                    "files": [{
                        "name": entry, "size_bytes": os.path.getsize(entry_path), "path": entry_path,
                    }],
                })
        return sessions

    def reset(self):
        self._close_file_handles()
        with self._lock:
            self._entries.clear()
            self._stats = {
                "total": 0, "by_level": {}, "by_event": {}, "by_agent": {},
                "start_time": current_log_timestamp(timespec="seconds"),
            }
            self._seq = 0
        self._session_id = ""
        return self


# ── 全局实例 ──
_logger = SimulationLogger("AgentNetwork")
system_log = _logger.system
agent_log = _logger.agent_action
message_log = _logger.agent_message
get_logger = lambda: _logger
