# AgentNetwork 设计文档索引

本目录描述 **当前代码已经实现并要求继续保持的系统设计，以及明确标注为设计阶段的目标设计**。实现与文档冲突时，应先确认是否为有意变更；若是有意变更，代码与相关设计文档必须在同一变更中同步更新。

> `scenes/` 下的 README、数据字典和示例文档用于指导场景编写，不属于本次系统设计文档范围。除非需求明确指向场景规范，否则不要修改它们。

## 文档地图

| 文档 | 内容 |
|---|---|
| [设计文档.md](设计文档.md) | 系统目标、总体架构、数据流和核心边界 |
| [剧本管理设计.md](剧本管理设计.md) | 剧本上传、查询、下载、删除、预览的 SR、AR、模块、接口、逻辑模型和时序设计 |
| [剧本管理数据模型.md](剧本管理数据模型.md) | 剧本管理类、结构体、枚举及其具体字段、关系和当前实现映射 |
| [仿真编排与容器运行时设计.md](仿真编排与容器运行时设计.md) | setup/launch、轮次调度、容器池、终止与失败处理 |
| [通信与网络仿真设计.md](通信与网络仿真设计.md) | DirectBus、通信矩阵、Linux `tc`、真实抓包 |
| [日志与实验数据设计.md](日志与实验数据设计.md) | `application.jsonl`、`network.jsonl`、`system.jsonl`、PCAP、实验清单与质量审计 |
| [AgentTrafficCapture.md](AgentTrafficCapture.md) | Agent runtime 真实抓包、分析 API 与端到端验收 |
| [AgentRuntimeBoundary.md](AgentRuntimeBoundary.md) | Skill 包、Tool、MCP 与后端适配器边界 |
| [OpenCLAWDeployment.md](OpenCLAWDeployment.md) | OpenCLAW 严格部署与验收 |
| [开发文档.md](开发文档.md) | 代码模块、API、开发与验证流程 |
| [设计决策与变更规则.md](设计决策与变更规则.md) | 不应被无意改回的设计决策和变更规则 |

## 当前权威实现入口

- 控制面：`services/server.py`
- 仿真编排：`agent_network/api/simulations.py`
- Agent 容器运行时：`services/agent_server.py`
- 容器分配与轮次执行：`agent_network/agent_management.py`
- Agent 直连通信：`agent_network/comm.py`
- Tool MCP：`agent_network/mcp_server.py`
- Skill 源文件 MCP：`agent_network/skill_mcp_server.py`
- 日志模式与持久化：`agent_network/log_manager.py`
- 真实抓包：`agent_network/full_packet_capture.py`
- PCAP 查询分析：`agent_network/real_packet_store.py`
- 网络仿真：`agent_network/network_emulation.py`
- 实验可追溯性与质量审计：`agent_network/experiment_manifest.py`

## 文档维护原则

1. 设计文档描述当前实现或明确标注为“设计阶段”的目标设计，不记录未确认的设想。
2. 早期开发阶段不保留无需求的兼容层；删除旧设计时，同时删除对应文档表述。
3. 影响职责边界、日志模式、通信链路、运行时协议或持久化格式的变更，必须更新本目录。
4. `scenes/` 中的文档只在明确修改场景编写规范时更新。