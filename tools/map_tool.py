"""
MapTool — 对应架构文档 Toolbox 体系中的地图工具

提供地图分析、路径规划、地形评估等功能。
"""

from agent_network.tool import tool, Tool
from typing import Any
import time
import random
import math


@tool
class MapTool(Tool):
    name = "map"
    description = "地图工具 — 模拟地图分析，提供路径规划、坐标查询和地形评估"

    # 模拟地形数据
    _terrain_grid = {
        "A1": {"type": "平原", "passable": True, "cover": 0.1, "elevation": 100},
        "A2": {"type": "森林", "passable": True, "cover": 0.8, "elevation": 150},
        "A3": {"type": "山地", "passable": False, "cover": 0.6, "elevation": 800},
        "B1": {"type": "平原", "passable": True, "cover": 0.2, "elevation": 120},
        "B2": {"type": "河流", "passable": False, "cover": 0.0, "elevation": 80},
        "B3": {"type": "丘陵", "passable": True, "cover": 0.5, "elevation": 300},
        "C1": {"type": "城市", "passable": True, "cover": 0.9, "elevation": 200},
        "C2": {"type": "平原", "passable": True, "cover": 0.1, "elevation": 110},
        "C3": {"type": "高地", "passable": True, "cover": 0.3, "elevation": 600},
    }

    def execute(self, action: str = "analyze", **kwargs) -> Any:
        """执行地图操作"""
        time.sleep(random.uniform(0.05, 0.2))

        if action == "analyze":
            return self._analyze_terrain(kwargs.get("location", "B2"))
        elif action == "path":
            return self._plan_path(
                kwargs.get("from_location", "A1"),
                kwargs.get("to_location", "C3"),
            )
        elif action == "grid":
            return self._get_grid_info()
        else:
            return {"action": action, "result": "unknown action"}

    def _analyze_terrain(self, location: str) -> dict:
        """分析指定位置的地形"""
        terrain = self._terrain_grid.get(location, {"type": "未知", "passable": True, "cover": 0.5})
        return {
            "location": location,
            "terrain": terrain,
            "recommendation": "适合部署" if terrain.get("passable") else "不适合通行",
            "risk_level": "低" if terrain.get("cover", 0) > 0.5 else "中" if terrain.get("cover", 0) > 0.2 else "高",
        }

    def _plan_path(self, from_loc: str, to_loc: str) -> dict:
        """规划两点间路径"""
        # 简单路径模拟
        waypoints = [from_loc]
        # 生成中间航点
        from_parts = (ord(from_loc[0]), int(from_loc[1]))
        to_parts = (ord(to_loc[0]), int(to_loc[1]))
        col_diff = to_parts[0] - from_parts[0]
        row_diff = to_parts[1] - from_parts[1]

        if abs(col_diff) > 0:
            mid_col = chr(from_parts[0] + col_diff // 2)
            mid_row = from_parts[1] + row_diff // 2
            waypoints.append(f"{mid_col}{mid_row}")

        waypoints.append(to_loc)
        distance = math.sqrt(col_diff**2 + row_diff**2) * 5  # km

        return {
            "from": from_loc,
            "to": to_loc,
            "waypoints": waypoints,
            "estimated_distance_km": round(distance, 1),
            "estimated_time_min": round(distance * 3, 0),  # 假设 3 min/km
            "obstacles": ["B2 河流"] if "B2" in waypoints else [],
        }

    def _get_grid_info(self) -> dict:
        """获取全局网格信息"""
        return {
            "grid_size": "3x3",
            "total_cells": len(self._terrain_grid),
            "cell_types": {t: sum(1 for v in self._terrain_grid.values() if v["type"] == t)
                          for t in set(v["type"] for v in self._terrain_grid.values())},
            "grid": self._terrain_grid,
        }
