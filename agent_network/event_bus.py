"""
Event Bus 事件总线 — 对应架构文档 第八节：通信架构 & 第九节：Packet Recorder

负责：
- Agent 间消息路由
- 收发包全链路记录（Packet Recorder）
- 消息发布/订阅

架构：
Agent → Message Gateway → Packet Recorder → MQ
"""

from typing import Dict, List, Callable, Any, Optional
from .message import Message, PacketRecord
import hashlib
import json
import time


class PacketRecorder:
    """
    收发包记录器 — 对应架构文档 第九节

    所有消息经过统一网关，记录：
    - packet_id, direction, source_agent, target_agent
    - message_type, payload_hash, token_usage, latency
    """
    _records: List[PacketRecord] = []

    @classmethod
    def record(
        cls,
        direction: str,
        message: Message,
        token_usage: int = 0,
        latency: float = 0.0,
    ):
        """记录一个数据包"""
        payload_str = json.dumps(message.payload, sort_keys=True, ensure_ascii=False)
        payload_hash = hashlib.md5(payload_str.encode()).hexdigest()[:8]

        record = PacketRecord(
            direction=direction,
            source_agent=message.source,
            target_agent=message.target,
            message_type=message.type,
            payload_hash=payload_hash,
            token_usage=token_usage,
            latency=latency,
        )
        cls._records.append(record)

    @classmethod
    def get_records(cls, agent_id: str = None) -> List[PacketRecord]:
        """按 Agent 过滤查询记录"""
        if agent_id:
            return [
                r for r in cls._records
                if r.source_agent == agent_id or r.target_agent == agent_id
            ]
        return list(cls._records)

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """获取收发包统计"""
        records = cls._records
        if not records:
            return {"total_packets": 0}
        return {
            "total_packets": len(records),
            "by_direction": {
                "outbound": len([r for r in records if r.direction == "outbound"]),
                "inbound": len([r for r in records if r.direction == "inbound"]),
            },
            "by_type": {
                t: len([r for r in records if r.message_type == t])
                for t in set(r.message_type for r in records)
            },
            "avg_latency_ms": round(
                sum(r.latency for r in records) / len(records), 2
            ),
        }

    @classmethod
    def reset(cls):
        cls._records.clear()


class EventBus:
    """
    事件总线 — Agent 间通信的核心

    对应架构文档：
    - Message Gateway: 统一消息入口
    - NATS/Kafka: 消息队列
    - Event Bus: 发布/订阅

    用法:
    bus = EventBus()
    bus.subscribe("agent-001", agent.receive_task)
    bus.publish(message)
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._subscribers: Dict[str, List[Callable]] = {}  # agent_id -> [callbacks]
        self._broadcast_subscribers: List[Callable] = []    # 全局订阅者
        self._message_log: List[Message] = []

    def subscribe(self, agent_id: str, callback: Callable[[Message], None]):
        """订阅者注册 — Agent 通过此方法接收消息"""
        if agent_id not in self._subscribers:
            self._subscribers[agent_id] = []
        self._subscribers[agent_id].append(callback)

    def on_broadcast(self, callback: Callable[[Message], None]):
        """注册广播消息处理器"""
        self._broadcast_subscribers.append(callback)

    def publish(self, message: Message) -> int:
        """
        发布消息 — 路由到目标 Agent

        返回实际投递的订阅者数量
        """
        delivered = 0
        start_time = time.time()

        # 记录出口包
        PacketRecorder.record("outbound", message)

        # 消息日志
        self._message_log.append(message)

        # 路由到目标 Agent
        target = message.target
        if target == "broadcast":
            # 广播消息
            for callback in self._broadcast_subscribers:
                callback(message)
                delivered += 1
            # 也发给所有 agent 订阅者
            for agent_id, callbacks in self._subscribers.items():
                for cb in callbacks:
                    cb(message)
                    delivered += 1
        elif target in self._subscribers:
            for callback in self._subscribers[target]:
                callback(message)
                delivered += 1

        # 计算延迟
        latency = (time.time() - start_time) * 1000  # ms

        # 记录入口包
        PacketRecorder.record("inbound", message, latency=latency)

        return delivered

    def request(self, message: Message, timeout: float = 5.0) -> Optional[Message]:
        """
        同步请求-响应模式

        发送消息并等待响应
        """
        response_holder: List[Optional[Message]] = [None]

        def on_response(resp: Message):
            if resp.type == "response" and resp.source == message.target:
                response_holder[0] = resp

        # 临时订阅响应
        self.subscribe(f"__caller_{message.message_id}", on_response)
        self.publish(message)

        # 简单轮询等待（简化实现）
        wait_start = time.time()
        while response_holder[0] is None:
            if time.time() - wait_start > timeout:
                break
            time.sleep(0.01)

        return response_holder[0]

    def get_message_log(self, agent_id: str = None) -> List[Message]:
        """查询消息日志"""
        if agent_id:
            return [
                m for m in self._message_log
                if m.source == agent_id or m.target == agent_id
            ]
        return list(self._message_log)

    def reset(self):
        """重置总线"""
        self._subscribers.clear()
        self._broadcast_subscribers.clear()
        self._message_log.clear()
        PacketRecorder.reset()
