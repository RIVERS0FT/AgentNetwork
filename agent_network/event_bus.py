"""
EventBus + PacketRecorder — Agent 间通信报文记录

每条 PacketRecord 完整记录通信元数据：
  - 时间戳
  - 源IP:源端口 → 目的IP:目的端口
  - 通信协议
  - IP包长度 (header + payload)
  - TCP Flags
  - channel_id
  - message type
  - 载荷内容
"""

import json
import threading
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from collections import deque

# HTTP 头估算大小 (模拟 TCP 头不单独计算，合并为 header_overhead)
HEADER_OVERHEAD = 200  # bytes: HTTP method line + headers + 模拟 TCP/IP 头


class PacketRecord:
    """单条 IP 包级通信记录"""
    __slots__ = (
        "timestamp", "direction",
        "src_ip", "src_port", "dst_ip", "dst_port",
        "protocol", "message_type",
        "payload_size", "header_size", "total_size",
        "tcp_flags", "channel_id",
        "method", "path", "status_code", "latency_ms",
        "agent_from", "agent_to", "content", "reasoning",
    )

    def __init__(self, **kw):
        self.timestamp = kw.get("timestamp", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
        self.direction = kw.get("direction", "relay")  # inbound | outbound | relay

        # ── L3 网络层 ──
        self.src_ip = kw.get("src_ip", "0.0.0.0")
        self.src_port = kw.get("src_port", 0)
        self.dst_ip = kw.get("dst_ip", "0.0.0.0")
        self.dst_port = kw.get("dst_port", 0)

        # ── L4 传输层 ──
        self.protocol = kw.get("protocol", "TCP/HTTP")
        self.message_type = kw.get("message_type", kw.get("event", "relay"))
        payload = kw.get("content", "")
        reasoning = kw.get("reasoning", "")
        self.payload_size = len(payload.encode('utf-8')) + len(reasoning.encode('utf-8'))
        self.header_size = kw.get("header_size", HEADER_OVERHEAD)
        self.total_size = self.header_size + self.payload_size
        # 模拟 TCP Flags: SYN=连接建立, ACK=响应, PSH=数据推送, FIN=结束
        self.tcp_flags = kw.get("tcp_flags", "PSH,ACK")
        self.channel_id = kw.get("channel_id", "")

        # ── L7 应用层 ──
        self.method = kw.get("method", "POST")
        self.path = kw.get("path", "/")
        self.status_code = kw.get("status_code", 0)
        self.latency_ms = round(kw.get("latency_ms", 0), 2)
        self.agent_from = kw.get("agent_from", "")
        self.agent_to = kw.get("agent_to", "")
        self.content = payload[:500]
        self.reasoning = reasoning[:200]

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            # 源/目的
            "src_ip": self.src_ip,
            "src_port": self.src_port,
            "dst_ip": self.dst_ip,
            "dst_port": self.dst_port,
            # 协议 & 类型
            "protocol": self.protocol,
            "message_type": self.message_type,
            # 包大小
            "payload_size": self.payload_size,
            "header_size": self.header_size,
            "total_size": self.total_size,
            # TCP
            "tcp_flags": self.tcp_flags,
            "channel_id": self.channel_id,
            # L7
            "method": self.method,
            "path": self.path,
            "status": self.status_code,
            "latency_ms": self.latency_ms,
            "agent_from": self.agent_from,
            "agent_to": self.agent_to,
            "content": self.content,
            "reasoning": self.reasoning,
        }

    def to_wireshark_style(self) -> str:
        return (
            f"{self.timestamp}  "
            f"{self.src_ip}:{self.src_port} → {self.dst_ip}:{self.dst_port}  "
            f"{self.protocol}  [{self.tcp_flags}]  "
            f"{self.total_size}B (hdr:{self.header_size} + payload:{self.payload_size})  "
            f"chan:{self.channel_id or '-'}  type:{self.message_type}  "
            f"{self.agent_from}→{self.agent_to}  "
            f"{self.content[:60]}"
        )


