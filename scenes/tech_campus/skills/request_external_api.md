---
name: request_external_api
description: "请求外部API资源（LLM推理/EDA云仿真等）。"
version: 1.0
inputs:

tools:
  - request_external_api_tool
---

# Skill: request_external_api

## 何时使用
当需要执行 请求外部API资源（LLM推理/EDA云仿真等）。 时使用此技能。

## 执行步骤
1. 调用 `request_external_api_tool` 工具。
2. 检查返回结果并根据需要反馈。

