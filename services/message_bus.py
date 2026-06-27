#!/usr/bin/env python3
"""
消息总线 — 运行在 Host，路由 Agent 容器间的消息。

职责边界：
- Agent 容器通过 HTTP POST /relay 发送消息；
- bus 根据注册表和通信权限矩阵转发到目标 Agent 容器；
- bus 是通信权限的强制执行点，不再只依赖 Agent prompt 自觉遵守拓扑；
- 同时记录应用层消息和模拟网络报文，用于通信分析。
"""

import os
import sys
import json
import time
import socket
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
import uvicorn
import requests

from agent_network.logger import get_logger
from agent_network.event_bus import PacketRecorder

app = FastAPI(title="Agent Message Bus")

# ── Docker HTTP middleware（LOG_DOCKER_HTTP=1 时启用）──
SERVER_URL = os.environ.get("SERVER_URL", "http://srv:8000")
from agent_network.traffic_log import TrafficMiddleware, traffic_enabled
if traffic_enabled():
    app.add_middleware(TrafficMiddleware, component="bus", server_url=f"{SERVER_URL}")

# ── 全局日志器 ──
logger = get_logger()

# ── 可选的外部服务转发 ──
LOG_COLLECTOR_URL = os.environ.get("LOG_COLLECTOR_URL", "")
PACKET_MONITOR_URL = os.environ.get("PACKET_MONITOR_URL", "")


class RelayMessage(BaseModel):
    from_id: str
    from_name: str = ""
    to: str
    content: str
    reasoning: str = ""
    protocol: str = "TCP/HTTP"
    allowed: Optional[list] = None  # broadcast 时的通信权限过滤
    channel_id: str = ""   # 信道标识（来自 topology edge）
    talk: str = ""         # 会话/对话 ID（仿真启动时生成）


class CommPolicyUpdate(BaseModel):
    """通信权限矩阵更新请求。matrix 格式: {"agent_a": ["agent_b"]}."""

    matrix: Dict[str, List[str]] = Field(default_factory=dict)
    enabled: Optional[bool] = None


# Agent 注册表只保存真实 agent_id，中文名/展示名放到 alias 表，避免广播重复投递。
agent_registry: Dict[str, str] = {}
agent_aliases: Dict[str, str] = {}

# 通信权限矩阵：source agent_id -> allowed target agent_id set。
# 默认开启；如果未加载矩阵，则兼容本地开发，允许通信。
comm_policy_enabled = os.environ.get("BUS_ENFORCE_COMM_POLICY", "1").lower() not in {"0", "false", "no", "off"}
comm_policy_matrix: Dict[str, set] = {}

stats = {
    "total_messages": 0,
    "by_source": {},
    "by_target": {},
    "policy_denied": 0,
    "start_time": datetime.now().isoformat(),
}


def _normalize_agent_key(value: str) -> str:
    return str(value or "").strip().lower()


def _policy_snapshot() -> Dict[str, List[str]]:
    return {src: sorted(list(targets)) for src, targets in sorted(comm_policy_matrix.items())}


def _set_comm_policy(matrix: Dict[str, List[str]]):
    comm_policy_matrix.clear()
    for src, targets in (matrix or {}).items():
        s = _normalize_agent_key(src)
        if not s:
            continue
        comm_policy_matrix[s] = {
            _normalize_agent_key(t)
            for t in (targets or [])
            if _normalize_agent_key(t)
        }


def _resolve_agent_id(value: str) -> str:
    key = _normalize_agent_key(value)
    if key in agent_registry:
        return key
    return agent_aliases.get(key, "")


def _is_policy_loaded() -> bool:
    return bool(comm_policy_matrix)


def _can_communicate(source: str, target: str) -> bool:
    if not comm_policy_enabled or not _is_policy_loaded():
        return True
    source_id = _normalize_agent_key(source)
    target_id = _normalize_agent_key(target)
    return target_id in comm_policy_matrix.get(source_id, set())


