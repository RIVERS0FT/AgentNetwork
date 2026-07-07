"""Compatibility layer for the unified Agent management module.

New code should import runtime types from ``agent_network.agent_management``.
"""

from .agent_management import (
    ContainerAgent,
    ContainerRuntime,
    SYSTEM_TOOLS,
    get_runtime,
    os,
    requests,
    socket,
)

__all__ = [
    "ContainerAgent",
    "ContainerRuntime",
    "SYSTEM_TOOLS",
    "get_runtime",
]
