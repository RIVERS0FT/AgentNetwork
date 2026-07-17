# ADR-026：SimulationManager 统一仿真生命周期

> 状态：已实施。适用于仿真配置、启动、停止、强制停止、最大持续时间和 Agent 容器资源分配。

## 决策

`agent_network/simulation_management/` 是仿真运行领域的唯一权威模块：

- `SimulationManager` 按 `simulation_id` 保存 `SimulationRun`，并负责全部状态迁移；
- `SimulationRuntimeConfig` 保存最大持续时间、Agent 调用超时、优雅停止等待时间和本次运行的资源分配；
- `SimulationResourceAllocator` 在启动容器前解析默认资源与逐 Agent 覆盖，并校验 Agent 引用和宿主机容量；
- `simulation_management/state.py` 承载现有控制面共享状态、WebSocket 观察者和 Token 使用统计，不得在顶层或 API 中复制；
- `agent_network/api/managed_simulations.py` 仅作为 HTTP 适配器调用 `SimulationManager`；
- `agent_network/api/simulations.py` 暂时只保留底层场景执行、抓包和网络配置流程，不拥有对外生命周期状态；
- `ContainerRuntime` 落实 Docker CPU、内存、PID 限制和 Agent 执行并发上限。

当前仍为单活动仿真。已配置但未启动的仿真也占用活动槽位，必须停止后才能配置下一次运行；所有外部操作均携带并返回 `simulation_id`，为以后按实例隔离并发保留边界。

## 配置合同

一次配置请求至少包含 `scene`，并可包含：

- `duration_seconds`：最大墙钟持续时间，范围 1 至 604800 秒；
- `agent_timeout_seconds`：单次 Agent `/run` 超时；
- `graceful_stop_timeout_seconds`：优雅停止等待时间；
- `resource_allocation.default_agent`：默认 `cpu_cores`、`memory_mb`、`pids_limit`；
- `resource_allocation.agent_overrides`：按精确 Agent ID 覆盖资源；
- `resource_allocation.max_parallel_agents`：同时执行 Agent 的最大数量。

资源覆盖引用不存在的 Agent、配置越界、最大并发资源超过可探测的 Docker 宿主容量，均在容器分配前拒绝。容器分配过程中任一 Agent 失败时，必须强制回滚本次已经分配的容器并写入 manifest。

## 生命周期与停止语义

状态为：

```text
CREATED -> CONFIGURED -> STARTING -> RUNNING
RUNNING -> COMPLETED | FAILED
RUNNING -> STOPPING -> STOPPED
RUNNING -> FORCE_STOPPING -> FORCE_STOPPED
CONFIGURED -> STOPPED | FORCE_STOPPED
```

- 启动时根据 `duration_seconds` 计算 `deadline_at` 并启动独立截止计时器；到期后以 `duration_exceeded` 原因执行强制停止。
- 优雅停止先禁止后续事件派发，逐 Agent 取消尚未执行的 A2A Task，使已登记的 Push 回调能够收到取消终态，然后等待运行清理完成。
- 强制停止设置强停信号，并仅 kill 当前仿真分配的容器；不得停止无关容器。
- 已进入终态的停止和强停请求幂等返回，不重复停止容器。
- 用户停止或到期强停的实验 manifest 状态为 `stopped`，不得伪报为 `complete`。

## HTTP 接口

- `POST /api/simulations/configure`
- `POST /api/simulations/{simulation_id}/start`
- `POST /api/simulations/{simulation_id}/stop`
- `POST /api/simulations/{simulation_id}/force-stop`
- `GET /api/simulations`
- `GET /api/simulations/{simulation_id}`

`setup`、`launch` 和无 ID 的 `stop` 暂时由同一管理器提供旧 Dashboard 入口；它们不得绕过 `SimulationManager` 或维护第二份状态机。

## 禁止回退

- 不得在 API、场景管理器或底层编排脚本中建立第二份仿真生命周期状态机；
- 不得把运行配置放回 `SceneDefinition` 或场景元数据；
- 不得只记录资源限制而不落实到 Docker 容器和执行并发；
- 不得将优雅停止直接实现为容器 kill；
- 不得让强制停止清空或停止当前仿真之外的运行资源；
- 不得在最大持续时间到期后继续派发 Agent 工作；
- 修改状态、配置、资源或停止语义时，必须同步更新本 ADR、API 测试和运行时测试。

## 实施状态

统一生命周期、持续时间、资源限制和底层事件驱动调度均已实施。`SimulationManager` 持有每次运行的调度器，停止信号会唤醒阻塞等待并取消待处理事件；不得恢复固定轮次执行器。
