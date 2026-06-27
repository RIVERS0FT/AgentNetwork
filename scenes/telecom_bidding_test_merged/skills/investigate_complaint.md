---
name: investigate_complaint
description: "调查投诉。参数: complaint_id(int), reporter(str), round(int)"
version: 1.0
inputs:

tools:
  - investigate_complaint_tool
---

# Skill: investigate_complaint

## 何时使用
当需要执行 调查投诉。参数: complaint_id(int), reporter(str), round(int) 时使用此技能。

## 执行步骤
1. 调用 `investigate_complaint_tool` 工具。
2. 检查返回结果并根据需要反馈。

