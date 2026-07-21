# ADR-004：日志分为 application、network、system

> 状态：已接受

## 决定

- `application.jsonl`：Agent 行为和传统应用层日志；
- `network.jsonl`：原始网络层和传输层日志；
- `system.jsonl`：平台调试日志。

Schema 变更采用新版本，并一次性迁移 producer、consumer、测试和文档，不保留无需求的旧字段兼容。
