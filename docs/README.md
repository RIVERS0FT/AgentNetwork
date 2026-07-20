# AgentNetwork 设计文档索引

本目录描述 **当前代码已经实现并要求继续保持的系统设计，以及明确标注为设计阶段的目标设计**。实现与文档冲突时，应先确认是否为有意变更；若是有意变更，代码与相关设计文档必须在同一变更中同步更新。

> `scenes/` 下的 README、数据字典和示例文档用于指导场景编写，不属于本次系统设计文档范围。除非需求明确指向场景规范，否则不要修改它们。

## 文档地图

| 文档 | 内容 |
|---|---|
| [系统需求规格.md](系统需求规格.md) | 权威 SR 需求基线、需求解释、设计追踪、实施状态和防回退规则 |
| [ADR-029-SR需求基线与追踪规则.md](ADR-029-SR需求基线与追踪规则.md) | 固定 SR-ID、文档优先级、冲突处理和需求变更同步规则 |
| [设计文档.md](设计文档.md) | 系统目标、总体架构、数据流和核心边界 |
| [剧本管理设计.md](剧本管理设计.md) | 剧本上传、查询、下载、删除、预览的 SR、AR、模块、接口、逻辑模型和时序设计 |
| [剧本管理数据模型.md](剧本管理数据模型.md) | 剧本管理类、结构体、枚举及其具体字段、关系和当前实现映射 |
| [剧本管理类操作设计.md](剧本管理类操作设计.md) | 剧本资源、批量处理任务和归档资源类的领域函数、状态迁移与带函数类图 |
| [剧本严格校验设计.md](剧本严格校验设计.md) | Skill、Tool、任务、Agent、严格 Schema 与跨文件一致性校验合同 |
| [实施记录-事件驱动仿真与严格剧本校验.md](实施记录-事件驱动仿真与严格剧本校验.md) | 本次事件调度、剧本校验、领域模型、API 与防回退实施基线 |
| [ADR-022-SceneManager统一剧本管理入口.md](ADR-022-SceneManager统一剧本管理入口.md) | 固定 SceneManager 批量编排、占用检查、逐项隔离和 SceneStorage/FileManager 职责边界 |
| [仿真编排与容器运行时设计.md](仿真编排与容器运行时设计.md) | setup/launch、事件驱动调度、持续时间控制、容器池、资源限制、终止与失败处理 |
| [ADR-019-持续时间约束的事件驱动仿真.md](ADR-019-持续时间约束的事件驱动仿真.md) | 禁止恢复固定执行计数调度的权威设计决策 |
| [ADR-026-SimulationManager统一仿真生命周期.md](ADR-026-SimulationManager统一仿真生命周期.md) | 固定统一仿真配置、启动、优雅停止、强制停止、持续时间与资源分配入口 |
| [ADR-028-AgentNetwork领域模块收敛.md](ADR-028-AgentNetwork领域模块收敛.md) | 固定抓包、通信、仿真状态和 Skill MCP 的领域归属及旧顶层入口删除规则 |
| [统一文件管理设计.md](统一文件管理设计.md) | 剧本、日志、PCAP、manifest 和归档共用的文件写入、读取、压缩、解压、下载与可见性基础设施 |
| [ADR-020-统一文件管理入口.md](ADR-020-统一文件管理入口.md) | 禁止业务模块新增直接文件操作和独立可见性机制的权威设计决策 |
| [统一抓包模块设计.md](统一抓包模块设计.md) | 抓包会话、Agent 本地 tcpdump、失败回滚、健康检查、PCAP 投影、分析与下载统一入口 |
| [ADR-021-统一抓包生命周期入口.md](ADR-021-统一抓包生命周期入口.md) | 禁止恢复分散抓包控制、模拟网络事实和非幂等网络日志投影的权威决策 |
| [统一日志管理设计.md](统一日志管理设计.md) | LogManager 批量下载、删除、解析、文件可见性和会话可见性统一设计 |
| [ADR-023-LogManager统一批量管理与会话可见性.md](ADR-023-LogManager统一批量管理与会话可见性.md) | 固定唯一 LogManager、批量结果和父子可见性门控规则 |
| [ADR-027-自定义日志根目录索引隔离.md](ADR-027-自定义日志根目录索引隔离.md) | 固定非默认日志根独立资源索引，防止可见性和日志列表跨根污染 |
| [通信与网络仿真设计.md](通信与网络仿真设计.md) | A2A CommManager、通信矩阵、Linux `tc`、真实抓包 |
| [ADR-024-CommManager统一A2A通信与禁止广播.md](ADR-024-CommManager统一A2A通信与禁止广播.md) | 固定统一 A2A 管理入口、点对点通信、禁止广播、顺序多目标发送和防回退规则 |
| [ADR-025-Agent任务下发与A2A回调.md](ADR-025-Agent任务下发与A2A回调.md) | 固定 Agent/仿真编排任务下发、持久化状态机、Push 回调认证、查询与取消规则 |
| [日志与实验数据设计.md](日志与实验数据设计.md) | `application.jsonl`、`network.jsonl`、`system.jsonl`、PCAP、实验清单与质量审计 |
| [实时展示设计.md](实时展示设计.md) | 实时日志、Agent 拓扑、通信流、仿真进度、Token 累计图和剧本自定义面板 |
| [AgentTrafficCapture.md](AgentTrafficCapture.md) | Agent runtime 真实抓包、分析 API 与端到端验收 |
| [AgentRuntimeBoundary.md](AgentRuntimeBoundary.md) | Skill 包、Tool、MCP 与后端适配器边界 |
| [OpenCLAWDeployment.md](OpenCLAWDeployment.md) | OpenCLAW 严格部署与验收 |
| [开发文档.md](开发文档.md) | 代码模块、API、开发与验证流程 |
| [设计决策与变更规则.md](设计决策与变更规则.md) | 不应被无意改回的设计决策和变更规则 |

