---
name: plan_next_ap
description: "PLANNER调用AI获取下一个AP的候选位置（每次只返回1个最优位置）。"
version: 1.0
inputs:

tools:
  - plan_next_ap_tool
---

# Skill: plan_next_ap

## 何时使用
当需要执行 PLANNER调用AI获取下一个AP的候选位置（每次只返回1个最优位置）。 时使用此技能。

## 执行步骤
1. 调用 `plan_next_ap_tool` 工具。
2. 检查返回结果并根据需要反馈。

