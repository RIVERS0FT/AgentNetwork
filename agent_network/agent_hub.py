"""
Agent Hub — 对应架构文档 第六节：Agent管理层（Agent Hub）

统一聚合层，将六大组件整合为一站式入口：

AgentHub (singleton)
├── registry: AgentRegistry          — Agent 注册中心
├── scheduler: AgentScheduler        — 任务调度器（优先级/并发/延迟）
├── discovery: → AgentRegistry       — Agent 发现（按角色/技能/标签/能力评分）
├── tool_manager: → ToolRegistry     — 工具管理
├── dispatcher: TaskDispatcher       — 任务分发器（路由/重试/持久化）
└── container: ContainerController   — 容器控制器（健康检查/扩缩容/资源监控）

使用方式:
    from agent_network.agent_hub import AgentHub, get_hub

    hub = get_hub()
    hub.register_agent(agent)
    hub.schedule_task(task)
    hub.dispatch(...)
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .agent import Agent, AgentRegistry
from .tool import ToolRegistry
from .agent_scheduler import AgentScheduler, ScheduledTask, TaskPriority, TaskStatus
from .task_dispatcher import TaskDispatcher, RoutingStrategy, DispatchRecord

if TYPE_CHECKING:
    from .container_controller import ContainerController


# ═══════════════════════════════════════════════
# AgentHub 统一门面
# ═══════════════════════════════════════════════

class AgentHub:
    """
    Agent Hub — 整个 Agent 管理层的统一入口（单例）

    聚合六大组件：
    - Agent Registry（注册 + 发现）
    - Agent Scheduler（调度）
    - Task Dispatcher（分发）
    - Tool Manager（工具）
    - Container Controller（容器）
    - Skill Registry（技能）

    对外暴露简洁的 API，业务代码只需与 AgentHub 交互。
    """

    _instance: Optional["AgentHub"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        max_concurrent: int = 10,
        persistence_path: Optional[str] = None,
        default_strategy: RoutingStrategy = RoutingStrategy.ROUND_ROBIN,
    ):
        if self._initialized:
            return
        self._initialized = True

        # ── 子组件 ──
        self.scheduler = AgentScheduler(max_concurrent=max_concurrent)
        self.dispatcher = TaskDispatcher(
            persistence_path=persistence_path or os.path.join(
                os.path.dirname(__file__), "..", "data", "task_queue.json"
            ),
        )
        self._container: Optional["ContainerController"] = None
        self._default_strategy = default_strategy
        self._started = False

    # ── 容器控制器（懒加载） ────────────────────

    @property
    def container(self) -> "ContainerController":
        if self._container is None:
            from .container_controller import ContainerController
            self._container = ContainerController()
        return self._container

    # ═══════════════════════════════════════════════
    # Agent Registry（代理）
    # ═══════════════════════════════════════════════

    def register_agent(self, agent: Agent) -> Agent:
        """注册 Agent"""
        AgentRegistry.register(agent)
        agent.start()
        return agent

    def unregister_agent(self, agent_id: str):
        """注销 Agent"""
        agent = AgentRegistry.get(agent_id)
        if agent:
            agent.stop()
        AgentRegistry.unregister(agent_id)

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """按 ID 获取 Agent"""
        return AgentRegistry.get(agent_id)

    def list_agents(self) -> List[Agent]:
        """列出所有 Agent"""
        return AgentRegistry.list_all()

    # ═══════════════════════════════════════════════
    # Agent Discovery（代理）
    # ═══════════════════════════════════════════════

    def find_agent(
        self,
        role: str = None,
        skill: str = None,
        tag: str = None,
    ) -> List[Agent]:
        """按角色/技能/标签发现 Agent"""
        return AgentRegistry.find_agent(role=role, skill=skill, tag=tag)

    def find_best_agent(self, skill: str) -> Optional[Agent]:
        """按能力评分找最优 Agent"""
        return AgentRegistry.find_best_agent(skill)

    # ═══════════════════════════════════════════════
    # Agent Scheduler（代理 + 增强）
    # ═══════════════════════════════════════════════

    def schedule_task(
        self,
        action: str,
        target_agent_id: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        delay_seconds: float = 0.0,
        params: Dict[str, Any] = None,
        max_retries: int = 3,
        source_script: str = "",
    ) -> str:
        """
        创建并提交一个任务到调度器

        Args:
            action: 任务动作
            target_agent_id: 目标 Agent ID（为空则由 Dispatcher 路由决定）
            priority: 优先级
            delay_seconds: 延迟执行（秒）
            params: 任务参数
            max_retries: 最大重试次数
            source_script: 来源剧本名称

        Returns:
            task_id
        """
        import time
        task = ScheduledTask(
            action=action,
            target_agent_id=target_agent_id,
            priority=priority,
            scheduled_at=time.time() + delay_seconds,
            params=params or {},
            max_retries=max_retries,
            source_script=source_script,
        )
        return self.scheduler.submit(task)

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        return self.scheduler.cancel(task_id)

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务状态"""
        return self.scheduler.get_task(task_id)

    def list_tasks(self) -> List[Dict]:
        """列出所有任务"""
        pending = self.scheduler.get_pending_tasks()
        running = self.scheduler.get_running_tasks()
        recent = self.scheduler.get_recent_tasks(limit=30)
        all_tasks = pending + running + recent
        # 去重
        seen = set()
        result = []
        for t in all_tasks:
            if t.task_id not in seen:
                result.append(t.to_dict())
                seen.add(t.task_id)
        return result

    # ═══════════════════════════════════════════════
    # Task Dispatcher（代理）
    # ═══════════════════════════════════════════════

    def dispatch_one(
        self,
        task: ScheduledTask,
        strategy: Optional[RoutingStrategy] = None,
    ) -> Optional[DispatchRecord]:
        """
        分发单个任务：路由 → 执行

        Args:
            task: 要分发的任务
            strategy: 路由策略（None 使用默认）

        Returns:
            DispatchRecord，或 None（无可用 Agent）
        """
        strategy = strategy or self._default_strategy
        target = self.dispatcher.route(AgentRegistry, task, strategy=strategy)
        if not target:
            record = DispatchRecord(
                task_id=task.task_id,
                target_agent_id="none",
                strategy=strategy.value,
                success=False,
                error="No available agent",
            )
            self.dispatcher._records.append(record)
            return record
        return self.dispatcher.dispatch(target, task, strategy=strategy)

    def dispatch_all(
        self,
        strategy: Optional[RoutingStrategy] = None,
    ) -> List[DispatchRecord]:
        """
        分发调度器中所有待处理的任务

        Returns:
            分发记录列表
        """
        tasks = self.scheduler.get_pending_tasks()
        if not tasks:
            return []
        return self.dispatcher.dispatch_batch(AgentRegistry, tasks, strategy=strategy)

    def set_routing_strategy(self, strategy: RoutingStrategy):
        """切换路由策略"""
        self.dispatcher.strategy = strategy
        self._default_strategy = strategy

    def get_routing_strategy(self) -> str:
        """获取当前路由策略"""
        return self.dispatcher.strategy.value

    # ═══════════════════════════════════════════════
    # Tool Manager（代理）
    # ═══════════════════════════════════════════════

    def get_tool(self, name: str):
        """按名称获取工具"""
        return ToolRegistry.get(name)

    def list_tools(self) -> list:
        """列出所有工具"""
        return ToolRegistry.list_tools()

    def execute_tool(self, tool_name: str, **kwargs) -> Any:
        """执行工具"""
        return ToolRegistry.execute(tool_name, **kwargs)

    # ═══════════════════════════════════════════════
    # Container Controller（代理）
    # ═══════════════════════════════════════════════

    def container_health(self) -> Dict[str, Any]:
        """所有 Agent 容器健康状态"""
        return self.container.get_health_summary()

    def container_status(self) -> Dict[str, Any]:
        """所有容器状态"""
        return self.container.get_full_status()

    def scale_up(self, role: str, count: int = 1):
        """扩容"""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.container.scale_up(role, count)
        )

    def scale_down(self, role: str, count: int = 1):
        """缩容"""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.container.scale_down(role, count)
        )

    def container_resources(self) -> Dict[str, Any]:
        """资源使用摘要"""
        return self.container.get_resource_summary()

    # ═══════════════════════════════════════════════
    # 持久化
    # ═══════════════════════════════════════════════

    def persist_tasks(self) -> int:
        """持久化当前任务队列"""
        return self.dispatcher.persist(self.scheduler)

    def restore_tasks(self) -> int:
        """从文件恢复任务队列"""
        return self.dispatcher.restore(self.scheduler)

    # ═══════════════════════════════════════════════
    # 综合状态 & 统计
    # ═══════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """聚合所有子系统的统计信息"""
        return {
            "registry": AgentRegistry.get_stats(),
            "scheduler": self.scheduler.get_stats(),
            "dispatcher": self.dispatcher.get_stats(),
            "routing_strategy": self._default_strategy.value,
            "tools": {"registered": len(ToolRegistry.list_tools()), "stats": ToolRegistry.get_stats()},
            "container": self.container.get_full_status() if self._container else {"mode": "not_started"},
        }

    def get_status(self) -> Dict[str, Any]:
        """获取 AgentHub 综合状态（轻量版，适合 API 返回）"""
        return {
            "started": self._started,
            "agents": AgentRegistry.get_stats(),
            "scheduler": {
                "queue_depth": self.scheduler.queue_depth,
                "running": self.scheduler.running_count,
                "priority_breakdown": self.scheduler.get_priority_breakdown(),
            },
            "dispatcher": {
                "strategy": self._default_strategy.value,
                "stats": self.dispatcher.get_stats(),
            },
            "tools_registered": len(ToolRegistry.list_tools()),
            "persistence_path": self.dispatcher.persistence_path,
        }

    # ═══════════════════════════════════════════════
    # 一个完整的 tick（调度 + 分发 + 执行）
    # ═══════════════════════════════════════════════

    def tick(self) -> List[DispatchRecord]:
        """
        执行一个完整周期：
        1. Scheduler 出队到期任务
        2. Dispatcher 路由 + 分发 + 执行

        Returns:
            分发记录列表
        """
        # 取出到期的任务
        ready_tasks = self.scheduler.tick()  # executor=None，只出队不执行

        # 对出队的 RUNNING 状态任务进行分发
        records = []
        for task in self.scheduler.get_running_tasks():
            record = self.dispatch_one(task)
            if record:
                records.append(record)
                # 标记任务完成
                if record.success:
                    task.status = TaskStatus.COMPLETED
                else:
                    task.status = TaskStatus.FAILED
                task.completed_at = __import__("time").time()

        return records

    # ═══════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════

    def start(self):
        """启动 AgentHub"""
        self.scheduler.start()
        self._started = True

    def stop(self):
        """停止 AgentHub"""
        self.scheduler.stop()
        self._started = False

    @classmethod
    def reset(cls):
        """重置 AgentHub 单例（测试用）"""
        if cls._instance:
            cls._instance.scheduler.reset()
            cls._instance.dispatcher.reset()
            if cls._instance._container:
                cls._instance._container.reset()
            cls._instance._started = False
        AgentRegistry.reset()
        ToolRegistry.reset()

    @classmethod
    def get_instance(cls) -> "AgentHub":
        """获取单例"""
        if cls._instance is None:
            cls._instance = AgentHub()
        return cls._instance


# ═══════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════

def get_hub(**kwargs) -> AgentHub:
    """获取 AgentHub 单例（可传入初始化参数）"""
    return AgentHub(**kwargs)