def _select_broadcast_targets(source: str, allowed: Optional[list]) -> Dict[str, str]:
    source_id = _normalize_agent_key(source)
    explicit_allowed = {
        _normalize_agent_key(a)
        for a in (allowed or [])
        if _normalize_agent_key(a)
    }

    if comm_policy_enabled and _is_policy_loaded():
        policy_allowed = comm_policy_matrix.get(source_id, set())
    else:
        policy_allowed = set(agent_registry.keys())

    selected = {}
    for aid, url in agent_registry.items():
        if aid == source_id:
            continue
        if explicit_allowed and aid not in explicit_allowed:
            continue
        if aid not in policy_allowed:
            continue
        selected[aid] = url
    return selected


def _record_policy_denied(msg: RelayMessage, source_id: str, target_id: str, reason: str):
    stats["policy_denied"] = stats.get("policy_denied", 0) + 1
    PacketRecorder.record(
        direction="outbound",
        src_ip="0.0.0.0", src_port=0,
        dst_ip=f"agent:{target_id}", dst_port=0,
        protocol="TCP/HTTP", method="POST", path="/relay",
        status_code=403, latency_ms=0,
        agent_from=source_id, agent_to=target_id,
        content=msg.content, reasoning=reason,
        message_type="policy_denied", tcp_flags="RST",
        channel_id=msg.channel_id,
    )
    logger.error(
        "message_policy_denied",
        f"[Bus] 通信被策略拒绝: {source_id} -> {target_id} | {reason}",
        agent_id=source_id,
        target=target_id,
        reason=reason,
        policy_enabled=comm_policy_enabled,
        policy_loaded=_is_policy_loaded(),
    )


def _resolve_ip(url: str) -> str:
    """从 URL 解析 hostname 为 IP 地址"""
    if not url or not url.startswith("http"):
        return url or "0.0.0.0"
    try:
        host = urlparse(url).hostname
        return socket.gethostbyname(host) if host else url
    except Exception:
        return url


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agents": len(agent_registry),
        "policy_enabled": comm_policy_enabled,
        "policy_loaded": _is_policy_loaded(),
    }


@app.post("/session/start")
async def session_start(session_dir: str = ""):
    """由 server 调用，复用已创建的 session 文件夹"""
    if not session_dir:
        return {"status": "error", "detail": "session_dir required"}
    logger.set_session_dir(session_dir)
    return {"session_dir": logger._session_dir, "status": "ok"}


@app.post("/policy/comm_matrix")
async def update_comm_policy(policy: CommPolicyUpdate):
    """加载当前场景的通信矩阵，bus 在 /relay 处强制执行。"""
    global comm_policy_enabled
    if policy.enabled is not None:
        comm_policy_enabled = bool(policy.enabled)
    _set_comm_policy(policy.matrix)
    logger.system(
        "comm_policy_updated",
        f"[Bus] 通信权限矩阵已更新: {len(comm_policy_matrix)} 个源 Agent",
        details={"enabled": comm_policy_enabled, "matrix": _policy_snapshot()},
    )
    return {"enabled": comm_policy_enabled, "matrix": _policy_snapshot()}


@app.post("/policy/reset")
async def reset_comm_policy():
    comm_policy_matrix.clear()
    logger.system("comm_policy_reset", "[Bus] 通信权限矩阵已清空")
    return {"enabled": comm_policy_enabled, "matrix": {}}


@app.get("/policy")
async def get_comm_policy():
    return {"enabled": comm_policy_enabled, "matrix": _policy_snapshot()}


@app.post("/register")
async def register(agent_id: str, url: str, name: str = ""):
    """Agent 容器注册自己。agent_id 是唯一路由键，name 仅作为精确别名。"""
    aid = _normalize_agent_key(agent_id)
    if not aid:
        raise HTTPException(status_code=400, detail="agent_id required")
    agent_registry[aid] = url
    if name:
        agent_aliases[_normalize_agent_key(name)] = aid
    logger.system(
        "agent_registered",
        f"[Bus] {aid} ({name}) @ {url}",
        agent_id=aid,
        details={"url": url, "name": name, "total": len(agent_registry), "aliases": len(agent_aliases)},
    )
    return {"registered": aid, "total": len(agent_registry)}


