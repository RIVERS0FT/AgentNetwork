import asyncio
import json
import logging
import os

from .base import AgentContext, AgentRunResult, BackendAdapter

try:
    from claude_agent_sdk import (
        AgentDefinition,
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        TextBlock,
        query,
    )
except ImportError:
    query = None
    ClaudeAgentOptions = None
    AssistantMessage = None
    TextBlock = None
    ResultMessage = None
    AgentDefinition = None
    HookMatcher = None

from agent_network.native_audit import native_audit_state
from agent_network.native_capabilities import (
    NativeCapabilityPolicy,
    backend_allowed_tools,
    backend_denied_tools,
)

logger = logging.getLogger(__name__)

_SKILL_TOOL_NAMES = (
    "list_available_skills",
    "list_skill_files",
    "read_skill_file",
)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys([item for item in items if item]))


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
    return (
        f"You are {agent_context.agent_name} ({agent_context.agent_id}).\n"
        f"Role: {agent_context.role}\n"
        f"Core Goal: {agent_context.core_goal}\n"
        f"Trace ID: {agent_context.trace_id}\n"
        "AgentNetwork uses point-to-point A2A 1.0 Agent messaging. "
        "Skills are source packages stored in the current scene. "
        "Use list_available_skills to discover only the Skills allowed for this Agent. "
        "Before using a Skill, read its SKILL.md with read_skill_file. "
        "Follow relative file references by listing or reading files from the same Skill package. "
        "Do not infer Skill instructions from its name and do not attempt to read unlisted Skills. "
        "Call only exposed MCP tools."
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


def _claude_mcp_server(agent_context: AgentContext) -> dict:
    scene_key = (
        agent_context.scene_key
        or os.environ.get("AGENT_SCENE_KEY", "default")
    )
    scenes_root = os.environ.get("AGENT_SCENES_ROOT", "/app/scenes")
    return {
        "type": "stdio",
        "command": "python",
        "args": [
            "-m",
            "agent_network.mcp_server",
            "--skill-source-mode",
            "--scene",
            scene_key,
            "--agent-id",
            agent_context.agent_id,
            "--agent-name",
            agent_context.agent_name,
            "--allowed-tools",
            ",".join(agent_context.allowed_tools),
            "--skill-refs",
            ",".join(agent_context.skill_refs),
            "--scenes-root",
            scenes_root,
            "--agent-directory-json",
            json.dumps(agent_context.agent_directory, ensure_ascii=False),
            "--comm-matrix-json",
            json.dumps(agent_context.comm_matrix, ensure_ascii=False),
            "--trace-id",
            agent_context.trace_id,
            "--simulation-seed",
            str(agent_context.simulation_seed + agent_context.event_sequence),
        ],
    }


def _claude_allowed_tools(agent_context: AgentContext) -> list[str]:
    tools = list(agent_context.allowed_tools or [])
    tools.extend(
        f"mcp__agent_tools__{tool}"
        for tool in (agent_context.allowed_tools or [])
    )
    tools.extend(
        f"mcp__agent_tools__{tool}"
        for tool in _SKILL_TOOL_NAMES
    )
    policy = NativeCapabilityPolicy.from_dict(
        agent_context.native_capabilities, backend="claude-code"
    )
    tools.extend(backend_allowed_tools("claude-code", policy))
    return _unique(tools)


def _claude_denied_tools(agent_context: AgentContext) -> list[str]:
    policy = NativeCapabilityPolicy.from_dict(
        agent_context.native_capabilities, backend="claude-code"
    )
    return backend_denied_tools("claude-code", policy)


def _claude_agents(agent_context: AgentContext) -> dict:
    policy = NativeCapabilityPolicy.from_dict(
        agent_context.native_capabilities, backend="claude-code"
    )
    if not policy.subagents.enabled or not policy.allows("agent.spawn"):
        return {}
    child_tools = backend_allowed_tools("claude-code", policy)
    if not policy.subagents.child_can_spawn:
        child_tools = [tool for tool in child_tools if tool != "Agent"]
    values = {
        "description": "Audited AgentNetwork worker for bounded delegated work",
        "prompt": (
            "Complete the delegated task within the granted tool policy. "
            "Do not attempt to communicate externally or expand your permissions. "
            "Return evidence and a concise result to the parent Agent."
        ),
        "tools": child_tools,
        "model": policy.subagents.model,
    }
    definition = AgentDefinition(**values) if AgentDefinition else values
    return {"agentnetwork-worker": definition}


def _claude_hooks(agent_context: AgentContext) -> dict:
    if not HookMatcher:
        return {}
    policy = NativeCapabilityPolicy.from_dict(
        agent_context.native_capabilities, backend="claude-code"
    )

    async def pre_tool(input_data, tool_use_id, _context):
        tool_name = str(input_data.get("tool_name") or "")
        if tool_name.startswith("mcp__agent_tools__"):
            return {}
        spawn_depth = 1 if input_data.get("agent_id") else 0
        decision = native_audit_state.check_tool(
            agent_id=agent_context.agent_id,
            backend="claude-code",
            tool_name=tool_name,
            tool_input=input_data.get("tool_input") or {},
            tool_call_id=str(tool_use_id or ""),
            session_id=str(input_data.get("session_id") or ""),
            spawn_depth=spawn_depth,
        )
        hook_output = {
            "hookEventName": input_data.get("hook_event_name", "PreToolUse"),
            "permissionDecision": "allow" if decision["allowed"] else "deny",
            "permissionDecisionReason": decision["reason"],
        }
        return {"hookSpecificOutput": hook_output}

    async def post_tool(input_data, tool_use_id, _context):
        tool_name = str(input_data.get("tool_name") or "")
        if not tool_name.startswith("mcp__agent_tools__"):
            native_audit_state.tool_result(
                agent_id=agent_context.agent_id,
                backend="claude-code",
                tool_name=tool_name,
                tool_call_id=str(tool_use_id or ""),
                output=input_data.get("tool_response"),
                session_id=str(input_data.get("session_id") or ""),
            )
        return {}

    async def failed_tool(input_data, tool_use_id, _context):
        tool_name = str(input_data.get("tool_name") or "")
        if not tool_name.startswith("mcp__agent_tools__"):
            native_audit_state.tool_result(
                agent_id=agent_context.agent_id,
                backend="claude-code",
                tool_name=tool_name,
                tool_call_id=str(tool_use_id or ""),
                output=input_data.get("tool_response"),
                error=str(input_data.get("error") or "tool execution failed"),
                session_id=str(input_data.get("session_id") or ""),
            )
        return {}

    async def subagent_start(input_data, tool_use_id, _context):
        native_audit_state.subagent_lifecycle(
            parent_agent_id=agent_context.agent_id,
            child_agent_id=str(input_data.get("agent_id") or tool_use_id or ""),
            backend="claude-code",
            status="running",
            session_id=str(input_data.get("session_id") or ""),
            run_id=str(tool_use_id or ""),
            agent_type=str(input_data.get("agent_type") or ""),
            model=policy.subagents.model,
        )
        return {}

    async def subagent_stop(input_data, tool_use_id, _context):
        native_audit_state.subagent_lifecycle(
            parent_agent_id=agent_context.agent_id,
            child_agent_id=str(input_data.get("agent_id") or tool_use_id or ""),
            backend="claude-code",
            status="completed",
            session_id=str(input_data.get("session_id") or ""),
            run_id=str(tool_use_id or ""),
            agent_type=str(input_data.get("agent_type") or ""),
            reason=str(input_data.get("stop_reason") or ""),
            model=policy.subagents.model,
        )
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool])],
        "PostToolUse": [HookMatcher(hooks=[post_tool])],
        "PostToolUseFailure": [HookMatcher(hooks=[failed_tool])],
        "SubagentStart": [HookMatcher(hooks=[subagent_start])],
        "SubagentStop": [HookMatcher(hooks=[subagent_stop])],
    }


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
    return {
        "truncated": True,
        "original_chars": len(encoded),
        "preview": encoded[:max_chars],
    }


