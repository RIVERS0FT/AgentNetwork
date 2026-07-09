import asyncio

from agent_network.api import packets


def test_temporal_correlation_matches_packets_for_same_agent_only(monkeypatch):
    event_time = packets._event_epoch("2026-07-02T12:00:01.000")
    events = [{
        "event": "llm_runtime_completed",
        "trace_id": "trace-1",
        "timestamp": "2026-07-02T12:00:01.000",
        "agent_id": "agent-a",
        "action": {"duration_ms": 1000},
        "metrics": {},
    }]
    packet_rows = [
        {"parsed": True, "timestamp_epoch": event_time - 0.5, "agent_id": "agent-a", "protocol": "TCP", "direction": "outbound", "ip_payload_bytes": 100},
        {"parsed": True, "timestamp_epoch": event_time - 0.5, "agent_id": "agent-b", "protocol": "TCP", "direction": "outbound", "ip_payload_bytes": 200},
    ]
    monkeypatch.setattr(packets.logger, "query", lambda **_kwargs: events)
    monkeypatch.setattr(packets, "query_packets", lambda *_args, **_kwargs: packet_rows)

    result = asyncio.run(packets.correlate(
        session_id="session-1",
        trace_id="trace-1",
        agent_id=None,
        window_ms=0,
        packet_limit=100,
    ))

    correlation = result["correlations"][0]
    assert result["method"] == "temporal_window_inference"
    assert correlation["agent_id"] == "agent-a"
    assert correlation["matched_packets"] == 1
    assert correlation["matched_ip_payload_bytes"] == 100
    assert "event_id" not in correlation
    assert "actor" not in correlation
    assert correlation["timestamp"] == "2026-07-02T12:00:01.000"
