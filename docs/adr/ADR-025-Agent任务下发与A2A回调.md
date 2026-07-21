# ADR-025：Agent 任务下发与 A2A 回调

- 状态：已接受并实现
- 日期：2026-07-16
- 适用范围：Agent 间任务委派、仿真编排任务下发、任务持久化、状态回调与取消
- 协议基线：A2A 1.0 HTTP+JSON REST binding
- 关联决策：[ADR-024-CommManager统一A2A通信与禁止广播.md](ADR-024-CommManager统一A2A通信与禁止广播.md)

## 1. 目标

在既有点对点消息通信之外增加真正的 Agent 执行任务：一个 Agent 或仿真编排器可向一个明确目标 Agent 下发任务，目标异步执行，并把工作中、产物、完成、失败或取消状态回调给下发方。

该能力必须继续遵守 ADR-024：不提供广播；多个目标必须按输入顺序逐个调用，每个目标拥有独立的 A2A Task、状态和失败结果。

## 2. 核心决策

### 2.1 唯一通信入口

任务下发仍由 `agent_network/comm_management/comm_manager.py` 中的 `CommManager` 完成：

- `delegate_task()` 下发单目标任务；
- `delegate_to_many()` 只对显式 Agent ID 列表按顺序逐个调用；
- Agent MCP 暴露原子工具 `delegate_task`；
- 仿真编排 API 以 `srv` 身份调用同一 `CommManager`，不得另写私有 HTTP 客户端；
- 普通 `send_message` 的“投递完成回执”语义保持不变。

任务消息使用 A2A `message:send`，并通过 `message.metadata.operation=agent.task.assign` 与普通消息区分。任务输入由文本 Goal Part 和 JSON Data Part 组成。

### 2.2 持久化任务生命周期

`agent_network/task_management/task_manager.py` 中的 `TaskManager` 是统一 Task 生命周期存储，使用 SQLite：

```text
SUBMITTED -> WORKING -> COMPLETED
                    -> FAILED
SUBMITTED/WORKING -> CANCELED
```

记录包含方向、`task_id`、`context_id`、仿真 ID、来源/目标 Agent、Goal、trace、父任务、幂等键、A2A Task、回调配置、回调状态和事件序号。终态不得迁移到不同终态。

Agent Server 与 MCP runtime 使用同一个 `data/tasks/{runtime_agent_id}.db`；该 ID 来自容器环境 `AGENT_ID`，因此容器池把逻辑身份重新配置给物理容器时两个进程仍指向同一文件。文件通过 `data/tasks` volume 持久化，SQLite 使用 WAL 和 busy timeout 以支持两个进程共享。编排器使用 `data/tasks/orchestrator.db`。

同一方向、来源 Agent 和非空幂等键只创建一个任务。重复回调使用单调递增 `sequence` 去重。

### 2.3 Agent 执行

目标 Agent 收到 `agent.task.assign` 后立即持久化并返回 `TASK_STATE_SUBMITTED`，不把它当作普通 inbox 消息。调度器通过 `/status.pending_tasks` 发现待执行任务；`POST /run` 未显式携带 task 时领取最早的待执行任务并转为 `WORKING`。

执行完成后保存 Artifact 并转为 `COMPLETED`；Adapter 返回错误或抛出异常时转为 `FAILED`。领取、执行和回调均不要求真实模型；协议测试必须使用 `MOCK_LLM=1`。

### 2.4 Push 回调

下发方为每个任务生成独立 callback URL 和高熵 token，放入 `taskPushNotificationConfig`。目标 Agent 通过 `CallbackDispatcher` 发送：

- `statusUpdate`：WORKING、COMPLETED、FAILED、CANCELED；
- `artifactUpdate`：最终 Artifact；
- Header：`A2A-Version: 1.0`、`Content-Type: application/a2a+json`、`X-A2A-Notification-Token`；
- metadata：随机 `eventId` 和递增 `sequence`。

接收方必须先按本地出站任务读取预登记 token，并使用恒定时间比较校验，然后才更新任务。回调发送失败时将 `callback_state` 保留为 `pending`，不得把业务任务错误地改成失败；当前实现由后续运行或运维重试处理，不承诺常驻后台重试线程。

Agent 之间的回调端点为 `/a2a/v1/task-events`。仿真编排回调端点为 `/api/simulations/{simulation_id}/task-events`。

### 2.5 仿真编排 API

```text
POST /api/simulations/{simulation_id}/agents/{agent_id}/tasks
GET  /api/simulations/{simulation_id}/tasks
GET  /api/simulations/{simulation_id}/tasks/{task_id}
POST /api/simulations/{simulation_id}/tasks/{task_id}:cancel
POST /api/simulations/{simulation_id}/task-events
```

下发接口只接受一个路径目标。目标必须是当前 AgentRegistry 中具有运行 URL 的精确 Agent ID。`srv` 是受信任的控制面任务来源，但普通 Agent 间任务仍受通信矩阵双向检查。取消由编排器经 `CommManager.cancel_remote_task()` 点对点转发给目标 Agent。

### 2.6 A2A 能力与端点

实现 Push 回调后 Agent Card 固定声明 `pushNotifications=true`。Agent Server 同时提供：

```text
POST   /a2a/v1/tasks/{task_id}/pushNotificationConfigs
GET    /a2a/v1/tasks/{task_id}/pushNotificationConfigs
GET    /a2a/v1/tasks/{task_id}/pushNotificationConfigs/{config_id}
DELETE /a2a/v1/tasks/{task_id}/pushNotificationConfigs/{config_id}
```

## 3. 不变量与防回退

- 不增加 `broadcast`、通配目标、角色目标或并发 fan-out。
- 不绕过 `CommManager` 下发或取消远程任务。
- 不把普通消息的 delivery receipt 改写为 Agent 执行完成。
- 不接受未预登记任务、错误仿真 ID或 token 不匹配的回调。
- 不把 Task 只存进进程内内存；Agent Server 与 MCP 必须共享持久化数据库。
- 不因回调网络失败回滚已经完成的 Agent 业务任务。
- 修改任务字段、状态机、回调认证、持久化路径或 API 时，必须同步更新本 ADR 和测试。

## 4. 验收

- Agent 间 `delegate_task` 能产生 SUBMITTED Task，并由目标 `/run` 领取执行；
- 仿真编排能向一个明确 Agent 下发、查询和取消任务；
- WORKING、Artifact、COMPLETED/FAILED/CANCELED 回调可验证 token 并更新出站 Task；
- 幂等下发不重复创建入站 Task，重复 sequence 不重复追加 Artifact；
- 多目标任务仍按输入顺序逐个发送并独立记录结果；
- 数据库在进程重启后保留任务；
- 测试与运行使用 `MOCK_LLM=1`，不产生模型 token 消耗。