def _tool_events_from_message(
    message,
    agent_context: AgentContext,
) -> list[dict]:
    """Extract backend tool intent/result blocks without coupling to SDK versions."""
    events = []
    for block in getattr(message, "content", []) or []:
        class_name = block.__class__.__name__.lower()
        tool_call_id = (
            getattr(block, "id", "")
            or getattr(block, "tool_use_id", "")
        )
        tool_name = getattr(block, "name", "")
        tool_input = getattr(block, "input", None)
        if (
            "tooluse" in class_name
            or (tool_call_id and tool_name and tool_input is not None)
        ):
            events.append(
                {
                    "event": "tool_call_requested",
                    "trace_id": agent_context.trace_id,
                    "agent_id": agent_context.agent_id,
                    "action": {
                        "type": "tool_call",
                        "name": tool_name,
                        "status": "requested",
                    },
                    "tool": {
                        "name": tool_name,
                        "tool_call_id": str(tool_call_id),
                        "input": _bounded_value(tool_input),
                        "status": "requested",
                    },
                    "result": {"status": "requested"},
                }
            )
            continue

        result_content = getattr(block, "content", None)
        if (
            "toolresult" in class_name
            or (
                getattr(block, "tool_use_id", "")
                and result_content is not None
            )
        ):
            is_error = bool(getattr(block, "is_error", False))
            events.append(
                {
                    "event": "tool_result_received",
                    "trace_id": agent_context.trace_id,
                    "agent_id": agent_context.agent_id,
                    "action": {
                        "type": "tool_result",
                        "name": "tool_result",
                        "status": "failed" if is_error else "success",
                    },
                    "tool": {
                        "tool_call_id": str(tool_call_id),
                        "output": _bounded_value(result_content),
                        "status": "failed" if is_error else "success",
                    },
                    "result": {
                        "status": "failed" if is_error else "success"
                    },
                }
            )
    return events


