"""
战场推演场景 — 小场景核心 Demo

对应架构文档 第三节 示例剧本：
class BattlefieldScript:
    def start(self, context):
        scout = context.find_agent(role="scout")
        scout.send_task("探测敌军位置")
        context.wait(10)
        commander = context.find_agent(role="commander")
        commander.send_task("制定攻击方案")

场景描述：
- Scout（侦察兵）: 负责情报收集和地形侦察
- Commander（指挥官）: 接收情报，制定作战方案
- 展示 Agent 协作、消息通信、工具调用、技能组合的完整链路
"""

from agent_network.agent import Agent, AgentRegistry
from agent_network.engine import SimulationContext
from agent_network.logger import LogLevel


class ScoutAgent(Agent):
    """
    侦察兵 Agent — 专精情报收集

    重写 execute_task 以展示 Agent 内部的任务处理逻辑：
    1. 收到侦察任务
    2. 使用 SearchTool 搜索
    3. 使用 MapTool 分析地形
    4. 生成情报报告
    5. 发送给指挥官
    """

    def execute_task(self, message):
        """侦察兵任务执行 — 情报收集流程"""
        from agent_network.logger import SimulationLogger

        self.status = "running"
        action = message.payload.get("action", "unknown")
        logger = SimulationLogger("scout")  # 使用仿真日志

        # L2 Agent 级日志
        print(f"\n  🎯 [{self.name}] 执行侦察任务: {action}")

        result = {
            "agent_id": self.agent_id,
            "agent_role": self.role,
            "task": action,
            "steps": [],
        }

        # Step 1: 搜索目标情报
        if "探测" in action or "搜索" in action or "侦察" in action:
            print(f"     ├─ 🔍 搜索敌军情报...")
            search_result = self.call_tool("search", keyword="敌军")
            result["steps"].append({"step": "搜索敌军", "tool": "search", "result": search_result})

            print(f"     ├─ 🗺️  分析目标地形...")
            map_result = self.call_tool("map", action="grid")
            result["steps"].append({"step": "地形分析", "tool": "map", "result": map_result})

            print(f"     └─ 📋 生成情报报告")

            # 生成情报摘要
            intel_report = {
                "enemy_forces": search_result.get("results", [{}])[0].get("data", {}) if search_result.get("results") else {},
                "terrain_analysis": map_result.get("cell_types", {}),
                "total_cells_scanned": map_result.get("total_cells", 0),
                "recommendation": self._analyze_intel(search_result, map_result),
            }

            result["intel_report"] = intel_report

            # L4 Prompt 级日志（模拟 LLM 交互）
            if hasattr(self, 'event_bus') and self.event_bus:
                from agent_network.logger import SimulationLogger
                sim_logger = getattr(self.event_bus, '_logger', None)

            print(f"     ✅ 情报收集完成: {intel_report['recommendation']}")

            # 发送情报给指挥官
            commanders = AgentRegistry.find_agent(role="commander")
            if commanders and self.event_bus:
                commander = commanders[0]
                self.send_response(commander, intel_report, summary=intel_report["recommendation"])
                print(f"     📨 情报已发送给指挥官 {commander.name}")

        elif "收集" in action:
            print(f"     ├─ 📊 收集目标区域数据...")
            search_result = self.call_tool("search", keyword="目标区域")
            result["steps"].append({"step": "区域数据", "tool": "search", "result": search_result})
            result["output"] = search_result
            print(f"     ✅ 数据收集完成")

        else:
            result["output"] = f"侦察兵执行: {action}"
            print(f"     ✅ 完成: {action}")

        self.completed_tasks.append(result)
        self.status = "idle"
        return result

    def _analyze_intel(self, search_result: dict, map_result: dict) -> str:
        """分析情报，给出建议"""
        results = search_result.get("results", [])
        threats = sum(1 for r in results if "threats" in r.get("data", {}))
        cell_types = map_result.get("cell_types", {})

        passable_cells = sum(v for k, v in cell_types.items()
                           if k in ["平原", "城市", "丘陵"])

        if threats > 2 and passable_cells < 3:
            return "⚠️ 高风险区域 — 建议暂缓行动，增派侦察力量"
        elif threats > 0:
            return "⚡ 中等风险 — 已知威胁存在，建议制定规避路线"
        else:
            return "✅ 低风险 — 区域安全，可执行推进方案"


