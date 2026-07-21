import json
from pathlib import Path

import pytest

from agent_network.native_capabilities import (
    NativeCapabilityPolicy,
    audit_value,
    backend_allowed_tools,
    evaluate_tool_call,
)


ROOT = Path(__file__).resolve().parents[1]


def test_default_native_policy_is_audited_and_read_only():
    policy = NativeCapabilityPolicy.from_dict(None, backend="claude-code")
    assert policy.enabled is True
    assert policy.audit.required is True
    assert policy.allows("fs.read") is True
    assert policy.allows("fs.search") is True
    assert policy.allows("agent.spawn") is True
    assert policy.allows("fs.write") is False
    assert policy.allows("process.exec") is False
    assert "Agent" in backend_allowed_tools("claude-code", policy)


def test_direct_llm_has_no_native_capabilities():
    policy = NativeCapabilityPolicy.from_dict(
        {"enabled": True, "tools": {"allow": ["fs.write"]}},
        backend="direct_llm",
    )
    assert policy.enabled is False
    assert policy.allow == frozenset()


def test_native_policy_rejects_unknown_fields_and_capabilities():
    with pytest.raises(ValueError, match="unknown native capability fields"):
        NativeCapabilityPolicy.from_dict({"surprise": True}, backend="openclaw")
    with pytest.raises(ValueError, match="unknown native capabilities"):
        NativeCapabilityPolicy.from_dict(
            {"tools": {"allow": ["root.shell"]}}, backend="openclaw"
        )
    with pytest.raises(ValueError, match="must use CommManager"):
        NativeCapabilityPolicy.from_dict(
            {"tools": {"allow": ["agent.message"]}}, backend="claude-code"
        )
    with pytest.raises(ValueError, match="audit.required"):
        NativeCapabilityPolicy.from_dict(
            {"audit": {"required": False}}, backend="openclaw"
        )
    with pytest.raises(ValueError, match="input_capture"):
        NativeCapabilityPolicy.from_dict(
            {"audit": {"input_capture": "none"}}, backend="openclaw"
        )


def test_native_policy_blocks_dangerous_commands_private_urls_and_quota():
    policy = NativeCapabilityPolicy.from_dict(
        {
            "tools": {
                "allow": ["process.exec", "web.fetch", "agent.spawn"],
                "deny": [],
            },
            "subagents": {"max_children": 1, "max_depth": 1},
        },
        backend="openclaw",
    )
    assert evaluate_tool_call(
        policy, "openclaw", "exec", {"command": "rm -rf /tmp/demo"}
    )["allowed"] is False
    assert evaluate_tool_call(
        policy, "openclaw", "web_fetch", {"url": "http://127.0.0.1/admin"}
    )["allowed"] is False
    assert evaluate_tool_call(
        policy, "openclaw", "sessions_spawn", {}, child_count=1, spawn_depth=0
    )["allowed"] is False
    assert evaluate_tool_call(
        policy, "openclaw", "sessions_spawn", {}, child_count=0, spawn_depth=1
    )["allowed"] is False


def test_native_policy_enforces_parallel_limit_and_child_spawn_rule():
    policy = NativeCapabilityPolicy.from_dict(
        {
            "tools": {"allow": ["agent.spawn"], "deny": []},
            "subagents": {
                "max_children": 3,
                "max_parallel": 1,
                "max_depth": 3,
                "child_can_spawn": False,
            },
        },
        backend="openclaw",
    )
    parallel = evaluate_tool_call(
        policy,
        "openclaw",
        "sessions_spawn",
        {},
        child_count=1,
        active_child_count=1,
        spawn_depth=0,
    )
    nested = evaluate_tool_call(
        policy,
        "openclaw",
        "sessions_spawn",
        {},
        child_count=0,
        active_child_count=0,
        spawn_depth=1,
    )
    assert parallel["reason"] == "maximum parallel child count reached"
    assert nested["reason"] == "child agents cannot spawn"


def test_native_audit_redacts_secrets_and_keeps_hash():
    policy = NativeCapabilityPolicy.from_dict(None, backend="openclaw")
    value = audit_value(
        {"query": "status", "authorization": "Bearer secret"}, policy.audit
    )
    assert len(value["sha256"]) == 64
    assert value["preview"]["authorization"] == "***REDACTED***"
    inline = audit_value(
        {"command": "curl -H 'Authorization: Bearer secret-token' example.com"},
        policy.audit,
    )
    assert "secret-token" not in inline["preview"]["command"]


def test_openclaw_audit_plugin_is_fail_closed_and_tracks_subagents():
    source = (
        ROOT / "docker" / "openclaw-audit-plugin" / "index.mjs"
    ).read_text(encoding="utf-8")
    config_text = (ROOT / "docker" / "openclaw.json").read_text(encoding="utf-8")
    config = json.loads(config_text)
    assert 'api.on("before_tool_call"' in source
    assert "block: true" in source
    assert 'api.on("after_tool_call"' in source
    assert 'api.on("subagent_spawned"' in source
    assert 'api.on("subagent_ended"' in source
    assert "event.childSessionKey || event.agentId" in source
    assert "event.childAgentId" not in source
    assert 'new Set(["timeout", "killed", "reset", "deleted"])' in source
    assert config["tools"]["agentToAgent"]["enabled"] is False
    assert config["agents"]["defaults"]["subagents"]["maxConcurrent"] == 32
    assert config["plugins"]["load"]["paths"] == [
        "./docker/openclaw-audit-plugin"
    ]
    assert "allow" not in config["plugins"]


def test_backend_versions_are_exactly_pinned():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    claude = (ROOT / "docker" / "Dockerfile.claude").read_text(encoding="utf-8")
    openclaw = (ROOT / "docker" / "Dockerfile.openclaw").read_text(encoding="utf-8")
    assert "@anthropic-ai/claude-code@2.1.216" in compose
    assert "openclaw@2026.7.1-2" in compose
    assert "CLAUDE_AGENT_SDK_VERSION=0.2.124" in claude
    assert "OPENCLAW_SDK_VERSION=2.1.0" in openclaw
    assert 'COPY docker/openclaw.json /app/docker/openclaw.json' in openclaw
    assert (
        'COPY docker/openclaw-audit-plugin/ /app/docker/openclaw-audit-plugin/'
        in openclaw
    )
    assert "@latest" not in compose
    assert "agentnetwork-native-audit" not in compose
