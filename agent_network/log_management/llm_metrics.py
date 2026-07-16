"""外部 LLM HTTP/SDK 调用元数据记录。

不记录 prompt 或 response 正文，只记录模型、耗时、状态与 Token 等元数据。
日志写入 application.jsonl，由 ``LOG_LLM_API=1`` 控制。

用法::

    from agent_network.log_management.llm_metrics import log_llm_call

    log_llm_call(
        provider="deepseek",
        model="deepseek-chat",
        method="POST",
        path="/v1/chat/completions",
        status="200",
        latency_ms=1234.5,
        prompt_chars=3500,
        response_chars=800,
        max_tokens=512,
        messages_count=1,
        component="ag-b1",
        agent_id="planner",
    )
"""

import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _send(record: dict):
    """将日志发送到 SERVER_URL/api/logs/ingest。"""

    server_url = os.environ.get("SERVER_URL", "http://srv:8000")
    try:
        import requests as requests_client

        requests_client.post(
            f"{server_url}/api/logs/ingest",
            json=record,
            timeout=2,
        )
    except Exception:
        # 指标日志不能影响 Agent 主执行链路。
        pass


def llm_api_enabled() -> bool:
    """返回是否启用 LLM 应用层指标日志。"""

    return os.environ.get("LOG_LLM_API", "0") == "1"


def _usage_number(usage: Dict[str, Any], key: str):
    value = usage.get(key)
    return value if isinstance(value, (int, float)) else None


def _usage_nested_number(
    usage: Dict[str, Any],
    parent: str,
    key: str,
):
    nested = usage.get(parent)
    if isinstance(nested, dict):
        value = nested.get(key)
        return value if isinstance(value, (int, float)) else None
    return None


def _first_number(*values):
    for value in values:
        if value is not None:
            return value
    return None


def log_llm_call(
    *,
    provider: str = "",
    model: str = "",
    method: str = "POST",
    path: str = "",
    host: str = "",
    status: str = "200",
    latency_ms: float = 0,
    prompt_chars: int = 0,
    response_chars: int = 0,
    max_tokens: int = 0,
    messages_count: int = 0,
    component: str = "unknown",
    agent_id: str = "",
    usage: Optional[Dict[str, Any]] = None,
    error: str = "",
):
    """记录一次外部 LLM HTTP/SDK 调用的元数据。"""

    if not llm_api_enabled():
        return

    usage = usage or {}
    prompt_cache_hit = _first_number(
        _usage_number(usage, "prompt_cache_hit_tokens"),
        _usage_nested_number(
            usage,
            "prompt_tokens_details",
            "cached_tokens",
        ),
        _usage_number(usage, "cache_read_input_tokens"),
    )
    prompt_cache_miss = _usage_number(usage, "prompt_cache_miss_tokens")
    cache_hit_ratio = None
    if isinstance(prompt_cache_hit, (int, float)) and isinstance(
        prompt_cache_miss,
        (int, float),
    ):
        cache_total = prompt_cache_hit + prompt_cache_miss
        if cache_total > 0:
            cache_hit_ratio = round(prompt_cache_hit / cache_total, 4)

    payload = {
        "component": component,
        "prompt_chars": prompt_chars,
        "messages_count": messages_count,
        "max_tokens": max_tokens,
        "response_chars": response_chars,
        "body_logged": False,
        "estimated": True,
    }
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        if usage.get(key) is not None:
            payload[key] = usage[key]
            payload["estimated"] = False
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        if isinstance(usage.get(key), dict):
            payload[key] = usage[key]
            payload["estimated"] = False
    if cache_hit_ratio is not None:
        payload["cache_hit_ratio"] = cache_hit_ratio

    failed = bool(error) or bool(status and not str(status).startswith("2"))
    result_status = "failed" if failed else "success"
    record = {
        "timestamp": _now_iso(),
        "event": "llm_api_call",
        "agent_id": str(agent_id or component or "unknown"),
        "target": {
            "provider": provider,
            "model": model,
            "host": host,
            "path": path,
        },
        "action": {
            "type": "llm_api_request",
            "name": method,
            "status": result_status,
        },
        "payload": payload,
        "result": {
            "status": result_status,
            "status_code": status,
            "error_message": error[:200] if error else "",
        },
        "metrics": {
            "duration_ms": round(latency_ms, 1),
            "external": True,
        },
    }
    threading.Thread(
        target=_send,
        args=(record,),
        daemon=True,
    ).start()


class LLMCallTracker:
    """包装一次 LLM API 调用，自动计时并记录元数据。"""

    def __init__(
        self,
        provider: str,
        model: str,
        method: str = "POST",
        path: str = "",
        host: str = "",
        component: str = "unknown",
        prompt_chars: int = 0,
        messages_count: int = 0,
        max_tokens: int = 0,
        agent_id: str = "",
    ):
        self.provider = provider
        self.model = model
        self.method = method
        self.path = path
        self.host = host
        self.component = component
        self.agent_id = agent_id
        self.prompt_chars = prompt_chars
        self.messages_count = messages_count
        self.max_tokens = max_tokens
        self.start = 0.0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        latency_ms = (time.time() - self.start) * 1000
        if exc_type:
            log_llm_call(
                provider=self.provider,
                model=self.model,
                method=self.method,
                path=self.path,
                host=self.host,
                status="0",
                latency_ms=latency_ms,
                prompt_chars=self.prompt_chars,
                max_tokens=self.max_tokens,
                messages_count=self.messages_count,
                component=self.component,
                agent_id=self.agent_id,
                error=f"{exc_type.__name__}: {exc_val}",
            )
        return False

    def ok(
        self,
        response_chars: int = 0,
        status: str = "200",
        usage: Optional[Dict[str, Any]] = None,
    ):
        latency_ms = (time.time() - self.start) * 1000
        log_llm_call(
            provider=self.provider,
            model=self.model,
            method=self.method,
            path=self.path,
            host=self.host,
            status=status,
            latency_ms=latency_ms,
            prompt_chars=self.prompt_chars,
            response_chars=response_chars,
            max_tokens=self.max_tokens,
            messages_count=self.messages_count,
            component=self.component,
            agent_id=self.agent_id,
            usage=usage,
        )
