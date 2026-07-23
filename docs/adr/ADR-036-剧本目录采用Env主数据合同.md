# ADR-036：剧本目录采用 Env 主数据合同

- 状态：已接受并完成运行时收敛
- 日期：2026-07-23
- 关联：ADR-005、ADR-013、ADR-017、ADR-022、ADR-030、ADR-037

## 背景

旧剧本把角色、实例绑定和任务拆分在多个 JSON 中，剧本级环境数据没有明确的权威载体，Tool 也只能聚合在单个根目录脚本中。新合同需要让剧本作者能清楚区分 Agent 执行任务与剧本级任务，并按资源类型拆分文件。

## 决策

### 1. 标准目录

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

`Agents.json`、`topology.json` 和 `env.py` 是必需入口。缺少任意一个的目录不属于可运行剧本。

### 2. `Agents.json` 持有 Agent 任务

`Agents.json` 根字段固定为 `agents`。每个 Agent 保存：

- 身份信息：`name`、`role`、`background`、`core_goal`；
- 运行后端：`backend`；
- 能力绑定：`skill_refs`、`tool_refs`、`native_capabilities`；
- Agent 执行任务：`tasks`。

Agent 任务的 `agent_id` 由其所在 Agent 自动确定，不在任务对象中重复填写。Agent 任务可以引用该 Agent 已绑定的 Skill 和 Tool，并作为运行时可下发任务进入 `AgentDef.tasks`。

### 3. `env.py` 持有剧本数据和剧本级任务

`env.py` 必须声明一个顶层 `ENV` 字典：

```python
ENV = {
    "metadata": {
        "title": "剧本名称",
        "description": "剧本说明",
    },
    "environment": {
        "global_rules": [],
        "initial_state": {},
        "shared_data": {},
    },
    "scene_tasks": [
        {
            "task_id": "finish-scene",
            "goal": "完成剧本级验收目标",
            "input": {},
            "depends_on": [],
        }
    ],
}
```

`scene_tasks` 描述全局目标、阶段、里程碑和验收任务。它们不直接绑定 Agent、Skill 或 Tool，不进入单个 Agent 的任务队列。Agent 任务和剧本级任务共享任务 ID 命名空间，因此可以建立统一依赖图，但不得重名或形成依赖环。

### 4. `env.py` 采用安全静态读取

平台通过 Python AST 查找 `ENV`，再使用 `ast.literal_eval` 读取数据。除模块文档字符串和单次 `ENV` 赋值外，禁止 import、函数调用、函数或类定义以及其他可执行语句。上传、列表和预览剧本时不得执行剧本代码。

### 5. Skill、Tool 和拓扑

- Skill 入口支持 `skills/<ref>.md` 与 `skills/<ref>/SKILL.md`；
- Tool 从 `tools/` 下所有 `.py` 文件递归发现，Tool ID 在整个剧本内唯一，注册函数必须定义在同一文件；
- `topology.json` 根字段固定为 `topology`，链路继续使用 `endpoint_a`、`endpoint_b`、`channel_id` 和网络 profile 字段；
- `panel.html` 是可选展示资源，不参与剧本发现和严格校验。

### 6. 版本和存量数据

文件合同和校验版本固定为 `agentnetwork-scene.v2`。ADR-037 已完成仓库存量剧本迁移，因此运行时发现、列表、详情、上传、批量解析和仿真加载不再保留 v1 读取入口。

`meta_and_roles.json`、`instances_and_skills.json` 和 `network_topology.json` 只允许由 `scripts/migrate_scenes_v2.py` 作为历史输入读取，不得由 `SceneStorage`、API、仿真编排或 Dashboard 直接读取。

文件注册表中遗留的旧目录记录不得出现在剧本列表中，也不得被按名称读取。

## 后果

- Agent 执行任务与剧本级任务职责清晰；
- `env.py` 成为剧本级数据权威载体，但不成为任意代码执行入口；
- Tool 可以按领域拆分为多个脚本；
- 任务依赖图可以跨 Agent 任务和剧本级任务统一校验；
- `SceneDefinition` 保存 `environment`，`TaskDefinition` 保存 `scope`；
- 剧本详情和批量解析固定返回 `environment`；
- `ValidationResult` 默认版本为 v2；
- 旧 `SceneValidator` 和 v1 fallback 被删除。

## 实现映射

- `agent_network/scene_management/validator_v2.py`：v2 严格校验；
- `agent_network/scene_management/scene_storage.py`：v2 文件发现、安全解析和领域模型构造；
- `agent_network/scene_management/models.py`：任务 scope、v2 校验版本和详情环境字段；
- `agent_network/scene_management/scene_def.py`：环境数据；
- `agent_network/scene_management/scene_manager.py`：批量解析环境投影；
- `agent_network/api/simulations.py`：执行辅助读取委托统一存储；
- `scenes/templates/basic/`：v2 基础模板；
- `tests/test_scene_env_contract.py`、`tests/test_scene_v2_reading.py`：Agent 任务、剧本级任务、安全解析、读取入口和无 v1 fallback 测试。

## 验证

```bash
PYTHONPATH=. pytest -q \
  tests/test_scene_v2_reading.py \
  tests/test_scene_env_contract.py \
  tests/test_scene_manager.py \
  tests/test_scene_building_boundary.py \
  tests/test_file_management_integrations.py \
  tests/test_skill_storage.py
PYTHONPATH=. python -m compileall -q agent_network tests
python scripts/check_design_traceability.py --base main
```
