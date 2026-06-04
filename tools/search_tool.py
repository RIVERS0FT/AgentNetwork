"""
SearchTool — 对应架构文档 Toolbox 体系中的搜索工具

@tool
class SearchTool:
    name = "search"
    async def execute(self, keyword):
        pass
"""

from agent_network.tool import tool, Tool
from typing import Any
import time
import random


@tool
class SearchTool(Tool):
    name = "search"
    description = "搜索工具 — 模拟信息检索，支持关键词搜索和目标区域情报收集"

    # 模拟情报数据库
    _intel_database = {
        "敌军": {"count": 500, "position": "北纬35° 东经120°", "type": "机械化步兵"},
        "地形": {"terrain": "山地", "elevation": "800-1200m", "cover": "森林覆盖60%"},
        "天气": {"condition": "多云", "temperature": "22°C", "wind": "西北风3级"},
        "目标区域": {"threats": 3, "resources": ["水源", "高地"], "accessibility": "中等"},
        "雷达": {"signal_count": 12, "nearest": "15km", "type": "防空雷达"},
    }

    def execute(self, keyword: str = "", **kwargs) -> Any:
        """执行搜索 — 模拟信息检索"""
        time.sleep(random.uniform(0.1, 0.3))  # 模拟网络延迟

        keyword_lower = keyword.lower() if keyword else ""

        # 模糊匹配
        results = []
        for key, data in self._intel_database.items():
            if keyword_lower in key.lower() or any(
                keyword_lower in str(v).lower() for v in data.values()
            ):
                results.append({"source": key, "data": data, "relevance": random.uniform(0.7, 1.0)})

        if not results:
            results.append({
                "source": "外部情报",
                "data": {"note": f"未找到与 '{keyword}' 直接相关的情报，建议扩大搜索范围"},
                "relevance": 0.3,
            })

        return {
            "keyword": keyword,
            "results_count": len(results),
            "results": sorted(results, key=lambda r: r["relevance"], reverse=True),
            "search_time_ms": random.randint(50, 300),
        }