class CommanderAgent(Agent):
    """
    指挥官 Agent — 制定战略方案

    重写 execute_task 以展示：
    1. 接收侦察兵情报
    2. 分析情报
    3. 使用 StrategyPlanningSkill 生成方案
    4. 下达指令
    """

    def execute_task(self, message):
        """指挥官任务执行 — 方案制定流程"""
        self.status = "running"

        # 处理响应消息（来自侦察兵的情报）
        if message.type == "response":
            intel = message.payload.get("result", {})
            summary = message.payload.get("summary", "")
            print(f"\n  🎖️  [{self.name}] 收到情报: {summary}")

            result = {
                "agent_id": self.agent_id,
                "agent_role": self.role,
                "task": "intel_received",
                "status": "completed",
                "output": f"情报已接收并存储: {summary}",
                "intel_report": intel,
            }
            self.completed_tasks.append(result)
            self.status = "idle"
            return result

        action = message.payload.get("action", "unknown")

        print(f"\n  🎖️  [{self.name}] 执行指挥任务: {action}")

        result = {
            "agent_id": self.agent_id,
            "agent_role": self.role,
            "task": action,
            "steps": [],
        }

        if "制定" in action or "攻击" in action or "方案" in action:
            # Step 1: 收集已收到的情报
            print(f"     ├─ 📥 收集可用情报...")

            intel_data = None
            for completed in self.completed_tasks:
                if "intel_report" in completed:
                    intel_data = completed["intel_report"]
                    break

            # 如果没有现成情报，自行搜索
            if not intel_data:
                print(f"     ├─ 🔍 自行搜索目标情报...")
                search_result = self.call_tool("search", keyword="目标区域")
                map_result = self.call_tool("map", action="grid")
                intel_data = {
                    "enemy_forces": search_result.get("results", [{}])[0].get("data", {}),
                    "terrain_analysis": map_result.get("cell_types", {}),
                }
                result["steps"].append({"step": "自行收集情报", "tool": "search+map", "result": intel_data})

            print(f"     ├─ 🧠 分析情报，制定方案...")

            # Step 2: 使用策略规划技能
            from skills.strategy_planning import StrategyPlanningSkill
            skill = StrategyPlanningSkill()
            plan = skill.run(
                intelligence={"intelligence_summary": {"threat_details": [{"data": intel_data.get("enemy_forces", {})}]},
                            "raw_map": {"grid": {}}},
                objective="区域控制与敌军压制"
            )
            result["steps"].append({"step": "策略规划", "skill": "strategy_planning", "result": plan})

            print(f"     ├─ 📋 选定方案: {plan['recommended_plan']['name']}")
            print(f"     │   成功率: {plan['recommended_plan']['estimated_success_rate']:.0%}")
            print(f"     │   兵力需求: {plan['recommended_plan']['required_forces']}")
            print(f"     │   执行步骤:")

            for s in plan["execution_steps"]:
                print(f"     │   {s['step']}. {s['action']} — {s['detail']} ({s['duration_min']}min)")

            print(f"     └─ 📢 下达作战指令")

            result["plan"] = plan
            result["output"] = f"作战方案已制定: {plan['recommended_plan']['name']}，预计成功率 {plan['recommended_plan']['estimated_success_rate']:.0%}"

        elif "情报" in action or "报告" in action:
            print(f"     ├─ 📖 分析情报报告...")
            intel = message.payload.get("result", {})
            print(f"     ├─ 📊 情报摘要: {message.payload.get('summary', 'N/A')}")
            print(f"     └─ ✅ 情报分析完成")
            result["output"] = f"情报已分析: {message.payload.get('summary', '')}"

        else:
            result["output"] = f"指挥官执行: {action}"
            print(f"     ✅ 完成: {action}")

        self.completed_tasks.append(result)
        self.status = "idle"
        return result


