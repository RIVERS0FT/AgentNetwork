import json
import os
import time
from typing import Optional

import requests

from .base import BackendAdapter, AgentContext, AgentRunResult
from agent_network.skill_md_loader import load_scene_skill_registry


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _provider() -> str:
    raw = _first_env("LLM_PROVIDER").lower()
    if raw:
        return raw
    if _first_env("ANTHROPIC_API_KEY") and not _first_env("LLM_API_BASE", "OPENAI_API_BASE"):
        return "anthropic"
    return "openai"


def _model() -> str:
    return _first_env("LLM_MODEL") or "deepseek-chat"


def _max_tokens() -> int:
    raw = _first_env("LLM_MAX_TOKENS", "MAX_TOKENS")
    try:
        return max(1, int(raw)) if raw else 2048
    except ValueError:
        return 2048


def _anthropic_base_url() -> Optional[str]:
    return _first_env("ANTHROPIC_BASE_URL") or None


def _openai_base_url() -> str:
    return (_first_env("LLM_API_BASE", "OPENAI_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")


def _extract_anthropic_text(response) -> str:
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _call_anthropic(system_prompt: str, user_payload: str) -> tuple[str, dict]:
    api_key = _first_env("ANTHROPIC_API_KEY", "LLM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Real LLM mode requires ANTHROPIC_API_KEY or LLM_API_KEY. "
            "Set MOCK_LLM=1 only when you intentionally want a mock run."
        )
    try:
        from anthropic import Anthropic
    except Exception as exc:  # pragma: no cover - dependency error path
        raise RuntimeError("anthropic package is not installed in this Agent image.") from exc

    kwargs = {"api_key": api_key}
    base_url = _anthropic_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    client = Anthropic(**kwargs)
    response = client.messages.create(
        model=_model(),
        max_tokens=_max_tokens(),
        system=system_prompt,
        messages=[{"role": "user", "content": user_payload}],
    )
    text = _extract_anthropic_text(response)
    usage = getattr(response, "usage", None)
    usage_data = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }
    return text or str(response), usage_data


def _call_openai_compatible(system_prompt: str, user_payload: str) -> tuple[str, dict]:
    api_key = _first_env("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Real LLM mode requires LLM_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY. "
            "Set MOCK_LLM=1 only when you intentionally want a mock run."
        )
    url = f"{_openai_base_url()}/chat/completions"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": _model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            "temperature": float(_first_env("LLM_TEMPERATURE") or 0.2),
            "max_tokens": _max_tokens(),
        },
        timeout=float(_first_env("LLM_TIMEOUT_SECONDS") or 120),
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"] or "", data.get("usage") or {}
    except Exception as exc:
        raise RuntimeError(f"Unexpected OpenAI-compatible response shape: {data}") from exc


def _completed_event(agent_context: AgentContext, output_text: str, backend_name: str) -> dict:
    return {
        "event": "agent_run_completed",
        "trace_id": agent_context.trace_id,
        "actor": {
            "agent_id": agent_context.agent_id,
            "name": agent_context.agent_name,
            "role": agent_context.role,
            "backend": backend_name,
        },
        "task": {"goal": agent_context.task, "status": "completed"},
        "action": {"type": "agent_run", "name": f"{backend_name}_run", "status": "success"},
        "content": {
            "content_type": "final_message",
            "text": output_text,
            "summary": output_text[:200],
            "size_bytes": len(output_text.encode("utf-8")),
        },
        "result": {"status": "success", "message": "agent run completed"},
        "metrics": {"backend": backend_name, "provider": _provider(), "model": _model()},
    }


def run_direct_llm(agent_context: AgentContext, backend_name: str, system_prompt: str, user_payload: str) -> AgentRunResult:
    """Run a real LLM call without requiring a framework-specific agent SDK.

    This backend does not expose MCP tools. Use AGENT_BACKEND=direct_llm only
    when you intentionally want a plain model call instead of OpenCLAW / Claude.
    """
    try:
        started = time.monotonic()
        provider = _provider()
        if provider in {"anthropic", "claude"}:
            output_text, usage = _call_anthropic(system_prompt, user_payload)
        else:
            output_text, usage = _call_openai_compatible(system_prompt, user_payload)
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        llm_event = {
            "event": "llm_runtime_completed",
            "trace_id": agent_context.trace_id,
            "actor": {"agent_id": agent_context.agent_id, "backend": backend_name},
            "action": {
                "type": "llm_call",
                "name": "direct_llm_request",
                "status": "success",
                "duration_ms": duration_ms,
            },
            "result": {"status": "success"},
            "metrics": {
                "provider": provider,
                "model": _model(),
                "duration_ms": duration_ms,
                "usage": usage,
            },
        }
        return AgentRunResult(
            trace_id=agent_context.trace_id,
            agent_id=agent_context.agent_id,
            status="completed",
            final_message=output_text,
            application_events=[llm_event, _completed_event(agent_context, output_text, backend_name)],
            tool_events=[],
            state_changes=[],
            outbound_messages=[],
            traffic_events=[],
            audit_events=[],
            error=None,
        )
    except Exception as exc:
        return AgentRunResult(
            trace_id=agent_context.trace_id,
            agent_id=agent_context.agent_id,
            status="error",
            final_message="",
            error=f"{backend_name} direct LLM error: {exc}",
        )




def _skill_context(agent_context: AgentContext) -> list[dict]:
    scene_key = agent_context.scene_key or os.environ.get("AGENT_SCENE_KEY", "default")
    scenes_root = os.environ.get("AGENT_SCENES_ROOT", "/app/scenes")
    registry = load_scene_skill_registry(
        scene_key=scene_key,
        scenes_root=scenes_root,
        skill_refs=agent_context.skill_refs,
    )
    specs = registry.context_specs()
    return specs


def _system_prompt(agent_context: AgentContext) -> str:
    return (
        f"You are {agent_context.agent_name} ({agent_context.agent_id}).\n"
        f"Role: {agent_context.role}\n"
        f"Core Goal: {agent_context.core_goal}\n"
        f"Trace ID: {agent_context.trace_id}\n"
        "This is the explicit direct_llm backend. No OpenCLAW Gateway, SDK, "
        "agent runtime, or MCP tool integration is available in this mode."
    )


def _build_task_payload(agent_context: AgentContext) -> str:
    payload = {
        "task": agent_context.task,
        "trace_id": agent_context.trace_id,
        "scene_key": agent_context.scene_key,
        "agent": {
            "agent_id": agent_context.agent_id,
            "name": agent_context.agent_name,
            "role": agent_context.role,
            "core_goal": agent_context.core_goal,
        },
        "messages": agent_context.messages,
        "skill_refs": agent_context.skill_refs,
        "skill_context": _skill_context(agent_context),
        "allowed_tools": agent_context.allowed_tools,
        "permissions": agent_context.permissions,
        "state_snapshot": agent_context.state_snapshot,
        "tick": agent_context.tick,
        "simulation_seed": agent_context.simulation_seed,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


class DirectLLMAdapter(BackendAdapter):
    def run_agent_task(self, agent_context: AgentContext) -> AgentRunResult:
        if os.environ.get("MOCK_LLM") == "1":
            output_text = "[MOCK_LLM] Dummy response from direct_llm"
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=[_completed_event(agent_context, output_text, "direct_llm")],
                tool_events=[],
                state_changes=[],
                outbound_messages=[],
                traffic_events=[],
                audit_events=[],
                error=None,
            )

        return run_direct_llm(
            agent_context,
            backend_name="direct_llm",
            system_prompt=_system_prompt(agent_context),
            user_payload=_build_task_payload(agent_context),
        )
