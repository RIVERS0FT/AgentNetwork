# ADR-035：Agent 容器启动采用就绪屏障

- 状态：已接受并实现
- 日期：2026-07-22
- 关联需求：SR-SIM-01、SR-SIM-02
- 扩展决策：ADR-019、ADR-024、ADR-034

## 背景

仿真运行时会按剧本一次性创建或复用多个 Claude Code 与 OpenClaw 容器。容器被 Docker 标记为 running 只表示入口进程已经启动，不表示容器内 Agent Server 已经监听 8000 端口。

OpenClaw 容器必须先启动 Gateway、等待 Gateway 端口开放，再启动 Agent Server。实际运行中该过程约需数秒，而编排器此前只在全部容器分配后固定等待 1 秒，随后立即调用 `/reset` 和 `/communication/configure`。因此镜像入口修复后，容器虽能创建，仍会因 Agent Server 尚未监听而产生 `Connection refused`，最终以 `assignment_failed` 回滚。

固定等待无法表达真实就绪状态，也会同时造成两类错误：启动较慢的健康容器被误判失败，启动已经退出的容器要等到固定时间结束后才暴露问题。

## 决定

1. 容器分配与控制面配置之间增加 Agent Server 就绪屏障。
2. 就绪条件固定为 Agent URL 的 `GET /status` 返回成功 HTTP 状态；Docker 的 running 状态不能替代应用层就绪。
3. 本次仿真的所有已分配容器共享一个启动窗口。编排器在窗口内轮询所有未就绪 Agent，而不是为每个 Agent 串行消耗一个完整超时。
4. 启动窗口由 `SimulationRuntimeConfig.agent_startup_timeout_seconds` 配置，默认 60 秒，允许范围为 1～3600 秒，并通过仿真配置 API 输入。
5. 检测到容器进入 `exited` 或 `dead` 时立即停止等待该 Agent，记录容器名、状态、退出码和 Docker 错误。
6. 窗口结束仍未就绪时记录 Agent URL 与最后一次连接错误。容器退出和就绪超时都属于 assignment error，仿真必须执行现有全量资源回滚。
7. 只有通过就绪屏障的 Agent 才允许执行 `/reset` 和 `/communication/configure`。任一 Agent 未通过时，不允许进入网络仿真、抓包或事件调度。
8. 实验 manifest 的 scheduler 快照记录 `agent_startup_timeout_seconds`，使启动失败可复现。

## 影响范围与不变边界

- 影响：仿真运行配置、受管仿真 API、ContainerRuntime 就绪检测、仿真启动顺序、实验 manifest 和容器边界测试。
- 不改变：容器镜像选择、容器池命名、资源分配算法、Gateway 启动合同、CommManager 权限语义、事件调度和 LLM 调用协议。
- `agent_timeout_seconds` 继续只约束单次 Agent 事件执行，不复用于容器启动。
- 本 ADR 不修改已有 ADR 正文；它补充 ADR-024 的控制面配置前置条件和 ADR-034 的运行时启动边界。

## 被放弃方案

1. **继续增加固定 sleep**：放弃。不同后端、主机负载和 Gateway 初始化时间不同，任何固定值都会在误判与无谓等待之间取舍。
2. **只检查 Docker running**：放弃。入口进程运行时，Agent Server 端口可能尚未监听。
3. **每个 Agent 独立等待 60 秒**：放弃。多个失败容器会使总等待时间按 Agent 数量线性增长。
4. **通信配置失败后再重试**：放弃。`/reset`、通信矩阵、网络仿真和抓包都依赖 Agent Server，统一的启动屏障比在每个下游步骤分别重试更清晰。
5. **OpenClaw 使用更长固定等待，Claude Code 保持原逻辑**：放弃。就绪是统一的 Agent Server 合同，不应把编排正确性绑定到后端名称。

## 迁移、失败与回滚

- 迁移：现有请求不提供新字段时使用 60 秒默认值；调用方无需修改。
- 兼容：`SimulationRuntimeConfig.to_dict()` 自动包含新字段，旧请求仍可由 `from_dict()` 使用默认值构造。
- 失败语义：容器提前退出或超时未就绪均写入 `assignment_errors`，状态为 `assignment_failed`，并调用 `force_stop_all()` 回滚本次已分配容器。
- 回滚：同时移除运行配置字段、API 字段、就绪检测、启动顺序调用、manifest 字段、测试、详细设计和本 ADR；不得只恢复固定 `sleep(1)`。

## 实现映射

- 就绪轮询与容器退出检测：`agent_network/agent_management.py`；
- 启动屏障与 manifest 快照：`agent_network/api/simulations.py`；
- 运行配置模型：`agent_network/simulation_management/models.py`；
- API 输入：`agent_network/api/managed_simulations.py`；
- 运行时与顺序回归测试：`tests/test_container_runtime_boundary.py`、`tests/test_simulation_management_boundary.py`、`tests/test_simulation_manager.py`；
- 详细设计：`docs/design/仿真编排与容器运行时设计.md`、`docs/design/通信与网络仿真设计.md`。

## 验证

预期执行：

```bash
python3 -m pytest tests/test_container_runtime_boundary.py tests/test_simulation_management_boundary.py tests/test_simulation_manager.py -q
python3 -m pytest tests/test_docs_layout.py -q
python3 -m py_compile agent_network/agent_management.py agent_network/api/simulations.py agent_network/api/managed_simulations.py agent_network/simulation_management/models.py
python3 scripts/check_design_traceability.py
git diff --check
```

2026-07-22 实际结果：

- `python3 -m pytest tests/test_container_runtime_boundary.py tests/test_simulation_management_boundary.py tests/test_simulation_manager.py tests/test_docs_layout.py tests/test_a2a_design_contract.py -q`：通过，32 项全部通过；
- `TASK_DB_PATH=/tmp/agentnetwork-adr035-pytest.db python3 -m pytest -q`：通过，197 项全部通过，1 项上游 Starlette/httpx2 弃用警告；使用 `/tmp` 是因为工作区现有 `data/tasks` 对宿主测试进程不可写；
- 修改文件的 `python3 -m py_compile`：通过；
- `python3 scripts/check_design_traceability.py`：通过，识别到 ADR-035、两份详细设计、需求追踪和 ADR 索引；
- `docker compose config --quiet`：通过；
- `git diff --check`：通过。
