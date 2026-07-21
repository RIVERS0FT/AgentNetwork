# ADR-001：Agent 使用点对点 A2A 通信

> 状态：已接受；由 ADR-024、ADR-025 扩展

## 决定

Agent 间消息统一由 `CommManager` 使用 A2A 1.0 HTTP+JSON 绑定直接发送到目标 Agent，不经过中心 relay。

## 原因

通信路径与真实网络抓包一致，减少中心代理对时延、流量和故障的干扰。

## 禁止回退

- 不把 `services/message_bus.py` 恢复为默认 relay；
- 不让 MCP `send_message` 调用中心 `/relay`；
- 不通过名称模糊匹配目标；
- 不增加广播工具；多个目标必须按顺序逐个发送。

详细实现合同见 [ADR-024](ADR-024-CommManager统一A2A通信与禁止广播.md)，任务生命周期和 Push 回调见 [ADR-025](ADR-025-Agent任务下发与A2A回调.md)。
