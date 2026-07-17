# ADR-028：AgentNetwork 领域模块收敛

- 状态：已接受
- 日期：2026-07-17
- 关联：ADR-021、ADR-024、ADR-026、AgentRuntimeBoundary

## 背景

`agent_network/` 顶层曾同时保留抓包、PCAP 查询、网络仿真、Skill MCP、Skill 文件读取、进程状态和旧本地 Tool Runtime。领域实现与统一管理包并存，造成入口重复和职责归属不清。

## 决策

1. 抓包 HTTP 适配器移入 `capture_management/http_adapter.py`；
2. PCAP 注册、解码、查询和统计移入 `capture_management/packet_store.py`；
3. 延迟、抖动、丢包和带宽配置移入 `comm_management/network_emulation.py`；
4. 控制面共享仿真状态移入 `simulation_management/state.py`；
5. 默认模型常量由 `scene_management/scene_def.py` 就地持有，不保留单字段配置模块；
6. Skill MCP 工具和安全 Skill 文件读取合并到 `mcp_server.py`；只有显式使用 `--skill-source-mode` 时才注册 Skill 读取工具，`--skill-refs` 继续执行 Agent 级授权；
7. 删除未进入生产链路的 `skill_md_loader.py` 和 `tool_runtime.py`；
8. 删除所有旧顶层兼容文件，不提供导入转发。

## 删除入口

```text
agent_network/config.py
agent_network/full_packet_capture.py
agent_network/network_emulation.py
agent_network/real_packet_store.py
agent_network/skill_mcp_server.py
agent_network/skill_md_loader.py
agent_network/skill_source.py
agent_network/state.py
agent_network/tool_runtime.py
```

此前删除的 `agent_network/comm.py` 和 `agent_network/event_bus.py` 同样不得恢复。

## 入口迁移

| 旧入口 | 新入口 |
|---|---|
| `agent_network.full_packet_capture` | `agent_network.capture_management` |
| `agent_network.real_packet_store` | `agent_network.capture_management.packet_store` |
| `agent_network.network_emulation` | `agent_network.comm_management` |
| `agent_network.state` | `agent_network.simulation_management.state` |
| `agent_network.skill_mcp_server` | `python -m agent_network.mcp_server --skill-source-mode` |
| `agent_network.skill_source` | `agent_network.mcp_server` 内部安全读取函数 |

## 禁止回退

- 不得在 `agent_network/` 顶层恢复上述同名文件或导入兼容层；
- 不得绕过 `CaptureManager`/`CaptureRuntime` 新增抓包生命周期入口；
- 不得在 `comm_management` 之外维护第二套网络条件配置；
- 不得启动独立 Skill MCP 模块或绕过 `--skill-refs`；
- 不得恢复 `LocalToolRuntime` 或 Markdown Skill 本地注册表作为生产执行链路；
- 后续进一步拆分必须同步更新本 ADR、模块布局测试和相关领域设计。
