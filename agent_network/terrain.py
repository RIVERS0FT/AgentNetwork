"""
地形地图生成模块 — AI 生成 + 程序化回退

提供:
- TerrainMap: 可通行/不可通行的网格地图
- 7 种地形类型，带颜色和属性
- LLM 驱动的智能地图生成
- 程序化回退 (河流连续、山脉聚集、城市稀有)
"""

import random
import json
import math
from typing import List, Dict, Optional, Tuple, Any

TERRAIN_TYPES = {
    "plain":    {"passable": True,  "cover": 0.1, "base_elevation": 100, "color": "#7ec850", "label": "平原"},
    "forest":   {"passable": True,  "cover": 0.8, "base_elevation": 180, "color": "#2d6a2d", "label": "森林"},
    "mountain": {"passable": False, "cover": 0.6, "base_elevation": 900, "color": "#8b7355", "label": "山地"},
    "river":    {"passable": False, "cover": 0.0, "base_elevation": 50,  "color": "#3b82f6", "label": "河流"},
    "hill":     {"passable": True,  "cover": 0.5, "base_elevation": 350, "color": "#a0a040", "label": "丘陵"},
    "city":     {"passable": True,  "cover": 0.9, "base_elevation": 120, "color": "#94a3b8", "label": "城市"},
    "highland": {"passable": True,  "cover": 0.3, "base_elevation": 650, "color": "#c4a882", "label": "高地"},
}


