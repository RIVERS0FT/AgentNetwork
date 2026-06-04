"""
Task Dispatcher — 对应架构文档 第六节：Agent管理层 / Task Dispatcher

升级版任务分发器，支持：
- 5 种路由策略（ROUND_ROBIN / LEAST_LOADED / AFFINITY / RANDOM / BEST_CAPABILITY）
- 指数退避重试（1s, 2s, 4s, 8s...）
- 任务持久化（JSON 文件存储，重启恢复）
- 分发日志（记录每次分发的时间、目标、结果）

使用方式:
    from agent_network.task_dispatcher import TaskDispatcher, RoutingStrategy

    dispatcher = TaskDispatcher(persistence_path="data/tasks.json")

    # 分发任务到最合适的 Agent
    target = dispatcher.route(registry, task, strategy=RoutingStrategy.LEAST_LOADED)
    result = dispatcher.dispatch(target, task)
"""

from __future__ import annotations

import os
import json
import time
import random
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime

if TYPE_CHECKING:
    from .agent import Agent, AgentRegistry
    from .agent_scheduler import ScheduledTask


# ═══════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════

class RoutingStrategy(Enum):
    """任务路由策略"""
    ROUND_ROBIN = "round_robin"           # 轮询
    LEAST_LOADED = "least_loaded"         # 最少负载（任务队列最短）
    AFFINITY = "affinity"                 # 亲和性（优先同类型）
    RANDOM = "random"                     # 随机
    BEST_CAPABILITY = "best_capability"   # 按能力评分选最优

    @classmethod
    def from_string(cls, s: str) -> "RoutingStrategy":
        mapping = {e.value: e for e in cls}
        return mapping.get(s.lower(), cls.ROUND_ROBIN)


@dataclass
class DispatchRecord:
    """分发记录"""
    task_id: str
    target_agent_id: str
    strategy: str
    success: bool
    error: str = ""
    timestamp: str = ""

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "target_agent_id": self.target_agent_id,
            "strategy": self.strategy,
            "success": self.success,
            "error": self.error,
            "timestamp": self.timestamp or datetime.now().isoformat(timespec="seconds"),
        }


# ═══════════════════════════════════════════════
# Task Dispatcher
# ═══════════════════════════════════════════════

