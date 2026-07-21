# ADR-031：SimulationRun 持有显式执行上下文

- 状态：已接受并实现
- 日期：2026-07-20
- 适用范围：`SimulationManager`、仿真 API 适配层、过渡期底层执行器及仿真状态查询
- 扩展：ADR-019、ADR-026

## 背景

统一仿真生命周期已经由 `SimulationManager` 管理，但 `managed_simulations.py` 仍直接读写旧编排模块的 `_pending_seed`、`_pending_config`、`_pending_scene_def`，并通过替换 `_capture`、`_capture_health` 私有函数注入抓包实现。底层执行器还使用全局 `_comm_matrix`。

这形成两套事实来源：新管理器持有 `SimulationRun`，旧模块私有全局变量却持有真正执行所需的场景、seed、配置和通信矩阵。配置第二次运行、并发策略扩展、失败重试或测试隔离时，均可能读到与目标 `simulation_id` 不一致的状态。

## 决定

每次运行所需的执行上下文由对应 `SimulationRun` 持有并显式传递：

- `scene_definition`：配置阶段加载并校验的静态剧本快照；
- `seed`：该运行唯一的随机种子；
- `execution_config`：启动该运行时使用的 LLM/后端配置，不通过 API 序列化返回；
- `runtime_config`、`resource_plan`、`control`：继续由同一个 `SimulationRun` 持有。

`SimulationManager.configure()` 先创建 `SimulationRun`，再把该运行交给 setup handler。run handler 只接收目标 `SimulationRun`，不得从模块级 pending 变量恢复执行输入。

过渡期执行模块提供无状态公开函数：

```text
prepare_scene(scene_definition, seed)
run_simulation(
    execution_config,
    scene_definition,
    seed,
    simulation_run,
    capture_handler,
    capture_health_handler,
)
```

通信矩阵由 `run_simulation()` 根据本次剧本在函数内构造。抓包入口以显式函数参数注入，不再通过修改旧模块私有函数实现。旧的 setup/launch 生命周期入口严格失败，不能重新保存 pending 状态。

`simulation_management.state` 中供 WebSocket、日志接收和调试工具使用的进程级字段仅作为可观测投影；仿真占用、生命周期判断和执行输入必须以 `SimulationManager` 与 `SimulationRun` 为权威来源。

## 被放弃方案

### 继续封装旧私有变量

放弃。即使增加 getter/setter，模块级单例仍不能表达按 `simulation_id` 隔离的执行上下文，也无法消除两套状态。

### 在新 API 模块复制一套 pending 变量

放弃。这只是把私有状态换一个位置，仍会形成第二套生命周期和并发阻碍。

### 本次立即重写全部底层容器执行代码

放弃。当前问题可以通过显式执行合同消除状态耦合；底层执行器整体迁入 `simulation_management/` 可作为后续独立重构，不应扩大本次风险范围。

## 影响

- setup handler 合同由 `(scene_definition, seed)` 改为 `(simulation_run)`；
- `SimulationRun.to_dict()` 不暴露 `scene_definition` 或 `execution_config`，避免序列化复杂对象和配置秘密；
- 新管理 API 不再访问或替换旧模块任何下划线私有成员；
- 执行结果、manifest、Agent context 和通信策略中的 seed、场景及矩阵均来自目标运行的显式参数；
- 当前并发上限仍为一，但执行合同已不依赖进程级 pending 槽位。

## 实现映射

- 运行上下文：`agent_network/simulation_management/models.py`
- 生命周期组装：`agent_network/simulation_management/simulation_manager.py`
- API 组合根：`agent_network/api/managed_simulations.py`
- 无状态过渡执行函数：`agent_network/api/simulations.py`
- 防回退检查：`tests/test_simulation_management_boundary.py`
- 生命周期回归：`tests/test_simulation_manager.py`

## 迁移、失败与回滚

迁移不改变 HTTP 请求或响应字段。旧进程中尚未启动的 pending 配置不再可用，部署时必须按常规方式重启服务；本项目不支持跨进程保留未启动仿真。

若显式上下文缺失，执行函数立即失败，不得回退读取旧全局变量。回滚必须整体恢复 manager handler 合同、执行函数参数和测试；禁止只恢复 `_pending_*` 变量形成混合模式。

## 验证

```bash
python -m pytest tests/test_simulation_manager.py tests/test_simulation_management_boundary.py -q
python -m pytest tests/test_network_emulation.py tests/test_simulation_task_api_boundary.py -q
python scripts/check_design_traceability.py
git diff --check
```

当前环境不具备 pytest runner 时，必须至少直接执行无第三方依赖的结构测试，并以自包含脚本验证两个不同运行的 scene、seed 和 execution config 不会交叉；交付环境仍需补跑完整 pytest。

2026-07-20 实际验证：

- 私有依赖扫描：`managed_simulations.py` 和过渡执行器中 `_pending_seed`、`_pending_config`、`_pending_scene_def`、`_comm_matrix`、`execution._*`、`orchestration._*` 为 0 个匹配；
- `test_simulation_management_boundary.py`：5 个测试通过直接函数调用；
- `test_simulation_manager.py`：使用仅实现 `pytest.raises` 的本地测试垫片直接调用，6 个测试通过；
- 双运行隔离脚本：依次执行 seed 为 11 和 22 的两个假运行，scene、seed、execution config 均保持隔离，内部上下文未进入 `to_dict()`；
- 管理状态权威脚本：`/scenes/state` 与剧本占用检查均从假 `SimulationManager` 当前运行得到 scene、running 和 simulation ID，不读取投影状态；
- `test_simulation_task_api_boundary.py`：3 个测试通过；
- `test_network_emulation.py`：3 个与本次执行边界相关且不需要 monkeypatch fixture 的测试通过；
- `test_a2a_design_contract.py`：3 个测试通过；
- `test_docs_layout.py`：5 个测试通过，ADR-001～031 连续、唯一且链接有效；
- `managed_simulations` 模块导入通过；139 个 Python 文件完成语法解析；
- `python scripts/check_design_traceability.py` 与 `git diff --check` 通过；
- 受限项：当前 Python 3.14 环境未安装 pytest，未执行正式 pytest runner；本次验证不启动容器、不调用真实 LLM，交付环境需补跑上述完整命令。

## 禁止回退

- 不得在新管理 API 中访问 `execution._*`、`orchestration._*` 或任何 `_pending_*` 状态；
- 不得让 `SimulationManager` 的 handler 通过模块级变量寻找当前运行；
- 不得把 `scene_definition`、seed、执行配置或通信矩阵重新放回进程级 pending 单例；
- 不得用猴子补丁替换底层执行模块的抓包函数；
- 不得使用可观测投影判断剧本占用或选择执行输入；
- 修改执行上下文合同时，必须同步更新本 ADR、ADR-026、详细设计和边界测试。
