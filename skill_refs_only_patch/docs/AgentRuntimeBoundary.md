# Agent 运行边界：Skill.md / Tool / MCP 注册

本文档用于固定当前项目的职责边界，避免把 Skill 和 Tool 注册逻辑继续放回 `srv` 控制面。

## 1. 核心结论

- `srv` 是仿真控制面，只负责场景解析、Agent 编排、容器分配、通信矩阵下发、日志聚合。
- `bus` 是通信面，只负责 Agent 注册、消息转发和通信权限强制校验。
- 容器内 Agent 才是 Skill / Tool 的注册与执行边界。
- `Skill.md` 只作为 SOP / 上下文说明，不直接作为可执行 Tool。
- 原子 Tool 来自场景目录内的 `tools.py`，由容器内 MCP server 加载并暴露给后端 Agent。

## 2. 正确链路

```text
srv
  ├─ 读取 meta_and_roles.json / instances_and_skills.json / network_topology.json
  ├─ 为 Agent 分配容器
  ├─ 向 bus 注册 agent_id -> container_url
  ├─ 向 bus 下发 comm_matrix
  └─ 向 Agent /run 注入 scene_key、skill_refs、allowed_tools、任务和消息

Agent 容器
  ├─ BackendAdapter 启动容器本地 MCP server
  ├─ BackendAdapter / MCP server 在容器内读取 /app/scenes/<scene>/skills/*.md
  ├─ MCP server 读取 /app/scenes/<scene>/tools.py
  ├─ Skill.md 作为 SOP/context 注入，不注册为可执行函数
  ├─ ToolRegistry 中的原子 Tool 注册为 MCP tools
  └─ Claude Code / OpenCLAW 通过 MCP 调用 Tool

bus
  ├─ 保存 agent_id 路由表
  ├─ 保存通信权限矩阵
  └─ 在 /relay 处强制校验 from_id -> to 是否允许通信
```

## 3. `srv` 不应该做什么

`srv` 不应该：

- import 场景 `tools.py` 来执行 Tool；
- 把 `Skill.md` 包装成可执行函数；
- 根据 Skill 的 `tools` 字段替 Agent 串行调用工具；
- 绕过 MCP 直接执行 `ToolRegistry.execute()`；
- 把 Skill/Tool 注册能力放在控制面统一完成。

允许保留的 `srv` 行为：

- 读取 `instances_and_skills.json` 中的 `skill_refs` / `tool_refs` 作为 allowlist；
- 把 `scene_key`、`skill_refs`、`allowed_tools` 传给 Agent 容器；
- 为前端展示 Agent 已绑定的 Skill 名称；
- 调试接口必须默认关闭，只能显式开启。

## 4. Skill.md 的定位

`Skill.md` 是 SOP，不是函数。

推荐格式：

```markdown
---
name: query_dashboard
description: 获取面板全量数据。
version: 1.0
inputs:
  scope:
    type: string
    description: 查询范围
    required: false
tools:
  - query_dashboard_tool
---

# Skill: query_dashboard

## 何时使用
当需要查看当前场景面板状态时使用。

## 执行步骤
1. 调用 `query_dashboard_tool`。
2. 检查返回结果。
3. 根据需要总结给用户。
```

其中：

- front matter 中的 `inputs` 用于描述 SOP 所需输入，不直接成为可执行接口；
- `tools` 表示该 SOP 推荐使用哪些原子工具；
- 真正可调用的工具必须由容器内 MCP server 从 `tools.py` 的 `ToolRegistry` 注册。

## 5. Tool 的定位

Tool 是原子动作，放在场景目录的 `tools.py` 中。

要求：

- Tool 必须小而明确；
- Tool 名称必须进入 `ToolRegistry.list_tools()`；
- Tool 执行入口必须是 `ToolRegistry.execute(name, **kwargs)`；
- MCP server 只注册 allowlist 中允许的工具；
- Agent 后端通过 MCP 调用工具，而不是让 `srv` 调用。

## 6. 通信权限控制

通信权限不能只写进 prompt。

`network_topology.json` 生成的通信矩阵需要下发给 `bus`，由 `bus` 在 `/relay` 处强制校验：

- 单播：`from_id -> to` 不在矩阵中则拒绝；
- 广播：只投递给矩阵允许的目标；
- bus 注册表只使用 `agent_id` 作为真实路由键，展示名只作为精确别名；
- 禁止模糊匹配目标 Agent，避免误路由。

## 7. 当前代码落实状态

已落实：

- `services/message_bus.py` 已增加通信矩阵接口，并在 `/relay` 中执行权限校验；
- `agent_network/api/simulations.py` 已删除 `srv` 侧 `tools.py` import 和 `Skill.md` 正文读取路径；
- `agent_network/api/simulations.py` 只从 `instances_and_skills.json` 读取 `skill_refs` / `tool_refs`，并作为 `skill_refs` / `allowed_tools` 下发；
- `agent_network/api/simulations.py` 在 launch 阶段把 `comm_matrix` 下发给 `bus`；
- `services/agent_server.py` 已接收 `skill_refs` 并写入 `AgentContext`；
- `agent_network/adapters/openclaw.py` 与 `agent_network/adapters/claude_code.py` 已在容器内读取 Skill.md SOP，并把 `skill_refs` 传给 MCP server；
- `agent_network/mcp_server.py` 负责在容器内读取 `tools.py`，并把 ToolRegistry 中允许的原子 Tool 注册为 MCP tools；
- `docker-compose.yml` 已增加 `ag-o1` / `ag-c1` 可构建 Agent runtime 镜像服务。

仍需运行验证：

- `docker compose config` 检查 compose 合法性；
- `docker compose up -d --build` 构建基础服务与 Agent runtime 镜像；
- 启动任一场景，确认 bus `/policy` 中 `policy_loaded=true`；
- 尝试无边通信，确认 bus 返回 403 并记录 policy denied 事件。
