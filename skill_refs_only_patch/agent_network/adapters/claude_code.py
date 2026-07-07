import json
import logging
import os
import asyncio
from .base import BackendAdapter, AgentContext, AgentRunResult
from .direct_llm import run_direct_llm
from agent_network.skill_md_loader import load_scene_skill_registry

try:
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage
except ImportError:
    query = None
    ClaudeAgentOptions = None
    AssistantMessage = None
    TextBlock = None
    ResultMessage = None

logger = logging.getLogger(__name__)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys([item for item in items if item]))



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
        "AgentNetwork uses direct Agent-to-Agent HTTP messaging. Call only exposed MCP tools."
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


def _claude_mcp_server(agent_context: AgentContext) -> dict:
    scene_key = agent_context.scene_key or os.environ.get("AGENT_SCENE_KEY", "default")
    return {
        "type": "stdio",
        "command": "python",
        "args": [
            "-m", "agent_network.mcp_server",
            "--scene", scene_key,
            "--agent-id", agent_context.agent_id,
            "--agent-name", agent_context.agent_name,
            "--allowed-tools", ",".join(agent_context.allowed_tools),
            "--agent-directory-json", json.dumps(agent_context.agent_directory, ensure_ascii=False),
            "--comm-matrix-json", json.dumps(agent_context.comm_matrix, ensure_ascii=False),
            "--trace-id", agent_context.trace_id,
            "--simulation-seed", str(agent_context.simulation_seed + agent_context.tick),
        ],
    }


def _claude_allowed_tools(agent_context: AgentContext) -> list[str]:
    tools = list(agent_context.allowed_tools or [])
    tools.extend([f"mcp__agent_tools__{tool}" for tool in (agent_context.allowed_tools or [])])
    return _unique(tools)


