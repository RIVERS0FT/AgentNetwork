import asyncio
import json
import logging
import os
import time

from .base import AgentContext, AgentRunResult, BackendAdapter
from agent_network.native_audit import native_audit_state
from agent_network.native_capabilities import NativeCapabilityPolicy

try:
    from openclaw_sdk import OpenClawClient
except ImportError:
    OpenClawClient = None

logger = logging.getLogger(__name__)


def _skill_access(agent_context: AgentContext) -> dict:
    scene_key = (
        agent_context.scene_key
        or os.environ.get("AGENT_SCENE_KEY", "default")
    )
    scenes_root = os.environ.get("AGENT_SCENES_ROOT", "/app/scenes")
    return {
        "scene_key": scene_key,
        "skills_root": os.path.join(scenes_root, scene_key, "skills"),
        "allowed_refs": list(agent_context.skill_refs or []),
        "package_entrypoint": "<skill_ref>/SKILL.md",
        "single_file_entrypoint": "<skill_ref>.md",
    }


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
        "skill_access": _skill_access(agent_context),
        "allowed_tools": agent_context.allowed_tools,
        "native_capabilities": agent_context.native_capabilities,
        "permissions": agent_context.permissions,
        "state_snapshot": agent_context.state_snapshot,
        "simulation_id": agent_context.simulation_id,
        "event_id": agent_context.event_id,
        "event_sequence": agent_context.event_sequence,
        "event_type": agent_context.event_type,
        "agent_directory": agent_context.agent_directory,
        "comm_matrix": agent_context.comm_matrix,
        "network_mode": "a2a",
        "simulation_seed": agent_context.simulation_seed,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _system_prompt(agent_context: AgentContext) -> str:
    native_policy = NativeCapabilityPolicy.from_dict(
        agent_context.native_capabilities, backend="openclaw"
    )
    return (
        f"You are {agent_context.agent_name} ({agent_context.agent_id}).\n"
        f"Role: {agent_context.role}\n"
        f"Core Goal: {agent_context.core_goal}\n"
        f"Trace ID: {agent_context.trace_id}\n"
        "AgentNetwork uses point-to-point A2A 1.0 Agent messaging. "
        "Skills are source packages already placed in the current scene directory. "
        "Only Skill names listed in skill_refs are available to this Agent. "
        "Before using a Skill, use the backend's local file capabilities to read "
        "the package SKILL.md entrypoint, or the matching single Markdown file. "
        "Follow relative references inside the same Skill package when needed. "
        "Do not infer Skill instructions from its name and do not read other Skills. "
        f"Native capability policy hash: {native_policy.sha256}. "
        f"Allowed native capabilities: {', '.join(sorted(native_policy.allow)) or 'none'}. "
        "Every native tool and sub-agent action is authorization checked and audited."
    )


def _completed_event(
    agent_context: AgentContext,
    output_text: str,
    backend_name: str,
) -> dict:
    return {
        "event": "agent_run_completed",
        "trace_id": agent_context.trace_id,
        "agent_id": agent_context.agent_id,
        "task": {"goal": agent_context.task, "status": "completed"},
        "action": {
            "type": "agent_run",
            "name": f"{backend_name}_run",
            "status": "success",
        },
        "content": {
            "content_type": "final_message",
            "text": output_text,
            "summary": output_text[:200],
            "size_bytes": len(output_text.encode("utf-8")),
        },
        "result": {"status": "success", "message": "agent run completed"},
        "metrics": {"backend": backend_name},
    }


def _openclaw_agent_id(agent_context: AgentContext) -> str:
    return (
        os.environ.get("OPENCLAW_AGENT_ID")
        or os.environ.get("OPENCLAW_DEFAULT_AGENT_ID")
        or agent_context.agent_id
    )


def _openclaw_session_name(agent_context: AgentContext) -> str:
    prefix = os.environ.get("OPENCLAW_SESSION_PREFIX", "").strip()
    policy = NativeCapabilityPolicy.from_dict(
        agent_context.native_capabilities, backend="openclaw"
    )
    if policy.session.persistent and agent_context.simulation_id:
        logical_session = f"{agent_context.agent_id}:{agent_context.simulation_id}"
    else:
        logical_session = (
            f"{agent_context.agent_id}:{agent_context.trace_id}"
            if agent_context.trace_id
            else f"{agent_context.agent_id}:event-{agent_context.event_sequence}"
        )
    return f"{prefix}:{logical_session}" if prefix else logical_session


def _extract_openclaw_text(result) -> str:
    content = getattr(result, "content", None)
    if content is not None:
        return str(content)
    data = getattr(result, "data", None)
    if data is not None:
        return str(data)
    return str(result) if result is not None else ""


class OpenCLAWAdapter(BackendAdapter):
    def run_agent_task(
        self,
        agent_context: AgentContext,
    ) -> AgentRunResult:
        if os.environ.get("MOCK_LLM") == "1":
            output_text = "[MOCK_LLM] Dummy response from OpenCLAW"
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=[
                    _completed_event(
                        agent_context,
                        output_text,
                        "openclaw",
                    )
                ],
            )

        system_prompt = _system_prompt(agent_context)
        current_task = _build_task_payload(agent_context)
        prompt = (
            f"{system_prompt}\n\n"
            f"AgentNetwork task payload:\n{current_task}"
        )

        if not OpenClawClient:
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="error",
                final_message="",
                error=(
                    "openclaw-sdk is not installed or not importable."
                ),
            )

        try:
            started = time.monotonic()

            async def _run():
                gateway_url = os.environ.get(
                    "OPENCLAW_GATEWAY_WS_URL",
                    "",
                )
                logger.info(
                    "Connecting to OpenCLAW gateway: %s",
                    gateway_url or "SDK default",
                )
                async with await OpenClawClient.connect() as client:
                    agent = client.get_agent(
                        _openclaw_agent_id(agent_context),
                        session_name=_openclaw_session_name(
                            agent_context
                        ),
                    )
                    return await agent.execute(prompt)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(_run())
            finally:
                loop.close()

            output_text = _extract_openclaw_text(response)
            native_audit_state.set_session_id(
                agent_context.agent_id,
                _openclaw_session_name(agent_context),
            )
            duration_ms = round(
                (time.monotonic() - started) * 1000,
                1,
            )
            runtime_event = {
                "event": "llm_runtime_completed",
                "trace_id": agent_context.trace_id,
                "agent_id": agent_context.agent_id,
                "action": {
                    "type": "llm_call",
                    "name": "openclaw_agent_execute",
                    "status": "success",
                    "duration_ms": duration_ms,
                },
                "result": {"status": "success"},
                "metrics": {
                    "duration_ms": duration_ms,
                    "session_id": _openclaw_session_name(
                        agent_context
                    ),
                    "gateway_url": os.environ.get(
                        "OPENCLAW_GATEWAY_WS_URL",
                        "",
                    ),
                },
            }
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=[
                    runtime_event,
                    _completed_event(
                        agent_context,
                        output_text,
                        "openclaw-sdk",
                    ),
                ],
            )
        except Exception as e:
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="error",
                final_message="",
                error=f"OpenCLAW SDK Error: {str(e)}",
            )
