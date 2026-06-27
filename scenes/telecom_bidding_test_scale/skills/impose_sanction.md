---
name: impose_sanction
description: "施加处罚。参数: target(str), fine(float), reason(str), round(int)"
version: 1.0
inputs:

tools:
  - impose_sanction_tool
---

# Skill: impose_sanction

## 何时使用
当需要执行 施加处罚。参数: target(str), fine(float), reason(str), round(int) 时使用此技能。

## 执行步骤
1. 调用 `impose_sanction_tool` 工具。
2. 检查返回结果并根据需要反馈。

