"""
容器控制器 — 对应架构文档 第六节：Agent管理层 / Container Controller

扩展 ContainerRuntime，提供完整的容器生命周期管理：
- 健康检查 + 自动重启
- 资源监控（CPU / 内存）
- 自动扩缩容（基于负载指标）
- 调度策略（亲和性/反亲和性）
- 与消息总线同步

使用方式:
    from agent_network.container_controller import ContainerController
    ctrl = ContainerController(mode="process")
    await ctrl.start()

    # 健康检查
    health = await ctrl.check_health("agent-001")

    # 扩缩容
    await ctrl.scale_up("scout", count=2)

依赖: docker SDK (可选), psutil
"""

import os
import json
import time
import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("agent_network.container_controller")

# 条件导入
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import docker
    HAS_DOCKER_SDK = True
except ImportError:
    HAS_DOCKER_SDK = False


# ═══════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════

@dataclass
class ResourceUsage:
    """容器资源使用"""
    agent_id: str
    cpu_percent: float
    memory_mb: float
    timestamp: str = ""

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "timestamp": self.timestamp or datetime.now().isoformat(),
        }


@dataclass
class HealthStatus:
    """Agent 健康状态"""
    agent_id: str
    healthy: bool
    last_check: str = ""
    failure_count: int = 0
    latency_ms: float = 0.0
    error_message: str = ""

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "healthy": self.healthy,
            "last_check": self.last_check or datetime.now().isoformat(),
            "failure_count": self.failure_count,
            "latency_ms": self.latency_ms,
            "error_message": self.error_message,
        }


@dataclass
class ScalingPolicy:
    """自动扩缩容策略"""
    role: str
    metric: str = "cpu"            # cpu / memory / queue_depth / message_rate
    min_instances: int = 1
    max_instances: int = 10
    scale_up_threshold: float = 80.0    # 触发扩容的阈值
    scale_down_threshold: float = 20.0  # 触发缩容的阈值
    cooldown_seconds: int = 30          # 冷却时间
    scale_step: int = 1                 # 每次扩缩步长

    def to_dict(self):
        return {
            "role": self.role,
            "metric": self.metric,
            "min_instances": self.min_instances,
            "max_instances": self.max_instances,
            "scale_up_threshold": self.scale_up_threshold,
            "scale_down_threshold": self.scale_down_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "scale_step": self.scale_step,
        }


@dataclass
class SchedulingRule:
    """调度规则"""
    type: str             # "affinity" / "anti_affinity"
    role: str
    target_role: str
    scope: str = "host"  # "host" / "zone"

    def to_dict(self):
        return self.__dict__


# ═══════════════════════════════════════════════
# 容器控制器
# ═══════════════════════════════════════════════

