"""
日志系统 — 对应架构文档 第十节：日志系统设计

实现 5 级日志：
- L1 系统级: container_start, engine_init 等
- L2 Agent级: task_received, task_completed
- L3 Tool级: tool_execute, tool_latency
- L4 Prompt级: prompt/response 记录
- L5 Packet级: 收发包记录

日志等级: TRACE, DEBUG, INFO, WARN, ERROR, FATAL, AUDIT
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum
import json


class LogLevel(Enum):
    TRACE = 0
    DEBUG = 1
    INFO = 2
    WARN = 3
    ERROR = 4
    FATAL = 5
    AUDIT = 6

    def __str__(self):
        return self.name


class LogEntry:
    """单条日志记录"""

    def __init__(
        self,
        level: LogLevel,
        level_type: str,  # "L1"|"L2"|"L3"|"L4"|"L5"
        event: str,
        agent_id: str = "",
        tool_name: str = "",
        details: Dict[str, Any] = None,
    ):
        self.timestamp = datetime.now().isoformat(timespec="milliseconds")
        self.level = level
        self.level_type = level_type
        self.event = event
        self.agent_id = agent_id
        self.tool_name = tool_name
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": str(self.level),
            "level_type": self.level_type,
            "event": self.event,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "details": self.details,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def __repr__(self):
        return f"[{self.timestamp}] [{self.level_type}:{self.level.name}] {self.event}"


class SimulationLogger:
    """
    仿真日志系统

    对应架构文档日志颗粒度设计：
    - L1 系统级: {"event":"container_start"}
    - L2 Agent级: {"agent":"001","event":"task_received"}
    - L3 Tool级: {"tool":"search","cost_time":250}
    - L4 Prompt级: {"prompt":"xxx","response":"xxx"}
    - L5 Packet级: {"packet_id":"xxx","direction":"out"}

    逻辑索引（对应 Elasticsearch 索引设计）：
    - logs-system / logs-agent / logs-tool / logs-prompt / logs-packet / logs-audit
    """

    def __init__(self, name: str = "simulation"):
        self.name = name
        self._entries: List[LogEntry] = []
        self._indices: Dict[str, List[LogEntry]] = {
            "logs-system": [],
            "logs-agent": [],
            "logs-tool": [],
            "logs-prompt": [],
            "logs-packet": [],
            "logs-audit": [],
        }

    def _log(self, level: LogLevel, level_type: str, event: str, **kwargs):
        entry = LogEntry(level=level, level_type=level_type, event=event, **kwargs)
        self._entries.append(entry)

        # 索引到对应的逻辑索引
        index_map = {
            "L1": "logs-system",
            "L2": "logs-agent",
            "L3": "logs-tool",
            "L4": "logs-prompt",
            "L5": "logs-packet",
        }
        index_name = index_map.get(level_type, "logs-system")
        if level == LogLevel.AUDIT:
            index_name = "logs-audit"
        self._indices[index_name].append(entry)

        return entry

    # ── L1 系统级日志 ───────────────────────

    def system(self, event: str, level: LogLevel = LogLevel.INFO, **details):
        """L1 系统级: 容器启动、引擎初始化等"""
        entry = self._log(level, "L1", event, details=details)
        self._print_entry(entry)
        return entry

    # ── L2 Agent 级日志 ────────────────────

    def agent(self, event: str, agent_id: str, level: LogLevel = LogLevel.INFO, **details):
        """L2 Agent级: 任务接收、状态变更等"""
        entry = self._log(level, "L2", event, agent_id=agent_id, details=details)
        self._print_entry(entry)
        return entry

    # ── L3 Tool 级日志 ─────────────────────

    def tool(self, event: str, tool_name: str, level: LogLevel = LogLevel.INFO, **details):
        """L3 Tool级: 工具调用、耗时等"""
        entry = self._log(level, "L3", event, tool_name=tool_name, details=details)
        self._print_entry(entry)
        return entry

    # ── L4 Prompt 级日志 ───────────────────

    def prompt(self, event: str, agent_id: str = "", prompt_text: str = "", response_text: str = "", **details):
        """L4 Prompt级: Prompt/Response 记录"""
        entry = self._log(
            LogLevel.DEBUG, "L4", event,
            agent_id=agent_id,
            details={"prompt": prompt_text[:200], "response": response_text[:200], **details},
        )
        self._print_entry(entry)
        return entry

    # ── L5 Packet 级日志 ───────────────────

    def packet(self, event: str, agent_id: str = "", **details):
        """L5 Packet级: 收发包记录"""
        entry = self._log(LogLevel.TRACE, "L5", event, agent_id=agent_id, details=details)
        # Packet 级太频繁，默认不打印
        return entry

    # ── 审计日志 ───────────────────────────

    def audit(self, event: str, **details):
        """审计日志"""
        entry = self._log(LogLevel.AUDIT, "L1", event, details=details)
        self._print_entry(entry)
        return entry

    # ── 便捷方法 ───────────────────────────

    def info(self, msg: str):
        self.system(msg, LogLevel.INFO)

    def warn(self, msg: str):
        self.system(msg, LogLevel.WARN)

    def error(self, msg: str):
        self.system(msg, LogLevel.ERROR)

    # ── 查询方法 — 对应架构文档 第十一节：日志搜索 ──

    def query(
        self,
        agent_id: str = None,
        level: LogLevel = None,
        level_type: str = None,
        event_contains: str = None,
        index: str = None,
        limit: int = 50,
    ) -> List[LogEntry]:
        """
        日志查询 — 类似 Elasticsearch 查询

        对应架构文档查询示例：
        agent_id = 'agent-001' AND level = 'ERROR' AND timestamp > now()-1h

        参数:
        - agent_id: 按 Agent 过滤
        - level: 按日志等级过滤
        - level_type: 按日志层级过滤 (L1/L2/L3/L4/L5)
        - event_contains: 事件名包含关键字
        - index: 按逻辑索引查询 (logs-system/logs-agent/...)
        - limit: 返回条数上限
        """
        # 选择数据源
        if index and index in self._indices:
            entries = self._indices[index]
        else:
            entries = self._entries

        # 过滤
        results = []
        for e in entries:
            if agent_id and e.agent_id != agent_id:
                continue
            if level and e.level != level:
                continue
            if level_type and e.level_type != level_type:
                continue
            if event_contains and event_contains.lower() not in e.event.lower():
                continue
            results.append(e)

        return results[-limit:]  # 返回最近的 N 条

    def get_index_stats(self) -> Dict[str, int]:
        """获取各索引的日志数量统计"""
        return {name: len(entries) for name, entries in self._indices.items()}

    def get_entries(self) -> List[LogEntry]:
        return list(self._entries)

    def reset(self):
        self._entries.clear()
        for k in self._indices:
            self._indices[k].clear()

    def _print_entry(self, entry: LogEntry):
        """打印日志条目到控制台"""
        prefix = {
            "L1": "📡 SYSTEM",
            "L2": "🤖 AGENT",
            "L3": "🔧 TOOL",
            "L4": "💬 PROMPT",
            "L5": "📦 PACKET",
        }.get(entry.level_type, "📋")

        agent_info = f" [{entry.agent_id}]" if entry.agent_id else ""
        tool_info = f" [{entry.tool_name}]" if entry.tool_name else ""

        print(f"{prefix}{agent_info}{tool_info} {entry.event}")
