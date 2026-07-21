# OpenCLAW 后端部署与验收

## 1. 严格运行语义

`AGENT_BACKEND=openclaw` 表示容器内必须真实运行 OpenCLAW Gateway 和 `openclaw-sdk`。SDK 不可导入、Gateway 无法启动或端口未就绪时，Agent runtime 必须失败，不能静默退化为 direct LLM。

只想直接调用模型时，应显式选择独立的 `direct_llm` 模式；当前场景加载器不接受它作为场景 Agent 后端。

## 2. 容器内启动链路

```text
ag-o1 container
  -> /app/start-openclaw-agent.sh
     -> 校验 openclaw-sdk
     -> 启动本地 OpenCLAW Gateway
     -> 等待 Gateway 端口
     -> 启动 services/agent_server.py
        -> OpenCLAWAdapter
        -> OpenClawClient.connect()
```

Agent-to-Agent 消息由 CommManager 使用 A2A 1.0 点对点发送到目标容器，不依赖 message bus 服务，也不提供广播。

## 3. 构建前置条件

### Gateway npm 包

Compose 默认：

```bash
OPENCLAW_GATEWAY_NPM_PACKAGE=openclaw@2026.7.1-2
```

若真实包名不同：

```bash
export OPENCLAW_GATEWAY_NPM_PACKAGE='<real-openclaw-gateway-package>'
```

若安装后没有 `openclaw gateway` 命令，显式设置：

```bash
export OPENCLAW_GATEWAY_CMD='<gateway start command --host 127.0.0.1 --port 18789>'
```

### Python SDK

镜像构建通过 PyPI 精确安装：

```bash
OPENCLAW_SDK_VERSION=2.1.0
```

不得恢复未固定版本或本地 wheel 通配符安装。

### 原生能力审计

镜像内置并强制加载 `/app/docker/openclaw.json` 和 `/app/docker/openclaw-audit-plugin/`。容器内保持与仓库相同的 `docker/` 相对结构，因此同一配置可由仓库和容器内 OpenClaw CLI 校验。必须配置：

```bash
NATIVE_AUDIT_TOKEN='<random-internal-token>'
NATIVE_AUDIT_REQUIRED=1
```

Plugin 的 `before_tool_call` 调用 Agent Server 的 `/internal/native/policy/check`；Token 缺失、错误、接口不可达或策略拒绝时 Tool 调用被阻止。`after_tool_call`、`subagent_spawned`、`subagent_ended` 写入 Tool 结果和子 Agent 生命周期。

OpenClaw 原生 Agent-to-Agent 能力在 `openclaw.json` 中关闭。AgentNetwork Agent 消息仍必须通过 MCP `send_message` 和 `CommManager`。

## 4. 启动

```bash
docker compose build ag-o1
docker compose up -d srv ag-o1
docker compose logs -f ag-o1
```

期望日志：

```text
[openclaw-agent] openclaw-sdk import ok
[openclaw-agent] starting OpenCLAW gateway: ...
[openclaw-agent] gateway port is open
[openclaw-agent] starting AgentNetwork server...
```

## 5. 一键验收

```bash
chmod +x scripts/check-openclaw-runtime.sh
./scripts/check-openclaw-runtime.sh ag-o1
```

通过标记：

```text
[check-openclaw] OK: strict OpenCLAW runtime checks passed.
```

## 6. 手动验收

### 容器与后端

```bash
docker compose ps ag-o1
docker compose exec -T ag-o1 sh -lc \
  'echo AGENT_BACKEND=$AGENT_BACKEND; echo AGENT_STRICT_BACKEND_SDK=$AGENT_STRICT_BACKEND_SDK'
```

期望：

```text
AGENT_BACKEND=openclaw
AGENT_STRICT_BACKEND_SDK=1
```

### Gateway 命令

```bash
docker compose exec -T ag-o1 sh -lc \
  'command -v openclaw || echo "$OPENCLAW_GATEWAY_CMD"'
```

### SDK 导入

```bash
docker compose exec -T ag-o1 python3 - <<'PY'
from openclaw_sdk import OpenClawClient
print("openclaw_sdk import ok")
print(OpenClawClient)
PY
```