class ContainerController:
    """
    Agent 容器控制器（单例）

    在 ContainerRuntime 基础上增加：
    - 健康检查循环
    - 资源监控循环
    - 自动扩缩容循环
    - 调度策略
    - Prometheus 指标上报
    """

    _instance: Optional["ContainerController"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._runtime = None  # ContainerRuntime 引用（延迟绑定）
        self._mode = os.environ.get("CONTAINER_MODE", "process")
        self._bus_url = os.environ.get("MESSAGE_BUS_URL", "http://localhost:9000")
        self._running = False
        self._health_interval = int(os.environ.get("HEALTH_CHECK_INTERVAL", 10))
        self._resource_interval = int(os.environ.get("RESOURCE_MONITOR_INTERVAL", 15))
        self._scale_interval = int(os.environ.get("SCALE_CHECK_INTERVAL", 30))

        # 状态
        self._health_statuses: Dict[str, HealthStatus] = {}
        self._resource_usage: Dict[str, ResourceUsage] = {}
        self._scaling_policies: Dict[str, ScalingPolicy] = {}
        self._scheduling_rules: List[SchedulingRule] = []
        self._last_scale_time: Dict[str, datetime] = {}

        # 后台任务
        self._health_task: Optional[asyncio.Task] = None
        self._resource_task: Optional[asyncio.Task] = None
        self._scale_task: Optional[asyncio.Task] = None

    # ── 依赖注入 ────────────────────────────────

    def set_runtime(self, runtime):
        """注入 ContainerRuntime"""
        from .container_runtime import ContainerRuntime
        self._runtime = runtime
        self._mode = runtime.mode

    def _get_runtime(self):
        """懒加载 ContainerRuntime"""
        if self._runtime is None:
            from .container_runtime import get_runtime
            self._runtime = get_runtime(mode=self._mode)
        return self._runtime

    # ── 生命周期 ────────────────────────────────

    async def start(self):
        """启动容器控制器（启动所有后台任务）"""
        if self._running:
            return

        runtime = self._get_runtime()
        self._running = True

        # 启动后台循环
        loop = asyncio.get_event_loop()
        self._health_task = loop.create_task(self._health_check_loop())
        self._resource_task = loop.create_task(self._resource_monitor_loop())
        self._scale_task = loop.create_task(self._auto_scaler_loop())

        logger.info("[ContainerController] Started — health/resource/scale loops active")

    async def stop(self):
        """停止容器控制器"""
        self._running = False
        for task in [self._health_task, self._resource_task, self._scale_task]:
            if task and not task.done():
                task.cancel()
        logger.info("[ContainerController] Stopped")

    # ═══════════════════════════════════════════════
    # 健康检查
    # ═══════════════════════════════════════════════

    async def check_health(self, agent_id: str) -> HealthStatus:
        """
        对单个 Agent 执行健康检查

        通过 HTTP GET /status 端点验证 Agent 可用性
        """
        import aiohttp

        runtime = self._get_runtime()
        agent = runtime.agents.get(agent_id)
        if not agent:
            return HealthStatus(
                agent_id=agent_id,
                healthy=False,
                error_message="Agent not found in runtime",
            )

        start = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{agent.url}/status", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    latency = (time.monotonic() - start) * 1000
                    if resp.status == 200:
                        status = HealthStatus(
                            agent_id=agent_id,
                            healthy=True,
                            latency_ms=round(latency, 2),
                            failure_count=0,
                        )
                    else:
                        status = HealthStatus(
                            agent_id=agent_id,
                            healthy=False,
                            latency_ms=round(latency, 2),
                            error_message=f"HTTP {resp.status}",
                        )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            status = HealthStatus(
                agent_id=agent_id,
                healthy=False,
                latency_ms=round(latency, 2),
                error_message=str(e),
            )

        # 累积失败计数
        prev = self._health_statuses.get(agent_id)
        if prev and not status.healthy:
            status.failure_count = prev.failure_count + 1
        status.last_check = datetime.now().isoformat(timespec="seconds")

        self._health_statuses[agent_id] = status

        # 更新 Prometheus
        try:
            from .metrics import MetricsRegistry
            agent_info = agent.to_dict() if hasattr(agent, 'to_dict') else {}
            MetricsRegistry().set_agent_active(
                agent_id=agent_id,
                role=agent_info.get("role", "unknown"),
                status="running" if status.healthy else "error",
            )
        except ImportError:
            pass

        return status

    async def _health_check_loop(self):
        """后台健康检查循环"""
        while self._running:
            runtime = self._get_runtime()
            for agent_id in list(runtime.agents.keys()):
                status = await self.check_health(agent_id)
                if not status.healthy and status.failure_count >= 3:
                    logger.warning(f"[ContainerController] Agent {agent_id} unhealthy after "
                                   f"{status.failure_count} failures, restarting...")
                    await self.restart_agent(agent_id)
            await asyncio.sleep(self._health_interval)

    async def restart_agent(self, agent_id: str):
        """重启 Agent"""
        runtime = self._get_runtime()
        agent = runtime.agents.get(agent_id)
        if not agent:
            return {"error": f"Agent {agent_id} not found"}

        llm_config = {}  # 可以存储并在重启时恢复
        role = agent.role
        name = agent.name
        port = agent.port

        runtime.stop_agent(agent_id)
        await asyncio.sleep(1)
        runtime.create_agent(agent_id, role, name, port, llm_config)

        # 重置健康状态
        if agent_id in self._health_statuses:
            self._health_statuses[agent_id].failure_count = 0

        logger.info(f"[ContainerController] Agent {agent_id} restarted")
        return {"restarted": agent_id}

    def get_health_summary(self) -> Dict[str, Any]:
        """获取所有 Agent 健康摘要"""
        healthy = sum(1 for s in self._health_statuses.values() if s.healthy)
        unhealthy = sum(1 for s in self._health_statuses.values() if not s.healthy)
        return {
            "total": len(self._health_statuses),
            "healthy": healthy,
            "unhealthy": unhealthy,
            "details": [s.to_dict() for s in self._health_statuses.values()],
        }

    # ═══════════════════════════════════════════════
    # 资源监控
    # ═══════════════════════════════════════════════

    async def get_resource_usage(self, agent_id: str = None) -> List[ResourceUsage]:
        """获取资源使用情况"""
        runtime = self._get_runtime()

        results = []
        agents_to_check = [agent_id] if agent_id else list(runtime.agents.keys())

        for aid in agents_to_check:
            agent = runtime.agents.get(aid)
            if not agent:
                continue

            if self._mode == "docker" and HAS_DOCKER_SDK:
                try:
                    client = runtime.docker_client
                    container = client.containers.get(agent.container_id)
                    stats = container.stats(stream=False)
                    cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                                stats["precpu_stats"]["cpu_usage"]["total_usage"]
                    system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                                   stats["precpu_stats"]["system_cpu_usage"]
                    cpu_percent = (cpu_delta / system_delta) * 100 if system_delta > 0 else 0
                    mem_usage = stats["memory_stats"].get("usage", 0)
                    mem_mb = mem_usage / (1024 * 1024)

                    usage = ResourceUsage(
                        agent_id=aid,
                        cpu_percent=round(cpu_percent, 2),
                        memory_mb=round(mem_mb, 2),
                    )
                except Exception as e:
                    usage = ResourceUsage(agent_id=aid, cpu_percent=0, memory_mb=0)
            elif HAS_PSUTIL:
                try:
                    proc = psutil.Process()  # 子进程模式 — 近似
                    cpu_percent = proc.cpu_percent(interval=0.1)
                    mem_mb = proc.memory_info().rss / (1024 * 1024)
                    usage = ResourceUsage(
                        agent_id=aid,
                        cpu_percent=round(cpu_percent, 2),
                        memory_mb=round(mem_mb, 2),
                    )
                except Exception:
                    usage = ResourceUsage(agent_id=aid, cpu_percent=0, memory_mb=0)
            else:
                usage = ResourceUsage(agent_id=aid, cpu_percent=0, memory_mb=0)

            usage.timestamp = datetime.now().isoformat(timespec="seconds")
            self._resource_usage[aid] = usage
            results.append(usage)

            # 更新 Prometheus
            try:
                from .metrics import MetricsRegistry
                MetricsRegistry().set_container_resource(aid, usage.cpu_percent, usage.memory_mb)
            except ImportError:
                pass

        return results

    async def _resource_monitor_loop(self):
        """后台资源监控循环"""
        while self._running:
            await self.get_resource_usage()
            await asyncio.sleep(self._resource_interval)

    def get_resource_summary(self) -> Dict[str, Any]:
        """获取资源摘要"""
        usages = list(self._resource_usage.values())
        if not usages:
            return {"total_agents": 0, "avg_cpu": 0, "avg_memory_mb": 0}
        return {
            "total_agents": len(usages),
            "avg_cpu": round(sum(u.cpu_percent for u in usages) / len(usages), 2),
            "avg_memory_mb": round(sum(u.memory_mb for u in usages) / len(usages), 2),
            "details": [u.to_dict() for u in usages],
        }

    # ═══════════════════════════════════════════════
    # 自动扩缩容
    # ═══════════════════════════════════════════════

    def set_scaling_policy(self, policy: ScalingPolicy):
        """设置扩缩容策略"""
        self._scaling_policies[policy.role] = policy
        logger.info(f"[ContainerController] Scaling policy set for role={policy.role}: "
                    f"min={policy.min_instances} max={policy.max_instances} metric={policy.metric}")

    def remove_scaling_policy(self, role: str):
        """移除扩缩容策略"""
        self._scaling_policies.pop(role, None)

    def get_scaling_policies(self) -> List[Dict]:
        """获取所有扩缩容策略"""
        return [p.to_dict() for p in self._scaling_policies.values()]

    async def _auto_scaler_loop(self):
        """后台自动扩缩容循环"""
        await asyncio.sleep(self._scale_interval)  # 延迟初始检查

        while self._running:
            for role, policy in self._scaling_policies.items():
                await self._evaluate_scaling(policy)
            await asyncio.sleep(self._scale_interval)

    async def _evaluate_scaling(self, policy: ScalingPolicy):
        """评估单一角色的扩缩容需求"""
        runtime = self._get_runtime()

        # 查找该角色的所有 Agent
        role_agents = [
            a for a in runtime.agents.values()
            if a.role == policy.role
        ]
        current_count = len(role_agents)

        # 计算当前指标值
        metric_value = await self._calculate_metric(policy, role_agents)

        # 冷却检查
        last_time = self._last_scale_time.get(policy.role)
        if last_time:
            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < policy.cooldown_seconds:
                return

        # 扩容判断
        if metric_value >= policy.scale_up_threshold and current_count < policy.max_instances:
            scale_count = min(policy.scale_step, policy.max_instances - current_count)
            logger.info(f"[ContainerController] Scaling UP role={policy.role}: "
                        f"{current_count} → {current_count + scale_count} "
                        f"(metric={policy.metric}={metric_value:.1f})")
            await self.scale_up(policy.role, scale_count)
            self._last_scale_time[policy.role] = datetime.now()

        # 缩容判断
        elif metric_value <= policy.scale_down_threshold and current_count > policy.min_instances:
            scale_count = min(policy.scale_step, current_count - policy.min_instances)
            logger.info(f"[ContainerController] Scaling DOWN role={policy.role}: "
                        f"{current_count} → {current_count - scale_count} "
                        f"(metric={policy.metric}={metric_value:.1f})")
            await self.scale_down(policy.role, scale_count)
            self._last_scale_time[policy.role] = datetime.now()

    async def _calculate_metric(self, policy: ScalingPolicy,
                                 role_agents: List) -> float:
        """计算扩缩容指标值"""
        if not role_agents:
            return 0.0

        if policy.metric == "cpu":
            usages = [self._resource_usage.get(a.agent_id) for a in role_agents]
            valid = [u.cpu_percent for u in usages if u]
            return sum(valid) / len(valid) if valid else 0.0

        elif policy.metric == "memory":
            usages = [self._resource_usage.get(a.agent_id) for a in role_agents]
            valid = [u.memory_mb for u in usages if u]
            return sum(valid) / len(valid) if valid else 0.0

        elif policy.metric == "queue_depth":
            total = 0
            for agent in role_agents:
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"{agent.url}/status", timeout=aiohttp.ClientTimeout(total=3)
                        ) as resp:
                            data = await resp.json()
                            total += data.get("inbox_size", 0)
                except Exception:
                    pass
            return total / len(role_agents) if role_agents else 0.0

        elif policy.metric == "message_rate":
            # 粗略近似：用最近 30 秒的健康状态估算
            return len(role_agents) * 5.0  # 占位实现

        return 0.0

    async def scale_up(self, role: str, count: int = 1):
        """
        扩容 — 为指定角色创建更多 Agent 实例

        Args:
            role: Agent 角色
            count: 新增实例数
        """
        runtime = self._get_runtime()

        existing = [a for a in runtime.agents.values() if a.role == role]
        base_port = max((a.port for a in existing), default=8100)

        created = []
        for i in range(count):
            agent_id = f"{role}-{len(existing) + i + 1:03d}"
            name = f"{role}-{len(existing) + i + 1}"
            port = base_port + i + 1
            ca = runtime.create_agent(agent_id, role, name, port)
            created.append(ca.to_dict())

        logger.info(f"[ContainerController] Scaled up role={role}: created {count} agents")
        return {"scaled": "up", "role": role, "created": count, "agents": created}

    async def scale_down(self, role: str, count: int = 1):
        """
        缩容 — 停止指定角色的多余 Agent 实例

        优先停止较晚创建的 Agent
        """
        runtime = self._get_runtime()
        role_agents = [a for a in runtime.agents.values() if a.role == role]

        # 按端口排序（较晚创建 = 较大端口）
        role_agents.sort(key=lambda a: a.port, reverse=True)

        stopped = []
        for i in range(min(count, len(role_agents))):
            agent = role_agents[i]
            runtime.stop_agent(agent.agent_id)
            self._health_statuses.pop(agent.agent_id, None)
            self._resource_usage.pop(agent.agent_id, None)
            stopped.append(agent.agent_id)

        logger.info(f"[ContainerController] Scaled down role={role}: stopped {stopped}")
        return {"scaled": "down", "role": role, "stopped": count, "agents": stopped}

    # ═══════════════════════════════════════════════
    # 调度策略
    # ═══════════════════════════════════════════════

    def add_scheduling_rule(self, rule: SchedulingRule):
        """添加调度规则"""
        self._scheduling_rules.append(rule)
        logger.info(f"[ContainerController] Added scheduling rule: {rule.type} "
                    f"{rule.role} ↔ {rule.target_role}")

    def remove_scheduling_rule(self, rule_type: str, role: str, target_role: str):
        """移除调度规则"""
        self._scheduling_rules = [
            r for r in self._scheduling_rules
            if not (r.type == rule_type and r.role == role and r.target_role == target_role)
        ]

    def get_scheduling_rules(self) -> List[Dict]:
        """获取所有调度规则"""
        return [r.to_dict() for r in self._scheduling_rules]

    def _apply_scheduling(self, role: str) -> Dict[str, Any]:
        """
        根据调度规则确定最佳放置位置

        对应架构文档：Container Controller 调度功能
        """
        runtime = self._get_runtime()

        # 查找匹配的调度规则
        affinity_targets = []
        anti_affinity_hosts = set()

        for rule in self._scheduling_rules:
            if rule.role != role:
                continue

            target_agents = [a for a in runtime.agents.values()
                           if a.role == rule.target_role]

            if rule.type == "affinity":
                # 亲和性 — 喜欢与 target 放在一起
                affinity_targets.extend(a.agent_id for a in target_agents)
            elif rule.type == "anti_affinity":
                # 反亲和性 — 避免与 target 放在一起
                anti_affinity_hosts.update(a.agent_id for a in target_agents)

        return {
            "preferred_targets": list(set(affinity_targets)),
            "avoid_hosts": list(anti_affinity_hosts),
        }

    # ═══════════════════════════════════════════════
    # 综合状态
    # ═══════════════════════════════════════════════

    def get_full_status(self) -> Dict[str, Any]:
        """获取完整状态（健康 + 资源 + 扩缩容 + 调度）"""
        runtime = self._get_runtime()
        return {
            "mode": self._mode,
            "running": self._running,
            "agents": runtime.get_all_status() if hasattr(runtime, 'get_all_status') else [],
            "health": self.get_health_summary(),
            "resources": self.get_resource_summary(),
            "scaling_policies": self.get_scaling_policies(),
            "scheduling_rules": self.get_scheduling_rules(),
        }

    # ── 重置 ────────────────────────────────────

    @classmethod
    def reset(cls):
        """重置单例（测试用）"""
        if cls._instance:
            cls._instance._running = False
            cls._instance._health_statuses.clear()
            cls._instance._resource_usage.clear()
            cls._instance._scaling_policies.clear()
            cls._instance._scheduling_rules.clear()
            cls._instance._last_scale_time.clear()
