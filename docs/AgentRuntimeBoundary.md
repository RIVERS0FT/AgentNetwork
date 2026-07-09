# Agent 运行边界：Skill 包 / Tool / MCP

本文档固定当前项目的职责边界，避免控制面或 Adapter 重新承担 Skill 内容解析与执行工作。

## 1. 核心结论

- `srv` 是仿真控制面，只负责场景解析、Agent 编排、容器分配、通信矩阵下发和日志聚合。
- `bus` 是通信面，只负责 Agent 注册、消息转发和通信权限强制校验。
- 剧本管理员负责提前把可运行的 Skill 文件或 Skill 包放入对应剧本的 `skills/` 目录。
- 场景中的 `skill_refs` 是当前 Agent 可以读取的 Skill 范围。
- Agent 后端负责主动读取 Skill 入口及其引用的模板、规则和辅助文件。
- Skill 是 SOP、模板与上下文，不直接注册成可执行 Tool。
- 原子 Tool 来自场景目录内的 `tools.py`，由容器内 MCP server 按 `allowed_tools` 暴露。

## 2. Skill 目录形态

剧本目录支持以下两种 Skill 形态。

单文件 Skill：

```text
scenes/<scene>/skills/<skill_ref>.md
```

目录型 Skill 包：

```text
scenes/<scene>/skills/<skill_ref>/
  ├─ SKILL.md
  ├─ templates/
  ├─ docs/
  └─ other runtime files
```

目录型 Skill 使用 `SKILL.md` 作为入口。Agent 根据入口内容决定是否继续读取包内其他文件。

## 3. 正确链路

```text
srv
  ├─ 读取 meta_and_roles.json / instances_and_skills.json / network_topology.json
  ├─ 为 Agent 分配可复用运行容器
  ├─ 向 bus 注册 agent_id -> container_url
  ├─ 向 bus 下发 comm_matrix
  └─ 向 Agent /run 注入 scene_key、skill_refs、allowed_tools、任务和消息

Agent 容器
  ├─ Adapter 将 scene_key、skill_refs 和任务上下文交给后端
  ├─ Claude Code 通过容器内 Skill MCP 查询和读取允许的 Skill
  ├─ OpenCLAW 根据 skill_refs 和剧本 Skill 路径使用后端本地文件能力读取 Skill
  ├─ Agent 首先读取目录型 Skill 的 SKILL.md 或单文件 Skill
  ├─ Agent 根据入口说明继续读取模板、规则或其他包内文件
  ├─ MCP server 从当前场景 tools.py 注册允许的原子 Tool
  └─ Agent 后端自主决定 Skill 文件读取顺序和使用方式

bus
  ├─ 保存 agent_id 路由表
  ├─ 保存通信权限矩阵
  └─ 在 /relay 处强制校验 from_id -> to 是否允许通信
```

## 4. `srv` 不应该做什么

`srv` 不应该：

- 读取 `SKILL.md` 或单文件 Skill 正文；
- 遍历 Skill 包并解析模板或规则；
- 把 Skill 正文或解析后的 `skill_context` 下发给 Agent；
- import 场景 `tools.py` 来执行 Tool；
- 把 Skill 包装成可执行函数；
- 根据 Skill 内容自动扩大 Agent 的 Tool 权限；
- 绕过 MCP 直接执行 `ToolRegistry.execute()`。

允许保留的 `srv` 行为：

- 从剧本配置读取 `skill_refs` 和 `tool_refs`；
- 把 `scene_key`、`skill_refs` 和 `allowed_tools` 传给 Agent 容器；
- 为前端展示 Agent 配置的 Skill 名称；
- 管理容器分配、释放和复用。

## 5. Adapter 的边界

Adapter 负责连接具体 Agent 后端，但不负责理解 Skill。

Adapter 可以传递：

- 当前任务和消息；
- Agent 身份与角色；
- `scene_key`；
- `skill_refs`；
- `allowed_tools`；
- 通信目录、通信矩阵和运行状态。

Adapter 不再：

- 调用 Skill Markdown Loader 生成上下文；
- 读取 Skill 正文；
- 解析 Skill 元数据；
- 生成或注入 `skill_context`。

## 6. Skill 文件访问边界

Claude Code 使用容器内 MCP 提供的 Skill 文件访问能力：

- 查看当前 Agent 允许的 Skill；
- 查看目录型 Skill 包内的文件；
- 读取 Skill 入口或指定包内文件。

Skill 文件访问必须同时满足：

- Skill 属于当前 `scene_key`；
- Skill 名称存在于当前 Agent 的 `skill_refs`；
- 文件路径位于对应 Skill 文件或 Skill 包内部；
- 不允许绝对路径、父目录跳转或符号链接逃逸。

## 7. Skill 与 Tool 权限分离

- `skill_refs` 控制 Agent 可以读取哪些 Skill。
- `allowed_tools` 控制 Agent 可以调用哪些原子 Tool。
- `SKILL.md` 中提到某个 Tool，不代表该 Tool 自动获得执行权限。
- Skill 文件读取能力不执行 Skill 内容，只向 Agent 返回允许读取的源文件。

## 8. 容器复用

Agent 容器是可重复使用的后端运行槽位，不是固定业务 Agent。

- Claude Code 的 Skill MCP 在每次任务中按本次 `scene_key` 和 `skill_refs` 启动。
- OpenCLAW Session 使用逻辑 Agent ID 与 Trace ID 隔离，不使用固定共享 Session。
- Skill 正文不保存在 Adapter 全局状态中。
- 容器重新分配前继续执行现有 `/reset`，清理 inbox、事件、抓包和网络模拟状态。

## 9. direct_llm 边界

`direct_llm` 没有本地文件或 MCP Tool 能力，因此不能用于配置了 `skill_refs` 的 Agent。需要使用 Skill 的 Agent应选择 Claude Code 或具备本地文件能力的 OpenCLAW 后端。
