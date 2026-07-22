# AgentNetwork 文档索引

本目录同时描述当前实现和明确标注为目标状态的设计。权威需求、架构决策、详细设计、指南和实施记录必须分目录维护，不能在同一文件中混合承担多种权威职责。

## 文档结构

| 目录 | 权威内容 |
|---|---|
| [requirements/](requirements/) | 系统需求基线和 SR-ID |
| [adr/](adr/) | 永久编号的架构决策、状态和替代关系 |
| [design/](design/) | 总体架构与领域详细设计 |
| [guides/](guides/) | 开发和部署操作指南 |
| [records/](records/) | 实施、迁移和验证记录 |

`scenes/` 下的 README、数据字典和示例文档只指导场景编写，不属于系统设计文档。除非需求明确修改场景规范，否则不要改动。

## 需求基线

- [系统需求规格](requirements/系统需求规格.md)：权威 SR、需求解释、实现状态和防回退合同。

## ADR

- [ADR 完整索引](adr/README.md)：`ADR-001`～`ADR-035` 的唯一编号、状态和替代关系。
- [ADR-019：持续时间约束的事件驱动仿真](adr/ADR-019-持续时间约束的事件驱动仿真.md)
- [ADR-020：统一文件管理入口](adr/ADR-020-统一文件管理入口.md)
- [ADR-021：统一抓包生命周期入口](adr/ADR-021-统一抓包生命周期入口.md)
- [ADR-022：SceneManager 统一剧本管理入口](adr/ADR-022-SceneManager统一剧本管理入口.md)
- [ADR-023：LogManager 统一批量管理与会话可见性](adr/ADR-023-LogManager统一批量管理与会话可见性.md)
- [ADR-024：CommManager 统一 A2A 通信与禁止广播](adr/ADR-024-CommManager统一A2A通信与禁止广播.md)
- [ADR-025：Agent 任务下发与 A2A 回调](adr/ADR-025-Agent任务下发与A2A回调.md)
- [ADR-026：SimulationManager 统一仿真生命周期](adr/ADR-026-SimulationManager统一仿真生命周期.md)
- [ADR-027：自定义日志根目录索引隔离](adr/ADR-027-自定义日志根目录索引隔离.md)
- [ADR-028：AgentNetwork 领域模块收敛](adr/ADR-028-AgentNetwork领域模块收敛.md)
- [ADR-029：SR 需求基线与追踪规则](adr/ADR-029-SR需求基线与追踪规则.md)
- [ADR-030：设计驱动与可追溯变更流程](adr/ADR-030-设计驱动与可追溯变更流程.md)
- [ADR-031：SimulationRun 持有显式执行上下文](adr/ADR-031-SimulationRun持有显式执行上下文.md)
- [ADR-032：后端原生能力统一授权与审计](adr/ADR-032-后端原生能力统一授权与审计.md)
- [ADR-033：Dashboard 使用 SceneListItem 字段合同](adr/ADR-033-Dashboard使用SceneListItem字段合同.md)
- [ADR-034：OpenClaw 网关使用固定版本 CLI 启动合同](adr/ADR-034-OpenClaw网关使用固定版本CLI启动合同.md)
- [ADR-035：Agent 容器启动采用就绪屏障](adr/ADR-035-Agent容器启动采用就绪屏障.md)

## 详细设计

| 文档 | 内容 |
|---|---|
| [设计文档](design/设计文档.md) | 系统目标、总体架构、数据流和核心边界 |
| [文档体系与编号设计](design/文档体系与编号设计.md) | 文档分域、ADR 编号生命周期和追踪门槛 |
| [剧本管理设计](design/剧本管理设计.md) | 剧本上传、查询、下载、删除和预览 |
| [剧本管理数据模型](design/剧本管理数据模型.md) | 剧本领域类、结构体、字段和关系 |
| [剧本管理类操作设计](design/剧本管理类操作设计.md) | 剧本资源和批量任务领域操作 |
| [剧本严格校验设计](design/剧本严格校验设计.md) | Skill、Tool、任务、Agent 和跨文件校验 |
| [仿真编排与容器运行时设计](design/仿真编排与容器运行时设计.md) | 事件调度、持续时间、容器和资源限制 |
| [统一文件管理设计](design/统一文件管理设计.md) | 受管文件基础设施和可见性 |
| [统一抓包模块设计](design/统一抓包模块设计.md) | 抓包生命周期、PCAP 投影和分析 |
| [统一日志管理设计](design/统一日志管理设计.md) | 日志批量管理和会话可见性 |
| [通信与网络仿真设计](design/通信与网络仿真设计.md) | A2A、通信矩阵、`tc` 和真实抓包 |
| [日志与实验数据设计](design/日志与实验数据设计.md) | 三类日志、manifest 和质量审计 |
| [实时展示设计](design/实时展示设计.md) | 实时日志、拓扑、通信流、Token 和面板 |
| [AgentTrafficCapture](design/AgentTrafficCapture.md) | Agent runtime 抓包与端到端验收 |
| [AgentRuntimeBoundary](design/AgentRuntimeBoundary.md) | Skill、Tool、MCP 和后端边界 |

## 指南与记录

- [开发文档](guides/开发文档.md)
- [OpenCLAW 部署](guides/OpenCLAWDeployment.md)
- [事件驱动仿真与严格剧本校验实施记录](records/实施记录-事件驱动仿真与严格剧本校验.md)

## 当前权威实现入口

- 控制面：`services/server.py`
- 仿真管理：`agent_network/simulation_management/`
- 仿真 HTTP 适配：`agent_network/api/managed_simulations.py`
- 剧本管理：`agent_network/scene_management/`
- Agent 容器运行时：`services/agent_server.py`
- 容器与 Agent 管理：`agent_network/agent_management.py`
- A2A 通信：`agent_network/comm_management/comm_manager.py`
- Agent Task：`agent_network/task_management/`
- MCP：`agent_network/mcp_server.py`
- 文件管理：`agent_network/file_management/`
- 抓包管理：`agent_network/capture_management/`
- 日志管理：`agent_network/log_management/`
- 实验清单：`agent_network/experiment_manifest.py`

## 维护原则

1. `requirements/` 是产品需求权威；详细设计不得弱化 SR。
2. `adr/` 一个编号只对应一个文件；废弃决策保留编号并标记替代关系。
3. `design/` 不得包含新的 `## ADR-NNN` 正文；不可轻易回退的决定写入 `adr/`。
4. 全部修改按 ADR-030 留下设计记录、实现映射和验证证据。
5. 修改 SR 时同步更新需求、ADR、详细设计、接口/模型和测试计划。
6. Agent 通信变更同步更新 ADR-024、ADR-025及通信测试。
7. 没有设计记录或实际验证证据的修改不得合并。
