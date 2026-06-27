---
name: handle_push
description: "处理推送，触发CI/CD流水线。"
version: 1.0
inputs:

tools:
  - handle_push_tool
---

# Skill: handle_push

## 何时使用
当需要执行 处理推送，触发CI/CD流水线。 时使用此技能。

## 执行步骤
1. 调用 `handle_push_tool` 工具。
2. 检查返回结果并根据需要反馈。

