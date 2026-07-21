# ADR-024：CommManager 统一 A2A 通信与禁止广播

- 状态：已接受并实现；Task 执行生命周期与 Push 回调部分由 ADR-025 扩展
- 日期：2026-07-16
- 适用范围：Agent 间消息数据面、MCP 系统通信 Tool、Agent 容器通信入口、通信日志与前端轨迹
- 协议基线：A2A 1.0，HTTP+JSON REST binding

## 1. 背景

旧实现把 Agent 通信分散在 `DirectBus`、MCP runtime、Agent Server 和场景初始化中，并暴露 `broadcast`。这造成以下问题：

- 缺少统一的 Agent Card 发现、协议封装、权限检查和错误模型；
- 发送端与接收端没有统一执行通信矩阵；
- 广播会隐式扩大消息目标，难以逐目标审计和重放；
- `/message` 是项目私有协议，无法与外部 A2A Agent 互操作；
- 启动并发时，接收方可能尚未获得本次仿真的逻辑身份和通信矩阵。

中心 `message_bus` 已经废弃且 `/relay` 固定返回 410。本次变更不得恢复中心 relay。

## 2. 决策

### 2.1 唯一管理入口

`agent_network/comm_management/comm_manager.py` 中的 `CommManager` 是 Agent 间通信的唯一业务管理入口，统一负责：

- Agent 目录注册与 Agent Card 发现；
- A2A 1.0 HTTP+JSON 接口选择；
- 发送端与接收端通信矩阵校验；
- A2A Message 构造、发送和响应解析；
- 多个明确目标的顺序发送；
- 入站消息投递、Task 回执、查询和取消语义；
- 每个目标的独立成功或失败结果。

业务代码不得绕过 `CommManager` 直接向其他 Agent 发 HTTP 请求。

### 2.2 点对点 A2A

Agent 间通信保持点对点，不经过 `srv` 或 `message_bus` 转发：

```text
Agent A MCP send_message
  -> CommManager 检查 A -> B 权限
  -> GET Agent B /.well-known/agent-card.json
  -> 选择 A2A 1.0 HTTP+JSON interface
  -> POST Agent B /a2a/v1/message:send
  -> Agent B CommManager 再次检查 A -> B 权限
  -> Agent B inbox
```

该路径仍经过真实容器网络，因此 Linux `tc` 网络仿真和 PCAP 抓包继续观察真实流量。

### 2.3 禁止广播

系统不提供 Agent 广播能力：

- `CommManager` 不定义 `broadcast()`；
- MCP 只注册 `send_message`，`ATOMIC_TOOL_NAMES` 不包含 `broadcast`；
- `SYSTEM_TOOLS` 不包含 `broadcast`；
- 旧 `/message` 收到 `type=broadcast` 时返回 HTTP 400；
- 前端不展开或渲染广播事件；
- 不允许通配符、角色名、模糊名称或 `0.0.0.0` 作为发送目标。

任何重新加入广播的修改都属于架构变更，必须先用新的 ADR 明确替代本决策。

### 2.4 多目标必须顺序发送

多目标调用只接受明确的 Agent ID 列表，并执行：

```python
for target in deduplicated_targets_in_input_order:
    result = send_message(target)
    results.append(result)
```

固定语义：

- 按首次出现顺序去重；
- 不并发 fan-out；
- 一个目标失败后继续处理后续目标；
- 每个目标生成独立 `message_id` 和 A2A Task；
- 汇总状态只能是 `success`、`partial` 或 `failed`；
- 传入单个字符串代替目标列表属于调用错误。

## 3. A2A 外部合同

### 3.1 Agent Card

每个 Agent Server 必须提供：

```text
GET /.well-known/agent-card.json
```

Agent Card 至少声明：

- `supportedInterfaces[].protocolBinding = HTTP+JSON`；
- `supportedInterfaces[].protocolVersion = 1.0`；
- interface URL 指向 `/a2a/v1`；
- 输入、输出 media type；
- 当前 Skill 列表；
- `streaming=false`、`extendedAgentCard=false`；`pushNotifications` 已按 ADR-025 实现并声明为 `true`。

在没有实现对应能力前，不得把这些 capability 标记为 `true`。

