# AgentNetwork 剧本目录合同

每个可运行剧本使用独立目录，标准结构如下：

```text
scenes/<scene_key>/
├── Agents.json
├── topology.json
├── env.py
├── skills/
│   ├── <skill_ref>.md
│   └── <skill_ref>/SKILL.md
├── tools/
│   └── *.py
└── panel.html            # 可选
```

基础示例位于 [`templates/basic`](templates/basic)。

## 文件职责

| 文件 | 职责 |
|---|---|
| `Agents.json` | Agent 身份、后端、能力绑定和每个 Agent 的执行任务 |
| `topology.json` | Agent 间通信权限及网络 profile |
| `env.py` | 剧本元数据、全局规则、初始状态、共享数据和剧本级任务 |
| `skills/` | Agent 可读取的 Skill 说明与配套资源 |
| `tools/` | Tool Python 脚本；每个脚本可注册一个或多个 Tool |
| `panel.html` | 可选的剧本可视化页面 |

## `Agents.json`

```json
{
  "agents": {
    "planner": {
      "name": "Planner",
      "role": "规划负责人",
      "background": "负责制定执行方案",
      "core_goal": "输出可执行计划",
      "backend": "openclaw",
      "skill_refs": ["planning"],
      "tool_refs": ["write_plan"],
      "tasks": [
        {
          "task_id": "draft-plan",
          "goal": "形成初版计划",
          "input": {},
          "skill_refs": ["planning"],
          "tool_refs": ["write_plan"],
          "depends_on": []
        }
      ]
    }
  }
}
```

任务所属 Agent 由外层 Agent ID 自动确定，不写 `agent_id`。任务只能引用该 Agent 已绑定的 Skill 和 Tool。

## `env.py`

`env.py` 是声明式数据文件，不是运行时脚本。平台只允许模块文档字符串和一次 `ENV` 字典赋值。

```python
ENV = {
    "metadata": {
        "title": "基础剧本",
        "description": "展示新剧本合同",
    },
    "environment": {
        "global_rules": ["所有输出必须可审计"],
        "initial_state": {"status": "ready"},
        "shared_data": {},
    },
    "scene_tasks": [
        {
            "task_id": "finish-scene",
            "goal": "完成剧本级验收",
            "input": {},
            "depends_on": ["draft-plan"],
        }
    ],
}
```

`scene_tasks` 是全局目标、阶段或验收任务，不直接绑定 Agent、Skill 或 Tool。它们与 Agent 任务共享任务 ID 命名空间和依赖图。

## `topology.json`

```json
{
  "topology": [
    {
      "endpoint_a": "planner",
      "endpoint_b": "reviewer",
      "channel_id": "planner-reviewer",
      "delay_ms": 10,
      "jitter_ms": 1,
      "loss_pct": 0,
      "rate_mbit": 100
    }
  ]
}
```

链路天然双向。`endpoint_a` 和 `endpoint_b` 必须引用 `Agents.json` 中存在的 Agent。

## Skill 与 Tool

Skill 入口支持：

- `skills/<skill_ref>.md`
- `skills/<skill_ref>/SKILL.md`

Tool 必须放在 `tools/` 下的 `.py` 文件中，并通过注册调用声明 Tool ID：

```python
def write_plan(**kwargs):
    return kwargs

ToolRegistry.register("write_plan", write_plan)
```

同一剧本内 Tool ID 必须唯一，注册函数必须定义在同一文件。

## 安全约束

`env.py` 不允许 import、函数调用、函数或类定义。平台使用 AST 和 `ast.literal_eval` 读取 `ENV`，上传和预览剧本时不会执行其中代码。