class PacketRecorder:
    """全局单例报文记录器 — 线程安全环形缓冲"""

    _instance: Optional["PacketRecorder"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self, max_packets: int = 1000):
        self._records: deque[PacketRecord] = deque(maxlen=max_packets)
        self._stats = {
            "total_packets": 0, "total_bytes": 0,
            "by_direction": {}, "by_agent": {},
            "by_message_type": {}, "by_status": {},
            "avg_latency_ms": 0.0,
        }

    @classmethod
    def record(cls, **kw):
        rec = PacketRecord(**kw)
        inst = cls()
        with cls._lock:
            inst._records.append(rec)
            st = inst._stats
            st["total_packets"] += 1
            st["total_bytes"] += rec.total_size
            st["by_direction"][rec.direction] = st["by_direction"].get(rec.direction, 0) + 1
            st["by_agent"][rec.agent_from] = st["by_agent"].get(rec.agent_from, 0) + 1
            st["by_message_type"][rec.message_type] = st["by_message_type"].get(rec.message_type, 0) + 1
            st["by_status"][str(rec.status_code)] = st["by_status"].get(str(rec.status_code), 0) + 1
            n = st["total_packets"]
            st["avg_latency_ms"] = round((st["avg_latency_ms"] * (n - 1) + rec.latency_ms) / n, 2) if n > 0 else 0

    @classmethod
    def record_inbound(cls, agent_id: str, src_ip: str, method: str = "POST",
                       path: str = "/message", status: int = 200, latency_ms: float = 0,
                       content: str = "", from_id: str = "", message_type: str = "relay",
                       channel_id: str = "", tcp_flags: str = "PSH,ACK", **kw):
        cls.record(
            direction="inbound", agent_to=agent_id, src_ip=src_ip, dst_ip="self",
            method=method, path=path, status_code=status, latency_ms=latency_ms,
            content=content, agent_from=from_id if from_id else kw.pop("agent_from", ""),
            message_type=message_type, channel_id=channel_id, tcp_flags=tcp_flags,
            **kw,
        )

    @classmethod
    def record_outbound(cls, agent_id: str, dst_ip: str, dst_port: int = 9000,
                        method: str = "POST", path: str = "/relay",
                        status: int = 200, latency_ms: float = 0,
                        content: str = "", reasoning: str = "",
                        message_type: str = "relay", channel_id: str = "",
                        tcp_flags: str = "PSH,ACK", agent_to: str = "", **kw):
        cls.record(
            direction="outbound", agent_from=agent_id, dst_ip=dst_ip, dst_port=dst_port,
            method=method, path=path, status_code=status, latency_ms=latency_ms,
            content=content, reasoning=reasoning,
            message_type=message_type, channel_id=channel_id, tcp_flags=tcp_flags,
            agent_to=agent_to, src_ip="self", **kw,
        )

    @classmethod
    def get_records(cls, agent_id: str = None, direction: str = None,
                    message_type: str = None, limit: int = 100) -> List[Dict]:
        inst = cls()
        with cls._lock:
            records = list(inst._records)
        if agent_id:
            records = [r for r in records if r.agent_from == agent_id or r.agent_to == agent_id]
        if direction:
            records = [r for r in records if r.direction == direction]
        if message_type:
            records = [r for r in records if r.message_type == message_type]
        return [r.to_dict() for r in records[-limit:]]

    @classmethod
    def get_wireshark_view(cls, agent_id: str = None, limit: int = 100) -> List[str]:
        records = cls()
        inst = cls()
        with cls._lock:
            all_records = list(inst._records)
        result = []
        for d in cls.get_records(agent_id=agent_id, limit=limit):
            for r in all_records:
                if r.timestamp == d["timestamp"]:
                    result.append(r.to_wireshark_style())
                    break
        return result

    @classmethod
    def get_stats(cls) -> Dict:
        inst = cls()
        with cls._lock:
            return dict(inst._stats)

    @classmethod
    def reset(cls):
        inst = cls()
        with cls._lock:
            inst._init()


class EventBus:
    def __init__(self, name="default"):
        self.name = name
        self._subscribers: Dict[str, List] = {}
    def subscribe(self, event: str, callback):
        self._subscribers.setdefault(event, []).append(callback)
    def publish(self, event: str, data: Dict = None) -> int:
        count = 0
        for cb in self._subscribers.get(event, []):
            try: cb(data); count += 1
            except Exception: pass
        return count
    def reset(self): self._subscribers.clear()