### Gateway 端口

```bash
docker compose exec -T ag-o1 python3 - <<'PY'
import os
import socket

host = os.environ.get("OPENCLAW_GATEWAY_HOST", "127.0.0.1")
port = int(os.environ.get("OPENCLAW_GATEWAY_PORT", "18789"))
with socket.create_connection((host, port), timeout=3):
    print(f"gateway port open: {host}:{port}")
PY
```

### Agent HTTP 状态

```bash
docker compose exec -T ag-o1 python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=3) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
PY
```

正常仿真由 `srv` 在 Docker 网络内访问 Agent `/status` 和 `/run`。状态 JSON 中应包含：

```json
{
  "backend": "openclaw"
}
```

### 原生审计接口

使用与 Gateway Plugin 相同的 Bearer Token 调用 `/internal/native/policy/check`。有效 Token 和允许的 `read` 应返回 `allowed=true`、`capability=fs.read` 与 `policy_sha256`；去掉 Authorization 后必须返回 401。

### 禁止 fallback

```bash
! docker compose logs --no-color ag-o1 \
  | grep -Ei 'openclaw-direct-llm|direct_llm|fallback'
```

## 7. Session 与 Skill

- OpenCLAW Agent ID 优先使用 `OPENCLAW_AGENT_ID` 或 `OPENCLAW_DEFAULT_AGENT_ID`，否则使用逻辑 Agent ID。
- session 名由 `OPENCLAW_SESSION_PREFIX`、逻辑 Agent ID 和 trace ID 组成，避免复用容器时串话。
- 原生子 Agent 与父 Agent 共享容器，但使用独立 child session；默认最多 3 个、并行 2 个、深度 1。
- 原生子 Agent 不得使用 `sessions_send` 或 channel message 绕过 CommManager。
- `scenes/` 以只读方式挂载到 `/app/scenes`。
- 后端只能读取 `/run` 中 `skill_refs` 允许的 Skill；目录型 Skill 入口为 `SKILL.md`，单文件入口为 `<skill_ref>.md`。
- Adapter 只传递 Skill 访问范围，不读取或注入 Skill 正文。

## 8. 常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| 构建提示 Gateway 包缺失 | `OPENCLAW_GATEWAY_NPM_PACKAGE` 无效 | 设置真实 npm 包后重建 |
| 构建提示 SDK 安装失败 | PyPI/镜像不可达或版本不存在 | 检查网络并确认 `OPENCLAW_SDK_VERSION=2.1.0` |
| 启动提示无 Gateway 命令 | npm 包未提供 CLI | 设置 `OPENCLAW_GATEWAY_CMD` |
| Gateway 未在超时内就绪 | 命令、端口、API key 或依赖错误 | 查看 `docker compose logs ag-o1` |
| Adapter 返回 SDK 不可导入 | 镜像构建或 wheel 错误 | 重新构建并执行手动导入检查 |
| 不同逻辑 Agent 串 session | session 前缀或 trace 传递错误 | 检查 `OPENCLAW_SESSION_PREFIX` 与 `/run` trace |
| 原生 Tool 全部被拒绝 | `NATIVE_AUDIT_TOKEN` 缺失/不一致、Plugin 未加载或策略拒绝 | 检查 Gateway 日志、Token 和 application `policy_check` |
| 实验质量提示子 Agent 未结束 | Gateway 未发送 `subagent_ended` 或停止流程未完成 | 检查 `subagent_lifecycle` 并修复终态上报 |

## 9. 设计约束

- OpenCLAW 缺失必须显式失败。
- 不增加自动 direct LLM fallback。
- 不恢复中心消息总线依赖。
- 不允许 OpenClaw 原生 A2A、`sessions_send` 或 channel message 绕过 `CommManager`。
- 审计 Plugin、Token 或内部接口失败时必须失败关闭。
- Gateway 与 SDK 使用 `openclaw@2026.7.1-2` 和 `openclaw-sdk==2.1.0`，不得使用 latest。
- 修改 Gateway、SDK、session 或 Skill 访问方式时，同步更新 `AgentRuntimeBoundary.md` 和本文件。