def _runtime_event_from_message(
    message,
    agent_context: AgentContext,
) -> list[dict]:
    class_name = message.__class__.__name__.lower()
    if (
        "resultmessage" not in class_name
        and not hasattr(message, "duration_ms")
    ):
        return []
    is_error = bool(getattr(message, "is_error", False))
    duration_ms = getattr(message, "duration_ms", 0) or 0
    return [
        {
            "event": "llm_runtime_completed",
            "trace_id": agent_context.trace_id,
            "agent_id": agent_context.agent_id,
            "action": {
                "type": "llm_call",
                "name": "claude_agent_query",
                "status": "failed" if is_error else "success",
                "duration_ms": duration_ms,
            },
            "result": {
                "status": "failed" if is_error else "success",
                "message": str(
                    getattr(message, "subtype", "") or ""
                ),
            },
            "metrics": {
                "duration_ms": duration_ms,
                "duration_api_ms": (
                    getattr(message, "duration_api_ms", 0) or 0
                ),
                "num_turns": getattr(message, "num_turns", 0) or 0,
                "total_cost_usd": (
                    getattr(message, "total_cost_usd", 0) or 0
                ),
                "usage": _bounded_value(
                    getattr(message, "usage", {}) or {}
                ),
                "session_id": str(
                    getattr(message, "session_id", "") or ""
                ),
            },
        }
    ]


