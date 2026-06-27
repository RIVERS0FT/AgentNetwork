---
name: llm_code_generate
description: "AI代码生成。developer→AI_ASSISTANT→EXTERNAL:LLM (南北向)"
version: 1.0
inputs:

tools:
  - llm_code_generate_tool
---

# Skill: llm_code_generate

## 何时使用
当需要执行 AI代码生成。developer→AI_ASSISTANT→EXTERNAL:LLM (南北向) 时使用此技能。

## 执行步骤
1. 调用 `llm_code_generate_tool` 工具。
2. 检查返回结果并根据需要反馈。

