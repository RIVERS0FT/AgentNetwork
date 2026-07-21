# ADR-003：网络事实只来自真实 PCAP

> 状态：已接受

## 决定

Agent 容器内 `tcpdump` 是网络证据来源，`network.jsonl` 是真实 packet 的结构化表示。

## 禁止回退

- 不从 HTTP middleware、Tool 事件或 Agent 消息构造 network record；
- 不把应用层 latency/status 伪装成 TCP/IP 字段；
- 不在没有 PCAP 证据时声称存在数据包。
