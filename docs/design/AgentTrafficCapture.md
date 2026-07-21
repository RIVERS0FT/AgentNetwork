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
  capture.session.json
  <logical_agent_id>.pcap
  <logical_agent_id>.manifest.json
  experiment.manifest.json
  network.projection.json
```

- `capture.session.json`：统一抓包会话、目标状态、终止原因、投影状态和审计状态；
- capture manifest：逻辑 Agent 与容器身份、trace、过滤器、网络 profile、时间、状态、文件大小和 SHA-256；
- experiment manifest：seed、scene 文件哈希、Agent 镜像身份、脱敏 LLM 配置、持续时间、资源限制、事件统计、网络配置和停止原因；
- `network.projection.json`：PCAP 源指纹、投影数据包数和错误。

全部产物通过 `FileManager` 注册，不向外部接口暴露物理路径。

## 3. 抓包生命周期

1. `srv` 完成容器分配、资源限制和网络 profile 配置；
2. `CaptureManager` 创建统一抓包会话；
3. `CaptureManager` 并发调用每个 Agent `/capture/start`；
4. 每个 Agent 的 `CaptureRuntime` 在本地网络命名空间启动 `tcpdump`；
5. 所有 Agent 抓包成功后仿真才继续；
6. 任一 Agent 启动失败时，统一停止已成功启动的目标；
7. 运行期间通过 `CaptureManager.check_health` 聚合状态；
8. 仿真达到持续时间、空闲完成、任务完成、用户停止或异常终止时统一停止；
9. 停止后刷新 PCAP 大小和 SHA-256；
10. 按 PCAP 源指纹幂等投影 `network.jsonl`；
11. 执行 session quality audit。

`PCAP_MAX_BYTES` 默认每 Agent 1 GiB。`tcpdump` 异常退出、超过限制、Agent 失联或投影失败时，抓包会话进入 `INCOMPLETE`，仿真不得报告为完整实验。

抓包健康检查不依赖固定仿真轮次。

## 4. PCAP 到网络日志

PCAP 是原始网络事实，`network.jsonl` 是结构化投影。

每条投影记录至少保留：

- `capture_id`；
- `packet_index`；
- `observer_agent_id`；
- `pcap_resource_id`；
- PCAP 派生的时间、IP、端口、协议、方向、TCP flags 和长度；
- 原始解码行及其 SHA-256。

投影幂等键为：

```text
capture_id + observer_agent_id + pcap_sha256 + packet_index
```

相同 PCAP 源指纹不得重复写入 `network.jsonl`。

## 5. 消息与应用证据

初始任务在仿真启动时转换为调度事件。Agent 收到消息后，消息作为新的事件进入目标 Agent 的队列；同一逻辑 Agent 默认单任务串行，执行期间新到消息继续保留等待处理。

Agent 直连消息保留 source、target、channel 和 trace。发送端 MCP Tool 事件与接收端 `agent_message_received` 都写入 application 日志，并使用同一 trace。

应用证据应使用事件 ID、消息 ID、Tool call ID 和 trace 表达关联，不得使用固定执行批次编号或 tick 作为权威因果标识。

## 6. 可选网络条件

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

## 7. Capture API

活动外部入口统一为：

- `POST /api/captures`：创建抓包会话；
- `POST /api/captures/{capture_id}/start`：启动全部 Agent 抓包；
- `GET /api/captures/{capture_id}`：查询会话状态，可选刷新健康状态；
- `POST /api/captures/{capture_id}/stop`：停止、刷新并投影；
- `GET /api/captures/{capture_id}/artifacts`：查询受管产物；
- `GET /api/captures/{capture_id}/packets`：查询结构化 packet；
- `GET /api/captures/{capture_id}/stats`：PCAP record 和 byte 统计；
- `GET /api/captures/{capture_id}/analysis`：协议、方向、traffic class、端点和 flow 摘要；
- `GET /api/captures/{capture_id}/quality`：覆盖率、运行身份、应用事件和 SHA-256 审计；
- `GET /api/captures/{capture_id}/bundle`：离线分析 ZIP；
- `GET /api/captures/{capture_id}/agents/{agent_id}/pcap`：下载单 Agent 原始 PCAP。

旧 `/api/packets` 不再挂载到活动服务。

解码使用 UTC epoch 时间。同一个 Agent-to-Agent packet 可能在两端 PCAP 中各观察一次；聚合统计使用 `per_agent_observations` 语义，不把它伪装成全网唯一 packet 数。

## 8. Bundle 内容

离线 bundle 可包含：

- 原始 PCAP 和 capture manifests；
- capture session 与 experiment manifest；
- `application.jsonl`、`network.jsonl`、`system.jsonl`；
- `network.projection.json`；
- `quality.json`；
- `analysis.json`；
- `packets.sample.jsonl`；
- `SHA256SUMS.json`。

## 9. 端到端验收

Docker 服务运行后执行：

```bash
python scripts/verify_agent_traffic.py --scene ap_deployment --seed 1234
```

命令只有在仿真完成，且 Agent 覆盖、运行身份、非空 PCAP、application event、网络日志投影和 SHA-256 检查通过时才返回成功。

事件驱动迁移完成后，验收脚本必须支持持续时间和资源限制参数，并拒绝仍包含旧调度计数字段的目标 manifest。

## 10. 设计约束

- 禁止恢复模拟 PacketRecorder 写入；
- 禁止把合成流量字段计入真实 packet 总量；
- 抓包不完整的 session 不得报告为完整实验；
- `network.jsonl` 只能从 PCAP 投影生成；
- 网络统计必须注明观察口径；
- 跨层关联必须保留“时间窗口推断”说明；
- 抓包健康检查不得依赖固定执行批次边界；
- 达到仿真持续时间后必须停止继续派发事件并进入抓包收尾；
- 抓包文件的写入、下载、可见性和删除必须经过 `FileManager`。
