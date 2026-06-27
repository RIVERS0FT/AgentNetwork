---
name: llm_document_assist
description: "AI文档辅助。PM/DOC_WRITER→AI_ASSISTANT→EXTERNAL:LLM"
version: 1.0
inputs:

tools:
  - llm_document_assist_tool
---

# Skill: llm_document_assist

## 何时使用
当需要执行 AI文档辅助。PM/DOC_WRITER→AI_ASSISTANT→EXTERNAL:LLM 时使用此技能。

## 执行步骤
1. 调用 `llm_document_assist_tool` 工具。
2. 检查返回结果并根据需要反馈。