@app.post("/unregister")
async def unregister(agent_id: str):
    aid = _normalize_agent_key(agent_id)
    agent_registry.pop(aid, None)
    for alias, mapped_id in list(agent_aliases.items()):
        if mapped_id == aid:
            agent_aliases.pop(alias, None)
    logger.system("agent_unregistered", f"[Bus] {aid} 已注销", agent_id=aid)
    return {"unregistered": aid}


@app.post("/relay")
async def relay(msg: RelayMessage, request: Request):
    """转发消息到目标 Agent；在转发前强制执行通信权限矩阵。"""
    relay_start = time.time()
    client_ip = request.client.host if request.client else "unknown"
    source_id = _resolve_agent_id(msg.from_id) or _normalize_agent_key(msg.from_id)

    stats["total_messages"] += 1
    stats["by_source"][source_id] = stats["by_source"].get(source_id, 0) + 1
    stats["by_target"][msg.to] = stats["by_target"].get(msg.to, 0) + 1

    payload_bytes = len(msg.content.encode("utf-8")) + len(msg.reasoning.encode("utf-8"))
    PacketRecorder.record(
        direction="inbound",
        src_ip=client_ip, src_port=0, dst_ip="0.0.0.0", dst_port=0,
        protocol="TCP/HTTP", method="POST", path="/relay",
        agent_from=source_id, agent_to=msg.to,
        content=msg.content, reasoning=msg.reasoning,
        message_type="relay", tcp_flags="PSH,ACK",
        channel_id=msg.channel_id,
    )

    if LOG_COLLECTOR_URL:
        try:
            requests.post(f"{LOG_COLLECTOR_URL}/api/logs/ingest", json={
                "level": "INFO", "event": "message_relayed",
                "agent_id": source_id,
                "index": "logs-agent",
                "message": msg.content,
                "details": {"to": msg.to, "reasoning": msg.reasoning},
            }, timeout=1)
        except Exception:
            pass

    if PACKET_MONITOR_URL:
        try:
            requests.post(f"{PACKET_MONITOR_URL}/api/packets/ingest", json={
                "from_id": source_id, "from_name": msg.from_name,
                "to": msg.to, "content": msg.content,
                "reasoning": msg.reasoning,
                "type": "relay",
                "direction": "outbound",
                "latency": (time.time() - relay_start) * 1000,
            }, timeout=1)
        except Exception:
            pass

    if msg.to == "broadcast":
        targets = _select_broadcast_targets(source_id, msg.allowed)
        results = {}
        for aid, url in targets.items():
            try:
                resp = requests.post(f"{url}/message", json={
                    "from_id": source_id,
                    "from_name": msg.from_name,
                    "content": msg.content,
                    "type": "broadcast",
                }, timeout=5)
                results[aid] = resp.status_code
            except Exception as e:
                results[aid] = str(e)

        for aid, status_code in results.items():
            PacketRecorder.record(
                direction="outbound", src_ip="0.0.0.0", src_port=0, dst_ip=f"agent:{aid}",
                protocol="HTTP/1.1", method="POST", path="/message",
                status_code=status_code if isinstance(status_code, int) else 0,
                agent_from=source_id, agent_to=aid,
                content=msg.content, reasoning=msg.reasoning,
                channel_id=msg.channel_id,
            )

        logger.agent_message(
            from_id=source_id,
            to="broadcast",
            content=msg.content,
            reasoning=msg.reasoning,
            status=f"broadcast({len(results)})",
            latency_ms=(time.time() - relay_start) * 1000,
            payload_len=payload_bytes,
            channel_id=msg.channel_id,
            message_type="broadcast",
            talk=msg.talk,
        )
        return {
            "broadcast": True,
            "targets": len(results),
            "results": results,
            "policy_enforced": comm_policy_enabled and _is_policy_loaded(),
        }

    target_id = _resolve_agent_id(msg.to)
    if not target_id:
        logger.error(
            "message_target_not_found",
            f"[Bus] 目标 '{msg.to}' 不在注册表中",
            agent_id=source_id,
            known_agents=list(agent_registry.keys()),
            aliases=agent_aliases,
        )
        return {"error": f"Target '{msg.to}' not found", "known": list(agent_registry.keys()), "aliases": agent_aliases}

    if not _can_communicate(source_id, target_id):
        reason = "target_not_allowed_by_comm_matrix"
        _record_policy_denied(msg, source_id, target_id, reason)
        raise HTTPException(
            status_code=403,
            detail={
                "error": "communication_denied",
                "reason": reason,
                "from": source_id,
                "to": target_id,
            },
        )

    target_url = agent_registry[target_id]
    try:
        resp = requests.post(f"{target_url}/message", json={
            "from_id": source_id,
            "from_name": msg.from_name,
            "content": msg.content,
            "type": "direct",
        }, timeout=5)
        latency = (time.time() - relay_start) * 1000
        PacketRecorder.record(
            direction="outbound",
            src_ip="0.0.0.0", src_port=0, dst_ip=_resolve_ip(target_url), dst_port=0,
            protocol="TCP/HTTP", method="POST", path="/message",
            status_code=resp.status_code, latency_ms=latency,
            agent_from=source_id, agent_to=target_id,
            content=msg.content, reasoning=msg.reasoning,
            message_type="relay", tcp_flags="PSH,ACK",
            channel_id=msg.channel_id,
        )
        logger.agent_message(
            from_id=source_id,
            to=target_id,
            content=msg.content,
            reasoning=msg.reasoning,
            latency_ms=latency,
            status=f"delivered({resp.status_code})",
            payload_len=payload_bytes,
            channel_id=msg.channel_id,
            message_type="relay",
            talk=msg.talk,
        )
        return {"relayed": True, "to": target_id, "status": resp.status_code, "latency_ms": round(latency, 1)}
    except Exception as e:
        latency = (time.time() - relay_start) * 1000
        PacketRecorder.record(
            direction="outbound", src_ip="0.0.0.0", src_port=0, dst_ip=_resolve_ip(target_url), dst_port=0,
            protocol="TCP/HTTP", method="POST", path="/message",
            status_code=0, latency_ms=latency,
            agent_from=source_id, agent_to=target_id,
            content=msg.content, reasoning=str(e),
            message_type="relay", tcp_flags="RST",
            channel_id=msg.channel_id,
        )
        logger.error(
            "message_relay_failed",
            f"[Bus] 转发给 {target_id} 失败: {e}",
            agent_id=source_id,
            target=target_id,
            error=str(e),
            latency_ms=latency,
        )
        return {"error": str(e), "to": target_id}