class ClaudeCodeAdapter(BackendAdapter):
    def run_agent_task(
        self,
        agent_context: AgentContext,
    ) -> AgentRunResult:
        if os.environ.get("MOCK_LLM") == "1":
            output_text = "[MOCK_LLM] Dummy response"
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=[
                    _completed_event(
                        agent_context,
                        output_text,
                        "claude-code",
                    )
                ],
            )

        system_prompt = _system_prompt(agent_context)
        current_task = _build_task_payload(agent_context)

        if not query or not ClaudeAgentOptions:
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="error",
                final_message="",
                error="claude-agent-sdk is not installed.",
            )

        try:
            native_policy = NativeCapabilityPolicy.from_dict(
                agent_context.native_capabilities, backend="claude-code"
            )
            options_kwargs = {
                "system_prompt": system_prompt,
                "tools": {"type": "preset", "preset": "claude_code"},
                "allowed_tools": _claude_allowed_tools(agent_context),
                "disallowed_tools": _claude_denied_tools(agent_context),
                "mcp_servers": {
                    "agent_tools": _claude_mcp_server(agent_context)
                },
                "strict_mcp_config": True,
                "permission_mode": "dontAsk",
                "include_hook_events": True,
                "enable_file_checkpointing": native_policy.allows("fs.write"),
            }
            hooks = _claude_hooks(agent_context)
            if hooks:
                options_kwargs["hooks"] = hooks
            agents = _claude_agents(agent_context)
            if agents:
                options_kwargs["agents"] = agents
            existing_session = native_audit_state.session_id(
                agent_context.agent_id
            )
            if native_policy.session.persistent and existing_session:
                options_kwargs["resume"] = existing_session
            max_turns = (
                agent_context.max_turns
                or int(os.environ.get("CLAUDE_AGENT_MAX_TURNS", "1"))
            )
            if max_turns:
                options_kwargs["max_turns"] = max_turns
            if os.environ.get("CLAUDE_AGENT_CWD"):
                options_kwargs["cwd"] = os.environ["CLAUDE_AGENT_CWD"]
            if os.environ.get("CLAUDE_AGENT_PERMISSION_MODE"):
                options_kwargs["permission_mode"] = os.environ[
                    "CLAUDE_AGENT_PERMISSION_MODE"
                ]

            options = ClaudeAgentOptions(**options_kwargs)

            async def _run():
                text_parts: list[str] = []
                tool_events: list[dict] = []
                runtime_events: list[dict] = []
                mcp_tool_call_ids: set[str] = set()
                async for message in query(
                    prompt=current_task,
                    options=options,
                ):
                    text_parts.extend(
                        _extract_text_from_message(message)
                    )
                    extracted_tool_events = _tool_events_from_message(
                        message,
                        agent_context,
                    )
                    if hooks:
                        for event in extracted_tool_events:
                            tool = event.get("tool") or {}
                            call_id = str(tool.get("tool_call_id") or "")
                            name = str(tool.get("name") or "")
                            if event["event"] == "tool_call_requested":
                                if name.startswith("mcp__agent_tools__"):
                                    mcp_tool_call_ids.add(call_id)
                                    tool_events.append(event)
                            elif call_id in mcp_tool_call_ids:
                                tool_events.append(event)
                    else:
                        tool_events.extend(extracted_tool_events)
                    runtime_events.extend(
                        _runtime_event_from_message(
                            message,
                            agent_context,
                        )
                    )
                    session_id = str(getattr(message, "session_id", "") or "")
                    if session_id:
                        native_audit_state.set_session_id(
                            agent_context.agent_id, session_id
                        )
                return (
                    "\n".join(
                        [part for part in text_parts if part]
                    ).strip(),
                    tool_events,
                    runtime_events,
                )

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                output_text, tool_events, runtime_events = (
                    loop.run_until_complete(_run())
                )
            finally:
                loop.close()

            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="completed",
                final_message=output_text,
                application_events=(
                    tool_events
                    + runtime_events
                    + [
                        _completed_event(
                            agent_context,
                            output_text,
                            "claude-agent-sdk",
                        )
                    ]
                ),
                tool_events=tool_events,
            )
        except Exception as e:
            return AgentRunResult(
                trace_id=agent_context.trace_id,
                agent_id=agent_context.agent_id,
                status="error",
                final_message="",
                error=f"Claude Agent SDK Error: {str(e)}",
            )
