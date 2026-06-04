"""
仿真引擎 — 对应架构文档 第三节：仿真剧本运行层

核心架构：
Simulation Engine
├── Python Script Loader
├── Script Executor
├── Workflow Runtime
├── Agent SDK
├── Skill Runtime
├── Toolbox Runtime
└── Event Bus

职责：
- 加载和执行仿真剧本
- 为剧本提供运行上下文（find_agent, wait 等）
- 管理 Agent 生命周期
- 调度任务执行
"""

from typing import Dict, Any, List, Optional, Type
from .agent import Agent, AgentRegistry
from .event_bus import EventBus, PacketRecorder
from .tool import ToolRegistry
from .skill import SkillRegistry
from .logger import SimulationLogger, LogLevel
from .message import Message
from .agent_hub import AgentHub, get_hub, TaskPriority, ScheduledTask
from .task_dispatcher import RoutingStrategy
import time


class SimulationContext:
    """
    仿真上下文 — 提供给剧本脚本的 API

    对应架构文档 Python 剧本示例：
    class BattlefieldScript:
        def start(self, context):
            scout = context.find_agent(role="scout")
            scout.send_task("探测敌军位置")
            context.wait(10)
            commander = context.find_agent(role="commander")
            commander.send_task("制定攻击方案")
    """

    def __init__(self, engine: "SimulationEngine"):
        self.engine = engine
        self.logger = engine.logger
        self._simulation_data: Dict[str, Any] = {}  # 剧本可用的共享数据

    def find_agent(self, role: str = None, skill: str = None, tag: str = None) -> List[Agent]:
        """按角色/技能/标签查找 Agent"""
        return AgentRegistry.find_agent(role=role, skill=skill, tag=tag)

    def find_best_agent(self, skill: str) -> Optional[Agent]:
        """按能力评分找最优 Agent"""
        return AgentRegistry.find_best_agent(skill)

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """按 ID 获取 Agent"""
        return AgentRegistry.get(agent_id)

    def wait(self, seconds: float):
        """等待指定秒数（模拟时间流逝）"""
        self.logger.system(f"⏳ 等待 {seconds}s...")
        time.sleep(seconds)

    def broadcast(self, message: str, **kwargs):
        """广播消息给所有 Agent"""
        msg = Message(
            source="simulation",
            target="broadcast",
            type="broadcast",
            payload={"action": message, **kwargs},
        )
        self.engine.event_bus.publish(msg)
        self.logger.system(f"📢 广播消息: {message}")

    def set_data(self, key: str, value: Any):
        """存储仿真共享数据"""
        self._simulation_data[key] = value

    def get_data(self, key: str, default: Any = None) -> Any:
        """读取仿真共享数据"""
        return self._simulation_data.get(key, default)

    def log(self, message: str, level: LogLevel = LogLevel.INFO):
        """剧本中输出日志"""
        self.logger.system(f"[剧本] {message}", level)

    def run_skill(self, skill_name: str, **kwargs) -> Any:
        """运行已注册的技能"""
        return SkillRegistry.execute(skill_name, **kwargs)

    def get_agents_stats(self) -> Dict[str, Any]:
        """获取所有 Agent 统计"""
        return AgentRegistry.get_stats()

    def get_packet_stats(self) -> Dict[str, Any]:
        """获取收发包统计"""
        return PacketRecorder.get_stats()


