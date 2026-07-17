"""Unified Agent-to-Agent communication management."""

from .comm_manager import (
    A2A_MEDIA_TYPE,
    A2A_PROTOCOL_VERSION,
    BatchSendResult,
    CommManager,
    CommunicationError,
    SendResult,
)
from .network_emulation import (
    clear_network_emulation,
    configure_network_emulation,
    normalize_profile,
)

__all__ = [
    "A2A_MEDIA_TYPE",
    "A2A_PROTOCOL_VERSION",
    "BatchSendResult",
    "CommManager",
    "CommunicationError",
    "SendResult",
    "clear_network_emulation",
    "configure_network_emulation",
    "normalize_profile",
]
