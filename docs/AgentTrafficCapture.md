# Agent 运行流量采集与验收

## 1. 事实来源

网络流量的权威来源是每个 Agent 容器网络命名空间内写出的 PCAP。`application.jsonl` 提供应用语义，场景或模型返回的合成流量字段不得混入真实 packet 统计。

默认抓包排除 `srv` 地址，因此 `/run`、抓包控制和日志回传不会污染 Agent runtime 测量；Agent-to-Agent、LLM、MCP、DNS 及响应流量保留。仅在控制面调试时设置：

```bash
AGENT_CAPTURE_INCLUDE_CONTROL_PLANE=1
```

## 2. Session 产物

```text
data/pcap/<session_id>/
  <logical_agent_id>.pcap
  <logical_agent_id>.manifest.json
  experiment.manifest.json
```

capture manifest 映射逻辑 Agent 与容器 ID、容器 IP、后端、trace、过滤器、网络 profile、时间、状态、文件大小和 SHA-256。

目标 experiment manifest 记录 seed、scene 文件哈希、Agent 镜像身份、脱敏 LLM 配置、`event_driven` 调度模式、持续时间、资源限制、事件统计、网络配置、抓包生命周期和停止原因。

## 3. 抓包生命周期

1. `srv` 完成容器分配、资源限制和网络 profile 配置；
2. 调用每个 Agent `/capture/start`；
3. 所有 Agent 抓包成功后才启动事件调度器；
4. 每次事件处理完成后以及健康检查周期内检查 `/capture/status`；
5. 仿真达到持续时间、空闲完成、任务完成、用户停止或异常终止时调用 `/capture/stop`；
6. 写入最终 manifest 和 SHA-256；
7. 执行 session quality audit。

`PCAP_MAX_BYTES` 默认每 Agent 1 GiB。tcpdump 异常退出或超过限制时，抓包健康失败，仿真以 `capture_incomplete` 或启动失败结束。

## 4. 消息与应用证据

初始任务在仿真启动时转换为调度事件。Agent 收到消息后，消息作为新的事件进入目标 Agent 的队列；同一逻辑 Agent 默认单任务串行，执行期间新到消息继续保留等待处理。

Agent 直连消息保留 source、target、channel 和 trace。发送端 MCP Tool 事件与接收端 `agent_message_received` 都写入 application 日志，并使用同一 trace。

应用证据应使用事件 ID、消息 ID、Tool call ID 和 trace 表达关联，不得使用固定执行批次编号或 tick 作为权威因果标识。

## 5. 可选网络条件

当前 topology 边直接携带网络参数：

```json
{
  "endpoint_a": "planner",
  "endpoint_b": "rf_engineer",
  "channel_id": "planner-rf",
  "delay_ms": 20,
  "jitter_ms": 5,
  "loss_pct": 0.5,
  "rate_mbit": 100
}
```

控制面会为两端分别配置出站 profile，因此该无向边形成近似对称链路。任一请求 profile 无法安装时，实验失败，不静默在不同网络条件下继续。

## 6. 分析 API

- `GET /api/packets/?session_id=...&agent_id=...`：最新结构化 packet；
- `GET /api/packets/stats?session_id=...`：PCAP record 和 byte 统计；
- `GET /api/packets/analysis?session_id=...`：协议、方向、traffic class、端点和 flow 摘要；
- `GET /api/packets/experiment?session_id=...`：实验 manifest；
- `GET /api/packets/quality?session_id=...`：覆盖率、运行身份、应用事件和 SHA-256 审计；
- `GET /api/packets/bundle?session_id=...`：离线分析 ZIP；
- `GET /api/packets/download?session_id=...&agent_id=...`：原始 PCAP；
- `GET /api/packets/correlate?session_id=...&trace_id=...`：应用事件与 packet 的时间窗口推断。

解码使用 UTC epoch 时间。关联结果不是协议级因果证明。

同一个 Agent-to-Agent packet 可能在两端 PCAP 中各观察一次；聚合统计使用 `per_agent_observations` 语义，不把它伪装成全网唯一 packet 数。

## 7. Bundle 内容

离线 bundle 可包含：

- 原始 PCAP 和 capture manifests；
- experiment manifest；
- `application.jsonl`、`network.jsonl`、`system.jsonl`；
- `quality.json`；
- `analysis.json`；
- `packets.sample.jsonl`；
- `SHA256SUMS.json`。

## 8. 端到端验收

Docker 服务运行后执行：

```bash
python scripts/verify_agent_traffic.py --scene ap_deployment --seed 1234
```

命令只有在仿真完成，且 Agent 覆盖、运行身份、非空 PCAP、application event、事件统计和 SHA-256 检查通过时才返回成功。

事件驱动迁移完成后，验收脚本必须支持持续时间和资源限制参数，并拒绝仍包含旧调度计数字段的目标 manifest。

## 9. 设计约束

- 禁止恢复模拟 PacketRecorder 写入；
- 禁止把合成流量字段计入真实 packet 总量；
- 抓包不完整的 session 不得报告为完整实验；
- 网络统计必须注明观察口径；
- 跨层关联必须保留“时间窗口推断”说明；
- 抓包健康检查不得依赖固定执行批次边界；
- 达到仿真持续时间后必须停止继续派发事件并进入抓包收尾。
