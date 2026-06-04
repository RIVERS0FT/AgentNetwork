"""
消息模型 — 统一的消息格式，用于 Agent 间通信。

对应架构文档 第八节：通信架构 — 消息模型
{
  "message_id":"uuid",
  "source":"agent-001",
  "target":"agent-002",
  "type":"task",
  "timestamp":"2026-06-03T12:00:00",
  "payload":{"action":"collect_data"}
}
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
import uuid
import json
from typing import Any, Dict, Optional


@dataclass
class Message:
    """Agent 间通信的统一消息格式"""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    source: str = ""
    target: str = "broadcast"
    type: str = "task"  # task, response, event, broadcast, error
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        return cls(**data)

    def __repr__(self):
        return (
            f"Message(id={self.message_id}, {self.source} -> {self.target}, "
            f"type={self.type}, action={self.payload.get('action', 'N/A')})"
        )


@dataclass
class PacketRecord:
    """
    收发包记录 — 对应架构文档 第九节：收包发包全链路记录

    {
      "packet_id":"uuid",
      "direction":"outbound",
      "source_agent":"agent-001",
      "target_agent":"agent-002",
      "message_type":"task",
      "payload_hash":"xxxx",
      "token_usage":123,
      "latency":245,
      "timestamp":"..."
    }
    """
    packet_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    direction: str = "outbound"  # outbound, inbound
    source_agent: str = ""
    target_agent: str = ""
    message_type: str = "task"
    payload_hash: str = ""
    token_usage: int = 0
    latency: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
