#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NETWORK_FIELDS = ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit")
LINK_FIELDS = {"endpoint_a", "endpoint_b", "channel_id", *NETWORK_FIELDS}
ALIASES = {
    "delay_ms": ("delay_ms", "latency_ms", "latency"),
    "jitter_ms": ("jitter_ms", "jitter"),
    "loss_pct": ("loss_pct", "loss_percent", "packet_loss_pct", "packet_loss"),
    "rate_mbit": ("rate_mbit", "bandwidth_mbps", "bandwidth"),
}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"cannot locate {label}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"cannot locate start of {label}")
    end_index = text.find(end, start_index + len(start))
    if end_index < 0:
        raise RuntimeError(f"cannot locate end of {label}")
    return text[:start_index] + replacement + text[end_index:]


def number(data: dict[str, Any], field: str) -> float:
    for name in ALIASES[field]:
        value = data.get(name)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{field} must be numeric") from exc
    return 0.0


def migrate_topology_file(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document.get("topology"), list):
        raw_links = document["topology"]
    elif isinstance(document.get("sub_networks"), list):
        raw_links = []
        for subnet in document["sub_networks"]:
            raw_links.extend(subnet.get("edges") or [])
    else:
        raise RuntimeError(f"{path}: root topology array is required")

    links = []
    channel_ids = set()
    for index, raw in enumerate(raw_links):
        if not isinstance(raw, dict):
            raise RuntimeError(f"{path}: topology[{index}] must be an object")
        if raw.get("direct_chat") is False or raw.get("can_direct_chat") is False:
            continue
        endpoint_a = raw.get("endpoint_a") or raw.get("source") or raw.get("from")
        endpoint_b = raw.get("endpoint_b") or raw.get("target") or raw.get("to")
        if not isinstance(endpoint_a, str) or not endpoint_a.strip():
            raise RuntimeError(f"{path}: topology[{index}] endpoint_a is required")
        if not isinstance(endpoint_b, str) or not endpoint_b.strip():
            raise RuntimeError(f"{path}: topology[{index}] endpoint_b is required")
        endpoint_a = endpoint_a.strip().lower()
        endpoint_b = endpoint_b.strip().lower()
        if endpoint_a == endpoint_b:
            raise RuntimeError(f"{path}: topology[{index}] cannot be a self-link")
        channel_id = str(raw.get("channel_id") or f"ch_{endpoint_a}_{endpoint_b}").strip()
        if channel_id in channel_ids:
            raise RuntimeError(f"{path}: duplicate channel_id {channel_id}")
        channel_ids.add(channel_id)
        profile = dict(raw)
        if isinstance(raw.get("network"), dict):
            profile.update(raw["network"])
        links.append({
            "endpoint_a": endpoint_a,
            "endpoint_b": endpoint_b,
            "channel_id": channel_id,
            **{field: number(profile, field) for field in NETWORK_FIELDS},
        })
    path.write_text(
        json.dumps({"topology": links}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def patch_simulations() -> None:
    path = "agent_network/api/simulations.py"
    text = read(path)
    text = replace_once(
        text,
        "_pending_seed: int = 0\n",
        "_pending_seed: int = 0\n\n"
        "_TOPOLOGY_NETWORK_FIELDS = (\"delay_ms\", \"jitter_ms\", \"loss_pct\", \"rate_mbit\")\n"
        "_TOPOLOGY_LINK_FIELDS = {\"endpoint_a\", \"endpoint_b\", \"channel_id\", *_TOPOLOGY_NETWORK_FIELDS}\n",
        "topology constants",
    )
    text = replace_between(
        text,
        "    for edge in topology or []:\n",
        "    profiles = {agent_id: list(items.values()) for agent_id, items in profile_maps.items()}\n",
        '''    for edge in topology or []:
        endpoint_a = str(edge.get("endpoint_a", "")).lower()
        endpoint_b = str(edge.get("endpoint_b", "")).lower()
        if endpoint_a not in agents or endpoint_b not in agents:
            validation_errors.append(
                f"unknown topology endpoints: {endpoint_a}<->{endpoint_b}"
            )
            continue
        try:
            network = normalize_profile({
                field: edge.get(field, 0)
                for field in _TOPOLOGY_NETWORK_FIELDS
            })
        except ValueError as exc:
            validation_errors.append(f"{endpoint_a}<->{endpoint_b}: {exc}")
            continue
        if not any(network.values()):
            continue
        add_profile(endpoint_a, endpoint_b, network)
        add_profile(endpoint_b, endpoint_a, network)

''',
        "network topology loop",
    )
    text = replace_between(
        text,
        "    _comm_matrix.clear()\n",
        "    logger.start_session(scene_def.scene_name)\n",
        '''    _comm_matrix.clear()
    for edge in (scene_def.topology or []):
        endpoint_a = str(edge.get("endpoint_a", "")).lower()
        endpoint_b = str(edge.get("endpoint_b", "")).lower()
        if endpoint_a and endpoint_b:
            _comm_matrix.setdefault(endpoint_a, set()).add(endpoint_b)
            _comm_matrix.setdefault(endpoint_b, set()).add(endpoint_a)

''',
        "communication matrix",
    )
    text = replace_between(
        text,
        "    topology_edges = []\n",
        "    return SceneDefinition(scene_name=title, description=bg, agents=agents, topology=topology_edges)\n",
        '''    raw_topology = topology_config.get("topology")
    if not isinstance(raw_topology, list):
        raise ValueError(
            f"Scene '{scene_name}' network_topology.json must contain a root-level topology array."
        )

    agent_ids = {agent.agent_id for agent in agents}
    channel_ids = set()
    topology_edges = []
    for index, edge in enumerate(raw_topology):
        if not isinstance(edge, dict):
            raise ValueError(f"Scene '{scene_name}' topology[{index}] must be an object.")
        unexpected = set(edge) - _TOPOLOGY_LINK_FIELDS
        missing = {"endpoint_a", "endpoint_b", "channel_id"} - set(edge)
        if unexpected:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] has unsupported fields: {sorted(unexpected)}"
            )
        if missing:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] is missing fields: {sorted(missing)}"
            )
        endpoint_a = str(edge["endpoint_a"]).strip().lower()
        endpoint_b = str(edge["endpoint_b"]).strip().lower()
        channel_id = str(edge["channel_id"]).strip()
        if not endpoint_a or not endpoint_b or endpoint_a == endpoint_b:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] must connect two distinct endpoints."
            )
        unknown = {endpoint_a, endpoint_b} - agent_ids
        if unknown:
            raise ValueError(
                f"Scene '{scene_name}' topology[{index}] references unknown agents: {sorted(unknown)}"
            )
        if not channel_id:
            raise ValueError(f"Scene '{scene_name}' topology[{index}] channel_id must be non-empty.")
        if channel_id in channel_ids:
            raise ValueError(f"Scene '{scene_name}' contains duplicate channel_id '{channel_id}'.")
        channel_ids.add(channel_id)
        network = normalize_profile({
            field: edge.get(field, 0)
            for field in _TOPOLOGY_NETWORK_FIELDS
        })
        topology_edges.append({
            "endpoint_a": endpoint_a,
            "endpoint_b": endpoint_b,
            "channel_id": channel_id,
            **network,
        })
''',
        "strict topology parser",
    )
    write(path, text)


def patch_network_emulation() -> None:
    path = "agent_network/network_emulation.py"
    text = read(path)
    text = replace_once(
        text,
        '        profile = normalize_profile(item.get("network", {}))\n',
        '        profile = normalize_profile(item)\n',
        "flat network profile",
    )
    write(path, text)


def patch_dashboard() -> None:
    path = "web/public/dashboard.js"
    text = read(path)
    text = replace_once(
        text,
        '''function hasTopologyEdge(fromId, toId) {
  return _topology.some(r => {
    const f = r.from.toLowerCase();
    const t = r.to.toLowerCase();
    return (f === fromId && t === toId) || (f === toId && t === fromId);
  });
}''',
        '''function hasTopologyEdge(fromId, toId) {
  return _topology.some(link => {
    const endpointA = String(link.endpoint_a || '').toLowerCase();
    const endpointB = String(link.endpoint_b || '').toLowerCase();
    return (
      (endpointA === fromId && endpointB === toId) ||
      (endpointA === toId && endpointB === fromId)
    );
  });
}''',
        "dashboard topology lookup",
    )
    start = text.find("function drawTopologyLines(topology, agents) {")
    end = text.find("\n// ── Draw a single trajectory", start)
    if start < 0 or end < 0:
        raise RuntimeError("cannot locate drawTopologyLines")
    replacement = '''function drawTopologyLines(topology, agents) {
  if (!ctx || !topology.length || !agents.length) return;

  ctx.save();
  ctx.lineCap = 'round';
  for (const link of topology) {
    const endpointA = String(link.endpoint_a || '').toLowerCase();
    const endpointB = String(link.endpoint_b || '').toLowerCase();
    const fromPos = getAgentWorldPos(endpointA);
    const toPos = getAgentWorldPos(endpointB);
    if (!fromPos || !toPos) continue;

    const from = worldToScreen(fromPos.x, fromPos.y);
    const to = worldToScreen(toPos.x, toPos.y);
    const delay = Math.max(0, Number(link.delay_ms) || 0);
    const jitter = Math.max(0, Number(link.jitter_ms) || 0);
    const loss = Math.max(0, Number(link.loss_pct) || 0);
    const rate = Math.max(0, Number(link.rate_mbit) || 0);
    const constrained = delay > 0 || jitter > 0 || loss > 0 || rate > 0;
    const color = constrained ? 'rgba(255,191,90,0.82)' : 'rgba(56,213,255,0.62)';
    const glow = constrained ? 'rgba(255,191,90,0.28)' : 'rgba(47,140,255,0.24)';

    ctx.beginPath();
    ctx.moveTo(from.sx, from.sy);
    ctx.lineTo(to.sx, to.sy);
    ctx.setLineDash(constrained ? [7, 5] : [10, 7]);
    ctx.lineWidth = 3.4;
    ctx.strokeStyle = glow;
    ctx.shadowColor = glow;
    ctx.shadowBlur = 14;
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(from.sx, from.sy);
    ctx.lineTo(to.sx, to.sy);
    ctx.lineWidth = 1;
    ctx.strokeStyle = color;
    ctx.shadowBlur = 8;
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.shadowBlur = 0;
  ctx.restore();
}
'''
    text = text[:start] + replacement + text[end:]
    text = replace_once(
        text,
        '''  for (const rel of _topology) {
    const f = rel.from.toLowerCase();
    const t = rel.to.toLowerCase();
    if (!adj.has(f)) adj.set(f, new Set());
    if (!adj.has(t)) adj.set(t, new Set());
    adj.get(f).add(t); adj.get(t).add(f);
  }''',
        '''  for (const link of _topology) {
    const endpointA = String(link.endpoint_a || '').toLowerCase();
    const endpointB = String(link.endpoint_b || '').toLowerCase();
    if (!adj.has(endpointA)) adj.set(endpointA, new Set());
    if (!adj.has(endpointB)) adj.set(endpointB, new Set());
    adj.get(endpointA).add(endpointB);
    adj.get(endpointB).add(endpointA);
  }''',
        "dashboard topology adjacency",
    )
    write(path, text)


def patch_tests() -> None:
    path = "tests/test_network_emulation.py"
    text = read(path)
    text = replace_once(
        text,
        '''        profiles=[{
            "target_agent": "agent_b",
            "target_host": "ag-c2",
            "network": {"delay_ms": 20, "jitter_ms": 5, "loss_pct": 1, "rate_mbit": 100},
        }],''',
        '''        profiles=[{
            "target_agent": "agent_b",
            "target_host": "ag-c2",
            "delay_ms": 20,
            "jitter_ms": 5,
            "loss_pct": 1,
            "rate_mbit": 100,
        }],''',
        "network test profile",
    )
    text = text.replace("test_simulation_translates_bidirectional_edge_into_two_agent_profiles", "test_simulation_translates_topology_link_into_two_agent_profiles")
    text = replace_once(
        text,
        '''        [{
            "from": "a",
            "to": "b",
            "bidirectional": True,
            "network": {"delay_ms": 20},
        }],''',
        '''        [{
            "endpoint_a": "a",
            "endpoint_b": "b",
            "channel_id": "ch_a_b",
            "delay_ms": 20,
            "jitter_ms": 0,
            "loss_pct": 0,
            "rate_mbit": 0,
        }],''',
        "TopologyLink network test",
    )
    write(path, text)

    path = "tests/test_scene_building_boundary.py"
    text = read(path)
    text = replace_once(
        text,
        '        json.dumps({"sub_networks": [{"edges": []}]}, ensure_ascii=False),\n',
        '        json.dumps({"topology": []}, ensure_ascii=False),\n',
        "scene topology fixture",
    )
    write(path, text)


def patch_scenario_generator() -> None:
    path = "scenes/scenario.py"
    text = read(path)
    text = text.replace(
        "你必须在剧本中声明总体拓扑类型（global_topology_type），并通过子网（sub_networks）的形式，把角色分入不同的拓扑层。",
        "network_topology.json 必须使用根级 topology 数组；每条链路使用 endpoint_a、endpoint_b、channel_id、delay_ms、jitter_ms、loss_pct、rate_mbit，链路天然双向。",
    )
    text = replace_between(
        text,
        '                # 模块三：丰富拓扑结构关系网络 (对应 network_topology.json)\n',
        '                # 模块四：技能可执行代码 (对应 skills.py)\n',
        '''                # 模块三：Agent 双向网络链路 (对应 network_topology.json)
                "network_topology": {
                    "type": "object",
                    "properties": {
                        "topology": {
                            "type": "array",
                            "description": "Agent 间天然双向的网络链路列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "endpoint_a": {"type": "string"},
                                    "endpoint_b": {"type": "string"},
                                    "channel_id": {"type": "string"},
                                    "delay_ms": {"type": "number", "minimum": 0, "maximum": 60000},
                                    "jitter_ms": {"type": "number", "minimum": 0, "maximum": 60000},
                                    "loss_pct": {"type": "number", "minimum": 0, "maximum": 100},
                                    "rate_mbit": {"type": "number", "minimum": 0, "maximum": 1000000}
                                },
                                "required": ["endpoint_a", "endpoint_b", "channel_id", "delay_ms", "jitter_ms", "loss_pct", "rate_mbit"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["topology"],
                    "additionalProperties": False
                },
''',
        "scenario topology schema",
    )
    text = replace_once(
        text,
        '''    # 从 edges 构建双向 peers
    peer_map = {rid: set() for rid in roles}
    for subnet in topo.get("sub_networks", []):
        for edge in subnet.get("edges", []):
            src, dst = edge["source"], edge["target"]
            if src in roles:
                peer_map[src].add(dst)
            if dst in roles:
                peer_map[dst].add(src)
''',
        '''    # TopologyLink 天然双向，构建 peers。
    peer_map = {rid: set() for rid in roles}
    for link in topo.get("topology", []):
        endpoint_a = link["endpoint_a"]
        endpoint_b = link["endpoint_b"]
        if endpoint_a in roles:
            peer_map[endpoint_a].add(endpoint_b)
        if endpoint_b in roles:
            peer_map[endpoint_b].add(endpoint_a)
''',
        "scenario peers",
    )
    text = text.replace('        "global_topology_type": topo["global_topology_type"],\n', '        "topology": topo["topology"],\n')
    text = text.replace(
        '    print(f"3. 宏观拓扑结构: {topo[\'global_topology_type\']}")\n    print(f"4. 包含子网络数: {len(topo[\'sub_networks\'])}")\n',
        '    print(f"3. 双向拓扑链路数: {len(topo[\'topology\'])}")\n',
    )
    write(path, text)


def patch_docs() -> None:
    path = "scenes/README.md"
    text = read(path)
    text = text.replace("通信信道层：子网划分 + 静态边 (STAR/MESH/BIPARTITE)", "通信信道层：天然双向的 TopologyLink 列表")
    text = replace_once(
        text,
        '''| `global_topology_type` | enum | `STAR` / `MESH` / `TREE` / `RING` / `HYBRID_MESH` |
| `sub_networks[]` | object | `{sub_id, topology_type, description, nodes[], edges[]}` |
| `sub_networks[].edges[]` | object | `{source, target, paradigm, channel_id}` |
| `edges[].paradigm` | enum | `COLLABORATION` / `NEGOTIATION` / `GAME` |
''',
        '''| `topology[]` | object[] | Agent 间天然双向的网络链路 |
| `topology[].endpoint_a` | string | 链路端点 A |
| `topology[].endpoint_b` | string | 链路端点 B |
| `topology[].channel_id` | string | 唯一通道 ID |
| `topology[].delay_ms` | number | 双向链路时延 |
| `topology[].jitter_ms` | number | 双向链路抖动 |
| `topology[].loss_pct` | number | 双向链路丢包率 |
| `topology[].rate_mbit` | number | 双向链路带宽限制 |
''',
        "README topology table",
    )
    text = text.replace("| `global_topology_type` | network_topology |", "| `topology` | network_topology |")
    text = text.replace("roles + edges 双向推导 + instances 提取", "roles + topology 双向推导 + instances 提取")
    write(path, text)

    path = "docs/设计文档.md"
    text = read(path)
    text = re.sub(
        r'```json\n\{\n\s*"sub_networks".*?\n\}\n```',
        '''```json
{
  "topology": [
    {
      "endpoint_a": "ceo",
      "endpoint_b": "cto",
      "channel_id": "ch_ceo_cto",
      "delay_ms": 20,
      "jitter_ms": 5,
      "loss_pct": 0.5,
      "rate_mbit": 100
    }
  ]
}
```''',
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace("`direct_chat=false` 的边不会进入 bus 通信矩阵", "未出现在 `topology` 中的 Agent 对不会进入 bus 通信矩阵")
    write(path, text)


def validate() -> None:
    simulations = read("agent_network/api/simulations.py")
    for token in ('edge.get("from"', 'edge.get("to"', 'edge.get("bidirectional"', 'edge.get("can_direct_chat"', 'topology_config.get("sub_networks"'):
        if token in simulations:
            raise RuntimeError(f"legacy simulation token remains: {token}")
    for token in ('edge.get("endpoint_a"', 'edge.get("endpoint_b"', 'topology_config.get("topology")'):
        if token not in simulations:
            raise RuntimeError(f"missing simulation token: {token}")

    if 'item.get("network"' in read("agent_network/network_emulation.py"):
        raise RuntimeError("nested network profile remains")

    dashboard = read("web/public/dashboard.js")
    for token in ("rel.from", "rel.to", "rel.value"):
        if token in dashboard:
            raise RuntimeError(f"legacy dashboard token remains: {token}")

    for path in sorted((ROOT / "scenes").rglob("network_topology.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if set(document) != {"topology"}:
            raise RuntimeError(f"{path}: topology must be the only root field")
        for link in document["topology"]:
            if set(link) != LINK_FIELDS:
                raise RuntimeError(f"{path}: invalid TopologyLink fields")


def main() -> None:
    patch_simulations()
    patch_network_emulation()
    patch_dashboard()
    patch_tests()
    patch_scenario_generator()
    patch_docs()
    for topology_path in sorted((ROOT / "scenes").rglob("network_topology.json")):
        migrate_topology_file(topology_path)
    validate()
    print("TopologyLink migration complete")


if __name__ == "__main__":
    main()
