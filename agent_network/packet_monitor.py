"""
数据包监控器 — 对应架构文档 第九节：收包发包全链路记录

实时捕获所有 Agent 间消息，提供查询、统计和 WebSocket 实时推送。

设计：
- 作为 EventBus 的广播订阅者，捕获所有消息
- 与现有 PacketRecorder (event_bus.py) 协同工作，不冲突
- 环形缓冲区存储最近 N 条数据包（默认 10000）
- WebSocket 订阅模式 — 多个客户端可同时订阅实时推送
- 集成 Prometheus 指标

使用方式（进程内）:
    from agent_network.packet_monitor import PacketMonitor
    monitor = PacketMonitor()
    monitor.start(event_bus)  # 订阅 EventBus 广播
    monitor.ingest_packet(msg)  # 或手动注入

    # WebSocket 流
    async for packet in monitor.subscribe():
        yield packet

使用方式（容器间）:
    # packet_monitor_server.py 通过 HTTP POST /api/packets/ingest 接收
    monitor.ingest_packet_from_dict(data)
"""

import os
import json
import hashlib
import time
import asyncio
from typing import Dict, List, Any, Optional, Callable, AsyncGenerator, Set
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque

from .message import PacketRecord, Message


class PacketMonitor:
    """
    数据包监控器（单例）

    - 环形缓冲区保存最近 N 条 PacketRecord
    - 支持 WebSocket 订阅者实时推送
    - 提供多维度过滤查询
    - 自动向 Prometheus 上报指标
    """

    _instance: Optional["PacketMonitor"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._buffer_size = int(os.environ.get("PACKET_BUFFER_SIZE", 10000))
        self._buffer: deque = deque(maxlen=self._buffer_size)
        self._subscribers: Set[asyncio.Queue] = set()
        self._subscriber_tasks: Set[asyncio.Task] = set()
        self._stats = {
            "total_packets": 0,
            "by_direction": {"outbound": 0, "inbound": 0},
            "by_type": {},
            "by_source": {},
            "by_target": {},
            "total_latency_ms": 0.0,
            "start_time": datetime.now().isoformat(),
        }

    # ── 数据摄入 ────────────────────────────────

    def ingest_packet(self, message: Message, direction: str = "outbound",
                      latency: float = 0):
        """
        从 Message 创建 PacketRecord 并存入缓冲区

        Args:
            message: 原始消息
            direction: "outbound" / "inbound"
            latency: 延迟（毫秒）
        """
        # 构建 PacketRecord
        packet = PacketRecord(
            packet_id=message.message_id,
            direction=direction,
            source_agent=message.source,
            target_agent=message.target,
            message_type=message.type,
            payload_hash=hashlib.md5(
                json.dumps(message.payload, sort_keys=True, default=str).encode()
            ).hexdigest()[:8],
            token_usage=message.payload.get("token_usage", 0),
            latency=latency,
            timestamp=message.timestamp,
        )

        self._buffer.append(packet)
        self._update_stats(packet)

        # 通知 WebSocket 订阅者
        self._notify_subscribers(packet)

        # 上报 Prometheus
        try:
            from .metrics import MetricsRegistry
            MetricsRegistry().record_packet(direction, message.type)
            MetricsRegistry().record_message(
                source=message.source,
                target=message.target,
                msg_type=message.type,
                latency=latency / 1000.0,
            )
        except ImportError:
            pass

    def ingest_packet_dict(self, data: Dict[str, Any], direction: str = "outbound"):
        """从字典创建数据包（由 message_bus 通过 HTTP POST 调用）"""
        message = Message(
            source=data.get("from_id", data.get("source", "unknown")),
            target=data.get("to", data.get("target", "broadcast")),
            type=data.get("type", "relay"),
            payload={
                "action": data.get("content", ""),
                "reasoning": data.get("reasoning", ""),
                "token_usage": data.get("token_usage", 0),
            },
        )
        latency = data.get("latency", 0)
        self.ingest_packet(message, direction, latency)

    def start(self, event_bus=None):
        """
        启动监控，订阅 EventBus

        Args:
            event_bus: EventBus 实例（可选，用于进程内模式）
        """
        if event_bus:
            event_bus.on_broadcast(self._on_eventbus_message)

    def _on_eventbus_message(self, message: Message):
        """EventBus 广播回调"""
        self.ingest_packet(message, "outbound")

    # ── 统计 ────────────────────────────────────

    def _update_stats(self, packet: PacketRecord):
        self._stats["total_packets"] += 1
        self._stats["by_direction"][packet.direction] = \
            self._stats["by_direction"].get(packet.direction, 0) + 1
        self._stats["by_type"][packet.message_type] = \
            self._stats["by_type"].get(packet.message_type, 0) + 1
        self._stats["by_source"][packet.source_agent] = \
            self._stats["by_source"].get(packet.source_agent, 0) + 1
        self._stats["by_target"][packet.target_agent] = \
            self._stats["by_target"].get(packet.target_agent, 0) + 1
        if packet.latency > 0:
            n = self._stats["total_packets"]
            old = self._stats["total_latency_ms"]
            self._stats["total_latency_ms"] = old + (packet.latency - old) / n if n > 1 else packet.latency

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        avg_latency = self._stats["total_latency_ms"]
        return {
            "total_packets": self._stats["total_packets"],
            "buffer_size": len(self._buffer),
            "by_direction": dict(self._stats["by_direction"]),
            "by_type": dict(self._stats["by_type"]),
            "by_source": dict(self._stats["by_source"]),
            "by_target": dict(self._stats["by_target"]),
            "avg_latency_ms": round(avg_latency, 2),
            "subscribers": len(self._subscribers),
            "start_time": self._stats["start_time"],
        }

    # ── 查询 ────────────────────────────────────

    def get_records(
        self,
        agent_id: str = None,
        direction: str = None,
        message_type: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """多维度过滤查询数据包记录"""
        results = []
        for p in self._buffer:
            if agent_id and p.source_agent != agent_id and p.target_agent != agent_id:
                continue
            if direction and p.direction != direction:
                continue
            if message_type and p.message_type != message_type:
                continue
            results.append(p.to_dict())

        return results[offset:offset + limit]

    # ── 实时订阅 ────────────────────────────────

    def _notify_subscribers(self, packet: PacketRecord):
        """通知所有 WebSocket 订阅者"""
        data = packet.to_dict()
        dead = set()
        for q in self._subscribers:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass  # 消费者太慢，丢弃
            except Exception:
                dead.add(q)
        self._subscribers -= dead

    async def subscribe(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        WebSocket 订阅器 — 返回 AsyncGenerator，实时 yield 数据包

        用法:
            async for packet in monitor.subscribe():
                await websocket.send_json(packet)
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            while True:
                packet = await queue.get()
                yield packet
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
            cls._instance._subscribers.clear()
            cls._instance._init()
