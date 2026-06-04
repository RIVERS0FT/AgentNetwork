"""
通信拓扑生成器 + 批量通信模拟
================================

研究海量 Agent 下通信行为变化的核心模块。

支持的拓扑:
- star:   星型（1 hub + N spokes），中心瓶颈
- tree:   树型分层，父→子通信
- ring:   环形，邻居间通信
- mesh:   全连接或部分 mesh
- pubsub: 发布/订阅，按 topic 分组
- random: 随机连接
- small_world: 小世界网络（局部+长程）

通信指标:
- 总消息量、消息速率
- 延迟分布（p50/p95/p99）
- 连接度分布
- 瓶颈节点识别
- 拓扑特征（直径、平均路径长度、聚类系数）
"""

import math
import random
import time
from enum import Enum
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict


class TopologyType(Enum):
    STAR = "star"
    TREE = "tree"
    RING = "ring"
    MESH = "mesh"
    PUBSUB = "pubsub"
    RANDOM = "random"
    SMALL_WORLD = "small_world"


@dataclass
class Connection:
    """Agent 间通信连接"""
    source: str
    target: str
    weight: float = 1.0          # 连接权重（消息频率）
    latency_ms: float = 10.0     # 链路延迟
    messages_sent: int = 0       # 已发送消息数
    bandwidth_used: float = 0.0  # 已用带宽 (KB)

    def to_dict(self):
        return {
            "source": self.source, "target": self.target,
            "weight": self.weight, "latency_ms": self.latency_ms,
            "messages_sent": self.messages_sent, "bandwidth_used": round(self.bandwidth_used, 2),
        }


