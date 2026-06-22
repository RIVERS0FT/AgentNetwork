"""
Agent 容器内 tcpdump 网络抓包 — 捕获外部 LLM API 访问的 TCP/TLS 元数据。

写入 agent_network 网络层日志，由 LOG_LLM_API=1 控制。
不做 HTTPS 解密，不记录 payload 明文。

用法（由仿真生命周期接口控制）:
  from agent_network.packet_capture import start_capture, stop_capture
  start_capture(agent_id=AGENT_ID, server_url=SERVER_URL)
  # ... 仿真运行 ...
  stop_capture()
"""

import os
import re
import json
import time
import socket
import subprocess
import threading
from datetime import datetime
from typing import Optional, Dict, List

# ── 配置 ──

# 内部流量排除：bus、srv 容器名和 IP 模式
INTERNAL_HOSTS = {"bus", "srv", "localhost", "127.0.0.1"}
INTERNAL_PORTS = {8000, 9000, 6379}  # Agent/srv 内网端口
# 聚合窗口：同一连接在此时间内合并为一条日志
AGGREGATION_WINDOW = 5.0  # seconds

# tcpdump 输出行正则，兼容：
#   12:34:56.123456 IP 172.18.0.2.53000 > 1.2.3.4.443: Flags [S], ... length 0
#   1718170000.123456 eth0 Out IP 172.18.0.2.53000 > 1.2.3.4.443: Flags [P.], ... length 123
TCPDUMP_LINE = re.compile(
    r'^(?P<time>\S+)\s+'
    r'(?:(?P<iface>\S+)\s+(?P<iface_dir>In|Out|in|out)\s+)?'
    r'IP\s+'
    r'(?P<src>.+?)\.(?P<src_port>\d+)\s+>\s+'
    r'(?P<dst>.+?)\.(?P<dst_port>\d+):'
    r'.*?Flags\s+\[(?P<flags>[^\]]+)\].*?length\s+(?P<length>\d+)'
)

_capture_process: Optional[subprocess.Popen] = None
_capture_thread: Optional[threading.Thread] = None
_running = False
_capture_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _resolve_llm_hosts() -> List[str]:
    """从环境变量解析 LLM API 目标 host"""
    hosts = set()
    for key in ("LLM_API_BASE", "ANTHROPIC_BASE_URL", "OPENAI_API_BASE"):
        url = os.environ.get(key, "")
        if url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                if parsed.hostname:
                    hosts.add(parsed.hostname)
            except Exception:
                pass
    # DeepSeek 默认
    if not hosts:
        hosts.add("api.deepseek.com")
    return list(hosts)


def _send_record(server_url: str, record: dict):
    """异步发送抓包记录到 /api/logs/ingest"""
    try:
        import requests as _r
        _r.post(f"{server_url}/api/logs/ingest", json=record, timeout=2)
    except Exception:
        pass


def _llm_capture_enabled() -> bool:
    """LLM 网络层抓包开关；LOG_TRAFFIC 仅作为旧配置兼容。"""
    return os.environ.get("LOG_LLM_API", os.environ.get("LOG_TRAFFIC", "0")) == "1"


def _parse_tcpdump_line(line: str) -> Optional[dict]:
    """解析一行 tcpdump 输出"""
    m = TCPDUMP_LINE.match(line.strip())
    if not m:
        return None
    gd = m.groupdict()
    return {
        "time": gd["time"],
        "interface": gd.get("iface") or "any",
        "interface_direction": (gd.get("iface_dir") or "").lower(),
        "src_ip": gd["src"],
        "src_port": int(gd["src_port"]),
        "dst_ip": gd["dst"],
        "dst_port": int(gd["dst_port"]),
        "tcp_flags": gd["flags"],
        "length": int(gd["length"]),
    }


def _is_internal_endpoint(host: str) -> bool:
    if host in INTERNAL_HOSTS:
        return True
    return host.startswith("172.") or host.startswith("10.") or host.startswith("192.168.")


def _external_endpoint(parsed: dict) -> Optional[dict]:
    """返回外部 LLM 端点和方向；TLS 回包通常是 src_port=443/80。"""
    if parsed["dst_port"] in INTERNAL_PORTS or parsed["src_port"] in INTERNAL_PORTS:
        return None
    if parsed["dst_port"] in (443, 80) and not _is_internal_endpoint(parsed["dst_ip"]):
        return {
            "direction": "out",
            "ip": parsed["dst_ip"],
            "port": parsed["dst_port"],
            "local_ip": parsed["src_ip"],
            "local_port": parsed["src_port"],
        }
    if parsed["src_port"] in (443, 80) and not _is_internal_endpoint(parsed["src_ip"]):
        return {
            "direction": "in",
            "ip": parsed["src_ip"],
            "port": parsed["src_port"],
            "local_ip": parsed["dst_ip"],
            "local_port": parsed["dst_port"],
        }
    return None


