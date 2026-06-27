import json
import logging
import os
import asyncio
from .base import BackendAdapter, AgentContext, AgentRunResult
from agent_network.skill_md_loader import load_scene_skill_registry

try:
    from openclaw import OpenCLAWClient
except ImportError:
    OpenCLAWClient = None

logger = logging.getLogger(__name__)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys([item for item in items if item]))


def _skill_names(agent_context: AgentContext) -> list[str]:
    """Return the control-plane Skill allowlist.

    srv only passes Skill names. Skill.md is loaded inside the Agent container and
    injected as SOP/context here; it is not parsed or executed by srv.
    """
    names = list(getattr(agent_context, "allowed_skills", []) or [])
    if not names:
        for item in agent_context.skills or []:
            if isinstance(item, dict):
                names.append(item.get("name") or item.get("skill_name") or "")
            elif isinstance(item, str):
                names.append(item)
    return _unique(names)


def _skill_context(agent_context: AgentContext) -> list[dict]:
    scene_key = agent_context.scene_key or os.environ.get("AGENT_SCENE_KEY", "default")
    scenes_root = os.environ.get("AGENT_SCENES_ROOT", "/app/scenes")
    registry = load_scene_skill_registry(
        scene_key=scene_key,
        scenes_root=scenes_root,
        allowed_skills=_skill_names(agent_context),
    )
    specs = registry.context_specs()
    if specs:
        return specs

    # Compatibility fallback for old callers. New srv code should not send SOP
    # bodies; container-local Skill.md remains the preferred source of truth.
    return [item for item in (agent_context.skills or []) if isinstance(item, dict)]


def _build_task_payload(agent_context: AgentContext) -> str:
    """Build a structured payload and let the backend decide how to use it."""
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
        "allowed_skills": _skill_names(agent_context),
        "skills": _skill_context(agent_context),
        "allowed_tools": agent_context.allowed_tools,
        "permissions": agent_context.permissions,
        "state_snapshot": agent_context.state_snapshot,
        "tick": agent_context.tick,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


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
        "task": {
            "goal": agent_context.task,
            "status": "completed",
        },
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
        "result": {
            "status": "success",
            "message": "agent run completed",
        },
        "metrics": {
            "backend": backend_name,
        },
    }


class OpenCLAWAdapter(BackendAdapter):
    def run_agent_task(self, agent_context: AgentContext) -> AgentRunResult:
        if os.environ.get("MOCK_LLM") == "1":
            output_text = "[MOCK_LLM] Dummy response from OpenCLAW"
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=[_completed_event(agent_context, output_text, "openclaw")],
                tool_events=[],
                state_changes=[],
                outbound_messages=[],
                traffic_events=[],
                audit_events=[],
                error=None,
            )

        if not OpenCLAWClient:
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="error",
                final_message="",
                error="openclaw SDK is not installed."
            )

        scene_key = agent_context.scene_key or os.environ.get("AGENT_SCENE_KEY", "default")
        skill_names = _skill_names(agent_context)
        mcp_config = {
            "mcpServers": {
                "agent_tools": {
                    "command": "python",
                    "args": [
                        "-m", "agent_network.mcp_server",
                        "--scene", scene_key,
                        "--agent-id", agent_context.agent_id,
                        "--agent-name", agent_context.agent_name,
                        "--allowed-skills", ",".join(skill_names),
                        "--allowed-tools", ",".join(agent_context.allowed_tools),
                    ],
                }
            }
        }

        system_prompt = (
            f"You are {agent_context.agent_name} ({agent_context.agent_id}).\n"
            f"Role: {agent_context.role}\n"
            f"Core Goal: {agent_context.core_goal}\n"
            f"Trace ID: {agent_context.trace_id}\n"
            "Skill.md content is SOP/context loaded inside this Agent container. "
            "Do not assume Skill names are executable tools; call only exposed MCP tools."
        )
        current_task = _build_task_payload(agent_context)

        try:
            client = OpenCLAWClient(system_prompt=system_prompt, mcp_config=mcp_config)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _run():
                return await client.chat(current_task)

            response = loop.run_until_complete(_run())
            loop.close()

            output_text = str(response) if response else ""
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=[_completed_event(agent_context, output_text, "openclaw")],
                tool_events=[],
                state_changes=[],
                outbound_messages=[],
                traffic_events=[],
                audit_events=[],
                error=None,
            )
        except Exception as e:
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="error",
                final_message="",
                error=f"OpenCLAW SDK Error: {str(e)}"
            )