class BattlefieldScene:
    """
    战场推演场景 — 小场景主入口

    剧本流程:
    1. 初始化 Agent: Scout × 1, Commander × 1
    2. Scout 探测敌军位置
    3. 等待一段时间（模拟侦察过程）
    4. Scout 收集目标区域详细数据
    5. Commander 接收情报并制定攻击方案
    6. 收集仿真结果

    对应架构文档:
    class BattlefieldScript:
        def start(self, context):
            scout = context.find_agent(role="scout")
            scout.send_task("探测敌军位置")
            context.wait(10)
            commander = context.find_agent(role="commander")
            commander.send_task("制定攻击方案")
    """

    def __init__(self):
        self.name = "战场推演 v1.0"
        self.description = "侦察兵 + 指挥官协作推演场景"

    def start(self, context: SimulationContext):
        """
        场景入口 — 由仿真引擎调用

        Args:
            context: 仿真上下文，提供 find_agent, wait, log 等 API
        """
        context.log("═" * 50)
        context.log(f"🎬 {self.name} 启动")
        context.log(f"   {self.description}")
        context.log("═" * 50)

        # ═══ 阶段 1: 初始化 Agent ═══
        context.log("阶段 1/4: 初始化作战单元", LogLevel.INFO)

        # 创建侦察兵
        scout = ScoutAgent(
            agent_id="scout-001",
            role="scout",
            name="侦察兵-阿尔法",
            skills=["intelligence_collection", "reconnaissance"],
            tags=["blue_force", "recon"],
            capability_scores={"intelligence_collection": 0.95, "reconnaissance": 0.90, "analysis": 0.75},
        )

        # 创建指挥官
        commander = CommanderAgent(
            agent_id="commander-001",
            role="commander",
            name="指挥官-布拉沃",
            skills=["strategy_planning", "command", "analysis"],
            tags=["blue_force", "command"],
            capability_scores={"strategy_planning": 0.92, "command": 0.88, "analysis": 0.85},
        )

        # 注册到仿真引擎
        context.engine.register_agents(scout, commander)
        context.log(f"✅ 单元就绪: {len(AgentRegistry.list_all())} 个 Agent")

        # 验证注册中心
        stats = context.get_agents_stats()
        context.log(f"   注册中心状态: {stats['by_role']}")

        # ═══ 阶段 2: 侦察兵执行任务 ═══
        context.log("\n阶段 2/4: 侦察兵情报收集", LogLevel.INFO)

        # 按角色发现 Agent（对应架构文档）
        scouts = context.find_agent(role="scout")
        context.log(f"   按角色发现(scout): {len(scouts)} 个")

        # 按技能发现
        recon_agents = context.find_agent(skill="reconnaissance")
        context.log(f"   按技能发现(reconnaissance): {len(recon_agents)} 个")

        # 按标签发现
        blue_agents = context.find_agent(tag="blue_force")
        context.log(f"   按标签发现(blue_force): {len(blue_agents)} 个")

        # 按能力评分找最优
        best_scout = context.find_best_agent(skill="reconnaissance")
        context.log(f"   最优侦察员: {best_scout.name if best_scout else 'N/A'}")

        # 侦察兵执行任务
        if scouts:
            scout = scouts[0]
            scout.send_task("探测敌军位置并分析地形")
            context.wait(1)  # 模拟时间流逝

        # ═══ 阶段 3: 指挥官情报分析 ═══
        context.log("\n阶段 3/4: 指挥官情报分析", LogLevel.INFO)

        commanders = context.find_agent(role="commander")
        if commanders:
            commander = commanders[0]
            # 先发送情报接收任务
            commander.send_task("接收并分析情报报告")
            context.wait(0.5)

        # ═══ 阶段 4: 指挥官制定方案 ═══
        context.log("\n阶段 4/4: 指挥官制定作战方案", LogLevel.INFO)

        if commanders:
            commander = commanders[0]
            commander.send_task("制定攻击方案并下达指令")
            context.wait(0.5)

        # ═══ 总结 ═══
        context.log("\n" + "═" * 50)
        context.log("🎬 场景剧本执行完毕")
        context.log("═" * 50)

        # 打印 Agent 状态
        for agent in AgentRegistry.list_all():
            status = agent.get_status()
            context.log(f"  {status['name']} ({status['role']}): {status['status']} | "
                       f"完成 {status['completed_tasks']} 个任务")


# ═══════════════════════════════════════════════
# 扩展场景: 多 Agent 编队推演
# ═══════════════════════════════════════════════

class MultiAgentFleetScene:
    """
    多 Agent 编队场景 — 扩展 Demo

    演示更大规模的 Agent 编排：
    - 1 个指挥官
    - 2 个侦察兵
    - 1 个后勤支援
    """

    def start(self, context: SimulationContext):
        context.log("🎬 多 Agent 编队推演启动")

        # 创建多个 Agent
        commander = CommanderAgent(
            agent_id="cmd-fleet", role="commander", name="舰队指挥官",
            skills=["strategy_planning", "command", "analysis"],
            tags=["blue_force", "fleet_command"],
            capability_scores={"strategy_planning": 0.95, "command": 0.91},
        )

        scout_a = ScoutAgent(
            agent_id="scout-fleet-a", role="scout", name="侦察兵A-猎鹰",
            skills=["intelligence_collection", "reconnaissance"],
            tags=["blue_force", "recon", "alpha_team"],
            capability_scores={"intelligence_collection": 0.88, "reconnaissance": 0.92},
        )

        scout_b = ScoutAgent(
            agent_id="scout-fleet-b", role="scout", name="侦察兵B-猫头鹰",
            skills=["intelligence_collection", "reconnaissance"],
            tags=["blue_force", "recon", "bravo_team"],
            capability_scores={"intelligence_collection": 0.85, "reconnaissance": 0.78},
        )

        context.engine.register_agents(commander, scout_a, scout_b)

        # 分发任务
        context.log(f"编队规模: {context.get_agents_stats()['total_agents']} 个 Agent")

        # 侦察兵 A 搜索敌军
        scout_a.send_task("搜索敌军雷达信号")

        # 侦察兵 B 分析地形
        scout_b.send_task("收集目标区域地形数据")

        context.wait(1)

        # 指挥官制定方案
        commander.send_task("综合分析多路情报，制定联合作战方案")

        context.log("✅ 编队推演完成")