## 当前权威实现入口

- 控制面：`services/server.py`
- 统一仿真管理包：`agent_network/simulation_management/`
- 仿真生命周期管理：`agent_network/simulation_management/simulation_manager.py`
- 仿真事件调度：`agent_network/simulation_management/event_scheduler.py`
- 仿真 HTTP 适配：`agent_network/api/managed_simulations.py`
- 统一剧本管理包：`agent_network/scene_management/`
- 统一剧本业务管理：`agent_network/scene_management/scene_manager.py`
- 单剧本存储与解析：`agent_network/scene_management/scene_storage.py`
- 剧本领域模型与严格校验：`agent_network/scene_management/models.py`、`agent_network/scene_management/validator.py`
- Agent 容器运行时：`services/agent_server.py`
- 容器分配与 Agent 执行：`agent_network/agent_management.py`
- Agent 统一 A2A 通信：`agent_network/comm_management/comm_manager.py`
- Agent Task 生命周期与回调：`agent_network/task_management/`
- 系统、场景 Tool 与受限 Skill 源 MCP：`agent_network/mcp_server.py`
- 统一文件管理基础设施：`agent_network/file_management/`
- 统一抓包管理：`agent_network/capture_management/`
- 抓包外部 API：`agent_network/api/captures.py`
- 统一日志管理包：`agent_network/log_management/`
- 日志模式、写入与实时索引：`agent_network/log_management/log_manager.py`
- 日志批量管理与会话可见性：`agent_network/log_management/log_batch.py`
- 日志批量能力安装适配：`agent_network/log_management/log_batch_install.py`
- 日志批量外部 API：`agent_network/api/log_batches.py`
- PCAP 内部解码分析：`agent_network/capture_management/packet_store.py`
- 网络仿真：`agent_network/comm_management/network_emulation.py`
- 仿真控制面共享状态：`agent_network/simulation_management/state.py`
- 实验可追溯性与质量审计：`agent_network/experiment_manifest.py`

## 文档维护原则

1. [系统需求规格.md](系统需求规格.md) 是 SR 权威基线；详细设计不得弱化或改写其含义。
2. 设计文档描述当前实现或明确标注为“设计阶段”的目标设计，不记录未确认的设想。
3. 早期开发阶段不保留无需求的兼容层；删除旧设计时，同时删除对应文档表述。
4. 影响职责边界、日志模式、通信链路、运行时协议或持久化格式的变更，必须更新本目录。
5. `scenes/` 中的文档只在明确修改场景编写规范时更新。
6. Agent 通信入口、协议、权限或目标选择变化时更新 ADR-024；Task 生命周期、回调或持久化变化时更新 ADR-025，并同步通信测试。
7. 修改任何 SR 时，必须同步更新系统需求规格、关联领域设计、接口/数据模型和验收测试计划。
