"""
IntelligenceCollectionSkill — 情报收集技能

对应架构文档 Skill 示例:
class IntelligenceCollectionSkill:
    async def run(self):
        result = await search_tool.execute("目标区域")
        report = await llm.generate(result)
        return report

组合 SearchTool + MapTool 实现完整的情报收集能力。
"""

from agent_network.skill import Skill, skill
from agent_network.tool import ToolRegistry


@skill
class IntelligenceCollectionSkill(Skill):
    name = "intelligence_collection"
    description = "情报收集技能 — 组合搜索和地图分析，生成综合情报报告"
    required_tools = ["search", "map"]

    def run(self, target: str = "目标区域", **kwargs) -> dict:
        """
        执行情报收集流程：
        1. 搜索目标信息
        2. 分析地形
        3. 生成情报摘要
        """
        # Step 1: 搜索情报
        search_result = self.use_tool("search", keyword=target)

        # Step 2: 地图分析
        map_result = self.use_tool("map", action="grid")

        # Step 3: 综合情报分析
        threats = []
        for item in search_result.get("results", []):
            data = item.get("data", {})
            if "threats" in data or "signal_count" in data:
                threats.append(item)

        # 构建情报报告
        report = {
            "target": target,
            "intelligence_summary": {
                "threats_detected": len(threats),
                "threat_details": threats,
                "terrain_overview": map_result.get("cell_types", {}),
                "recommendation": self._assess_threat_level(threats, map_result),
            },
            "raw_search": search_result,
            "raw_map": map_result,
        }

        return report

    def _assess_threat_level(self, threats: list, map_data: dict) -> str:
        """评估威胁等级"""
        if not threats:
            return "低风险 — 目标区域安全，建议推进"
        elif len(threats) <= 2:
            return "中风险 — 存在潜在威胁，建议谨慎行动"
        else:
            return "高风险 — 多重威胁检测，建议重新评估行动方案"
