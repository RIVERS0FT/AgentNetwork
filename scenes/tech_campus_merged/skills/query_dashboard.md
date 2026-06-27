---
name: query_dashboard
description: "获取面板全量数据。无参数，返回 agent/拓扑/流量/任务/事件的聚合快照。"
version: 1.0
inputs:

tools:
  - query_dashboard_tool
---

# Skill: query_dashboard

## 何时使用
当需要执行 获取面板全量数据。无参数，返回 agent/拓扑/流量/任务/事件的聚合快照。 时使用此技能。

## 执行步骤
1. 调用 `query_dashboard_tool` 工具。
2. 检查返回结果并根据需要反馈。