@app.get("/agents")
async def list_agents():
    return {"agents": agent_registry, "aliases": agent_aliases, "count": len(agent_registry)}


@app.get("/messages")
async def get_messages(limit: int = 50):
    """获取报文记录 (兼容旧API + 新格式)"""
    entries = logger.get_message_log(limit)
    return {"total": stats["total_messages"], "messages": entries, "stats": stats}


@app.get("/messages/raw")
async def get_raw_messages(limit: int = 50):
    """获取原始报文内容 (无过滤器)"""
    entries = logger.query(event="agent_message", limit=limit)
    return {"total": len(entries), "messages": entries}


@app.get("/packets")
async def get_packets(agent_id: str = None, direction: str = None, limit: int = 100):
    """获取 IP 包级别的通信报文"""
    records = PacketRecorder.get_records(agent_id=agent_id, direction=direction, limit=limit)
    return {
        "total": PacketRecorder.get_stats()["total_packets"],
        "packets": records,
        "stats": PacketRecorder.get_stats(),
    }


@app.get("/packets/stream")
async def get_packets_stream(agent_id: str = None, limit: int = 100):
    """Wireshark 风格的报文文本流"""
    lines = PacketRecorder.get_wireshark_view(agent_id=agent_id, limit=limit)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines), media_type="text/plain")


@app.get("/packets/stats")
async def get_packet_stats():
    """报文统计"""
    return PacketRecorder.get_stats()


@app.get("/stats")
async def get_stats():
    return {
        **stats,
        "agent_count": len(agent_registry),
        "alias_count": len(agent_aliases),
        "policy_enabled": comm_policy_enabled,
        "policy_loaded": _is_policy_loaded(),
        "log_stats": logger.get_index_stats(),
    }


if __name__ == "__main__":
    port = int(os.environ.get("BUS_PORT", 9000))
    logger.system("message_bus_start", f"Message Bus starting on :{port}", details={"port": port})
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