### 3.2 当前端点

```text
POST /a2a/v1/message:send
GET  /a2a/v1/tasks
GET  /a2a/v1/tasks/{task_id}
POST /a2a/v1/tasks/{task_id}:cancel
```

除 Agent Card 外，A2A 请求必须携带：

```text
A2A-Version: 1.0
Content-Type: application/a2a+json
```

缺失或不支持的版本返回 `VERSION_NOT_SUPPORTED`，不得静默按私有协议处理。

### 3.3 Message 映射

| AgentNetwork 字段 | A2A 字段 |
|---|---|
| 单次发送 ID | `message.messageId` |
| 会话或通道 ID | `message.contextId` |
| 文本内容 | `message.parts[].text`，`mediaType=text/plain` |
| 调用方角色 | `message.role=ROLE_USER` |
| 来源 Agent | `message.metadata.fromAgentId` |
| 来源显示名 | `message.metadata.fromAgentName` |
| 目标 Agent | `message.metadata.targetAgentId` |
| 仿真 trace | `message.metadata.traceId` |

当前模块只接受至少一个文本 Part。文件、URL、二进制和任意 Data Part 的业务处理必须通过后续设计扩展，不能被静默当作文本。

### 3.4 Task 回执边界

当前 A2A Task 表示“消息已通过权限检查并写入目标 inbox”的交付任务，不表示目标 Agent 已完成推理：

- 成功写入 inbox 后立即返回 `TASK_STATE_COMPLETED`；
- `Artifact` 为结构化 delivery receipt；
- Task 保存原始 Message history 和来源、目标、trace metadata；
- 已完成 Task 不可取消，取消请求返回 `TASK_NOT_CANCELABLE`；
- TaskStore 当前是容器内内存存储，`/reset` 时清空。

完整 Agent 执行生命周期、持久化、状态迁移、调度与回调已由 ADR-025 新增；本节普通消息 delivery Task 的语义保持不变。

## 4. 身份、目录和权限

### 4.1 控制面配置顺序

`srv` 在启动 Agent 执行前完成：

1. 分配容器；
2. 生成 `agent_directory`；
3. 从拓扑生成 `comm_matrix`；
4. 调用每个容器的 `POST /communication/configure`；
5. 全部配置成功后才允许进入 `/run` 和网络仿真阶段。

配置失败属于 Agent 分配失败，仿真不得带着未配置的通信权限继续运行。

### 4.2 权限规则

- Agent ID 标准化为小写并进行精确匹配；
- 发送前检查 `source -> target`；
- 接收入 inbox 前再次检查同一条权限；
- 正式仿真必须注入通信矩阵；
- 拒绝目标不匹配、自发自收和未注册目标；
- Agent Card URL 只能来自 `agent_directory`，不得由消息正文提供。

拓扑当前生成对称矩阵，但 `CommManager` 不假设矩阵一定对称；未来可在不修改管理器的前提下支持有向权限。

## 5. 代码职责映射

| 文件或目录 | 本次确定的职责 |
|---|---|
| `agent_network/comm_management/comm_manager.py` | 唯一 A2A 通信管理、发现、权限、顺序发送和 TaskStore |
| `agent_network/comm_management/network_emulation.py` | 通信延迟、抖动、丢包和带宽配置及 Linux `tc` 生命周期 |
| `agent_network/comm_management/__init__.py` | 统一导出通信类型 |
| `agent_network/mcp_server.py` | 暴露 `send_message` 与 `delegate_task`，统一委托 `CommManager` |
| `agent_network/task_management/` | 按 ADR-025 持久化执行 Task 并投递 Push 回调 |
| `services/agent_server.py` | A2A 路由、Agent Card、入站投递和控制面通信配置 |
| `agent_network/api/simulations.py` | 生成目录与矩阵，并在执行前配置所有 Agent 容器 |
| `agent_network/agent_management.py` | 注入 `send_message`、`delegate_task` 系统 Tool，容器模式固定为 `a2a` |
| `agent_network/adapters/claude_code.py` | 向 Claude 后端声明 A2A 网络模式 |
| `agent_network/adapters/openclaw.py` | 向 OpenCLAW 后端声明 A2A 网络模式 |
| `agent_network/api/system.py` | 对外报告 `network_mode=a2a` |
| `docker-compose.yml` | 所有服务环境统一声明 `AGENT_COMM_MODE=a2a` |
| `services/message_bus.py` | 继续作为 410 废弃桩，不参与数据面 |
| `agent_network/log_management/log_manager.py` | 消息日志默认类型为 `a2a`，不记录广播标志 |
| `web/public/dashboard.js` | 只为成功的点对点 `send_message` 绘制通信轨迹 |

