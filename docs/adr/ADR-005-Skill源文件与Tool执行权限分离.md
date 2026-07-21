# ADR-005：Skill 源文件与 Tool 执行权限分离

> 状态：已接受

## 决定

`skill_refs` 控制源文件读取；`allowed_tools` 控制原子 Tool 执行。

## 禁止回退

- `srv` 不读 Skill 正文；
- Adapter 不注入完整 `skill_context`；
- Skill 名不注册成 Tool；
- Skill 提及某 Tool 不自动授权。
