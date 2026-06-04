"""
日志收集器 — 对应架构文档 第十节：日志系统设计

集中收集所有 Agent 的日志，支持 5 级日志体系 (L1-L5) + AUDIT。
- 6 个逻辑索引（与 SimulationLogger 一致）
- 可选 Elasticsearch 后端
- WebSocket 实时日志流
- 多维度过滤查询

使用方式（进程内）:
    from agent_network.log_collector import LogCollector
    collector = LogCollector()
    collector.ingest_log(entry_dict, "logs-agent")

使用方式（容器间）:
    # log_collector_server.py 通过 HTTP POST /api/logs/ingest 接收
"""

import os
import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable, Set, AsyncGenerator
from datetime import datetime
from collections import deque

logger = logging.getLogger("agent_network.log_collector")


class LogCollector:
    """
    日志收集器（单例）

    6 个索引（对应架构文档 Elasticsearch 索引设计）:
    - logs-system   (L1) — 系统事件
    - logs-agent    (L2) — Agent 事件
    - logs-tool     (L3) — 工具调用
    - logs-prompt   (L4) — LLM 提示
    - logs-packet   (L5) — 数据包
    - logs-audit    — 审计事件
    """

    _instance: Optional["LogCollector"] = None

    INDEX_NAMES = [
        "logs-system",
        "logs-agent",
        "logs-tool",
        "logs-prompt",
        "logs-packet",
        "logs-audit",
    ]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._buffer_size = int(os.environ.get("LOG_BUFFER_SIZE", 5000))
        self._buffer: deque = deque(maxlen=self._buffer_size)
        self._indices: Dict[str, deque] = {name: deque(maxlen=2000) for name in self.INDEX_NAMES}
        self._subscribers: Set[asyncio.Queue] = set()
        self._es_client = None
        self._es_enabled = os.environ.get("ES_ENABLED", "").lower() == "true"
        self._es_bulk_buffer: List[tuple] = []
        self._es_bulk_task: Optional[asyncio.Task] = None
        self._stats = {
            "total_entries": 0,
            "by_index": {name: 0 for name in self.INDEX_NAMES},
            "by_level": {},
            "start_time": datetime.now().isoformat(),
        }

    # ── ES 集成 ─────────────────────────────────

    def set_es_client(self, client):
        """设置 Elasticsearch 客户端，启用 ES 存储"""
        self._es_client = client
        self._es_enabled = True

    async def _start_es_bulk_shipping(self, interval: int = 5):
        """后台任务：定期批量将日志写入 ES"""
        while self._es_enabled:
            await asyncio.sleep(interval)
            if self._es_bulk_buffer and self._es_client and self._es_client.available:
                batch = self._es_bulk_buffer[:500]
                del self._es_bulk_buffer[:500]
                try:
                    await self._es_client.bulk_index(batch)
                except Exception as e:
                    logger.error(f"[LogCollector] ES bulk error: {e}")

    # ── 日志摄入 ────────────────────────────────

    def ingest_log(self, entry: Dict[str, Any], index_name: str):
        """
        摄入单条日志

        Args:
            entry: 日志条目字典（LogEntry.to_dict() 兼容格式）
            index_name: 逻辑索引名 (如 "logs-agent")
        """
        # 确保 timestamp
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.now().isoformat(timespec="milliseconds")

        # 存入主缓冲
        self._buffer.append(entry)

        # 存入对应索引
        if index_name in self._indices:
            self._indices[index_name].append(entry)

        # 更新统计
        self._stats["total_entries"] += 1
        self._stats["by_index"][index_name] = self._stats["by_index"].get(index_name, 0) + 1
        level = entry.get("level", "INFO")
        self._stats["by_level"][level] = self._stats["by_level"].get(level, 0) + 1

        # 通知 WebSocket 订阅者
        self._notify_subscribers({"index": index_name, **entry})

        # Prometheus 指标
        try:
            from .metrics import MetricsRegistry
            level_type = self._index_to_level_type(index_name)
            MetricsRegistry().record_log(level_type, level)
        except ImportError:
            pass

        # ES 批量缓冲
        if self._es_enabled and self._es_client:
            self._es_bulk_buffer.append((entry, index_name))
            # 启动后台任务（若未启动）
            if self._es_bulk_task is None or self._es_bulk_task.done():
                try:
                    loop = asyncio.get_event_loop()
                    self._es_bulk_task = loop.create_task(self._start_es_bulk_shipping())
                except RuntimeError:
                    pass

    def ingest_batch(self, entries: List[tuple]):
        """
        批量摄入日志

        Args:
            entries: [(entry_dict, index_name), ...]
        """
        for entry, index_name in entries:
            self.ingest_log(entry, index_name)

    def _index_to_level_type(self, index_name: str) -> str:
        return index_name.replace("logs-", "L")

    # ── 查询 ────────────────────────────────────

    def query(
        self,
        agent_id: str = None,
        level: str = None,
        level_type: str = None,
        event_contains: str = None,
        index: str = None,
        keyword: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        多维度过滤查询日志

        Args:
            agent_id: 按 Agent 过滤
            level: 按日志等级过滤 (INFO, WARN, ERROR, ...)
            level_type: L1/L2/L3/L4/L5/AUDIT
            event_contains: 事件名包含
            index: 指定索引名 (logs-agent, logs-system, ...)
            keyword: 全文关键词搜索
            limit: 返回条数
            offset: 分页偏移

        Returns:
            {"total": int, "entries": List[dict], "limit": int, "offset": int}
        """
        results = []

        # 选择数据源
        if index and index in self._indices:
            source = self._indices[index]
        else:
            source = self._buffer

        for entry in source:
            if agent_id and entry.get("agent_id") != agent_id:
                continue
            if level and entry.get("level") != level:
                continue
            if level_type:
                idx = entry.get("index", "")
                lt = idx.replace("logs-", "L") if "L" not in idx else idx
                if lt != level_type:
                    continue
            if event_contains and event_contains.lower() not in entry.get("event", "").lower():
                continue
            if keyword:
                entry_str = str(entry).lower()
                if keyword.lower() not in entry_str:
                    continue
            results.append(entry)

        total = len(results)
        return {
            "total": total,
            "entries": list(results)[offset:offset + limit],
            "limit": limit,
            "offset": offset,
        }

    async def es_query(
        self,
        index: str = "logs-*",
        keyword: str = "",
        agent_id: str = None,
        level: str = None,
        event: str = None,
        time_from: str = None,
        time_to: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        通过 Elasticsearch 查询日志（需要 ES 可用）

        Returns:
            ES 搜索结果，fallback 到内存查询
        """
        if self._es_client and self._es_client.available:
            return await self._es_client.search_simple(
                index=index,
                keyword=keyword,
                agent_id=agent_id,
                level=level,
                event=event,
                time_from=time_from,
                time_to=time_to,
                size=limit,
            )
        # Fallback to memory
        return self.query(agent_id=agent_id, level=level,
                          event_contains=event, index=index,
                          keyword=keyword, limit=limit)

    def get_index_stats(self) -> Dict[str, Any]:
        """获取各索引统计"""
        return {
            "total_entries": self._stats["total_entries"],
            "buffer_size": len(self._buffer),
            "by_index": {name: len(self._indices[name]) for name in self.INDEX_NAMES},
            "by_level": dict(self._stats["by_level"]),
            "subscribers": len(self._subscribers),
            "es_enabled": self._es_enabled,
            "es_buffer_pending": len(self._es_bulk_buffer),
            "start_time": self._stats["start_time"],
        }

    # ── 实时订阅 ────────────────────────────────

    def _notify_subscribers(self, entry: Dict[str, Any]):
        dead = set()
        for q in self._subscribers:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead.add(q)
        self._subscribers -= dead

    async def subscribe(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        WebSocket 订阅器 — 实时 yield 日志条目

        用法:
            async for entry in collector.subscribe():
                await websocket.send_json(entry)
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.add(queue)
        try:
            while True:
                entry = await queue.get()
                yield entry
        except asyncio.CancelledError:
            pass
        finally:
            self._subscribers.discard(queue)

    # ── 重置 ────────────────────────────────────

    @classmethod
    def reset(cls):
        """重置单例（测试用）"""
        if cls._instance:
            cls._instance._buffer.clear()
            for d in cls._instance._indices.values():
                d.clear()
            cls._instance._subscribers.clear()
            cls._instance._es_bulk_buffer.clear()
            cls._instance._init()
