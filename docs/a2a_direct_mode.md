# A2A 直连通信模式

本项目的 agent 间数据面已经从 message bus 切换为直连模式。本方案将直连 HTTP 消息协议升级为最小 A2A `message/send` 兼容协议，同时保留仿真需要的拓扑、权限、trace 和流量观测能力。

## 通信路径

```text
Agent A DirectBus
  -> client-side policy check
  -> trace / channel metadata injection
  -> POST Agent B /a2a
  -> server-side policy check
  -> inbox append
  -> inbound / outbound network log ingest
```

## Agent 暴露接口

每个 agent 容器提供：

- `GET /.well-known/agent-card.json`：返回 Agent Card，用于发现 agent 的 `/a2a` 端点和能力。
- `GET /agent-card.json`：Agent Card 兼容别名。
- `POST /a2a`：A2A JSON-RPC `message/send` 数据面。
- `POST /message`：旧直连接口，仅作兼容入口；仍执行同一套服务端权限和网络日志逻辑，并在响应中提示改用 `/a2a`。

## 权限和拓扑

`comm_matrix` 不再由中心 bus 执行，而是在两端执行：

1. 调用方 `DirectBus.send()` 先做 client-side policy check。
2. 接收方 `/a2a` 再做 server-side policy check。

当 `comm_matrix` 为空时默认允许，用于本地调试和未初始化场景；仿真运行时由 `srv` 在每轮 `/run` 请求中下发完整 `comm_matrix`。

## Trace 和日志

A2A 消息会在 `metadata` 中携带：

- `from_agent`
- `from_name`
- `to_agent`
- `channel_id`
- `talk`
- `trace_id`
- `network_mode=direct`
- `protocol=a2a`

agent 两端都会向 `srv /api/logs/ingest` 写入 `agent_network` 层日志：

- `a2a_http_outbound`
- `a2a_http_inbound`

这些日志只描述应用层 A2A HTTP 事务；底层 pcap/tcpdump 仍由现有全量抓包能力负责。

## 最小 A2A 请求示例

```json
{
  "jsonrpc": "2.0",
  "id": "a2a_123",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "messageId": "msg_123",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "请检查这个代码修改"}
      ],
      "contextId": "talk_123",
      "metadata": {
        "from_agent": "dev_fe",
        "to_agent": "repo_admin",
        "trace_id": "talk_123",
        "channel_id": "ch_dev_fe_repo_admin",
        "protocol": "a2a",
        "network_mode": "direct"
      }
    }
  }
}
```
