"""Real-time audit and authorization state for backend-native capabilities."""

from __future__ import annotations

import hmac
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from agent_network.log_management import get_log_manager
from agent_network.native_capabilities import (
    NativeCapabilityPolicy,
    audit_value,
    evaluate_tool_call,
)


TERMINAL_SUBAGENT_STATUSES = frozenset(
    {'completed', 'failed', 'cancelled', 'terminated', 'timeout', 'killed', 'reset', 'deleted'}
)


def _post_server(record: dict[str, Any]) -> None:
    server_url = os.environ.get('SERVER_URL', 'http://localhost:8000').rstrip('/')
    try:
        response = requests.post(
            f'{server_url}/api/logs/ingest', json=record, timeout=2
        )
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f'native audit delivery to AgentNetwork failed: {exc}'
        ) from exc


def emit_native_event(record: dict[str, Any]) -> dict[str, Any]:
    logger = get_log_manager()
    normalized = logger.emit_application_event(
        event=record['event'],
        agent_id=record['agent_id'],
        target=record.get('target', {}),
        task=record.get('task', {}),
        conversation=record.get('conversation', {}),
        action=record.get('action', {}),
        content=record.get('content', {}),
        skill=record.get('skill', {}),
        tool=record.get('tool', {}),
        state_change=record.get('state_change', {}),
        result=record.get('result', {}),
        metrics=record.get('metrics', {}),
        payload=record.get('payload', {}),
        trace_id=record.get('trace_id', ''),
    )
    _post_server(normalized)
    return normalized