class TaskDispatcher:
    """
    任务分发器

    职责：
    - 根据路由策略决定任务发给哪个 Agent
    - 执行重试逻辑（指数退避）
    - 持久化任务队列到磁盘
    - 记录分发日志

    Args:
        persistence_path: 持久化文件路径，None 表示不持久化
        max_retries: 默认最大重试次数
        retry_backoff_base: 指数退避基数（秒）
    """

    def __init__(
        self,
        persistence_path: Optional[str] = None,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
    ):
        self.persistence_path = persistence_path
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base

        # 路由状态
        self._strategy: RoutingStrategy = RoutingStrategy.ROUND_ROBIN
        self._round_robin_index: int = 0
        self._affinity_map: Dict[str, str] = {}    # task_type → agent_id
        self._lock = threading.Lock()

        # 分发日志
        self._records: List[DispatchRecord] = []

        # 统计
        self._stats = {
            "total_dispatched": 0,
            "total_succeeded": 0,
            "total_failed": 0,
            "total_retried": 0,
        }

        # 确保持久化目录存在
        if self.persistence_path:
            os.makedirs(os.path.dirname(self.persistence_path) or ".", exist_ok=True)

    # ── 路由策略 ────────────────────────────────

    @property
    def strategy(self) -> RoutingStrategy:
        return self._strategy

    @strategy.setter
    def strategy(self, s: RoutingStrategy):
        with self._lock:
            self._strategy = s

    def route(
        self,
        registry,   # AgentRegistry
        task,       # ScheduledTask
        strategy: Optional[RoutingStrategy] = None,
    ) -> Optional[Any]:   # → Agent or None
        """
        根据路由策略选择目标 Agent

        Args:
            registry: AgentRegistry 实例
            task: 要分发的任务
            strategy: 覆盖默认策略

        Returns:
            目标 Agent，或 None（无可用的 Agent）
        """
        strategy = strategy or self._strategy
        agents = registry.list_all()

        # 筛选 IDLE 状态的 Agent
        available = [a for a in agents if a.status in ("idle", "created", "running")]

        # 如果指定了 target_agent_id 且存在，直接返回
        if task.target_agent_id:
            target = registry.get(task.target_agent_id)
            if target and target in available:
                return target

        if not available:
            # 没有任何可用 Agent，尝试所有
            available = agents
        if not available:
            return None

        with self._lock:
            if strategy == RoutingStrategy.ROUND_ROBIN:
                target = self._route_round_robin(available)
            elif strategy == RoutingStrategy.LEAST_LOADED:
                target = self._route_least_loaded(available)
            elif strategy == RoutingStrategy.AFFINITY:
                target = self._route_affinity(task, available)
            elif strategy == RoutingStrategy.RANDOM:
                target = random.choice(available)
            elif strategy == RoutingStrategy.BEST_CAPABILITY:
                target = self._route_best_capability(task, available)
            else:
                target = available[0]

        return target

    def _route_round_robin(self, agents: List) -> Any:
        """轮询"""
        agent = agents[self._round_robin_index % len(agents)]
        self._round_robin_index += 1
        return agent

    def _route_least_loaded(self, agents: List) -> Any:
        """选任务队列最短的 Agent"""
        return min(agents, key=lambda a: len(a.task_queue))

    def _route_affinity(self, task, agents: List) -> Any:
        """
        亲和性路由：同一 action 类型优先发给上次处理过该类型的 Agent

        如果该 Agent 不可用，回退到 ROUND_ROBIN
        """
        action_type = task.action or task.params.get("action", "")
        preferred_id = self._affinity_map.get(action_type)
        if preferred_id:
            for a in agents:
                if a.agent_id == preferred_id:
                    return a
        # 回退：轮询
        return self._route_round_robin(agents)

    def _route_best_capability(self, task, agents: List) -> Any:
        """
        按能力评分选最优 Agent

        根据 task.action 匹配 capability_scores 中对应技能评分最高的 Agent
        无匹配时回退到 LEAST_LOADED
        """
        action = task.action or task.params.get("action", "")
        scored = []
        for a in agents:
            score = a.capability_scores.get(action, 0)
            scored.append((score, a))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] > 0:
            return scored[0][1]
        return self._route_least_loaded(agents)

    # ── 分发执行 ────────────────────────────────

    def dispatch(
        self,
        target_agent,   # Agent
        task,           # ScheduledTask
        strategy: Optional[RoutingStrategy] = None,
    ) -> DispatchRecord:
        """
        将任务分发给目标 Agent 并执行

        包含重试逻辑（指数退避）

        Args:
            target_agent: 目标 Agent 实例
            task: 要执行的任务
            strategy: 使用的路由策略（用于日志）

        Returns:
            DispatchRecord
        """
        strategy = strategy or self._strategy
        self._stats["total_dispatched"] += 1

        # 更新亲和性映射
        action_type = task.action or task.params.get("action", "")
        if action_type:
            with self._lock:
                self._affinity_map[action_type] = target_agent.agent_id

        record = DispatchRecord(
            task_id=task.task_id,
            target_agent_id=target_agent.agent_id,
            strategy=strategy.value,
            success=False,
            timestamp=datetime.now().isoformat(timespec="seconds"),
        )

        # 执行 + 重试
        last_error = ""
        for attempt in range(task.max_retries + 1):
            try:
                # 通过 Agent.send_task 发送任务
                msg = target_agent.send_task(
                    task=task.action,
                    target=target_agent,
                    **task.params,
                )

                # 立即执行任务
                if target_agent.task_queue:
                    task_msg = target_agent.task_queue.pop(0)
                    result = target_agent.execute_task(task_msg)
                    task.result = result
                else:
                    task.result = {"status": "dispatched", "message_id": msg.message_id}

                record.success = True
                self._stats["total_succeeded"] += 1
                break

            except Exception as e:
                last_error = str(e)
                if attempt < task.max_retries:
                    self._stats["total_retried"] += 1
                    task.retry_count = attempt + 1
                    # 指数退避
                    backoff = self.retry_backoff_base * (2 ** attempt)
                    time.sleep(min(backoff, 30))  # 最多等 30 秒
                else:
                    self._stats["total_failed"] += 1

        if not record.success:
            record.error = last_error
            task.error = last_error

        # 记录日志
        self._records.append(record)
        if len(self._records) > 500:
            self._records.pop(0)

        return record

    def dispatch_batch(
        self,
        registry,        # AgentRegistry
        tasks: List,     # List[ScheduledTask]
        strategy: Optional[RoutingStrategy] = None,
    ) -> List[DispatchRecord]:
        """
        批量分发任务

        每个任务独立路由 → 分发，适合多 Agent 并行执行
        """
        results = []
        for task in tasks:
            target = self.route(registry, task, strategy=strategy)
            if target:
                record = self.dispatch(target, task, strategy=strategy)
            else:
                record = DispatchRecord(
                    task_id=task.task_id,
                    target_agent_id="none",
                    strategy=(strategy or self._strategy).value,
                    success=False,
                    error="No available agent",
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                )
                self._records.append(record)
            results.append(record)
        return results

    # ── 持久化 ──────────────────────────────────

    def persist(self, scheduler) -> int:
        """
        持久化任务队列到 JSON 文件

        Args:
            scheduler: AgentScheduler 实例（获取待处理任务）

        Returns:
            持久化的任务数量
        """
        if not self.persistence_path:
            return 0

        pending = scheduler.get_pending_tasks()
        data = {
            "persisted_at": datetime.now().isoformat(timespec="seconds"),
            "task_count": len(pending),
            "tasks": [t.to_dict() for t in pending],
            "affinity_map": self._affinity_map,
        }

        with open(self.persistence_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return len(pending)

    def restore(self, scheduler) -> int:
        """
        从 JSON 文件恢复任务队列

        Args:
            scheduler: AgentScheduler 实例（将任务重新提交）

        Returns:
            恢复的任务数量
        """
        if not self.persistence_path or not os.path.exists(self.persistence_path):
            return 0

        try:
            with open(self.persistence_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return 0

        # 恢复亲和性映射
        self._affinity_map = data.get("affinity_map", {})

        # 恢复任务
        from .agent_scheduler import ScheduledTask, TaskPriority
        count = 0
        for td in data.get("tasks", []):
            task = ScheduledTask(
                task_id=td.get("task_id", ""),
                action=td.get("action", ""),
                target_agent_id=td.get("target_agent_id", ""),
                priority=TaskPriority.from_string(td.get("priority", "normal")),
                params=td.get("params", {}),
                max_retries=td.get("max_retries", self.max_retries),
                source_script=td.get("source_script", ""),
            )
            scheduler.submit(task)
            count += 1

        return count

    # ── 查询与统计 ──────────────────────────────

    def get_records(self, limit: int = 50) -> List[Dict]:
        """获取最近的分发记录"""
        return [r.to_dict() for r in self._records[-limit:]]

    def get_stats(self) -> Dict[str, Any]:
        """获取分发统计"""
        with self._lock:
            stats = {
                **self._stats,
                "strategy": self._strategy.value,
                "affinity_entries": len(self._affinity_map),
                "records_count": len(self._records),
                "persistence_path": self.persistence_path,
                "persisted": os.path.exists(self.persistence_path) if self.persistence_path else False,
            }
        return stats

    def get_affinity_map(self) -> Dict[str, str]:
        """获取亲和性映射（action_type → agent_id）"""
        with self._lock:
            return dict(self._affinity_map)

    # ── 重置 ──────────────────────────────────

    def reset(self):
        """重置分发器状态"""
        with self._lock:
            self._round_robin_index = 0
            self._affinity_map.clear()
            self._records.clear()
            self._stats = {
                "total_dispatched": 0, "total_succeeded": 0,
                "total_failed": 0, "total_retried": 0,
            }
