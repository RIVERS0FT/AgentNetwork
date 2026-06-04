#!/usr/bin/env python3
"""
AI Agent 仿真运行平台 — 入口程序
==================================

基于《AI Agent 仿真运行平台系统架构设计说明》实现的小场景 Demo。

运行方式:
    python main.py              # 运行战场推演场景
    python main.py --fleet      # 运行多 Agent 编队场景
    python main.py --verify     # 运行验证测试

架构层次:
    展示层 ──→ 仿真剧本运行层 ──→ Agent管理层 ──→ Docker Agent运行层
                                          ↑
                                  Security & Governance
"""

import sys
import os

# 确保 Windows 控制台支持 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_network.engine import SimulationEngine
from agent_network.agent import AgentRegistry
from agent_network.tool import ToolRegistry
from agent_network.skill import SkillRegistry
from agent_network.event_bus import PacketRecorder
from agent_network.logger import SimulationLogger

# 导入工具（触发 @tool 装饰器注册）
import tools.search_tool
import tools.map_tool

# 导入技能（触发 @skill 装饰器注册）
import skills.intelligence_collection
import skills.strategy_planning

# 导入场景
from scenes.battlefield import BattlefieldScene, MultiAgentFleetScene


def print_banner():
    """打印启动横幅"""
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║   AI Agent 仿真运行平台 v0.1.0                               ║")
    print("║   Agent Network Simulation Platform                          ║")
    print("║   基于《AI Agent 仿真运行平台系统架构设计说明》              ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()


def print_system_status():
    """打印系统初始化状态"""
    print("━ 系统初始化状态 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  已注册工具: {[t['name'] for t in ToolRegistry.list_tools()]}")
    print(f"  已注册技能: {[s['name'] for s in SkillRegistry.list_skills()]}")
    print(f"  已注册 Agent: {AgentRegistry.get_stats()['total_agents']}")
    print("━" * 64)
    print()


def run_battlefield_scene():
    """运行战场推演场景"""
    print("\n" + "🎯 运行场景: 战场推演（侦察兵 + 指挥官）")
    print("─" * 60)

    # 创建仿真引擎
    engine = SimulationEngine(name="战场推演-001")

    # 加载场景
    scene = BattlefieldScene()
    engine.load_script(scene)

    # 运行仿真
    result = engine.run()

    # 打印报告
    engine.print_summary(result)

    return result


def run_fleet_scene():
    """运行多 Agent 编队场景"""
    print("\n" + "🎯 运行场景: 多 Agent 编队推演")
    print("─" * 60)

    engine = SimulationEngine(name="编队推演-001")
    scene = MultiAgentFleetScene()
    engine.load_script(scene)
    result = engine.run()
    engine.print_summary(result)
    return result


def run_verification():
    """运行全面验证测试"""
    print("\n" + "🧪 运行系统验证")
    print("═" * 60)
    passed = 0
    failed = 0
    failures = []

    def check(name: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name} — {detail}")
            failed += 1
            failures.append((name, detail))

    # ── 1. 核心模块检查 ──
    print("\n📦 1. 核心模块检查")

    # 工具注册
    tools_list = ToolRegistry.list_tools()
    check("SearchTool 已注册", any(t["name"] == "search" for t in tools_list),
          f"当前工具: {[t['name'] for t in tools_list]}")
    check("MapTool 已注册", any(t["name"] == "map" for t in tools_list),
          f"当前工具: {[t['name'] for t in tools_list]}")

    # 技能注册
    skills_list = SkillRegistry.list_skills()
    check("IntelligenceCollectionSkill 已注册",
          any(s["name"] == "intelligence_collection" for s in skills_list),
          f"当前技能: {[s['name'] for s in skills_list]}")
    check("StrategyPlanningSkill 已注册",
          any(s["name"] == "strategy_planning" for s in skills_list),
          f"当前技能: {[s['name'] for s in skills_list]}")

    # ── 2. Tool 功能验证 ──
    print("\n🔧 2. Tool 功能验证")
    try:
        result = ToolRegistry.execute("search", keyword="敌军")
        check("SearchTool.execute() 成功", result["results_count"] > 0,
              f"搜索结果数: {result['results_count']}")
        check("SearchTool 返回格式正确", "keyword" in result and "results" in result)
    except Exception as e:
        check("SearchTool 执行", False, str(e))

    try:
        result = ToolRegistry.execute("map", action="analyze", location="B2")
        check("MapTool.execute(analyze) 成功", "terrain" in result,
              f"结果: {result}")
        check("MapTool 地形评估正确", result["location"] == "B2")
    except Exception as e:
        check("MapTool 执行", False, str(e))

    try:
        result = ToolRegistry.execute("map", action="path", from_location="A1", to_location="C3")
        check("MapTool.execute(path) 成功", "waypoints" in result,
              f"航点: {result.get('waypoints', [])}")
        check("MapTool 路径包含起终点", result.get("from") == "A1" and result.get("to") == "C3")
    except Exception as e:
        check("MapTool 路径规划", False, str(e))

    # ── 3. Skill 功能验证 ──
    print("\n🎯 3. Skill 功能验证")
    try:
        result = SkillRegistry.execute("intelligence_collection", target="敌军")
        check("IntelligenceCollectionSkill 执行成功",
              "intelligence_summary" in result,
              f"结果keys: {list(result.keys())}")
        check("情报包含威胁评估",
              "recommendation" in result.get("intelligence_summary", {}))
    except Exception as e:
        check("IntelligenceCollectionSkill", False, str(e))

    try:
        mock_intel = {
            "intelligence_summary": {
                "threat_details": [
                    {"data": {"count": 500, "type": "机械化步兵"}}
                ]
            },
            "raw_map": {"grid": {"A1": {"type": "平原", "passable": True}}}
        }
        result = SkillRegistry.execute("strategy_planning",
                                       intelligence=mock_intel,
                                       objective="突破防线")
        check("StrategyPlanningSkill 执行成功",
              "recommended_plan" in result,
              f"结果keys: {list(result.keys())}")
        check("策略包含候选方案",
              len(result.get("candidate_plans", [])) >= 1,
              f"候选方案数: {len(result.get('candidate_plans', []))}")
        check("包含执行步骤",
              len(result.get("execution_steps", [])) > 0,
              f"步骤数: {len(result.get('execution_steps', []))}")
    except Exception as e:
        check("StrategyPlanningSkill", False, str(e))

    # ── 4. Agent & Registry 验证 ──
    print("\n🤖 4. Agent & Registry 验证")

    # 重置注册中心
    AgentRegistry.reset()

    from agent_network.agent import Agent
    a1 = Agent(agent_id="test-001", role="scout", skills=["recon", "stealth"],
               tags=["team_a"], capability_scores={"recon": 0.9})
    a2 = Agent(agent_id="test-002", role="commander", skills=["command", "analysis"],
               tags=["team_a"], capability_scores={"command": 0.85, "analysis": 0.92})
    a3 = Agent(agent_id="test-003", role="scout", skills=["recon", "drone"],
               tags=["team_b"], capability_scores={"recon": 0.75})

    AgentRegistry.register(a1)
    AgentRegistry.register(a2)
    AgentRegistry.register(a3)

    check("Agent 注册成功", AgentRegistry.get_stats()["total_agents"] == 3,
          f"总数: {AgentRegistry.get_stats()['total_agents']}")

    # 角色发现
    scouts = AgentRegistry.find_agent(role="scout")
    check("按角色发现(scout)", len(scouts) == 2, f"找到: {len(scouts)}")

    commanders = AgentRegistry.find_agent(role="commander")
    check("按角色发现(commander)", len(commanders) == 1, f"找到: {len(commanders)}")

    # 技能发现
    recon_agents = AgentRegistry.find_agent(skill="recon")
    check("按技能发现(recon)", len(recon_agents) == 2, f"找到: {len(recon_agents)}")

    drone_agents = AgentRegistry.find_agent(skill="drone")
    check("按技能发现(drone)", len(drone_agents) == 1, f"找到: {len(drone_agents)}")

    # 标签发现
    team_a = AgentRegistry.find_agent(tag="team_a")
    check("按标签发现(team_a)", len(team_a) == 2, f"找到: {len(team_a)}")

    # 最优 Agent
    best = AgentRegistry.find_best_agent(skill="recon")
    check("找到最优侦察兵", best is not None and best.agent_id == "test-001",
          f"最优: {best.agent_id if best else 'None'}, score={best.capability_scores.get('recon', 0) if best else 'N/A'}")

    best_cmd = AgentRegistry.find_best_agent(skill="command")
    check("找到最优指挥官", best_cmd is not None and best_cmd.agent_id == "test-002",
          f"最优: {best_cmd.agent_id if best_cmd else 'None'}")

    # 无匹配
    no_match = AgentRegistry.find_agent(role="medic")
    check("无匹配返回空列表", len(no_match) == 0, f"找到: {len(no_match)}")

    # ── 5. EventBus & Message 验证 ──
    print("\n📡 5. EventBus & Message 验证")

    from agent_network.event_bus import EventBus, PacketRecorder
    from agent_network.message import Message

    bus = EventBus("test_bus")
    received = []

    bus.subscribe("agent-1", lambda m: received.append(m))

    msg = Message(source="agent-1", target="agent-2", type="task",
                  payload={"action": "test_action"})
    delivered = bus.publish(msg)
    check("Message 创建成功", msg.message_id is not None and len(msg.message_id) > 0)
    check("Message to_dict/to_json", "message_id" in msg.to_dict() and "message_id" in msg.to_json())

    # 因为目标不匹配，agent-2 没订阅，应 deliver 0
    # 但 broadcast subscribers 也会收到... 不，target 是 "agent-2"，不是 broadcast
    check("EventBus 消息路由（无匹配）", delivered == 0,
          f"delivered={delivered}, received={len(received)}")

    # 目标匹配的订阅
    bus.subscribe("agent-2", lambda m: received.append(m))
    msg2 = Message(source="agent-1", target="agent-2", type="task",
                   payload={"action": "hello"})
    delivered2 = bus.publish(msg2)
    check("EventBus 消息路由（匹配目标）", delivered2 >= 1,
          f"delivered={delivered2}, received={len(received)}")

    # Packet Recorder
    records = PacketRecorder.get_records()
    check("PacketRecorder 记录消息", len(records) > 0,
          f"记录数: {len(records)}")

    pkt_stats = PacketRecorder.get_stats()
    check("PacketRecorder 统计", pkt_stats["total_packets"] > 0,
          f"统计: {pkt_stats}")

    # ── 6. Logger 验证 ──
    print("\n📋 6. Logger 验证")

    logger = SimulationLogger("test")
    logger.system("engine_start")
    logger.agent("task_received", "agent-001")
    logger.tool("search_executed", "search")
    logger.prompt("llm_call", "agent-001", prompt_text="分析战场", response_text="建议推进")
    logger.packet("outbound", "agent-001", direction="out")

    check("Logger L1 系统级日志", len(logger.query(level_type="L1")) > 0)
    check("Logger L2 Agent 级日志", len(logger.query(level_type="L2")) > 0)
    check("Logger L3 Tool 级日志", len(logger.query(level_type="L3")) > 0)
    check("Logger L4 Prompt 级日志", len(logger.query(level_type="L4")) > 0)

    # 按 agent 查询
    check("Logger 按 agent 查询", len(logger.query(agent_id="agent-001")) >= 2)

    # 索引统计
    stats = logger.get_index_stats()
    # 检查除了 logs-audit 之外的所有索引都有记录
    non_audit_stats = {k: v for k, v in stats.items() if "audit" not in k}
    check("Logger 索引统计", all(v > 0 for v in non_audit_stats.values()),
          f"统计: {stats}")

    # ── 7. 完整场景集成测试 ──
    print("\n🎬 7. 完整场景集成测试")

    # 重置
    AgentRegistry.reset()
    ToolRegistry.reset()
    SkillRegistry.reset()
    PacketRecorder.reset()

    # 重新注册（因为 reset 清空了）
    import importlib
    importlib.reload(tools.search_tool)
    importlib.reload(tools.map_tool)
    importlib.reload(skills.intelligence_collection)
    importlib.reload(skills.strategy_planning)

    try:
        engine = SimulationEngine(name="verify-test")
        scene = BattlefieldScene()
        result = engine.run(scene)

        check("仿真引擎运行成功", result["duration_seconds"] > 0,
              f"耗时: {result['duration_seconds']}s")
        check("Agent 全部完成", result["agent_stats"]["total_agents"] == 2,
              f"Agent数: {result['agent_stats']['total_agents']}")
        check("消息包已记录", result["packet_stats"]["total_packets"] > 0,
              f"包数: {result['packet_stats']['total_packets']}")
        check("工具已调用", result["tool_stats"]["search"]["calls"] > 0,
              f"search工具调用: {result['tool_stats']['search']['calls']}次")
    except Exception as e:
        import traceback
        check("完整场景集成测试", False, f"{e}\n{traceback.format_exc()}")

    # ── 结果汇总 ──
    print("\n" + "═" * 60)
    total = passed + failed
    print(f"\n  📊 验证结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败 ❌")
        print(f"\n  失败项:")
        for name, detail in failures:
            print(f"    ❌ {name}: {detail}")
    else:
        print(" ✅ 全部通过!")
    print()

    return passed, failed


def main():
    """主入口"""
    print_banner()
    print_system_status()

    args = sys.argv[1:]

    if "--fleet" in args:
        run_fleet_scene()
    elif "--verify" in args:
        passed, failed = run_verification()
        sys.exit(0 if failed == 0 else 1)
    else:
        run_battlefield_scene()


if __name__ == "__main__":
    main()