def _is_llm_traffic(parsed: dict, llm_hosts: List[str]) -> bool:
    """判断是否为外部 HTTPS/HTTP 流量（包含请求包和回包）。"""
    return _external_endpoint(parsed) is not None


def _actor(agent_id: str, agent_name: str = "") -> dict:
    actor = {"id": agent_id}
    if agent_name:
        actor["name"] = agent_name
    return actor


def _flush_aggregated(agent_id: str, server_url: str, connections: dict,
                      force: bool = False, agent_name: str = ""):
    """将聚合的连接数据写入日志"""
    now = time.time()
    expired = []
    for key, data in list(connections.items()):
        if not force and now - data["last_time"] < AGGREGATION_WINDOW:
            continue
        expired.append(key)
        direction = data.get("direction", "out")
        dir_label = direction.upper()
        record = {
            "timestamp": _now_iso(),
            "level": "INFO",
            "source": "agent",
            "component": agent_id,
            "category": "agent_network",
            "layer": "agent_network",
            "event": "llm_api_packet",
            "actor": _actor(agent_id, agent_name),
            "target": {
                "host": data["host"],
                "ip": data["external_ip"],
                "port": data["external_port"],
            },
            "action": {"name": f"SEND" if direction == "out" else "RECV", "status": f"{data['count']} packets"},
            "payload": {
                "line_summary": f"{dir_label}: {data['count']} packets, {data['total_bytes']} bytes",
                "capture_source": "tcpdump",
                "body_logged": False,
            },
            "network": {
                "direction": direction,
                "protocol": "TCP/TLS",
                "src_ip": data["src_ip"],
                "src_port": data["src_port"],
                "dst_ip": data["dst_ip"],
                "dst_port": data["dst_port"],
                "external_ip": data["external_ip"],
                "external_port": data["external_port"],
                "local_ip": data["local_ip"],
                "local_port": data["local_port"],
                "tcp_flags": data.get("last_flags", ""),
                "packet_len": data["total_bytes"],
                "capture_interface": data.get("interface", "any"),
                "interface_direction": data.get("interface_direction", ""),
                "external": True,
            },
            "trace": {},
        }
        threading.Thread(target=_send_record, args=(server_url, record), daemon=True).start()
    for key in expired:
        del connections[key]