class TerrainMap:
    """地形地图 — 2D 网格，每格包含 terrain type 属性"""

    def __init__(self, size: int = 16):
        self.size = size
        self.grid: List[List[Dict[str, Any]]] = []
        self.total_cells = size * size

    # ── 程序化生成 ────────────────────────────────

    def generate_procedural(self) -> "TerrainMap":
        """程序化生成战术地图"""
        self.grid = [[self._default_cell() for _ in range(self.size)] for _ in range(self.size)]

        # 1. 山脉聚集 (2-4 个聚集区)
        for _ in range(random.randint(2, 4)):
            mx = random.randint(0, self.size - 1)
            my = random.randint(0, self.size - 1)
            cluster_size = random.randint(5, 12)
            for _ in range(cluster_size):
                cx = min(self.size - 1, max(0, mx + random.randint(-2, 2)))
                cy = min(self.size - 1, max(0, my + random.randint(-2, 2)))
                self.grid[cy][cx] = self._cell("mountain")

        # 2. 河流 — 连续路径从边缘到边缘
        river_start = (random.choice([0, self.size - 1]), random.randint(0, self.size - 1))
        river_end = (random.choice([0, self.size - 1]), random.randint(0, self.size - 1))
        self._draw_river(river_start, river_end)

        # 3. 湖泊 (河流旁的小型水体)
        for row in range(self.size):
            for col in range(self.size):
                if self.grid[row][col]["type"] == "river":
                    for _ in range(random.randint(0, 2)):
                        lx = min(self.size - 1, max(0, col + random.randint(-1, 1)))
                        ly = min(self.size - 1, max(0, row + random.randint(-1, 1)))
                        if self.grid[ly][lx]["type"] == "plain":
                            self.grid[ly][lx] = self._cell("river")

        # 4. 森林 — 聚集在平原周围
        for row in range(self.size):
            for col in range(self.size):
                if self.grid[row][col]["type"] == "plain" and random.random() < 0.35:
                    self.grid[row][col] = self._cell("forest")

        # 5. 丘陵 — 散落在地图各处
        for row in range(self.size):
            for col in range(self.size):
                if self.grid[row][col]["type"] == "plain" and random.random() < 0.15:
                    self.grid[row][col] = self._cell("hill")

        # 6. 城市 — 稀有，只放在平原上
        city_candidates = [(r, c) for r in range(self.size) for c in range(self.size)
                          if self.grid[r][c]["type"] == "plain"]
        for _ in range(random.randint(1, max(1, self.size // 8))):
            if city_candidates:
                r, c = random.choice(city_candidates)
                self.grid[r][c] = self._cell("city")
                city_candidates.remove((r, c))

        # 7. 高地 — 靠近山脉
        for row in range(self.size):
            for col in range(self.size):
                if self.grid[row][col]["type"] in ("plain", "hill") and self._count_nearby(row, col, "mountain", 3) > 0:
                    if random.random() < 0.5:
                        self.grid[row][col] = self._cell("highland")

        # 8. 高程平滑处理 (生成等高线效果)
        self._smooth_elevation()

        # 9. 更新每个格子的颜色为高程颜色
        self._apply_elevation_colors()

        return self

    # ── LLM 生成 ──────────────────────────────────

    def generate_with_llm(self, config: dict) -> "TerrainMap":
        """调用 LLM 生成战术地图，失败则回退到程序化生成"""
        prompt = self._build_llm_prompt()
        llm_json = self._call_llm(prompt, config)
        if llm_json and self._parse_llm_response(llm_json):
            return self
        # 回退
        return self.generate_procedural()

    # ── 序列化 ────────────────────────────────────

    def to_dict(self) -> dict:
        """转为前端可消费的 JSON 格式"""
        legend = [{"type": k, "color": v["color"], "label": v["label"]} for k, v in TERRAIN_TYPES.items()]
        # 统计
        type_counts = {}
        for row in self.grid:
            for cell in row:
                t = cell["type"]
                type_counts[t] = type_counts.get(t, 0) + 1
        return {
            "size": self.size,
            "total_cells": self.total_cells,
            "grid": self.grid,
            "legend": legend,
            "type_counts": type_counts,
        }

    # ── 查询 ──────────────────────────────────────

    def is_passable(self, col: int, row: int) -> bool:
        """检查坐标是否可通行"""
        if not (0 <= col < self.size and 0 <= row < self.size):
            return False
        return self.grid[row][col].get("passable", True)

    def get_cell(self, col: int, row: int) -> Optional[dict]:
        """获取单个格子"""
        if not (0 <= col < self.size and 0 <= row < self.size):
            return None
        return self.grid[row][col]

    def get_neighbors(self, col: int, row: int) -> List[Tuple[int, int]]:
        """获取可通行的四方向邻居"""
        result = []
        for dc, dr in [(0, -1), (1, 0), (0, 1), (-1, 0)]:
            nc, nr = col + dc, row + dr
            if self.is_passable(nc, nr):
                result.append((nc, nr))
        return result

    def find_passable_cells(self) -> List[Tuple[int, int]]:
        """返回所有可通行格的坐标列表"""
        cells = []
        for row in range(self.size):
            for col in range(self.size):
                if self.is_passable(col, row):
                    cells.append((col, row))
        return cells

    # ── 内部方法 ──────────────────────────────────

    def _default_cell(self) -> dict:
        return {"type": "plain", "passable": True, "cover": 0.1, "elevation": 100, "color": "#7ec850"}

    def _cell(self, terrain_type: str) -> dict:
        t = TERRAIN_TYPES.get(terrain_type, TERRAIN_TYPES["plain"])
        base = t["base_elevation"] + random.randint(-30, 30)
        return {"type": terrain_type, "passable": t["passable"], "cover": t["cover"],
                "elevation": base, "color": t["color"]}

    def _smooth_elevation(self, passes: int = 4):
        """Box-blur elevation with terrain-aware weighting for realistic contours"""
        h, w = self.size, self.size
        # Terrain weight: mountains/highlands resist change, rivers pull neighbors down
        def terrain_weight(t):
            if t == "mountain": return 0.05   # almost locked
            if t == "highland": return 0.15   # barely changes
            if t == "hill": return 0.4
            if t == "river": return 0.85      # pulls neighbors down
            return 0.45  # plain/forest/city
        for _ in range(passes):
            new_grid = [[0.0 for _ in range(w)] for _ in range(h)]
            for r in range(h):
                for c in range(w):
                    cell = self.grid[r][c]
                    tw = terrain_weight(cell["type"])
                    # Weighted average: own value weighted by terrain_weight, neighbors weighted by (1-terrain_weight)
                    total_neighbor = 0.0
                    count = 0
                    for dr in (-1, 0, 1):
                        for dc in (-1, 0, 1):
                            nr, nc = r + dr, c + dc
                            if 0 <= nr < h and 0 <= nc < w:
                                total_neighbor += self.grid[nr][nc]["elevation"]
                                count += 1
                    avg_neighbor = total_neighbor / max(count, 1)
                    new_grid[r][c] = tw * cell["elevation"] + (1 - tw) * avg_neighbor
            for r in range(h):
                for c in range(w):
                    self.grid[r][c]["elevation"] = round(new_grid[r][c])
    def _apply_elevation_colors(self):
        """根据高程设置格子颜色 — 等高线色阶"""
        for row in range(self.size):
            for col in range(self.size):
                elev = self.grid[row][col]["elevation"]
                self.grid[row][col]["color"] = self._elevation_to_color(elev)

    @staticmethod
    def _elevation_to_color(elev: int) -> str:
        """高程 → 颜色映射 (绿→黄绿→棕→灰→白)"""
        if elev <= 80:
            return "#4a90d9"     # 低洼水域 蓝
        elif elev <= 120:
            return "#7ec850"     # 平原 绿
        elif elev <= 200:
            return "#8bc34a"     # 森林 浅绿
        elif elev <= 300:
            return "#c5a843"     # 丘陵 黄绿
        elif elev <= 450:
            return "#b8954a"     # 丘陵 黄棕
        elif elev <= 600:
            return "#a07840"     # 高地 棕
        elif elev <= 750:
            return "#8b7355"     # 山地 暗棕
        elif elev <= 900:
            return "#9e9e9e"     # 高海拔 灰
        else:
            return "#e0e0e0"     # 峰顶 白"""

    def _draw_river(self, start: Tuple[int, int], end: Tuple[int, int]):
        """绘制河流线"""
        cx, cy = start
        ex, ey = end
        steps = max(abs(ex - cx), abs(ey - cy)) * 2
        for i in range(steps + 1):
            t = i / max(steps, 1)
            x = int(cx + (ex - cx) * t + random.randint(-1, 1))
            y = int(cy + (ey - cy) * t + random.randint(-1, 1))
            x = min(self.size - 1, max(0, x))
            y = min(self.size - 1, max(0, y))
            self.grid[y][x] = self._cell("river")
            # 加宽
            if random.random() < 0.3:
                wx = min(self.size - 1, max(0, x + random.choice([-1, 1])))
                wy = min(self.size - 1, max(0, y + random.choice([-1, 1])))
                self.grid[wy][wx] = self._cell("river")

    def _count_nearby(self, row: int, col: int, terrain_type: str, radius: int) -> int:
        """统计指定半径内某类地形的数量"""
        count = 0
        for r in range(max(0, row - radius), min(self.size, row + radius + 1)):
            for c in range(max(0, col - radius), min(self.size, col + radius + 1)):
                if (r != row or c != col) and self.grid[r][c]["type"] == terrain_type:
                    count += 1
        return count

    def _build_llm_prompt(self) -> str:
        type_desc = "\n".join(
            f"- {v['label']}({k}): {'可通行' if v['passable'] else '不可通行'}, 掩体={v['cover']}, 海拔={v['elevation']}m"
            for k, v in TERRAIN_TYPES.items()
        )
        return f"""你是一个战场地形生成器。生成一个 {self.size}x{self.size} 的战术网格地图。

地形类型:
{type_desc}

规则:
1. 河流形成连续路径（相邻单元格），从地图一侧流到另一侧
2. 山脉以 3-8 格聚集出现
3. 城市稀有（1-{max(1, self.size // 8)} 个）
4. 森林围绕平原和丘陵
5. 地图应有战术趣味（咽喉点、高地优势、掩护路线）

返回严格的 JSON 数组，行为主序: [[cell, cell, ...], ...]
每个 cell 格式: {{"type": "plain"}}  — 只填英文类型名
仅输出 JSON 数组，无其他文本。"""

    def _call_llm(self, prompt: str, config: dict) -> Optional[str]:
        """调用 LLM API"""
        try:
            import anthropic

            api_key = config.get("api_key", "")
            if not api_key:
                return None

            client = anthropic.Anthropic(api_key=api_key)
            model = config.get("model", "claude-sonnet-4-6")

            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                system="只输出有效的 JSON。不输出任何其他内容。",
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text if isinstance(resp.content, list) else resp.content
            return text.strip()
        except Exception:
            return None

    def _parse_llm_response(self, text: str) -> bool:
        """解析 LLM 返回的 JSON 网格"""
        try:
            # 去除可能的 markdown 包裹
            text = text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            grid_data = json.loads(text)
            if not isinstance(grid_data, list) or len(grid_data) < 2:
                return False

            self.size = len(grid_data)
            self.grid = []
            for row_data in grid_data:
                row = []
                for cell_data in row_data:
                    t = cell_data.get("type", "plain")
                    row.append(self._cell(t))
                self.grid.append(row)
            self.total_cells = self.size * self.size
            return len(self.grid) == len(self.grid[0]) if self.grid else False
        except (json.JSONDecodeError, IndexError, KeyError):
            return False


# ── 独立函数 ──────────────────────────────────────

def generate_map_with_llm(size: int, config: dict, use_llm: bool = True) -> TerrainMap:
    """生成地形地图 —— 入口函数"""
    tm = TerrainMap(size)
    if use_llm and config.get("api_key"):
        try:
            return tm.generate_with_llm(config)
        except Exception:
            pass
    return tm.generate_procedural()


def is_passable(map_dict: dict, col: int, row: int) -> bool:
    """从 dict 格式检查可通行性 (供 API 层使用)"""
    if not map_dict or not map_dict.get("grid"):
        return True
    grid = map_dict["grid"]
    size = map_dict.get("size", len(grid))
    if not (0 <= col < size and 0 <= row < size):
        return False
    return grid[row][col].get("passable", True)
