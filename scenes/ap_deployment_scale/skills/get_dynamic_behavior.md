---
name: get_dynamic_behavior
description: "平台每轮启动时调用此函数，基于当前仿真状态返回各分类的动态行为权重。"
version: 1.0
inputs:

tools:
  - get_dynamic_behavior_tool
---

# Skill: get_dynamic_behavior

## 何时使用
当需要执行 平台每轮启动时调用此函数，基于当前仿真状态返回各分类的动态行为权重。 时使用此技能。

## 执行步骤
1. 调用 `get_dynamic_behavior_tool` 工具。
2. 检查返回结果并根据需要反馈。

