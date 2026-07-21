"""Backend-native capability policy, validation and audit helpers.

AgentNetwork scene Tools remain separate from backend-native tools.  This
module provides the canonical names used to configure Claude Code and
OpenClaw without letting backend-specific names become the authorization
contract.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CANONICAL_CAPABILITIES = frozenset(
    {
        "fs.read",
        "fs.search",
        "fs.write",
        "process.exec",
        "web.fetch",
        "web.search",
        "browser.control",
        "agent.spawn",
        "agent.message",
        "automation.schedule",
        "channel.send",
        "device.control",
        "media.generate",
    }
)

DEFAULT_ALLOWED = frozenset(
    {"fs.read", "fs.search", "web.fetch", "agent.spawn"}
)
DEFAULT_DENIED = CANONICAL_CAPABILITIES - DEFAULT_ALLOWED
PLATFORM_ONLY_CAPABILITIES = frozenset({"agent.message", "channel.send"})

CLAUDE_TOOL_CAPABILITIES = {
    "Read": "fs.read",
    "Glob": "fs.search",
    "Grep": "fs.search",
    "Write": "fs.write",
    "Edit": "fs.write",
    "NotebookEdit": "fs.write",
    "Bash": "process.exec",
    "WebFetch": "web.fetch",
    "WebSearch": "web.search",
    "Chrome": "browser.control",
    "Computer": "browser.control",
    "Agent": "agent.spawn",
    "SendMessage": "agent.message",
}

OPENCLAW_TOOL_CAPABILITIES = {
    "read": "fs.read",
    "write": "fs.write",
    "edit": "fs.write",
    "apply_patch": "fs.write",
    "exec": "process.exec",
    "process": "process.exec",
    "terminal": "process.exec",
    "code_execution": "process.exec",
    "web_fetch": "web.fetch",
    "web_search": "web.search",
    "x_search": "web.search",
    "browser": "browser.control",
    "sessions_spawn": "agent.spawn",
    "subagents": "agent.spawn",
    "sessions_send": "agent.message",
    "message": "channel.send",
    "cron": "automation.schedule",
    "nodes": "device.control",
    "image_generate": "media.generate",
    "music_generate": "media.generate",
    "video_generate": "media.generate",
}

_SECRET_KEYS = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|cookie|credential)",
    re.IGNORECASE,
)
_SECRET_VALUES = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|"
    r"((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+"
)
_DANGEROUS_COMMANDS = re.compile(
    r"(^|[;&|]\s*)(rm\s+-rf|git\s+reset\s+--hard|shutdown|reboot|mkfs|"
    r"diskpart|format\s+[a-z]:|Remove-Item\s+.*-Recurse)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NativeSubagentPolicy:
    enabled: bool = True
    max_children: int = 3
    max_depth: int = 1
    max_parallel: int = 2
    context_mode: str = "summary"
    child_can_spawn: bool = False
    register_in_platform: bool = True
    model: str = "inherit"

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "NativeSubagentPolicy":
        raw = dict(value or {})
        unknown = set(raw) - {
            'enabled', 'max_children', 'max_depth', 'max_parallel',
            'context_mode', 'child_can_spawn', 'register_in_platform', 'model'
        }
        if unknown:
            raise ValueError(f'unknown native subagent fields: {sorted(unknown)}')
        policy = cls(
            enabled=bool(raw.get("enabled", True)),
            max_children=int(raw.get("max_children", 3)),
            max_depth=int(raw.get("max_depth", 1)),
            max_parallel=int(raw.get("max_parallel", 2)),
            context_mode=str(raw.get("context_mode", "summary")),
            child_can_spawn=bool(raw.get("child_can_spawn", False)),
            register_in_platform=bool(raw.get("register_in_platform", True)),
            model=str(raw.get("model", "inherit") or "inherit"),
        )
        if not 0 <= policy.max_children <= 32:
            raise ValueError("native subagents.max_children must be between 0 and 32")
        if not 0 <= policy.max_depth <= 5:
            raise ValueError("native subagents.max_depth must be between 0 and 5")
        if not 1 <= policy.max_parallel <= 32:
            raise ValueError("native subagents.max_parallel must be between 1 and 32")
        if policy.context_mode not in {"none", "summary", "fork"}:
            raise ValueError("native subagents.context_mode must be none, summary or fork")
        return policy


@dataclass(frozen=True)
class NativeSessionPolicy:
    persistent: bool = True
    ttl_seconds: int = 1800

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "NativeSessionPolicy":
        raw = dict(value or {})
        unknown = set(raw) - {'persistent', 'ttl_seconds'}
        if unknown:
            raise ValueError(f'unknown native session fields: {sorted(unknown)}')
        policy = cls(
            persistent=bool(raw.get("persistent", True)),
            ttl_seconds=int(raw.get("ttl_seconds", 1800)),
        )
        if not 0 <= policy.ttl_seconds <= 604800:
            raise ValueError("native session.ttl_seconds must be between 0 and 604800")
        return policy


@dataclass(frozen=True)
class NativeAuditPolicy:
    required: bool = True
    input_capture: str = "redacted"
    output_capture: str = "hash_and_preview"
    preview_chars: int = 2000

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "NativeAuditPolicy":
        raw = dict(value or {})
        unknown = set(raw) - {
            'required', 'input_capture', 'output_capture', 'preview_chars'
        }
        if unknown:
            raise ValueError(f'unknown native audit fields: {sorted(unknown)}')
        policy = cls(
            required=bool(raw.get("required", True)),
            input_capture=str(raw.get("input_capture", "redacted")),
            output_capture=str(raw.get("output_capture", "hash_and_preview")),
            preview_chars=int(raw.get("preview_chars", 2000)),
        )
        if not policy.required:
            raise ValueError("native audit.required must remain true")
        if policy.input_capture not in {"hash", "redacted"}:
            raise ValueError("native audit.input_capture is invalid")
        if policy.output_capture not in {"hash", "hash_and_preview"}:
            raise ValueError("native audit.output_capture is invalid")
        if not 0 <= policy.preview_chars <= 20000:
            raise ValueError("native audit.preview_chars must be between 0 and 20000")
        return policy


@dataclass(frozen=True)
class NativeCapabilityPolicy:
    enabled: bool = True
    profile: str = "audited_read_only"
    allow: frozenset[str] = field(default_factory=lambda: DEFAULT_ALLOWED)
    deny: frozenset[str] = field(default_factory=lambda: DEFAULT_DENIED)
    subagents: NativeSubagentPolicy = field(default_factory=NativeSubagentPolicy)
    session: NativeSessionPolicy = field(default_factory=NativeSessionPolicy)
    audit: NativeAuditPolicy = field(default_factory=NativeAuditPolicy)

    @classmethod
    def disabled(cls) -> "NativeCapabilityPolicy":
        return cls(
            enabled=False,
            profile="disabled",
            allow=frozenset(),
            deny=CANONICAL_CAPABILITIES,
            subagents=NativeSubagentPolicy(enabled=False, max_children=0),
        )

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any] | None,
        *,
        backend: str = "",
    ) -> "NativeCapabilityPolicy":
        if backend == "direct_llm":
            return cls.disabled()
        raw = dict(value or {})
        unknown = set(raw) - {
            'enabled', 'profile', 'tools', 'subagents', 'session', 'audit'
        }
        if unknown:
            raise ValueError(f'unknown native capability fields: {sorted(unknown)}')
        if raw.get("enabled") is False:
            return cls.disabled()
        tools = raw.get("tools") or {}
        if not isinstance(tools, dict):
            raise ValueError("native_capabilities.tools must be an object")
        unknown_tools = set(tools) - {'allow', 'deny'}
        if unknown_tools:
            raise ValueError(f'unknown native tool policy fields: {sorted(unknown_tools)}')
        for field_name in ('allow', 'deny'):
            value_list = tools.get(field_name)
            if value_list is not None and (
                not isinstance(value_list, list)
                or not all(isinstance(item, str) and item for item in value_list)
            ):
                raise ValueError(f'native_capabilities.tools.{field_name} must be a string array')
        allow = frozenset(tools.get("allow", DEFAULT_ALLOWED))
        deny = frozenset(tools.get("deny", CANONICAL_CAPABILITIES - allow))
        unknown = (allow | deny) - CANONICAL_CAPABILITIES
        if unknown:
            raise ValueError(f"unknown native capabilities: {sorted(unknown)}")
        platform_only = allow & PLATFORM_ONLY_CAPABILITIES
        if platform_only:
            raise ValueError(
                "native messaging capabilities must use CommManager: "
                f"{sorted(platform_only)}"
            )
        return cls(
            enabled=bool(raw.get("enabled", True)),
            profile=str(raw.get("profile", "audited_read_only")),
            allow=allow,
            deny=deny,
            subagents=NativeSubagentPolicy.from_dict(raw.get("subagents")),
            session=NativeSessionPolicy.from_dict(raw.get("session")),
            audit=NativeAuditPolicy.from_dict(raw.get("audit")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "profile": self.profile,
            "tools": {
                "allow": sorted(self.allow),
                "deny": sorted(self.deny),
            },
            "subagents": asdict(self.subagents),
            "session": asdict(self.session),
            "audit": asdict(self.audit),
        }

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def allows(self, capability: str) -> bool:
        return (
            self.enabled
            and capability not in PLATFORM_ONLY_CAPABILITIES
            and capability in self.allow
            and capability not in self.deny
        )


def capability_for_tool(backend: str, tool_name: str) -> str:
    name = str(tool_name or "")
    if name.startswith("mcp__agent_tools__"):
        return "platform.tool"
    mapping = (
        CLAUDE_TOOL_CAPABILITIES
        if backend == "claude-code"
        else OPENCLAW_TOOL_CAPABILITIES
    )
    return mapping.get(name, "unknown")


def backend_allowed_tools(backend: str, policy: NativeCapabilityPolicy) -> list[str]:
    mapping = (
        CLAUDE_TOOL_CAPABILITIES
        if backend == "claude-code"
        else OPENCLAW_TOOL_CAPABILITIES
    )
    return sorted(name for name, capability in mapping.items() if policy.allows(capability))


def backend_denied_tools(backend: str, policy: NativeCapabilityPolicy) -> list[str]:
    mapping = (
        CLAUDE_TOOL_CAPABILITIES
        if backend == "claude-code"
        else OPENCLAW_TOOL_CAPABILITIES
    )
    return sorted(name for name, capability in mapping.items() if not policy.allows(capability))


def redact_value(value: Any, preview_chars: int = 2000) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[str(key)] = (
                "***REDACTED***"
                if _SECRET_KEYS.search(str(key))
                else redact_value(item, preview_chars)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(item, preview_chars) for item in value]
    text = str(value) if value is not None else ""
    text = _SECRET_VALUES.sub(
        lambda match: (match.group(1) or match.group(2) or "") + "***REDACTED***",
        text,
    )
    return text if len(text) <= preview_chars else text[:preview_chars] + "…"


def value_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def audit_value(value: Any, policy: NativeAuditPolicy, *, output: bool = False) -> dict:
    mode = policy.output_capture if output else policy.input_capture
    result = {"sha256": value_sha256(value)}
    if mode in {"redacted", "hash_and_preview"}:
        result["preview"] = redact_value(value, policy.preview_chars)
    return result if mode != "none" else {}


def _candidate_path(tool_input: dict[str, Any]) -> str:
    for key in ("file_path", "path", "notebook_path", "cwd"):
        if tool_input.get(key):
            return str(tool_input[key])
    return ""


def _path_allowed(path: str, capability: str) -> tuple[bool, str]:
    if not path:
        return True, "no path argument"
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path(os.environ.get("AGENT_NATIVE_WORKSPACE", "/app")) / resolved
    try:
        resolved = resolved.resolve()
    except OSError:
        return False, "path cannot be resolved"
    allowed_roots = [
        Path(item).resolve()
        for item in os.environ.get("AGENT_NATIVE_ALLOWED_ROOTS", "/app").split(os.pathsep)
        if item
    ]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        return False, "path is outside native capability roots"
    lowered_parts = {part.lower() for part in resolved.parts}
    if (
        resolved.name.lower()
        in {".env", "credentials", "auth-profiles.json", "id_rsa", "id_ed25519"}
        or lowered_parts & {".ssh", ".aws", ".gnupg"}
    ):
        return False, "sensitive credential path is denied"
    if capability == "fs.write":
        read_only_roots = [Path(os.environ.get("SCENE_DIR", "/app/scenes")).resolve()]
        if any(resolved == root or root in resolved.parents for root in read_only_roots):
            return False, "scene sources are read-only"
    return True, "path is allowed"


def _url_allowed(value: str) -> tuple[bool, str]:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "only http and https URLs are allowed"
    host = parsed.hostname.lower()
    if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):
        return False, "local network URLs are denied"
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    host, parsed.port or 443, type=socket.SOCK_STREAM
                )
            }
        except (OSError, ValueError):
            return False, "hostname cannot be resolved safely"
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        for address in addresses
    ):
        return False, "private network URLs are denied"
    return True, "public URL allowed"


def evaluate_tool_call(
    policy: NativeCapabilityPolicy,
    backend: str,
    tool_name: str,
    tool_input: dict[str, Any] | None,
    *,
    child_count: int = 0,
    active_child_count: int | None = None,
    spawn_depth: int = 0,
) -> dict[str, Any]:
    payload = dict(tool_input or {})
    capability = capability_for_tool(backend, tool_name)
    if capability == "platform.tool":
        return {"allowed": True, "capability": capability, "reason": "platform tool"}
    if capability == "unknown":
        return {"allowed": False, "capability": capability, "reason": "unknown native tool"}
    if not policy.allows(capability):
        return {"allowed": False, "capability": capability, "reason": "capability denied by policy"}
    if capability.startswith("fs."):
        allowed, reason = _path_allowed(_candidate_path(payload), capability)
        if not allowed:
            return {"allowed": False, "capability": capability, "reason": reason}
    if capability == "process.exec":
        command = str(payload.get("command") or payload.get("cmd") or "")
        if _DANGEROUS_COMMANDS.search(command):
            return {"allowed": False, "capability": capability, "reason": "dangerous command denied"}
    if capability in {"web.fetch", "web.search", "browser.control"}:
        url = str(payload.get("url") or payload.get("query_url") or "")
        if url:
            allowed, reason = _url_allowed(url)
            if not allowed:
                return {"allowed": False, "capability": capability, "reason": reason}
    if capability == "agent.spawn":
        subagents = policy.subagents
        active_children = child_count if active_child_count is None else active_child_count
        if not subagents.enabled:
            return {"allowed": False, "capability": capability, "reason": "subagents disabled"}
        if spawn_depth > 0 and not subagents.child_can_spawn:
            return {"allowed": False, "capability": capability, "reason": "child agents cannot spawn"}
        if child_count >= subagents.max_children:
            return {"allowed": False, "capability": capability, "reason": "maximum child count reached"}
        if active_children >= subagents.max_parallel:
            return {"allowed": False, "capability": capability, "reason": "maximum parallel child count reached"}
        if spawn_depth >= subagents.max_depth:
            return {"allowed": False, "capability": capability, "reason": "maximum spawn depth reached"}
    return {"allowed": True, "capability": capability, "reason": "allowed by native capability policy"}
