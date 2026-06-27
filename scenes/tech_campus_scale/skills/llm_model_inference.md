---
name: llm_model_inference
description: "AI模型推理。DEV_AI→AI_ASSISTANT→EXTERNAL:LLM"
version: 1.0
inputs:

tools:
  - llm_model_inference_tool
---

# Skill: llm_model_inference

## 何时使用
当需要执行 AI模型推理。DEV_AI→AI_ASSISTANT→EXTERNAL:LLM 时使用此技能。

## 执行步骤
1. 调用 `llm_model_inference_tool` 工具。
2. 检查返回结果并根据需要反馈。