def _extract_text_from_message(message) -> list[str]:
    parts: list[str] = []
    if AssistantMessage is not None and isinstance(message, AssistantMessage):
        for block in getattr(message, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
    elif ResultMessage is not None and isinstance(message, ResultMessage):
        result = getattr(message, "result", None)
        if result:
            parts.append(str(result))
    else:
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            parts.append(content)
    return parts


def _bounded_value(value, max_chars: int = 64 * 1024):
    """Keep SDK evidence serializable without allowing unbounded log records."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        encoded = str(value)
    if len(encoded) <= max_chars:
        try:
            return json.loads(encoded)
        except Exception:
            return encoded
    return {"truncated": True, "original_chars": len(encoded), "preview": encoded[:max_chars]}


def _tool_events_from_message(message, agent_context: AgentContext) -> list[dict]:
    """Extract backend tool intent/result blocks without coupling to SDK versions."""
    events = []
    for block in getattr(message, "content", []) or []:
        class_name = block.__class__.__name__.lower()
        tool_call_id = getattr(block, "id", "") or getattr(block, "tool_use_id", "")
        tool_name = getattr(block, "name", "")
        tool_input = getattr(block, "input", None)
        if "tooluse" in class_name or (tool_call_id and tool_name and tool_input is not None):
            events.append({
                "event": "tool_call_requested",
                "trace_id": agent_context.trace_id,
                "actor": {"agent_id": agent_context.agent_id, "backend": "claude-agent-sdk"},
                "action": {"type": "tool_call", "name": tool_name, "status": "requested"},
                "tool": {
                    "name": tool_name,
                    "tool_call_id": str(tool_call_id),
                    "input": _bounded_value(tool_input),
                    "status": "requested",
                },
                "result": {"status": "requested"},
                "links": {"tool_call_id": str(tool_call_id)},
            })
            continue

        result_content = getattr(block, "content", None)
        if "toolresult" in class_name or (getattr(block, "tool_use_id", "") and result_content is not None):
            is_error = bool(getattr(block, "is_error", False))
            events.append({
                "event": "tool_result_received",
                "trace_id": agent_context.trace_id,
                "actor": {"agent_id": agent_context.agent_id, "backend": "claude-agent-sdk"},
                "action": {"type": "tool_result", "name": "tool_result", "status": "failed" if is_error else "success"},
                "tool": {
                    "tool_call_id": str(tool_call_id),
                    "output": _bounded_value(result_content),
                    "status": "failed" if is_error else "success",
                },
                "result": {"status": "failed" if is_error else "success"},
                "links": {"tool_call_id": str(tool_call_id)},
            })
    return events


def _runtime_event_from_message(message, agent_context: AgentContext) -> list[dict]:
    class_name = message.__class__.__name__.lower()
    if "resultmessage" not in class_name and not hasattr(message, "duration_ms"):
        return []
    is_error = bool(getattr(message, "is_error", False))
    duration_ms = getattr(message, "duration_ms", 0) or 0
    return [{
        "event": "llm_runtime_completed",
        "trace_id": agent_context.trace_id,
        "actor": {"agent_id": agent_context.agent_id, "backend": "claude-agent-sdk"},
        "action": {
            "type": "llm_call",
            "name": "claude_agent_query",
            "status": "failed" if is_error else "success",
            "duration_ms": duration_ms,
        },
        "result": {
            "status": "failed" if is_error else "success",
            "message": str(getattr(message, "subtype", "") or ""),
        },
        "metrics": {
            "duration_ms": duration_ms,
            "duration_api_ms": getattr(message, "duration_api_ms", 0) or 0,
            "num_turns": getattr(message, "num_turns", 0) or 0,
            "total_cost_usd": getattr(message, "total_cost_usd", 0) or 0,
            "usage": _bounded_value(getattr(message, "usage", {}) or {}),
            "session_id": str(getattr(message, "session_id", "") or ""),
        },
    }]


class ClaudeCodeAdapter(BackendAdapter):
    def run_agent_task(self, agent_context: AgentContext) -> AgentRunResult:
        if os.environ.get("MOCK_LLM") == "1":
            output_text = "[MOCK_LLM] Dummy response"
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=[_completed_event(agent_context, output_text, "claude-code")],
            )

        system_prompt = _system_prompt(agent_context)
        current_task = _build_task_payload(agent_context)

        if not query or not ClaudeAgentOptions:
            if os.environ.get("AGENT_STRICT_BACKEND_SDK") == "1":
                return AgentRunResult(trace_id=agent_context.trace_id, agent_id=agent_context.agent_id, status="error", final_message="", error="claude-agent-sdk is not installed.")
            return run_direct_llm(agent_context, backend_name="claude-agent-direct-llm", system_prompt=system_prompt, user_payload=current_task)

        try:
            options_kwargs = {
                "system_prompt": system_prompt,
                "allowed_tools": _claude_allowed_tools(agent_context),
                "mcp_servers": {"agent_tools": _claude_mcp_server(agent_context)},
            }
            max_turns = agent_context.max_turns or int(os.environ.get("CLAUDE_AGENT_MAX_TURNS", "1"))
            if max_turns:
                options_kwargs["max_turns"] = max_turns
            if os.environ.get("CLAUDE_AGENT_CWD"):
                options_kwargs["cwd"] = os.environ["CLAUDE_AGENT_CWD"]
            if os.environ.get("CLAUDE_AGENT_PERMISSION_MODE"):
                options_kwargs["permission_mode"] = os.environ["CLAUDE_AGENT_PERMISSION_MODE"]

            options = ClaudeAgentOptions(**options_kwargs)

            async def _run():
                text_parts: list[str] = []
                tool_events: list[dict] = []
                runtime_events: list[dict] = []
                async for message in query(prompt=current_task, options=options):
                    text_parts.extend(_extract_text_from_message(message))
                    tool_events.extend(_tool_events_from_message(message, agent_context))
                    runtime_events.extend(_runtime_event_from_message(message, agent_context))
                return "\n".join([part for part in text_parts if part]).strip(), tool_events, runtime_events

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                output_text, tool_events, runtime_events = loop.run_until_complete(_run())
            finally:
                loop.close()

            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=tool_events + runtime_events + [_completed_event(agent_context, output_text, "claude-agent-sdk")],
                tool_events=tool_events,
            )
        except Exception as e:
            return AgentRunResult(trace_id=agent_context.trace_id, agent_id=agent_context.agent_id, status="error", final_message="", error=f"Claude Agent SDK Error: {str(e)}")
