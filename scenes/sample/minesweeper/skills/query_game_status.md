---
name: query_game_status
description: "查询当前 9x9 地图全局推进状态 — 返回完整棋盘网格供指挥官决策"
version: 1.0
inputs:

tools:
  - query_game_status_tool
---

# Skill: query_game_status

## 何时使用
当需要执行 查询当前 9x9 地图全局推进状态 — 返回完整棋盘网格供指挥官决策 时使用此技能。

## 执行步骤
1. 调用 `query_game_status_tool` 工具。
2. 检查返回结果并根据需要反馈。

