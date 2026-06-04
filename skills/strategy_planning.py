"""
StrategyPlanningSkill — 策略规划技能

对应架构文档 Skill 体系中的 StrategyPlanningSkill。
基于情报输入，制定作战/行动方案。
"""

from agent_network.skill import Skill, skill
from agent_network.tool import ToolRegistry
import random


@skill
class StrategyPlanningSkill(Skill):
    name = "strategy_planning"
    description = "策略规划技能 — 基于情报分析，生成行动方案"
    required_tools = ["search", "map"]

    def run(self, intelligence: dict = None, objective: str = "区域控制", **kwargs) -> dict:
        """
        基于情报生成策略计划

        Args:
            intelligence: 情报报告（来自 IntelligenceCollectionSkill）
            objective: 行动目标
        """
        # 分析地形可行性
        map_data = intelligence.get("raw_map", {}) if intelligence else {}
        threats = intelligence.get("intelligence_summary", {}).get("threat_details", []) if intelligence else []

        # 制定方案
        plans = []

        # 方案1: 正面推进
        if self._is_terrain_passable(map_data, "平原"):
            plans.append({
                "name": "正面推进",
                "type": "offensive",
                "route": "A1 → A2 → B2",
                "risk": "中",
                "estimated_success_rate": 0.65,
                "required_forces": "步兵连×2 + 装甲排×1",
                "description": "利用平原地形快速推进，以装甲力量为先导",
            })

        # 方案2: 侧翼包抄
        plans.append({
            "name": "侧翼包抄",
            "type": "flanking",
            "route": "C1 → C2 → B2",
            "risk": "低",
            "estimated_success_rate": 0.82,
            "required_forces": "步兵连×1 + 侦察排×1",
            "description": "利用城市掩护进行侧翼机动，出其不意",
        })

        # 方案3: 多点突破
        if len(threats) <= 2:
            plans.append({
                "name": "多点突破",
                "type": "multi_point",
                "route": "A1→B2, C1→B2, A3→B2",
                "risk": "高",
                "estimated_success_rate": 0.45,
                "required_forces": "步兵连×3 + 装甲连×1 + 空中支援",
                "description": "三路同时进攻，分散敌军防御力量",
            })

        # 选择最优方案
        best_plan = max(plans, key=lambda p: p["estimated_success_rate"])

        # 生成行动计划
        action_plan = {
            "objective": objective,
            "strategy": best_plan["name"],
            "candidate_plans": plans,
            "recommended_plan": best_plan,
            "execution_steps": self._generate_steps(best_plan, objective),
            "contingency": "如果主力受阻，立即转入方案2（侧翼包抄）",
        }

        return action_plan

    def _is_terrain_passable(self, map_data: dict, terrain_type: str) -> bool:
        """检查地形类型是否可通过"""
        grid = map_data.get("grid", {})
        for cell_data in grid.values():
            if cell_data.get("type") == terrain_type and cell_data.get("passable"):
                return True
        return False

    def _generate_steps(self, plan: dict, objective: str) -> list:
        """生成具体执行步骤"""
        return [
            {"step": 1, "action": "情报确认", "detail": f"利用 SearchTool 再次确认目标区域情报", "duration_min": 5},
            {"step": 2, "action": "兵力部署", "detail": f"部署 {plan['required_forces']}", "duration_min": 15},
            {"step": 3, "action": "路线检查", "detail": f"利用 MapTool 确认路径 {plan['route']}", "duration_min": 3},
            {"step": 4, "action": "执行计划", "detail": f"按 {plan['name']} 方案执行，目标: {objective}", "duration_min": 30},
            {"step": 5, "action": "效果评估", "detail": "收集反馈，评估战果", "duration_min": 10},
        ]
