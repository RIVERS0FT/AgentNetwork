import json
import logging
import os
import asyncio
import time
from .base import BackendAdapter, AgentContext, AgentRunResult
from agent_network.skill_md_loader import load_scene_skill_registry

try:
    from openclaw_sdk import OpenClawClient
except ImportError:
    OpenClawClient = None

logger = logging.getLogger(__name__)




def _skill_context(agent_context: AgentContext) -> list[dict]:
    scene_key = agent_context.scene_key or os.environ.get("AGENT_SCENE_KEY", "default")
    scenes_root = os.environ.get("AGENT_SCENES_ROOT", "/app/scenes")
    registry = load_scene_skill_registry(scene_key=scene_key, scenes_root=scenes_root, skill_refs=agent_context.skill_refs)
    specs = registry.context_specs()
    return specs


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
        "agent_directory": agent_context.agent_directory,
        "comm_matrix": agent_context.comm_matrix,
        "network_mode": "direct",
        "simulation_seed": agent_context.simulation_seed,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _system_prompt(agent_context: AgentContext) -> str:
    return (
        f"You are {agent_context.agent_name} ({agent_context.agent_id}).\n"
        f"Role: {agent_context.role}\n"
        f"Core Goal: {agent_context.core_goal}\n"
        f"Trace ID: {agent_context.trace_id}\n"
        "AgentNetwork uses direct Agent-to-Agent HTTP messaging. Skill.md content is SOP/context."
    )


def _completed_event(agent_context: AgentContext, output_text: str, backend_name: str) -> dict:
    return {
        "event": "agent_run_completed",
        "trace_id": agent_context.trace_id,
        "actor": {"agent_id": agent_context.agent_id, "name": agent_context.agent_name, "role": agent_context.role, "backend": backend_name},
        "task": {"goal": agent_context.task, "status": "completed"},
        "action": {"type": "agent_run", "name": f"{backend_name}_run", "status": "success"},
        "content": {"content_type": "final_message", "text": output_text, "summary": output_text[:200], "size_bytes": len(output_text.encode("utf-8"))},
        "result": {"status": "success", "message": "agent run completed"},
        "metrics": {"backend": backend_name},
    }


def _openclaw_agent_id(agent_context: AgentContext) -> str:
    return os.environ.get("OPENCLAW_AGENT_ID") or os.environ.get("OPENCLAW_DEFAULT_AGENT_ID") or agent_context.agent_id


def _openclaw_session_name(agent_context: AgentContext) -> str:
    return os.environ.get("OPENCLAW_SESSION_NAME") or agent_context.trace_id or "main"


def _extract_openclaw_text(result) -> str:
    content = getattr(result, "content", None)
    if content is not None:
        return str(content)
    data = getattr(result, "data", None)
    if data is not None:
        return str(data)
    return str(result) if result is not None else ""


class OpenCLAWAdapter(BackendAdapter):
    def run_agent_task(self, agent_context: AgentContext) -> AgentRunResult:
        if os.environ.get("MOCK_LLM") == "1":
            output_text = "[MOCK_LLM] Dummy response from OpenCLAW"
            return AgentRunResult(trace_id=agent_context.trace_id, agent_id=agent_context.agent_id, status="completed", final_message=output_text, application_events=[_completed_event(agent_context, output_text, "openclaw")])

        system_prompt = _system_prompt(agent_context)
        current_task = _build_task_payload(agent_context)
        prompt = f"{system_prompt}\n\nAgentNetwork task payload:\n{current_task}"

        if not OpenClawClient:
            return AgentRunResult(trace_id=agent_context.trace_id, agent_id=agent_context.agent_id, status="error", final_message="", error="openclaw-sdk is not installed or not importable.")

        try:
            started = time.monotonic()
            async def _run():
                gateway_url = os.environ.get("OPENCLAW_GATEWAY_WS_URL", "")
                logger.info("Connecting to OpenCLAW gateway: %s", gateway_url or "SDK default")
                async with OpenClawClient.connect() as client:
                    agent = client.get_agent(_openclaw_agent_id(agent_context), session_name=_openclaw_session_name(agent_context))
                    return await agent.execute(prompt)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(_run())
            finally:
                loop.close()

            output_text = _extract_openclaw_text(response)
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            runtime_event = {
                "event": "llm_runtime_completed",
                "trace_id": agent_context.trace_id,
                "actor": {"agent_id": agent_context.agent_id, "backend": "openclaw-sdk"},
                "action": {
                    "type": "llm_call",
                    "name": "openclaw_agent_execute",
                    "status": "success",
                    "duration_ms": duration_ms,
                },
                "result": {"status": "success"},
                "metrics": {
                    "duration_ms": duration_ms,
                    "session_id": _openclaw_session_name(agent_context),
                    "gateway_url": os.environ.get("OPENCLAW_GATEWAY_WS_URL", ""),
                },
            }
            return AgentRunResult(trace_id=agent_context.trace_id, agent_id=agent_context.agent_id, status="completed", final_message=output_text, application_events=[runtime_event, _completed_event(agent_context, output_text, "openclaw-sdk")])
        except Exception as e:
            return AgentRunResult(trace_id=agent_context.trace_id, agent_id=agent_context.agent_id, status="error", final_message="", error=f"OpenCLAW SDK Error: {str(e)}")