def _capture_loop(agent_id: str, agent_name: str, server_url: str):
    """后台抓包线程 — 运行 tcpdump 并解析输出"""
    global _running, _capture_process
    llm_hosts = _resolve_llm_hosts()

    # 构建 tcpdump 命令
    # -i any: 所有接口
    # -nn: 不解析 hostname/port name，保证端口是数字
    # tcp port 443 or tcp port 80: 只抓外部 HTTPS/HTTP
    cmd = ["tcpdump", "-i", "any", "-nn", "-l",
           "tcp port 443 or tcp port 80"]

    try:
        _capture_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        _running = False
        record = {
            "timestamp": _now_iso(), "level": "WARN",
            "source": "agent", "component": agent_id,
            "category": "system", "event": "tcpdump_missing",
            "actor": _actor(agent_id, agent_name),
            "message": f"[{agent_id}] tcpdump not found, packet capture disabled",
        }
        _send_record(server_url, record)
        return
    except PermissionError:
        _running = False
        record = {
            "timestamp": _now_iso(), "level": "WARN",
            "source": "agent", "component": agent_id,
            "category": "system", "event": "tcpdump_permission_denied",
            "actor": _actor(agent_id, agent_name),
            "message": f"[{agent_id}] No permission for tcpdump (NET_RAW/NET_ADMIN needed)",
        }
        _send_record(server_url, record)
        return

    _send_record(server_url, {
        "timestamp": _now_iso(),
        "level": "INFO",
        "source": "agent",
        "component": agent_id,
        "category": "system",
        "event": "tcpdump_started",
        "actor": _actor(agent_id, agent_name),
        "message": f"[{agent_id}] tcpdump started for LLM API network capture",
        "payload": {"command": " ".join(cmd), "llm_hosts": llm_hosts},
    })
    # 聚合缓冲区：connection_key → {count, bytes, timestamps}
    connections: Dict[str, dict] = {}
    last_flush = time.time()
    parse_miss = 0

    while _running:
        line = _capture_process.stdout.readline()
        if not line and _capture_process.poll() is not None:
            break

        parsed = _parse_tcpdump_line(line)
        if not parsed:
            parse_miss += 1
            if parse_miss in (10, 100, 1000):
                _send_record(server_url, {
                    "timestamp": _now_iso(),
                    "level": "WARN",
                    "source": "agent",
                    "component": agent_id,
                    "category": "system",
                    "event": "tcpdump_parse_miss",
                    "actor": _actor(agent_id, agent_name),
                    "message": f"[{agent_id}] tcpdump output parse miss x{parse_miss}",
                    "payload": {"sample": line.strip()[:300]},
                })
            continue
        if not _is_llm_traffic(parsed, llm_hosts):
            continue

        # 聚合：按外部端点 + 本地端口 + 方向分组，收发分离。
        endpoint = _external_endpoint(parsed)
        if not endpoint:
            continue
        dir_key = endpoint["direction"]
        key = f"{endpoint['local_ip']}:{endpoint['local_port']}:{endpoint['ip']}:{endpoint['port']}:{dir_key}"
        if key not in connections:
            # 尝试 DNS 解析 host
            host = endpoint["ip"]
            for h in llm_hosts:
                try:
                    ips = socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP)
                    if any(addr[4][0] == endpoint["ip"] for addr in ips):
                        host = h
                        break
                except Exception:
                    pass
            connections[key] = {
                "host": host,
                "src_ip": parsed["src_ip"],
                "src_port": parsed["src_port"],
                "dst_ip": parsed["dst_ip"],
                "dst_port": parsed["dst_port"],
                "external_ip": endpoint["ip"],
                "external_port": endpoint["port"],
                "local_ip": endpoint["local_ip"],
                "local_port": endpoint["local_port"],
                "interface": parsed.get("interface", "any"),
                "interface_direction": parsed.get("interface_direction", ""),
                "direction": dir_key,
                "count": 0,
                "total_bytes": 0,
                "last_time": time.time(),
                "last_flags": "",
            }
        conn = connections[key]
        conn["count"] += 1
        conn["total_bytes"] += parsed["length"]
        conn["last_time"] = time.time()
        conn["last_flags"] = parsed["tcp_flags"]

        # 定期刷新聚合数据
        if time.time() - last_flush > AGGREGATION_WINDOW:
            _flush_aggregated(agent_id, server_url, connections, agent_name=agent_name)
            last_flush = time.time()

    # 非主动停止时才冲刷缓冲；仿真停止时不补写尾部 llm_api_packet。
    _flush_aggregated(agent_id, server_url, connections, force=_running, agent_name=agent_name)
    stderr = ""
    returncode = _capture_process.poll() if _capture_process else None
    try:
        stderr = (_capture_process.stderr.read() if _capture_process and _capture_process.stderr else "") or ""
    except Exception:
        stderr = ""
    _send_record(server_url, {
        "timestamp": _now_iso(),
        "level": "WARN" if returncode not in (0, None) else "INFO",
        "source": "agent",
        "component": agent_id,
        "category": "system",
        "event": "tcpdump_exited",
        "actor": _actor(agent_id, agent_name),
        "message": f"[{agent_id}] tcpdump exited rc={returncode}",
        "payload": {"returncode": returncode, "stderr": stderr[-500:]},
    })
    _running = False


def start_capture(agent_id: str = "", agent_name: str = "", server_url: str = "http://srv:8000"):
    """启动后台抓包（由 agent_server main 调用）"""
    global _capture_thread, _running
    if not _llm_capture_enabled():
        return {"status": "disabled"}
    agent_id = agent_id or os.environ.get("AGENT_ID", "agent-001")
    agent_name = agent_name or os.environ.get("AGENT_NAME", agent_id)
    with _capture_lock:
        if _running or (_capture_thread and _capture_thread.is_alive()):
            return {"status": "running"}
        _running = True
        _capture_thread = threading.Thread(
            target=_capture_loop,
            args=(agent_id, agent_name, server_url),
            daemon=True,
        )
        _capture_thread.start()
    return {"status": "started"}


def stop_capture():
    """停止抓包"""
    global _running, _capture_process, _capture_thread
    with _capture_lock:
        _running = False
        proc = _capture_process
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            _capture_process = None
    if _capture_thread and _capture_thread.is_alive() and threading.current_thread() is not _capture_thread:
        _capture_thread.join(timeout=2)
    _capture_thread = None
    return {"status": "stopped"}
