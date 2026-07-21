# ADR-006：逻辑 Agent 与容器槽位分离

> 状态：已接受

## 决定

容器可复用，逻辑身份由每次 `/run` 上下文决定。

## 影响

- 日志顶层 `agent_id` 必须使用逻辑身份；
- OpenCLAW session 由逻辑 Agent 和 trace 隔离；
- 分配前必须 reset；
- 容器环境中的静态 ID 仅用于槽位自描述。