新增通信入口时必须更新此表，不能形成第二套发送实现。

## 6. 兼容边界

- `/message` 暂时保留给旧调用方，但新的 Agent 间发送不得使用它；
- `/message` 不接受广播；
- `agent_network/comm.py` 兼容导入已删除，所有调用方必须迁移到 `agent_network.comm_management`；
- `services/message_bus.py` 保留明确的 410 提示；
- 不得重新增加通信模块导入兼容层；外部调用方应直接迁移，缺失旧模块必须显式失败。

兼容不允许成为恢复旧数据面的理由。

## 7. 日志与可观测性

成功接收 A2A 消息时，应用日志必须能够关联：

- `from_agent` 与 `to_agent`；
- A2A `task_id`；
- `context_id`；
- `trace_id`；
- `protocol_version=1.0`；
- `message_type=a2a`。

应用日志用于表达业务事件；`network.jsonl` 仍只能来自真实 PCAP，不得根据 A2A 请求伪造 packet record。

## 8. 错误与失败语义

发送操作不抛出目标级网络异常给上层，而是返回该目标的失败 `SendResult`。入站协议错误使用结构化 A2A HTTP 错误：

- 未注册目标：`AGENT_NOT_FOUND`；
- 没有兼容 interface：`A2A_INTERFACE_NOT_FOUND`；
- 无效请求：`INVALID_REQUEST`；
- 不支持的内容：`CONTENT_TYPE_NOT_SUPPORTED`；
- 权限拒绝：`COMMUNICATION_DENIED`；
- 目标不一致：`TARGET_MISMATCH`；
- Task 不存在或不可取消：`TASK_NOT_FOUND`、`TASK_NOT_CANCELABLE`。

不得把权限拒绝或协议错误降级为旧 `/message` 重试。

## 9. 测试与验收门槛

通信变更至少必须覆盖：

- Agent Card 的 A2A 1.0 HTTP+JSON interface；
- A2A header、Message、Part 和 metadata 构造；
- 发送端权限拒绝时不产生 HTTP 请求；
- 接收端拒绝不允许的来源；
- 多目标顺序、去重、继续执行和汇总状态；
- Agent Card、发送、Task 列表、Task 查询和终态取消端点；
- MCP 系统通信工具只包含点对点 `send_message` 与 `delegate_task`；
- Agent Server 不再导入 `DirectBus`；
- 容器运行时只注入点对点 `send_message` 与 `delegate_task`；
- 前端 JavaScript 语法检查；
- Python 模块编译检查。

测试仿真必须使用 `MOCK_LLM=1` 或纯协议测试，不得为了验证通信协议产生真实模型 token 消耗。

## 10. 禁止回退与变更规则

以下改动未经新 ADR 明确替代时禁止：

- 不得恢复 `DirectBus`；
- 恢复 MCP 或系统级 `broadcast`；
- 并发发送多个目标；
- 恢复中心 relay；
- 绕过 Agent Card 直接拼接私有发送端点；
- 只在发送端检查权限而取消接收端检查；
- 在 `/run` 已开始后才首次配置接收方身份和矩阵；
- 把 delivery Task 解释为 Agent 推理完成；
- 把 A2A 应用事件直接写成网络事实。

任何影响通信入口、A2A 版本、字段映射、权限、目标选择、Task 生命周期、兼容端点、日志或网络模式的代码修改，必须在同一变更中：

1. 更新本 ADR 和相关设计文档；
2. 更新对应单元测试与边界测试；
3. 运行通信回归测试；
4. 明确说明是否改变协议兼容性；
5. 若推翻本决策，新增后续 ADR，并将本 ADR 标记为“已被替代”，不得直接删除历史。

参考规范：[A2A Protocol Specification 1.0](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)。
