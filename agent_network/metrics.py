"""
Prometheus 监控指标模块 — 对应架构文档 第七节：Docker运行环境 / Runtime Metrics

提供统一的 Prometheus 指标注册中心，所有服务（server, message_bus, agent_server,
packet_monitor, log_collector）共用此模块暴露 /metrics 端点。

使用方式:
    from agent_network.metrics import MetricsRegistry
    metrics = MetricsRegistry()

    # 记录指标
    metrics.http_requests_total.labels(method="GET", endpoint="/api/agents", status="200").inc()
    metrics.message_throughput.labels(source="scout", target="commander", type="task").inc()

    # 挂载 /metrics 端点
    app.mount("/metrics", MetricsRegistry.get_metrics_app())

依赖: pip install prometheus_client
"""

from prometheus_client import Counter, Histogram, Gauge, Summary, generate_latest, CollectorRegistry
from prometheus_client import CONTENT_TYPE_LATEST
from typing import Optional
from starlette.requests import Request
from starlette.responses import Response


class MetricsRegistry:
    """
    Prometheus 指标注册中心（单例）

    覆盖架构文档中定义的 Runtime Metrics：
    - 请求计数 / 延迟
    - 活跃 Agent 数
    - 消息吞吐量 / 延迟
    - 错误计数
    - Agent 操作计数
    """

    _instance: Optional["MetricsRegistry"] = None
    _registry: Optional[CollectorRegistry] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._registry = CollectorRegistry()
            cls._instance._init_metrics()
        return cls._instance

    def _init_metrics(self):
        """初始化所有 Prometheus 指标"""
        r = self._registry

        # ── HTTP 请求 ──
        self.http_requests_total = Counter(
            "agentnetwork_http_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status"],
            registry=r,
        )
        self.http_request_duration_seconds = Histogram(
            "agentnetwork_http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "endpoint"],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=r,
        )

        # ── Agent 状态 ──
        self.active_agents = Gauge(
            "agentnetwork_active_agents",
            "Number of active agents",
            ["role"],
            registry=r,
        )
        self.agent_status = Gauge(
            "agentnetwork_agent_status",
            "Agent status (1=active, 0=inactive)",
            ["agent_id", "role", "status"],
            registry=r,
        )

        # ── 消息 ──
        self.message_throughput = Counter(
            "agentnetwork_message_throughput_total",
            "Total messages relayed",
            ["source", "target", "type"],
            registry=r,
        )
        self.message_latency_seconds = Histogram(
            "agentnetwork_message_latency_seconds",
            "Message relay latency in seconds",
            ["from_role", "to_role"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
            registry=r,
        )

        # ── 错误 ──
        self.error_total = Counter(
            "agentnetwork_error_total",
            "Total errors",
            ["service", "error_type"],
            registry=r,
        )

        # ── Agent 操作 ──
        self.agent_operations_total = Counter(
            "agentnetwork_agent_operations_total",
            "Total agent operations",
            ["agent_id", "operation", "status"],
            registry=r,
        )

        # ── 工具/技能调用 ──
        self.tool_calls_total = Counter(
            "agentnetwork_tool_calls_total",
            "Total tool calls",
            ["tool_name", "status"],
            registry=r,
        )
        self.tool_call_duration_seconds = Histogram(
            "agentnetwork_tool_call_duration_seconds",
            "Tool call duration in seconds",
            ["tool_name"],
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
            registry=r,
        )

        # ── 日志 ──
        self.log_entries_total = Counter(
            "agentnetwork_log_entries_total",
            "Total log entries",
            ["level_type", "level"],
            registry=r,
        )

        # ── 数据包 ──
        self.packet_total = Counter(
            "agentnetwork_packet_total",
            "Total packet records",
            ["direction", "message_type"],
            registry=r,
        )

        # ── 容器 ──
        self.container_cpu_percent = Gauge(
            "agentnetwork_container_cpu_percent",
            "Container CPU usage percentage",
            ["agent_id"],
            registry=r,
        )
        self.container_memory_mb = Gauge(
            "agentnetwork_container_memory_mb",
            "Container memory usage in MB",
            ["agent_id"],
            registry=r,
        )

    # ── 便捷方法 ────────────────────────────────

    def record_http(self, method: str, endpoint: str, status: int, duration: float):
        """记录一次 HTTP 请求"""
        self.http_requests_total.labels(method=method, endpoint=endpoint, status=str(status)).inc()
        self.http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)

    def record_message(self, source: str, target: str, msg_type: str, latency: float = 0):
        """记录一次消息转发"""
        self.message_throughput.labels(source=source, target=target, type=msg_type).inc()
        if latency > 0:
            self.message_latency_seconds.labels(from_role=source, to_role=target).observe(latency)

    def record_error(self, service: str, error_type: str):
        """记录一次错误"""
        self.error_total.labels(service=service, error_type=error_type).inc()

    def record_agent_operation(self, agent_id: str, operation: str, status: str):
        """记录一次 Agent 操作"""
        self.agent_operations_total.labels(agent_id=agent_id, operation=operation, status=status).inc()

    def record_tool_call(self, tool_name: str, status: str, duration: float = 0):
        """记录一次工具调用"""
        self.tool_calls_total.labels(tool_name=tool_name, status=status).inc()
        if duration > 0:
            self.tool_call_duration_seconds.labels(tool_name=tool_name).observe(duration)

    def record_log(self, level_type: str, level: str):
        """记录一条日志"""
        self.log_entries_total.labels(level_type=level_type, level=level).inc()

    def record_packet(self, direction: str, message_type: str):
        """记录一个数据包"""
        self.packet_total.labels(direction=direction, message_type=message_type).inc()

    def set_agent_active(self, agent_id: str, role: str, status: str):
        """更新 Agent 活跃状态"""
        self.agent_status.labels(agent_id=agent_id, role=role, status=status).set(
            1 if status in ("idle", "running") else 0
        )

    def set_container_resource(self, agent_id: str, cpu_percent: float, memory_mb: float):
        """更新容器资源使用"""
        self.container_cpu_percent.labels(agent_id=agent_id).set(cpu_percent)
        self.container_memory_mb.labels(agent_id=agent_id).set(memory_mb)

    # ── /metrics 端点 ───────────────────────────

    @classmethod
    def get_metrics_app(cls):
        """返回一个 ASGI 应用（用于 mount），已废弃，推荐用 add_metrics_route"""
        return _MetricsASGIApp(cls()._registry)

    @classmethod
    def add_metrics_route(cls, app):
        """在 FastAPI app 上添加 /metrics 端点（推荐方式，无重定向问题）"""
        async def metrics_endpoint(request):
            return Response(
                content=generate_latest(cls()._registry),
                media_type=CONTENT_TYPE_LATEST,
            )
        app.add_route("/metrics", metrics_endpoint, methods=["GET"])

    @classmethod
    def generate_metrics(cls) -> bytes:
        """生成 Prometheus 文本格式的指标数据"""
        return generate_latest(cls()._registry)

    @classmethod
    def reset(cls):
        """重置所有指标（测试用）"""
        if cls._instance and cls._instance._registry:
            cls._instance._registry = CollectorRegistry()
            cls._instance._init_metrics()


class _MetricsASGIApp:
    """轻量级 ASGI 应用（用于 mount），处理 /metrics 和 /metrics/"""

    def __init__(self, registry: CollectorRegistry):
        self.registry = registry

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)
            path = request.url.path.rstrip("/")
            if path == "/metrics" or path == "" or path.endswith("/metrics"):
                data = generate_latest(self.registry)
                response = Response(
                    content=data,
                    media_type=CONTENT_TYPE_LATEST,
                    status_code=200,
                )
            else:
                response = Response(content="Not Found", status_code=404)
            await response(scope, receive, send)
