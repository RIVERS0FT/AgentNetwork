# Agent 运行边界：Skill 包、Tool、MCP 与后端

## 1. 核心结论

- `srv` 只负责场景解析、编排、容器分配、上下文下发和日志聚合。
- Skill 是由剧本管理员预先放入 `scenes/<scene>/skills/` 的源文件包。
- `skill_refs` 是当前逻辑 Agent 可以读取的 Skill allowlist。
- Agent 后端必须主动读取 Skill 入口及其引用的模板、规则和辅助文件。
- Skill 不是可执行 Tool，不注册成函数。
- 场景原子 Tool 来自 `tools.py`，由容器内 MCP server 按 `allowed_tools` 暴露。
- 后端原生能力来自独立的 `native_capabilities` 策略，不与场景 `allowed_tools` 合并。
- 原生 Tool 与原生子 Agent 必须先授权、后执行、再记录终态；审计链不可用时失败关闭。
- Agent 间通信使用 MCP 的 `send_message`，底层由统一 `CommManager` 使用 A2A 1.0 点对点 HTTP；不提供广播。

## 2. Skill 形态

单文件 Skill：

```text
scenes/<scene>/skills/<skill_ref>.md
```

目录型 Skill 包：

```text
scenes/<scene>/skills/<skill_ref>/
  SKILL.md
  templates/
  docs/
  other runtime files
```

目录型 Skill 的入口固定为 `SKILL.md`。Agent 读取入口后，按入口中的相对引用决定是否继续读取包内文件。

## 3. 正确运行链路

```text
srv
  -> 读取 scene 配置中的 skill_refs / tool_refs
  -> 分配后端容器
  -> 事件到达后通过 /run 下发 scene_key、skill_refs、allowed_tools、native_capabilities、任务、消息、目录和通信矩阵

Agent 容器
  -> Adapter 把统一上下文交给真实后端
  -> Claude Code 通过 mcp_server 的受限 Skill 模式查询和读取允许的 Skill
  -> OpenCLAW 使用后端本地文件能力读取允许的 Skill
  -> Claude Hook / OpenClaw Plugin 在原生 Tool 执行前调用统一策略
  -> 原生 Tool 结果和子 Agent 生命周期写入 application.v12
  -> MCP server 从当前 scene 的 tools.py 注册允许的原子 Tool
  -> send_message 通过 CommManager 调用目标 A2A message:send
```

## 4. `srv` 的边界

`srv` 可以：

- 读取 `skill_refs`、`tool_refs`；
- 传递 `scene_key`、`skill_refs`、`allowed_tools`、`native_capabilities`；
- 展示 Agent 配置；
- 管理容器、事件调度和日志。

`srv` 不得：

- 读取 Skill 正文或遍历 Skill 包；
- 生成或注入 `skill_context`；
- import 场景 `tools.py`；
- 执行 `ToolRegistry.execute()`；
- 把 Skill 包装成 Tool；
- 根据 Skill 内容扩大 Tool 权限。

## 5. Adapter 的边界

Adapter 负责把 `AgentContext` 映射到具体后端，并把后端结果转换为 `AgentRunResult`。

Adapter 可以传递身份、任务、消息、Skill/Tool allowlist、目录、通信矩阵、状态、trace 和 seed；但不负责理解 Skill 业务语义，也不得绕过 allowlist。

目标 `AgentContext` 不携带固定执行批次编号、调度循环编号或 tick。调度状态由控制面的 `SimulationRun` 和事件队列维护，不泄漏给后端 SDK。

### Claude Code

- 启动 `agent_network.mcp_server --skill-source-mode`；
- 暴露 `list_available_skills`、`list_skill_files`、`read_skill_file`；
- 同一个 MCP 同时注册 allowlist 内的场景 Tool 和系统通信 Tool；
- 系统提示要求先读取 Skill 入口，不根据名称猜测内容。
- 使用 Claude Code Tool preset、真实 `allowed_tools/disallowed_tools`、`strict_mcp_config=True` 和 `permission_mode=dontAsk`；
- `PreToolUse` 强制授权，`PostToolUse/PostToolUseFailure` 记录结果，`SubagentStart/SubagentStop` 记录生命周期；
- 原生子 Agent 只使用 `agentnetwork-worker` 定义，默认不拥有 `Agent` Tool；
- 保存 SDK session ID 并按策略 resume。