class SimulationEngine:
    """
    仿真引擎 — 运行仿真剧本的核心

    调度流程（对应架构文档）：
    Script → Task Dispatcher → Agent Discovery → Target Agent → Task Queue → Docker Agent
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self.event_bus = EventBus(name)
        self.logger = SimulationLogger(name)
        self.context = SimulationContext(self)
        self._running = False
        self._scripts: List[Any] = []
        self._start_time: Optional[float] = None
        # 接入 AgentHub — 统一调度与分发
        self.hub: AgentHub = get_hub()
        self.hub.start()

    def register_agent(self, agent: Agent) -> Agent:
        """
        注册 Agent 到仿真环境

        自动：
        1. 注入 EventBus
        2. 注册到 AgentRegistry
        3. 订阅消息
        """
        # 注入 EventBus
        agent.event_bus = self.event_bus

        # 注册到注册中心
        AgentRegistry.register(agent)

        # 订阅消息
        self.event_bus.subscribe(agent.agent_id, agent.receive_task)

        # 日志
        self.logger.agent("agent_registered", agent.agent_id,
                         role=agent.role, skills=agent.skills, tags=agent.tags)

        # 启动 Agent
        agent.start()

        return agent

    def register_agents(self, *agents: Agent):
        """批量注册 Agent"""
        for agent in agents:
            self.register_agent(agent)

    def load_script(self, script):
        """
        加载仿真剧本

        剧本需实现 start(context) 方法
        """
        self._scripts.append(script)
        self.logger.system(f"剧本已加载: {script.__class__.__name__}")

    def run(self, script=None) -> Dict[str, Any]:
        """
        运行仿真

        1. 初始化
        2. 执行剧本 start(context)
        3. 处理 Agent 任务队列
        4. 收集结果
        """
        self._running = True
        self._start_time = time.time()

        self.logger.system("=" * 60)
        self.logger.system(f"🚀 仿真引擎 [{self.name}] 启动")
        self.logger.system("=" * 60)

        # L1 系统级日志 — 引擎启动
        self.logger.system("engine_start", LogLevel.INFO,
                          engine_name=self.name,
                          registered_agents=len(AgentRegistry.list_all()))

        try:
            # 执行剧本
            if script:
                self.load_script(script)

            for s in self._scripts:
                self.logger.system(f"▶ 执行剧本: {s.__class__.__name__}")
                s.start(self.context)

            # 处理所有 Agent 的任务队列
            self._process_all_tasks()

        except Exception as e:
            self.logger.system(f"engine_error", LogLevel.ERROR, error=str(e))
            raise
        finally:
            self._running = False

        # 收集统计结果
        elapsed = time.time() - self._start_time
        result = self._collect_results(elapsed)

        self.logger.system("=" * 60)
        self.logger.system(f"✅ 仿真完成 — 耗时 {elapsed:.2f}s")
        self.logger.system("=" * 60)

        return result

    def _process_all_tasks(self):
        """
        处理所有 Agent 的任务队列

        调度流程（通过 AgentHub）：
        Task Queue → AgentHub.scheduler.submit() → AgentHub.dispatcher.dispatch()
                    → Agent Discovery → Target Agent → Execute
        """
        self.logger.system("task_dispatch_start", details={"phase": "dispatch"})

        all_agents = AgentRegistry.list_all()
        total_tasks = 0

        # 第一遍：将所有 Agent 的任务队列提交到 AgentHub 调度器
        for agent in all_agents:
            while agent.task_queue:
                message = agent.task_queue.pop(0)
                action = message.payload.get("action", "unknown")

                # 创建调度任务
                task = ScheduledTask(
                    action=action,
                    target_agent_id=agent.agent_id,
                    priority=TaskPriority.NORMAL,
                    params=message.payload,
                    source_script=self.name,
                )
                self.hub.scheduler.submit(task)

                # L2 Agent 级日志 — 任务接收
                self.logger.agent(
                    "task_received", agent.agent_id,
                    task=action,
                    source=message.source,
                    message_id=message.message_id,
                )
                total_tasks += 1

        # 第二遍：通过 AgentHub 分发并执行所有待处理任务
        records = self.hub.dispatch_all()
        succeeded = sum(1 for r in records if r.success)
        failed = sum(1 for r in records if not r.success)

        # 对每个 Agent 标记任务完成状态
        for agent in all_agents:
            # 执行剩余未分发的任务（直接执行作为回退）
            for record in records:
                if record.target_agent_id == agent.agent_id and record.success:
                    self.logger.agent(
                        "task_completed", agent.agent_id,
                        result=str(record.task_id),
                        status="completed",
                    )

        self.logger.system("task_dispatch_complete",
                          details={
                              "total_tasks_processed": total_tasks,
                              "dispatched": len(records),
                              "succeeded": succeeded,
                              "failed": failed,
                          })

    def _collect_results(self, elapsed: float) -> Dict[str, Any]:
        """收集仿真运行结果"""
        agents_status = []
        for agent in AgentRegistry.list_all():
            agents_status.append(agent.get_status())

        return {
            "simulation_name": self.name,
            "duration_seconds": round(elapsed, 2),
            "agents": agents_status,
            "agent_stats": AgentRegistry.get_stats(),
            "packet_stats": PacketRecorder.get_stats(),
            "log_index_stats": self.logger.get_index_stats(),
            "tool_stats": ToolRegistry.get_stats(),
        }

    def print_summary(self, result: Dict[str, Any]):
        """打印仿真结果摘要"""
        print()
        print("╔══════════════════════════════════════════════════════════╗")
        print("║           📊 仿真运行报告                                ║")
        print("╠══════════════════════════════════════════════════════════╣")
        print(f"║  仿真名称 : {result['simulation_name']:<45s}║")
        print(f"║  运行时间 : {result['duration_seconds']}s{' ' * 46}║")
        print("╠══════════════════════════════════════════════════════════╣")

        stats = result["agent_stats"]
        print(f"║  Agent 总数 : {stats['total_agents']}{' ' * 44}║")
        print(f"║  按角色分布 : {stats['by_role']}{' ' * (42 - len(str(stats['by_role'])))}║")
        print(f"║  按状态分布 : {stats['by_status']}{' ' * (42 - len(str(stats['by_status'])))}║")
        print("╠══════════════════════════════════════════════════════════╣")

        # 每个 Agent 详情
        for agent_info in result["agents"]:
            print(f"║  {agent_info['name']} ({agent_info['role']}){' ' * (48 - len(agent_info['name']) - len(agent_info['role']))}║")
            print(f"║    Status: {agent_info['status']} | Tasks done: {agent_info['completed_tasks']}{' ' * (39 - len(str(agent_info['completed_tasks'])))}║")
            print(f"║    Skills: {', '.join(agent_info['skills']) or 'N/A'}{' ' * (44 - len(', '.join(agent_info['skills']) or 'N/A'))}║")

        print("╠══════════════════════════════════════════════════════════╣")

        # Packet 统计
        pkt = result["packet_stats"]
        print(f"║  消息包统计 : {pkt}{' ' * (42 - len(str(pkt)))}║")

        # 日志索引统计
        log_stats = result["log_index_stats"]
        print(f"║  日志索引   : {log_stats}{' ' * (42 - len(str(log_stats)))}║")

        print("╚══════════════════════════════════════════════════════════╝")
        print()

    # ── 地图移动 tick ────────────────────────────

    def simulation_tick(self, dt: float = 1.0):
        """执行一次仿真 tick，更新所有 Agent 朝向目标的移动"""
        import math

        for agent in AgentRegistry.list_all():
            self._update_agent_position(agent, dt)

    def _update_agent_position(self, agent, dt: float):
        """移动 agent 朝向 _target_x/_target_y"""
        if agent._target_x is None or agent._target_y is None:
            return
        dx = agent._target_x - agent.x
        dy = agent._target_y - agent.y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 0.05:
            agent.x = agent._target_x
            agent.y = agent._target_y
            agent._target_x = None
            agent._target_y = None
            return
        step = agent.speed * dt * 0.3
        agent.x += (dx / dist) * min(step, dist)
        agent.y += (dy / dist) * min(step, dist)