@dataclass
class TopologyMetrics:
    """通信拓扑指标"""
    total_agents: int = 0
    total_connections: int = 0
    total_messages: int = 0
    messages_per_round: List[int] = field(default_factory=list)
    avg_latency_ms: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    avg_degree: float = 0.0
    max_degree: int = 0
    bottleneck_agents: List[str] = field(default_factory=list)
    topology_diameter: int = 0
    avg_path_length: float = 0.0
    total_bandwidth_kb: float = 0.0
    topology_type: str = ""

    def to_dict(self):
        return {
            "total_agents": self.total_agents,
            "total_connections": self.total_connections,
            "total_messages": self.total_messages,
            "messages_per_round": self.messages_per_round,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "latency_p50_ms": round(self.latency_p50_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "latency_p99_ms": round(self.latency_p99_ms, 2),
            "avg_degree": round(self.avg_degree, 2),
            "max_degree": self.max_degree,
            "bottleneck_agents": self.bottleneck_agents[:5],
            "topology_diameter": self.topology_diameter,
            "avg_path_length": round(self.avg_path_length, 2),
            "total_bandwidth_kb": round(self.total_bandwidth_kb, 2),
            "topology_type": self.topology_type,
        }


# ═══════════════════════════════════════════════
# 拓扑生成
# ═══════════════════════════════════════════════

def generate_topology(
    n: int,
    topology: str,
    params: Dict[str, Any] = None,
) -> Tuple[List[Dict], List[Connection], List[Tuple[float, float]]]:
    """
    生成 N 个 Agent 和指定拓扑的连接关系

    Args:
        n: Agent 数量
        topology: 拓扑类型
        params: 拓扑参数
            - star: (无)
            - tree: branching=3
            - ring: (无)
            - mesh: probability=0.3 (部分mesh) 或 full=True (全连接)
            - pubsub: topics=5
            - random: degree=4
            - small_world: neighbors=4, rewire_prob=0.1

    Returns:
        (agents, connections, positions) — positions 是 (x,y) 坐标列表，用于 Canvas 布局
    """
    params = params or {}
    topology = TopologyType(topology)

    # 生成 Agent 定义
    agents = _generate_agents(n, params)

    # 生成连接
    if topology == TopologyType.STAR:
        connections, positions = _gen_star(n)
    elif topology == TopologyType.TREE:
        connections, positions = _gen_tree(n, params.get("branching", 3))
    elif topology == TopologyType.RING:
        connections, positions = _gen_ring(n)
    elif topology == TopologyType.MESH:
        if params.get("full"):
            connections, positions = _gen_full_mesh(n)
        else:
            connections, positions = _gen_partial_mesh(n, params.get("probability", 0.3))
    elif topology == TopologyType.PUBSUB:
        connections, positions = _gen_pubsub(n, params.get("topics", 5))
    elif topology == TopologyType.RANDOM:
        connections, positions = _gen_random(n, params.get("degree", 4))
    elif topology == TopologyType.SMALL_WORLD:
        connections, positions = _gen_small_world(n, params.get("neighbors", 4), params.get("rewire_prob", 0.1))
    else:
        raise ValueError(f"Unknown topology: {topology}")

    # 为每个 connection 分配随机延迟（模拟不同链路质量）
    for conn in connections:
        conn.latency_ms = random.uniform(2, 50)
        conn.weight = random.uniform(0.3, 1.0)

    return agents, connections, positions


def _generate_agents(n: int, params: Dict) -> List[Dict]:
    """批量生成 Agent 定义"""
    roles_str = params.get("roles", "scout:100")
    role_dist = _parse_role_distribution(roles_str, n)

    agents = []
    idx = 0
    for role, count in role_dist.items():
        for i in range(count):
            role_templates = {
                "scout": {"skills": ["intelligence_collection", "reconnaissance"],
                          "tags": ["sensor", f"group_{idx//10}"]},
                "commander": {"skills": ["strategy_planning", "command", "analysis"],
                              "tags": ["command", f"group_{idx//10}"]},
                "analyst": {"skills": ["data_analysis", "intelligence_collection"],
                            "tags": ["analyst", f"group_{idx//10}"]},
                "support": {"skills": ["logistics"],
                            "tags": ["support", f"group_{idx//10}"]},
                "observer": {"skills": ["monitoring"],
                             "tags": ["observer", f"group_{idx//10}"]},
            }
            tmpl = role_templates.get(role, role_templates["scout"])
            agents.append({
                "agent_id": f"{role}-{idx:04d}",
                "role": role,
                "name": f"{role}-{idx:04d}",
                "skills": tmpl["skills"],
                "tags": tmpl["tags"],
                "capability_scores": {s: random.uniform(0.5, 1.0) for s in tmpl["skills"]},
            })
            idx += 1
    return agents


def _parse_role_distribution(roles_str: str, n: int) -> Dict[str, int]:
    """解析角色分布配置: "scout:60,commander:20,analyst:20" """
    dist = {}
    total_pct = 0
    parts = [p.strip() for p in roles_str.split(",")]
    for part in parts:
        if ":" in part:
            role, pct = part.split(":")
            dist[role] = int(pct)
            total_pct += int(pct)

    if total_pct > 0:
        # 按比例分配
        result = {}
        cumulative = 0
        roles = list(dist.keys())
        for i, role in enumerate(roles):
            if i == len(roles) - 1:
                result[role] = n - cumulative
            else:
                count = max(1, int(n * dist[role] / total_pct))
                result[role] = count
                cumulative += count
        return result

    return {"scout": n}


# ═══════════════════════════════════════════════
# 各拓扑生成函数
# ═══════════════════════════════════════════════

def _gen_star(n: int) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """星型: 1 个中心 hub + (n-1) 个 spoke"""
    connections = []
    positions = [(0.5, 0.5)]  # hub 在中心
    for i in range(1, n):
        angle = 2 * math.pi * (i - 1) / max(1, n - 1)
        x = 0.5 + 0.35 * math.cos(angle)
        y = 0.5 + 0.35 * math.sin(angle)
        positions.append((x, y))
        connections.append(Connection(source=f"agent-{i:04d}", target="agent-0000"))
    return connections, positions


def _gen_tree(n: int, branching: int = 3) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """树型: 分层结构"""
    connections = []
    positions = []
    levels = []
    remaining = n
    level = 0
    nodes_at_level = 1
    while remaining > 0:
        count = min(nodes_at_level, remaining)
        levels.append(count)
        remaining -= count
        nodes_at_level *= branching
        level += 1

    # 计算位置
    node_idx = 0
    for lvl, count in enumerate(levels):
        y = 0.1 + 0.8 * lvl / max(1, len(levels) - 1)
        for i in range(count):
            x = 0.1 + 0.8 * (i + 0.5) / count
            positions.append((x, y))
            node_idx += 1

    # 生成父子连接
    parent_idx = 0
    child_offset = levels[0] if levels else 1
    for lvl in range(len(levels) - 1):
        for i in range(levels[lvl]):
            parent = parent_idx + i
            for j in range(branching):
                child = child_offset + i * branching + j
                if child < n:
                    connections.append(Connection(
                        source=f"agent-{parent:04d}", target=f"agent-{child:04d}"
                    ))
        parent_idx += levels[lvl]
        child_offset += levels[lvl + 1]

    return connections, positions


def _gen_ring(n: int) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """环形"""
    connections = []
    positions = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        x = 0.5 + 0.4 * math.cos(angle)
        y = 0.5 + 0.4 * math.sin(angle)
        positions.append((x, y))
        connections.append(Connection(
            source=f"agent-{i:04d}", target=f"agent-{(i+1)%n:04d}"
        ))
    return connections, positions


def _gen_full_mesh(n: int) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """全连接 mesh — 使用力导向布局初始位置"""
    connections = []
    positions = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        r = 0.15 + 0.3 * random.random()
        x = 0.5 + r * math.cos(angle)
        y = 0.5 + r * math.sin(angle)
        positions.append((x, y))
        for j in range(i + 1, n):
            connections.append(Connection(
                source=f"agent-{i:04d}", target=f"agent-{j:04d}"
            ))
    return connections, positions


def _gen_partial_mesh(n: int, probability: float = 0.3) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """部分 mesh"""
    connections = []
    positions = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        r = 0.15 + 0.3 * random.random()
        positions.append((0.5 + r * math.cos(angle), 0.5 + r * math.sin(angle)))
        for j in range(i + 1, n):
            if random.random() < probability:
                connections.append(Connection(
                    source=f"agent-{i:04d}", target=f"agent-{j:04d}"
                ))
    return connections, positions


def _gen_pubsub(n: int, topics: int = 5) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """发布/订阅: 每个 agent 随机订阅 1-3 个 topic，同 topic 内通信"""
    connections = []
    positions = []

    # 分配 subscriptions
    subs = defaultdict(list)
    for i in range(n):
        n_subs = random.randint(1, min(3, topics))
        subbed = random.sample(range(topics), n_subs)
        for t in subbed:
            subs[t].append(i)

    # 同 topic 内建立连接（发布者→订阅者）
    for topic, members in subs.items():
        publisher = members[0]  # 第一个成员作为 publisher
        for member in members[1:]:
            connections.append(Connection(
                source=f"agent-{publisher:04d}", target=f"agent-{member:04d}"
            ))

    # 按 topic 分组排布位置
    for i in range(n):
        # 找到该 agent 的主要 topic
        main_topic = 0
        for t in range(topics):
            if i in subs.get(t, []):
                main_topic = t
                break
        angle = 2 * math.pi * (main_topic * n // topics + (i % max(1, n // topics))) / n
        r = 0.15 + 0.3 * (0.5 + 0.5 * main_topic / topics)
        positions.append((0.5 + r * math.cos(angle), 0.5 + r * math.sin(angle)))

    return connections, positions


def _gen_random(n: int, degree: int = 4) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """随机连接: 每个 agent 连 degree 个随机 peer"""
    connections = []
    positions = []
    total_edges = n * degree // 2
    possible = set()
    for i in range(n):
        angle = 2 * math.pi * i / n
        r = 0.15 + 0.3 * random.random()
        positions.append((0.5 + r * math.cos(angle), 0.5 + r * math.sin(angle)))
        for j in range(i + 1, n):
            possible.add((i, j))

    selected = random.sample(list(possible), min(total_edges, len(possible)))
    for i, j in selected:
        connections.append(Connection(
            source=f"agent-{i:04d}", target=f"agent-{j:04d}"
        ))
    return connections, positions


def _gen_small_world(n: int, neighbors: int = 4, rewire_prob: float = 0.1) -> Tuple[List[Connection], List[Tuple[float, float]]]:
    """小世界: 先 ring，再以概率 rewire 长程连接"""
    connections = []
    positions = []
    edge_set = set()

    # Ring 基础
    for i in range(n):
        angle = 2 * math.pi * i / n
        positions.append((0.5 + 0.4 * math.cos(angle), 0.5 + 0.4 * math.sin(angle)))
        for k in range(1, neighbors // 2 + 1):
            j = (i + k) % n
            edge_set.add((min(i, j), max(i, j)))

    # Rewire
    new_edges = set()
    for (i, j) in edge_set:
        if random.random() < rewire_prob:
            new_j = random.randint(0, n - 1)
            while new_j == i or (min(i, new_j), max(i, new_j)) in edge_set:
                new_j = random.randint(0, n - 1)
            new_edges.add((min(i, new_j), max(i, new_j)))
        else:
            new_edges.add((i, j))

    for i, j in new_edges:
        connections.append(Connection(
            source=f"agent-{i:04d}", target=f"agent-{j:04d}"
        ))
    return connections, positions


# ═══════════════════════════════════════════════
# 通信模拟
# ═══════════════════════════════════════════════

def run_communication_sim(
    connections: List[Connection],
    rounds: int = 10,
    msg_rate: float = 0.5,
    agents: List[Dict] = None,
) -> TopologyMetrics:
    """
    模拟 Agent 通信

    Args:
        connections: 连接列表
        rounds: 通信轮数
        msg_rate: 每轮每个连接发送消息的概率
        agents: Agent 定义列表

    Returns:
        TopologyMetrics 通信指标
    """
    if agents is None:
        agents = []

    # 构建邻接表和度分布
    adj = defaultdict(set)
    all_agent_ids = set()
    for conn in connections:
        adj[conn.source].add(conn.target)
        adj[conn.target].add(conn.source)
        all_agent_ids.add(conn.source)
        all_agent_ids.add(conn.target)

    n = len(all_agent_ids) or len(agents) or 1
    total_messages = 0
    all_latencies = []
    messages_per_round = []
    total_bandwidth = 0.0

    for r in range(rounds):
        round_msgs = 0
        for conn in connections:
            if random.random() < msg_rate:
                # 模拟消息发送
                msg_size_kb = random.uniform(0.5, 10.0)
                latency = conn.latency_ms * (1 + 0.5 * random.random())  # 加随机抖动
                conn.messages_sent += 1
                conn.bandwidth_used += msg_size_kb
                total_messages += 1
                round_msgs += 1
                total_bandwidth += msg_size_kb
                all_latencies.append(latency)
        messages_per_round.append(round_msgs)

    # 计算延迟分位数
    all_latencies.sort()
    m = len(all_latencies)
    p50 = all_latencies[int(m * 0.5)] if m > 0 else 0
    p95 = all_latencies[int(m * 0.95)] if m > 1 else 0
    p99 = all_latencies[int(m * 0.99)] if m > 1 else 0
    avg_lat = sum(all_latencies) / m if m > 0 else 0

    # 度分布
    degrees = [len(adj.get(aid, set())) for aid in all_agent_ids]
    if not degrees:
        degrees = [0]
    avg_degree = sum(degrees) / len(degrees)
    max_degree = max(degrees)

    # 瓶颈节点：度最高的 top-5
    sorted_agents = sorted(all_agent_ids, key=lambda a: len(adj.get(a, set())), reverse=True)
    bottleneck = sorted_agents[:5]

    # 拓扑直径（BFS 近似）
    diameter = _estimate_diameter(adj, n)

    # 平均路径长度（采样估算，大 n 时不精确计算）
    avg_path = _estimate_avg_path(adj, n)

    return TopologyMetrics(
        total_agents=n,
        total_connections=len(connections),
        total_messages=total_messages,
        messages_per_round=messages_per_round,
        avg_latency_ms=avg_lat,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        latency_p99_ms=p99,
        avg_degree=avg_degree,
        max_degree=max_degree,
        bottleneck_agents=bottleneck,
        topology_diameter=diameter,
        avg_path_length=avg_path,
        total_bandwidth_kb=total_bandwidth,
    )


def _estimate_diameter(adj: Dict[str, set], n: int) -> int:
    """BFS 估算拓扑直径（采样 10 个起点取最大）"""
    if n <= 1:
        return 0
    sample_keys = list(adj.keys())
    if not sample_keys:
        return 0
    sample = random.sample(sample_keys, min(10, len(sample_keys)))
    max_dist = 0
    for start in sample:
        visited = {start}
        frontier = {start}
        dist = 0
        while frontier and dist < 50:  # 防止无限循环
            next_frontier = set()
            for node in frontier:
                for nb in adj.get(node, set()):
                    if nb not in visited:
                        visited.add(nb)
                        next_frontier.add(nb)
            frontier = next_frontier
            if frontier:
                dist += 1
        max_dist = max(max_dist, dist)
    return max_dist


def _estimate_avg_path(adj: Dict[str, set], n: int) -> float:
    """BFS 采样估算平均路径长度"""
    if n <= 1:
        return 0.0
    sample_keys = list(adj.keys())
    if not sample_keys:
        return 0.0
    sample = random.sample(sample_keys, min(5, len(sample_keys)))
    total_dist = 0
    total_pairs = 0
    for start in sample:
        visited = {start: 0}
        frontier = {start}
        dist = 0
        while frontier and dist < 20:
            next_frontier = set()
            for node in frontier:
                for nb in adj.get(node, set()):
                    if nb not in visited:
                        visited[nb] = dist + 1
                        next_frontier.add(nb)
            frontier = next_frontier
            dist += 1
        total_dist += sum(visited.values())
        total_pairs += len(visited)
    return total_dist / max(1, total_pairs)


# ═══════════════════════════════════════════════
# 拓扑对比
# ═══════════════════════════════════════════════

def compare_topologies(
    n: int = 100,
    rounds: int = 10,
    msg_rate: float = 0.3,
) -> Dict[str, TopologyMetrics]:
    """对比所有拓扑的通信指标"""
    results = {}
    topology_params = {
        "star": {},
        "ring": {},
        "tree": {"branching": 3},
        "mesh": {"probability": 0.3},
        "pubsub": {"topics": 8},
        "random": {"degree": 4},
        "small_world": {"neighbors": 4, "rewire_prob": 0.1},
    }
    for topo_name, params in topology_params.items():
        agents, conns, positions = generate_topology(n, topo_name, params)
        metrics = run_communication_sim(conns, rounds, msg_rate, agents)
        metrics.topology_type = topo_name
        results[topo_name] = metrics
    return results
