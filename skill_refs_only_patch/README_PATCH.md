# Skill compatibility removal patch

This patch makes `skill_refs` the only persistent skill-selection field.

Removed contracts:

- `AgentDef.skills`
- `AgentDef.tags`
- runtime `Agent.skills`
- runtime `Agent.tags`
- `RunRequest.skills`
- `RunRequest.allowed_skills`
- `AgentContext.skills`
- `AgentContext.allowed_skills`
- MCP `_SKILLS_CACHE`
- MCP `_ALLOWED_SKILLS`
- MCP `--allowed-skills`
- adapters that infer skill names from legacy skill objects
- scene parsing fallback from `instance.skills`

The only persistent reference field is `skill_refs: List[str]`. Backend adapters load
Skill.md files inside the Agent container and create a temporary `skill_context` for
model prompting. MCP only loads executable tools.

Apply from the repository root:

```powershell
python .\apply_skill_refs_patch.py . --apply --test
```

Check after application:

```powershell
python .\apply_skill_refs_patch.py . --check
```


## v2 校验修正

校验器只检查技能契约相关文件，不再把 FastAPI `include_router(..., tags=[...])` 误判为 Agent `tags` 字段。若 v1 已经完成文件复制但在检查阶段失败，请覆盖补丁目录后运行：

```powershell
python .\skill_refs_only_patch\apply_skill_refs_patch.py . --check --test
```


## v3 校验变化

`--check` 改为语义校验，不再要求项目文件与补丁模板逐字节相同。
合法的格式化、注释调整、换行符变化和后续业务修改不会再触发
`Patched file differs`。校验仍会检查旧字段是否残留，以及
`AgentDef.skill_refs`、`RunRequest.skill_refs` 和 MCP 边界是否正确。
