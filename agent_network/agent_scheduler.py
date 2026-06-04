"""
Agent Scheduler — 对应架构文档 第六节：Agent管理层 / Agent Scheduler

升级版调度器，支持：
- 任务优先级（CRITICAL > HIGH > NORMAL > LOW > BACKGROUND）
- 并发限制（max_concurrent 控制同时执行数）
- 延迟调度（delay_seconds 延迟执行）
- 任务状态追踪（PENDING → RUNNING → COMPLETED / FAILED / CANCELLED）
- 回调机制（on_complete / on_error）
- 调度统计（吞吐量、平均等待时间、队列深度）

使用方式:
    from agent_network.agent_scheduler import AgentScheduler, TaskPriority, ScheduledTask

    scheduler = AgentScheduler(max_concurrent=5)
    task = ScheduledTask(
        action="analyze_target",
        target_agent_id="agent-001",
        priority=TaskPriority.HIGH,
        params={"target": "enemy_base"},
    )
    scheduler.submit(task)
    scheduler.tick()  # 处理队列
"""

from __future__ import annotations

import time
import uuid
import heapq
import threading
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime


# ═══════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════

class TaskPriority(IntEnum):
    """任务优先级 — 数值越小优先级越高"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4

    @classmethod
    def from_string(cls, s: str) -> "TaskPriority":
        mapping = {
            "critical": cls.CRITICAL, "high": cls.HIGH,
            "normal": cls.NORMAL, "low": cls.LOW,
            "background": cls.BACKGROUND,
        }
        return mapping.get(s.lower(), cls.NORMAL)


class TaskStatus(IntEnum):
    """任务状态"""
    PENDING = 0
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3
    CANCELLED = 4


# ═══════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════

@dataclass(order=True)
class ScheduledTask:
    """
    调度任务

    使用 __lt__ 实现优先级队列排序：
    1. 优先级高的先执行（priority 数值小）
    2. 同优先级按 scheduled_at 时间排序
    3. 同时间按创建顺序（FIFO）
    """
    # 排序字段（用于 heapq）
    _sort_priority: int = field(init=False, repr=False, compare=True)
    _sort_scheduled_at: float = field(init=False, repr=False, compare=True)
    _sort_seq: int = field(init=False, repr=False, compare=True)

    # 任务属性
    task_id: str = ""
    action: str = ""
    target_agent_id: str = ""
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = 0.0
    scheduled_at: float = 0.0      # 计划执行时间（支持延迟）
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    params: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    source_script: str = ""         # 来源剧本
    on_complete: Optional[Callable[[ScheduledTask], None]] = field(default=None, repr=False)
    on_error: Optional[Callable[[ScheduledTask, str], None]] = field(default=None, repr=False)

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"task-{str(uuid.uuid4())[:8]}"
        if self.created_at == 0.0:
            self.created_at = time.time()
        if self.scheduled_at == 0.0:
            self.scheduled_at = self.created_at
        self._sort_priority = int(self.priority)
        self._sort_scheduled_at = self.scheduled_at
        self._sort_seq = _task_sequence()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "target_agent_id": self.target_agent_id,
            "priority": self.priority.name,
            "status": self.status.name,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(timespec="seconds"),
            "scheduled_at": datetime.fromtimestamp(self.scheduled_at).isoformat(timespec="seconds"),
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds") if self.started_at else None,
            "completed_at": datetime.fromtimestamp(self.completed_at).isoformat(timespec="seconds") if self.completed_at else None,
            "params": self.params,
            "result": str(self.result)[:500] if self.result else None,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "source_script": self.source_script,
            "wait_time_ms": round((self.started_at - self.created_at) * 1000, 1) if self.started_at else None,
            "duration_ms": round((self.completed_at - self.started_at) * 1000, 1) if self.completed_at and self.started_at else None,
        }


# 全局任务序号（保证同优先级 FIFO）
_seq_counter = 0
_seq_lock = threading.Lock()

def _task_sequence() -> int:
    global _seq_counter
    with _seq_lock:
        _seq_counter += 1
        return _seq_counter


# ═══════════════════════════════════════════════
# Agent Scheduler
# ═══════════════════════════════════════════════

class AgentScheduler:
    """
    Agent 任务调度器

    功能：
    - 优先级队列（最小堆），高优先级先出队
    - 并发控制（max_concurrent 限制同时运行的任务数）
    - 延迟任务（scheduled_at 未来的任务暂不出队）
    - 统计指标（吞吐量、等待时间等）

    使用方式：
        scheduler = AgentScheduler(max_concurrent=5)
        scheduler.submit(task)
        results = scheduler.tick()  # 每帧调用，处理到期任务
    """

    def __init__(self, max_concurrent: int = 10):
        self.max_concurrent = max_concurrent
        self._heap: List[ScheduledTask] = []      # 最小堆
        self._running: Dict[str, ScheduledTask] = {}  # 正在执行的任务
        self._completed: List[ScheduledTask] = []     # 已完成（最近 200 个）
        self._all_tasks: Dict[str, ScheduledTask] = {}  # 所有任务索引
        self._lock = threading.Lock()

        # 统计
        self._stats = {
            "total_submitted": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_cancelled": 0,
            "total_retried": 0,
            "total_wait_time_ms": 0.0,
            "total_duration_ms": 0.0,
        }

    # ── 提交与取消 ──────────────────────────────

    def submit(self, task: ScheduledTask) -> str:
        """
        提交任务到调度队列

        Args:
            task: ScheduledTask 实例

        Returns:
            task_id
        """
        with self._lock:
            heapq.heappush(self._heap, task)
            self._all_tasks[task.task_id] = task
            self._stats["total_submitted"] += 1
        return task.task_id

    def submit_batch(self, tasks: List[ScheduledTask]) -> List[str]:
        """批量提交"""
        return [self.submit(t) for t in tasks]

    def cancel(self, task_id: str) -> bool:
        """
        取消任务（仅 PENDING 状态可取消）

        Returns:
            是否取消成功
        """
        with self._lock:
            task = self._all_tasks.get(task_id)
            if not task:
                return False
            if task.status != TaskStatus.PENDING:
                return False
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            self._stats["total_cancelled"] += 1
            # 从运行态移除
            self._running.pop(task_id, None)
            return True

    # ── 调度执行（每帧调用） ───────────────────

    def tick(self, executor: Callable[[ScheduledTask], Any] = None) -> List[ScheduledTask]:
        """
        处理一个调度周期

        1. 将到期且可执行的任务从堆中取出 → 变为 RUNNING
        2. 如果有 executor，执行任务
        3. 返回本周期处理的任务列表

        Args:
            executor: 任务执行函数，签名为 fn(task) -> result。
                     如果为 None，任务仅标记为 COMPLETED（模拟模式）

        Returns:
            本周期完成/失败的任务列表
        """
        now = time.time()
        finished: List[ScheduledTask] = []

        with self._lock:
            # 出队：从堆顶取出到期且可执行的任务
            while (self._heap
                   and len(self._running) < self.max_concurrent):
                # 只看堆顶
                peek = self._heap[0]
                if peek.status == TaskStatus.CANCELLED:
                    heapq.heappop(self._heap)
                    continue
                if peek.scheduled_at > now:
                    # 堆顶任务还没到执行时间 → 后面的更晚
                    break
                task = heapq.heappop(self._heap)
                if task.status != TaskStatus.PENDING:
                    continue
                task.status = TaskStatus.RUNNING
                task.started_at = now
                self._running[task.task_id] = task

            # 收集运行中但已完成的任务（由 executor 标记）
            completed_ids = []
            for tid, task in list(self._running.items()):
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    completed_ids.append(tid)

            for tid in completed_ids:
                task = self._running.pop(tid)
                self._archive_task(task)

        # 执行（在锁外，避免阻塞）
        if executor:
            for task in list(self._running.values()):
                if task.status == TaskStatus.RUNNING:
                    try:
                        result = executor(task)
                        task.result = result
                        task.status = TaskStatus.COMPLETED
                        task.completed_at = time.time()
                        if task.on_complete:
                            try:
                                task.on_complete(task)
                            except Exception:
                                pass
                    except Exception as e:
                        self._handle_failure(task, str(e))
                    finished.append(task)

        return finished

    def _handle_failure(self, task: ScheduledTask, error: str):
        """处理任务失败 — 决定重试还是标记失败"""
        task.error = error
        if task.retry_count < task.max_retries:
            task.retry_count += 1
            self._stats["total_retried"] += 1
            # 指数退避重试
            backoff = 2 ** (task.retry_count - 1)  # 1s, 2s, 4s, 8s...
            task.status = TaskStatus.PENDING
            task.scheduled_at = time.time() + backoff
            task.started_at = None
            task.result = None
            with self._lock:
                heapq.heappush(self._heap, task)
        else:
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            self._stats["total_failed"] += 1
            if task.on_error:
                try:
                    task.on_error(task, error)
                except Exception:
                    pass
            self._archive_task(task)

    def _archive_task(self, task: ScheduledTask):
        """归档已完成的任务"""
        if task.status == TaskStatus.COMPLETED:
            self._stats["total_completed"] += 1
        if task.started_at:
            self._stats["total_wait_time_ms"] += (task.started_at - task.created_at) * 1000
        if task.completed_at and task.started_at:
            self._stats["total_duration_ms"] += (task.completed_at - task.started_at) * 1000
        self._completed.append(task)
        # 保留最近 200 个
        if len(self._completed) > 200:
            self._completed.pop(0)

    # ── 查询 ──────────────────────────────────

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """按 ID 获取任务"""
        return self._all_tasks.get(task_id)

    def get_pending_tasks(self) -> List[ScheduledTask]:
        """获取所有待处理任务（按优先级排序）"""
        with self._lock:
            return sorted(
                [t for t in self._heap if t.status == TaskStatus.PENDING],
                key=lambda t: (int(t.priority), t.scheduled_at),
            )

    def get_running_tasks(self) -> List[ScheduledTask]:
        """获取所有运行中的任务"""
        with self._lock:
            return list(self._running.values())

    def get_recent_tasks(self, limit: int = 50) -> List[ScheduledTask]:
        """获取最近完成的任务"""
        return self._completed[-limit:]

    @property
    def queue_depth(self) -> int:
        """当前排队任务数（不含运行中）"""
        with self._lock:
            return len(self._heap)

    @property
    def running_count(self) -> int:
        """当前运行中的任务数"""
        with self._lock:
            return len(self._running)

    # ── 统计 ──────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """获取调度器统计指标"""
        with self._lock:
            total_done = self._stats["total_completed"] + self._stats["total_failed"]
            stats = {
                **self._stats,
                "queue_depth": len(self._heap),
                "running_count": len(self._running),
                "completed_recent": len(self._completed),
                "avg_wait_time_ms": round(
                    self._stats["total_wait_time_ms"] / max(1, total_done), 1
                ),
                "avg_duration_ms": round(
                    self._stats["total_duration_ms"] / max(1, self._stats["total_completed"]), 1
                ),
                "throughput_per_sec": round(
                    total_done / max(1, time.time() - self._start_time if hasattr(self, '_start_time') else 1), 2
                ),
            }
        return stats

    def get_priority_breakdown(self) -> Dict[str, int]:
        """按优先级统计队列中的任务数"""
        with self._lock:
            breakdown = {p.name: 0 for p in TaskPriority}
            for task in self._heap:
                if task.status == TaskStatus.PENDING:
                    breakdown[task.priority.name] += 1
            return breakdown

    # ── 生命周期 ──────────────────────────────

    def start(self):
        """启动调度器（记录启动时间）"""
        self._start_time = time.time()

    def stop(self):
        """停止调度器 — 取消所有待处理任务"""
        with self._lock:
            for task in self._heap:
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.CANCELLED
            self._heap.clear()

    def reset(self):
        """重置调度器（测试用）"""
        with self._lock:
            self._heap.clear()
            self._running.clear()
            self._completed.clear()
            self._all_tasks.clear()
            self._stats = {
                "total_submitted": 0, "total_completed": 0,
                "total_failed": 0, "total_cancelled": 0,
                "total_retried": 0, "total_wait_time_ms": 0.0,
                "total_duration_ms": 0.0,
            }

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "max_concurrent": self.max_concurrent,
            "queue_depth": self.queue_depth,
            "running_count": self.running_count,
            "stats": self.get_stats(),
            "priority_breakdown": self.get_priority_breakdown(),
            "pending_tasks": [t.to_dict() for t in self.get_pending_tasks()[:20]],
            "running_tasks": [t.to_dict() for t in self.get_running_tasks()],
        }