class NativeAuditState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._policies: dict[str, NativeCapabilityPolicy] = {}
        self._contexts: dict[str, dict[str, str]] = {}
        self._children: dict[str, dict[str, dict[str, Any]]] = {}
        self._sessions: dict[str, tuple[str, float]] = {}
        self._calls: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _file_snapshot(tool_input: dict[str, Any]) -> dict[str, Any]:
        path_value = next(
            (
                tool_input.get(key)
                for key in ('file_path', 'path', 'notebook_path')
                if tool_input.get(key)
            ),
            '',
        )
        if not path_value:
            return {}
        path = Path(str(path_value)).expanduser()
        if not path.is_absolute():
            path = Path(os.environ.get('AGENT_NATIVE_WORKSPACE', '/app')) / path
        try:
            path = path.resolve()
            exists = path.is_file()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if exists else ''
            return {'path': str(path), 'exists': exists, 'sha256': digest}
        except OSError as exc:
            return {'path': str(path), 'error': str(exc)}

    def configure(
        self,
        agent_id: str,
        backend: str,
        policy_value: dict[str, Any] | NativeCapabilityPolicy | None,
        *,
        trace_id: str = '',
        simulation_id: str = '',
    ) -> NativeCapabilityPolicy:
        policy = (
            policy_value
            if isinstance(policy_value, NativeCapabilityPolicy)
            else NativeCapabilityPolicy.from_dict(policy_value, backend=backend)
        )
        normalized = str(agent_id).lower()
        with self._lock:
            self._policies[normalized] = policy
            self._contexts[normalized] = {
                'backend': backend,
                'trace_id': trace_id,
                'simulation_id': simulation_id,
            }
            self._children.setdefault(normalized, {})
        emit_native_event(
            {
                'event': 'policy_check',
                'trace_id': trace_id,
                'agent_id': normalized,
                'action': {
                    'type': 'native_capability_policy',
                    'name': 'apply',
                    'status': 'success',
                },
                'result': {
                    'status': 'success',
                    'decision': 'applied',
                    'policy_sha256': policy.sha256,
                },
                'payload': {
                    'backend': backend,
                    'simulation_id': simulation_id,
                    'profile': policy.profile,
                },
            }
        )
        return policy

    def policy(self, agent_id: str, backend: str = '') -> NativeCapabilityPolicy:
        normalized = str(agent_id).lower()
        with self._lock:
            return self._policies.get(normalized) or NativeCapabilityPolicy.from_dict(
                None, backend=backend
            )

    def context(self, agent_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._contexts.get(str(agent_id).lower(), {}))

    def session_id(self, agent_id: str) -> str:
        normalized = str(agent_id).lower()
        with self._lock:
            entry = self._sessions.get(normalized)
            policy = self._policies.get(normalized)
            if not entry:
                return ''
            session_id, stored_at = entry
            if (
                not policy
                or not policy.session.persistent
                or policy.session.ttl_seconds == 0
                or time.monotonic() - stored_at > policy.session.ttl_seconds
            ):
                self._sessions.pop(normalized, None)
                return ''
            return session_id

    def set_session_id(self, agent_id: str, session_id: str) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions[str(agent_id).lower()] = (
                str(session_id),
                time.monotonic(),
            )

    def check_tool(
        self,
        *,
        agent_id: str,
        backend: str,
        tool_name: str,
        tool_input: dict[str, Any] | None,
        tool_call_id: str = '',
        session_id: str = '',
        spawn_depth: int = 0,
    ) -> dict[str, Any]:
        normalized = str(agent_id).lower()
        policy = self.policy(normalized, backend)
        context = self.context(normalized)
        with self._lock:
            children = self._children.get(normalized, {})
            pending_spawn_count = sum(
                1
                for (call_agent, _), call in self._calls.items()
                if call_agent == normalized and call.get('capability') == 'agent.spawn'
            )
            child_count = len(children) + pending_spawn_count
            active_child_count = sum(
                1
                for child in children.values()
                if child.get('status') not in TERMINAL_SUBAGENT_STATUSES
            ) + pending_spawn_count
            decision = evaluate_tool_call(
                policy,
                backend,
                tool_name,
                tool_input,
                child_count=child_count,
                active_child_count=active_child_count,
                spawn_depth=spawn_depth,
            )
            if tool_call_id and decision['allowed']:
                self._calls[(normalized, tool_call_id)] = {
                    'input': dict(tool_input or {}),
                    'capability': decision['capability'],
                    'file_before': self._file_snapshot(dict(tool_input or {})),
                }
        status = 'allowed' if decision['allowed'] else 'denied'
        audited_input = audit_value(tool_input or {}, policy.audit)
        emit_native_event(
            {
                'event': 'policy_check',
                'trace_id': context.get('trace_id', ''),
                'agent_id': normalized,
                'action': {
                    'type': 'native_tool_policy',
                    'name': tool_name,
                    'status': status,
                },
                'tool': {
                    'name': tool_name,
                    'tool_call_id': tool_call_id,
                    'canonical_capability': decision['capability'],
                    'input': audited_input,
                },
                'result': {
                    'status': status,
                    'decision': status,
                    'reason': decision['reason'],
                },
                'metrics': {
                    'child_count': child_count,
                    'active_child_count': active_child_count,
                    'spawn_depth': spawn_depth,
                },
                'payload': {
                    'backend': backend,
                    'session_id': session_id,
                    'policy_sha256': policy.sha256,
                },
            }
        )
        emit_native_event(
            {
                'event': 'tool_call_requested',
                'trace_id': context.get('trace_id', ''),
                'agent_id': normalized,
                'action': {
                    'type': 'native_tool_call',
                    'name': tool_name,
                    'status': status,
                },
                'tool': {
                    'name': tool_name,
                    'tool_call_id': tool_call_id,
                    'canonical_capability': decision['capability'],
                    'input': audited_input,
                    'status': status,
                },
                'result': {'status': status, 'reason': decision['reason']},
            }
        )
        return {
            **decision,
            'policy_sha256': policy.sha256,
            'agent_id': normalized,
        }

    def tool_result(
        self,
        *,
        agent_id: str,
        backend: str,
        tool_name: str,
        tool_call_id: str,
        output: Any = None,
        error: str = '',
        duration_ms: float = 0,
        session_id: str = '',
    ) -> dict[str, Any]:
        normalized = str(agent_id).lower()
        policy = self.policy(normalized, backend)
        context = self.context(normalized)
        status = 'failed' if error else 'success'
        with self._lock:
            call = self._calls.pop((normalized, tool_call_id), {})
        file_after = (
            self._file_snapshot(call.get('input') or {})
            if call.get('capability') == 'fs.write'
            else {}
        )
        record = {
            'event': 'tool_result_received',
            'trace_id': context.get('trace_id', ''),
            'agent_id': normalized,
            'action': {
                'type': 'native_tool_result',
                'name': tool_name or 'tool_result',
                'status': status,
            },
            'tool': {
                'name': tool_name,
                'tool_call_id': tool_call_id,
                'output': audit_value(output, policy.audit, output=True),
                'file_before': call.get('file_before', {}),
                'file_after': file_after,
                'status': status,
            },
            'result': {'status': status, 'error_message': error},
            'metrics': {'duration_ms': duration_ms, 'session_id': session_id},
        }
        return emit_native_event(record)

    def subagent_lifecycle(
        self,
        *,
        parent_agent_id: str,
        child_agent_id: str,
        backend: str,
        status: str,
        session_id: str = '',
        run_id: str = '',
        agent_type: str = '',
        reason: str = '',
        model: str = '',
        provider: str = '',
    ) -> dict[str, Any]:
        parent = str(parent_agent_id).lower()
        child = str(child_agent_id or session_id or run_id).lower()
        context = self.context(parent)
        with self._lock:
            self._children.setdefault(parent, {})[child] = {
                'status': status,
                'session_id': session_id,
                'run_id': run_id,
                'backend': backend,
                'agent_type': agent_type,
            }
        policy = self.policy(parent, backend)
        if policy.subagents.register_in_platform and child:
            from agent_network.agent_management import Agent, AgentRegistry

            registered = AgentRegistry.get(child)
            if not registered:
                parent_agent = AgentRegistry.get(parent)
                registered = Agent(
                    agent_id=child,
                    role=agent_type or 'native-subagent',
                    name=agent_type or child,
                    core_goal='Backend-native delegated execution',
                    backend=backend,
                    native_capabilities=NativeCapabilityPolicy.disabled(),
                    parent_agent_id=parent,
                    runtime_kind=f'{backend}_native',
                    runtime_session_id=session_id,
                    spawn_depth=1,
                )
                if parent_agent:
                    registered.container_id = parent_agent.container_id
                    registered.container_url = parent_agent.container_url
                AgentRegistry.register(registered)
            registered.status = status
            registered.runtime_session_id = session_id
        return emit_native_event(
            {
                'event': 'subagent_lifecycle',
                'trace_id': context.get('trace_id', ''),
                'agent_id': parent,
                'target': {'agent_id': child, 'role': agent_type},
                'action': {
                    'type': 'native_subagent',
                    'name': 'lifecycle',
                    'status': status,
                },
                'result': {'status': status, 'reason': reason},
                'payload': {
                    'backend': backend,
                    'parent_agent_id': parent,
                    'child_session_id': session_id,
                    'run_id': run_id,
                    'model': model,
                    'provider': provider,
                    'policy_sha256': self.policy(parent, backend).sha256,
                },
            }
        )

    def child_snapshot(self, parent_agent_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {'agent_id': child_id, **dict(value)}
                for child_id, value in self._children.get(
                    str(parent_agent_id).lower(), {}
                ).items()
            ]

    def reset(self) -> None:
        with self._lock:
            active_children = [
                (parent_id, child_id, dict(child))
                for parent_id, children in self._children.items()
                for child_id, child in children.items()
                if child.get('status') not in TERMINAL_SUBAGENT_STATUSES
            ]
            child_ids = {
                child_id
                for children in self._children.values()
                for child_id in children
            }
        for parent_id, child_id, child in active_children:
            self.subagent_lifecycle(
                parent_agent_id=parent_id,
                child_agent_id=child_id,
                backend=str(child.get('backend') or self.context(parent_id).get('backend') or ''),
                status='reset',
                session_id=str(child.get('session_id') or ''),
                run_id=str(child.get('run_id') or ''),
                agent_type=str(child.get('agent_type') or ''),
                reason='agent runtime reset',
            )
        if child_ids:
            from agent_network.agent_management import AgentRegistry

            for child_id in child_ids:
                AgentRegistry.unregister(child_id)
        with self._lock:
            self._policies.clear()
            self._contexts.clear()
            self._children.clear()
            self._sessions.clear()
            self._calls.clear()


native_audit_state = NativeAuditState()


def native_token_valid(value: str) -> bool:
    expected = os.environ.get('NATIVE_AUDIT_TOKEN', '')
    if not expected:
        return False
    return hmac.compare_digest(str(value or ''), expected)
