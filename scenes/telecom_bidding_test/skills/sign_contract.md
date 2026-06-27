---
name: sign_contract
description: "签署合约。会先自动创建 NEGOTIATING 连线（如不存在）。"
version: 1.0
inputs:

tools:
  - sign_contract_tool
---

# Skill: sign_contract

## 何时使用
当需要执行 签署合约。会先自动创建 NEGOTIATING 连线（如不存在）。 时使用此技能。

## 执行步骤
1. 调用 `sign_contract_tool` 工具。
2. 检查返回结果并根据需要反馈。

