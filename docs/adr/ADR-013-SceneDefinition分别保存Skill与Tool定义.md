# ADR-013：SceneDefinition 分别保存 Skill 与 Tool 定义

> 状态：已接受

## 决定

`SceneDefinition` 分别保存 `SkillDefinition[]` 与 `ToolDefinition[]`。Agent 通过 `skill_refs` 和 `allowed_tools` 引用对应定义，不使用统一能力集合混合表示。

## 禁止回退

- 不得合并 Skill 和 Tool 定义集合；
- 不得用同一枚举字段区分后共享相同结构；
- Skill 文档提及 Tool 不自动创建 Tool 授权；
- 必须校验 Agent 引用对应定义确实存在。
