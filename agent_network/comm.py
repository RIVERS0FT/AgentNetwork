"""Compatibility imports for the unified A2A communication manager.

New code must import from :mod:`agent_network.comm_management`.  The former
DirectBus and its broadcast operation have been removed.
"""

from agent_network.comm_management import (
    A2A_MEDIA_TYPE,
    A2A_PROTOCOL_VERSION,
    BatchSendResult,
    CommManager,
    CommunicationError,
    SendResult,
)

__all__ = [
    "A2A_MEDIA_TYPE",
    "A2A_PROTOCOL_VERSION",
    "BatchSendResult",
    "CommManager",
    "CommunicationError",
    "SendResult",
]