### OpenCLAW

- 将当前 scene 的只读 Skill 路径和 `allowed_refs` 传给后端；
- 使用后端本地文件能力读取源文件；
- session 名由逻辑 Agent ID 与 trace ID 隔离；
- Gateway 强制加载 `agentnetwork-audit` 插件，`before_tool_call` 失败关闭，`after_tool_call` 和子 Agent Hook 写入审计；
- 原生 A2A、`sessions_send` 和 channel 消息不得用于 AgentNetwork Agent 通信；
- SDK 或 Gateway 缺失时失败，不静默退化为 direct LLM。

### Direct LLM

`direct_llm` 没有本地文件或 MCP Tool 能力，因此不能承载配置了 `skill_refs` 的 Agent。当前 scene loader 也不接受它作为场景后端。

## 6. Skill 文件安全

Skill 访问必须同时满足：

- `scene_key` 和 `skill_ref` 都是单段相对名称；
- Skill 在当前 Agent 的 `skill_refs` 中；
- 单文件 Skill 只能读取自身入口；
- 包内文件必须位于 package root 内；
- 拒绝绝对路径、`..` 和解析后的路径逃逸；
- 单文件读取上限为 512 KiB；
- `scenes/` 在运行容器中只读挂载。

## 7. Skill 与 Tool 权限分离

- `skill_refs` 控制“能读什么”。
- `allowed_tools` 控制“能执行什么”。
- Skill 文档提到 Tool，不自动授予执行权限。
- Tool allowlist 之外的场景 Tool 不注册到 MCP。
- `send_message` 是平台系统原子 Tool，仍受通信矩阵约束；多目标操作由 `CommManager` 按顺序逐个发送。

### 7.1 原生能力策略

`native_capabilities` 使用规范能力名，不把 Claude/OpenClaw 的具体 Tool 名当作剧本合同。默认允许 `fs.read`、`fs.search`、`web.fetch` 和 `agent.spawn`，其余默认拒绝。

策略校验和执行必须满足：

- 未知字段、未知 Tool、目录越界、敏感凭证路径、危险命令和私网 URL 拒绝；
- `agent.message`、`channel.send` 永远不能配置放行，消息只能走 `CommManager`；
- Tool 输入输出保存 SHA-256 和脱敏预览，文件写入保存前后哈希；
- 默认子 Agent 最多 3 个、并行 2 个、深度 1，且子 Agent 不可继续 spawn；
- 子 Agent 与父 Agent 共用容器，使用独立 session，并投影到 `AgentRegistry`；
- reset 注销原生子 Agent 投影并清理策略、session、Tool 调用和父子树状态。

## 8. 容器复用

容器是可重复使用的后端槽位，不是固定业务 Agent：

- 每次 `/run` 使用请求中的逻辑 Agent 身份；
- Claude Skill MCP 按本次 scene 和 allowlist 启动；
- OpenCLAW session 按逻辑 Agent 与 trace 隔离；
- 原生子 Agent session 独立于父 session，但共享同一容器身份；
- Adapter 不缓存 Skill 正文；
- 重分配前 `/reset` 清理 inbox、事件、PCAP 和网络仿真状态。

同一逻辑 Agent 默认只允许一个事件处理任务处于执行中；不同 Agent 的 `/run` 可以在仿真资源限制范围内并行。

## 9. 禁止回退的旧设计

以下做法已移除，不应重新引入：

- `srv` 解析 Skill Markdown；
- Adapter 把完整 Skill 正文注入 `skill_context`；
- Skill 名称直接注册为 Tool；
- 通过中心 message bus relay 发送 Agent 消息；
- OpenCLAW/Claude SDK 缺失时静默 fallback；
- 将原生 Tool 合并到场景 `allowed_tools`；
- 允许原生消息或 OpenClaw A2A 绕过 `CommManager`；
- 审计接口或 Hook/Plugin 失败时继续执行；
- 向 Adapter 或后端暴露固定执行批次编号、调度循环编号或 tick。
