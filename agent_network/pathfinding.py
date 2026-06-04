"""
A* 寻路模块 — 用于地形地图上的 Agent 移动

用法:
    from agent_network.pathfinding import astar
    path = astar(grid, (0, 0), (10, 10))  # → [(0,0), (1,0), ..., (10,10)]
"""

import heapq
from typing import List, Tuple, Optional, Dict


def astar(
    grid: List[List[Dict]],
    start: Tuple[int, int],
    goal: Tuple[int, int],
    allow_diagonal: bool = False,
) -> List[Tuple[int, int]]:
    """
    A* 寻路算法

    Args:
        grid: 二维列表，每个 cell 是 dict，包含 "passable" 键
        start: (col, row) 起点
        goal: (col, row) 终点
        allow_diagonal: 是否允许对角线移动

    Returns:
        List[Tuple[int, int]]: 路径坐标列表（含起止点），无路径返回空列表
    """
    if not grid:
        return []

    rows = len(grid)
    cols = len(grid[0]) if rows > 0 else 0

    if cols == 0:
        return []

    # 边界检查并取整
    start = (int(start[0]), int(start[1]))
    goal = (int(goal[0]), int(goal[1]))

    if (start[0] < 0 or start[0] >= cols or start[1] < 0 or start[1] >= rows):
        return []
    if (goal[0] < 0 or goal[0] >= cols or goal[1] < 0 or goal[1] >= rows):
        return []
    if not grid[start[1]][start[0]].get("passable", True):
        return []
    if not grid[goal[1]][goal[0]].get("passable", True):
        return []
    if start == goal:
        return [start]

    # 方向
    if allow_diagonal:
        directions = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
    else:
        directions = [(0, -1), (1, 0), (0, 1), (-1, 0)]

    # 启发式
    def heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> float:
        dx = abs(a[0] - b[0])
        dy = abs(a[1] - b[1])
        if allow_diagonal:
            return dx + dy + (1.414 - 2) * min(dx, dy)
        return dx + dy

    # A* 数据结构
    open_set: List[Tuple[float, int, Tuple[int, int]]] = []
    heapq.heappush(open_set, (0, 0, start))
    came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
    g_score: Dict[Tuple[int, int], float] = {start: 0}
    counter = 1  # 打破平局

    max_iterations = cols * rows * 2  # 安全上限

    for _ in range(max_iterations):
        if not open_set:
            return []  # 无路径

        _, _, current = heapq.heappop(open_set)

        if current == goal:
            # 重构路径
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        for dcol, drow in directions:
            neighbor = (current[0] + dcol, current[1] + drow)
            nc, nr = neighbor

            if nc < 0 or nc >= cols or nr < 0 or nr >= rows:
                continue
            if not grid[nr][nc].get("passable", True):
                continue

            move_cost = 1.414 if (abs(dcol) + abs(drow)) == 2 else 1.0
            # 地形额外代价 (植被/地形微调)
            terrain_penalty = 1.0
            cell = grid[nr][nc]
            if cell.get("cover", 0) > 0.5:
                terrain_penalty = 1.3  # 密林/城市略慢
            if cell.get("elevation", 100) > 500:
                terrain_penalty = 1.5  # 高地更慢

            tentative_g = g_score[current] + move_cost * terrain_penalty

            if neighbor not in g_score or tentative_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + heuristic(neighbor, goal)
                heapq.heappush(open_set, (f_score, counter, neighbor))
                counter += 1

    return []  # 迭代上限，无路径


def get_nearest_passable(
    grid: List[List[Dict]],
    col: int,
    row: int,
    max_radius: int = 5,
) -> Optional[Tuple[int, int]]:
    """
    在目标不可通行时，找到最近的可通行走。螺旋搜索。

    Args:
        grid: 二维列表
        col, row: 目标坐标
        max_radius: 最大搜索半径

    Returns:
        Optional[Tuple[int, int]]: 最近可通行坐标，或 None
    """
    if not grid:
        return None
    rows = len(grid)
    cols = len(grid[0]) if rows > 0 else 0

    for r in range(1, max_radius + 1):
        for dc in range(-r, r + 1):
            for dr in range(-r, r + 1):
                if abs(dc) != r and abs(dr) != r:
                    continue
                nc, nr = col + dc, row + dr
                if 0 <= nc < cols and 0 <= nr < rows:
                    if grid[nr][nc].get("passable", True):
                        return (nc, nr)
    return None
