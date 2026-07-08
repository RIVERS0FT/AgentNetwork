import os
import math
import threading
import asyncio
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, Set

# ── 服务发现 ──
MESSAGE_BUS_URL = os.environ.get("MESSAGE_BUS_URL", "http://bus:9000")

# ── WebSocket 连接池 ──
ws_clients: set = set()
server_loop: Optional[asyncio.AbstractEventLoop] = None

# ── 统一 Agent 日志缓冲区 ──
agent_logs: List[Dict[str, Any]] = []

# ── 全局服务状态 ──
service_state = {
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "simulations_run": 0,
    "active_engine": None,
}

simulation_stop_requested = False
simulation_active = False

current_topology: List[Dict[str, Any]] = []
current_turn = 0
current_scene_name = ""
current_max_rounds = 20
termination_config: Dict[str, int] = {"max_rounds": 10, "stalemate_rounds": 3}

active_tools_module = None

# ── Token Usage State & Logic ──
def _new_token_usage_state(session_id: str = "") -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "points": [],
        "seen_keys": set(),
        "totals": {
            "hit": 0,
            "miss": 0,
            "prompt": 0,
            "completion": 0,
            "total": 0,
            "provider_total": 0,
            "estimated_events": 0,
            "exact_events": 0,
            "events": 0,
            "mismatch_events": 0,
        },
        "last_event": None,
    }

token_usage_lock = threading.Lock()
token_usage_state: Dict[str, Any] = _new_token_usage_state()

def _token_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
        if not math.isfinite(n):
            return None
        return max(0, int(round(n)))
    except (TypeError, ValueError):
        return None

def _token_first(payload: Dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = _token_int(payload.get(key))
        if value is not None:
            return value
    return None

def _token_nested(payload: Dict[str, Any], parent: str, key: str) -> Optional[int]:
    nested = payload.get(parent)
    if not isinstance(nested, dict):
        return None
    return _token_int(nested.get(key))

def reset_token_usage_state(session_id: str = ""):
    global token_usage_state
    with token_usage_lock:
        token_usage_state = _new_token_usage_state(session_id)

def get_token_usage_snapshot() -> Dict[str, Any]:
    with token_usage_lock:
        return {
            "session_id": token_usage_state.get("session_id", ""),
            "points": [dict(p) for p in token_usage_state.get("points", [])],
            "totals": dict(token_usage_state.get("totals", {})),
            "last_event": dict(token_usage_state["last_event"]) if token_usage_state.get("last_event") else None,
        }

def _extract_token_usage_delta(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if record.get("event") not in {"llm_api_call", "llm_cli_call"}:
        return None
    payload = record.get("payload") or {}
    target = record.get("target") or {}
    provider = str(target.get("provider") or payload.get("provider") or "").lower()
    completion = _token_first(payload, "completion_tokens", "output_tokens") or 0
    provider_total = _token_first(payload, "total_tokens")
    estimated = False

    prompt = _token_first(payload, "prompt_tokens", "input_tokens")
    hit = 0
    miss = 0

    anthropic_cache_hit = _token_first(payload, "cache_read_input_tokens")
    anthropic_cache_create = _token_first(payload, "cache_creation_input_tokens")
    if provider == "anthropic" or anthropic_cache_hit is not None or anthropic_cache_create is not None:
        hit = anthropic_cache_hit
        if hit is None:
            hit = _token_first(payload, "prompt_cache_hit_tokens") or 0
        cache_create = anthropic_cache_create
        if cache_create is None:
            cache_create = _token_first(payload, "prompt_cache_miss_tokens") or 0
        input_tokens = _token_first(payload, "input_tokens") or 0
        miss = cache_create + input_tokens
        prompt = hit + miss
        estimated = bool(payload.get("estimated"))
    else:
        openai_cached = _token_nested(payload, "prompt_tokens_details", "cached_tokens")
        if openai_cached is not None:
            hit = openai_cached
            prompt = prompt if prompt is not None else hit
            miss = max(0, prompt - hit)
            estimated = bool(payload.get("estimated"))
        elif (
            payload.get("prompt_cache_hit_tokens") is not None
            or payload.get("prompt_cache_miss_tokens") is not None
        ):
            hit = _token_first(payload, "prompt_cache_hit_tokens") or 0
            miss = _token_first(payload, "prompt_cache_miss_tokens") or 0
            prompt = prompt if prompt is not None else hit + miss
            estimated = bool(payload.get("estimated"))
        elif prompt is not None:
            hit = 0
            miss = prompt
            estimated = True
        elif completion <= 0:
            return None
        else:
            prompt = 0
            estimated = True

    if prompt is None:
        prompt = hit + miss
    total = hit + miss + completion
    if total <= 0 and (provider_total is None or provider_total <= 0):
        return None
    if total <= 0:
        hit = 0
        miss = provider_total or 0
        prompt = miss
        total = miss + completion
        estimated = True
    total_mismatch = provider_total is not None and provider_total != total

    return {
        "hit": hit,
        "miss": miss,
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "provider_total": provider_total,
        "total_mismatch": total_mismatch,
        "estimated": estimated,
    }

def append_token_usage_record(record: Dict[str, Any]) -> bool:
    delta = _extract_token_usage_delta(record)
    if not delta:
        return False

    event_id = str(record.get("event_id") or "")
    actor = (record.get("actor") or {}).get("agent_id") or (record.get("actor") or {}).get("id", "")
    target = record.get("target") or {}
    payload = record.get("payload") or {}
    key = event_id or "|".join([
        record.get("timestamp") or "",
        record.get("event") or "",
        actor,
        str(target.get("provider") or ""),
        str(target.get("model") or ""),
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ])

    with token_usage_lock:
        if key in token_usage_state["seen_keys"]:
            return False
        token_usage_state["seen_keys"].add(key)
        # 防止 seen_keys 无限增长（长时间仿真内存泄漏）
        if len(token_usage_state["seen_keys"]) > 5000:
            _iter = iter(token_usage_state["seen_keys"])
            to_remove = [next(_iter) for _ in range(2500)]
            token_usage_state["seen_keys"].difference_update(to_remove)
        totals = token_usage_state["totals"]
        totals["hit"] += delta["hit"]
        totals["miss"] += delta["miss"]
        totals["prompt"] += delta["prompt"]
        totals["completion"] += delta["completion"]
        totals["total"] += delta["total"]
        if delta.get("provider_total") is not None:
            totals["provider_total"] += delta["provider_total"]
        totals["events"] += 1
        if delta.get("total_mismatch"):
            totals["mismatch_events"] += 1
        if delta["estimated"]:
            totals["estimated_events"] += 1
        else:
            totals["exact_events"] += 1

        point = {
            "key": key,
            "event_id": event_id,
            "timestamp": record.get("timestamp") or datetime.now().isoformat(timespec="milliseconds"),
            "hit": totals["hit"],
            "miss": totals["miss"],
            "prompt": totals["prompt"],
            "completion": totals["completion"],
            "total": totals["total"],
            "delta_hit": delta["hit"],
            "delta_miss": delta["miss"],
            "delta_prompt": delta["prompt"],
            "delta_completion": delta["completion"],
            "delta_total": delta["total"],
            "provider_total": totals["provider_total"],
            "delta_provider_total": delta.get("provider_total"),
            "total_mismatch": delta.get("total_mismatch", False),
            "estimated": delta["estimated"],
            "actor": actor,
            "provider": target.get("provider", ""),
            "model": target.get("model", ""),
        }
        token_usage_state["points"].append(point)
        token_usage_state["last_event"] = point
        return True
