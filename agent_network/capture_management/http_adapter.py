"""Agent-local HTTP capture adapter backed by CaptureRuntime.

All tcpdump lifecycle and resource behavior is owned by CaptureRuntime.
"""
from __future__ import annotations

import os
from typing import Optional

from agent_network.capture_management.models import CaptureConfig
from agent_network.capture_management.runtime import get_capture_runtime


def _config(interface: str = "any") -> CaptureConfig:
    try:
        max_bytes = max(0, int(os.environ.get("PCAP_MAX_BYTES", str(1024 * 1024 * 1024))))
    except ValueError:
        max_bytes = 1024 * 1024 * 1024
    return CaptureConfig(
        interface=interface,
        max_bytes=max_bytes,
        include_control_plane=os.environ.get("AGENT_CAPTURE_INCLUDE_CONTROL_PLANE", "0") == "1",
    )


def start_full_capture(
    agent_id: str,
    session_id: str = "",
    pcap_dir: str = "/app/data/pcap",
    interface: str = "any",
    runtime_container: str = "",
    runtime_container_id: str = "",
    runtime_ip: str = "",
    trace_id: str = "",
    server_url: str = "",
    network_profiles: Optional[list] = None,
):
    del pcap_dir
    if os.environ.get("LOG_FULL_PCAP", "1") != "1":
        return {"status": "disabled", "reason": "LOG_FULL_PCAP!=1"}
    # Until the Agent HTTP contract carries capture_id explicitly, the shared
    # session_id is the stable distributed capture identifier.
    capture_id = session_id or f"capture-{agent_id}"
    return get_capture_runtime().start(
        capture_id=capture_id,
        session_id=session_id or capture_id,
        agent_id=agent_id,
        config=_config(interface),
        runtime_container=runtime_container,
        runtime_container_id=runtime_container_id,
        runtime_ip=runtime_ip,
        trace_id=trace_id,
        server_url=server_url,
        network_profiles=network_profiles or [],
    )


def capture_status() -> dict:
    return get_capture_runtime().status()


def stop_full_capture() -> dict:
    return get_capture_runtime().stop(reason="requested")
