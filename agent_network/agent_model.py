"""Compatibility layer for the unified Agent management module.

New code should import Agent, AgentRegistry, and Message from
``agent_network.agent_management``.
"""

from .agent_management import Agent, AgentRegistry, Message

__all__ = ["Agent", "AgentRegistry", "Message"]
