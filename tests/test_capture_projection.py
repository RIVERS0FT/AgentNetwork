from __future__ import annotations

from agent_network.capture_management.models import CaptureConfig, CaptureSession
from agent_network.capture_management.projection import PacketProjectionService
from agent_network.file_management import FileManager


class FakeLogManager:
    def __init__(self):
        self.records = []
        self.session_dir = ""

    def set_session_dir(self, value):
        self.session_dir = value

    def emit_network_event(self, **value):
        self.records.append(value)
        return value


def test_projection_is_idempotent_for_same_pcap_fingerprint(tmp_path, monkeypatch):
    files = FileManager(
        {
            "pcap": tmp_path / "pcap",
            "logs": tmp_path / "logs",
            "archives": tmp_path / "archives",
            "temp": tmp_path / "temp",
            "scenes": tmp_path / "scenes",
        },
        catalog_path=tmp_path / "registry.json",
    )
    files.write_bytes(
        b"pcap-source",
        owner_type="capture_session",
        owner_id="capture-1",
        resource_type="pcap",
        root_name="pcap",
        relative_path="session-1/planner.pcap",
        logical_name="planner.pcap",
        resource_id="pcap-planner",
    )
    session = CaptureSession(
        capture_id="capture-1",
        simulation_id="simulation-1",
        session_id="session-1",
        trace_id="trace-1",
        config=CaptureConfig(),
        expected_agents=["planner"],
        targets={},
    )
    packets = [{
        "parsed": True,
        "timestamp": "2026-07-15T00:00:00+00:00",
        "trace_id": "trace-1",
        "agent_id": "planner",
        "runtime_container": "ag-c1",
        "pcap_resource_id": "pcap-planner",
        "pcap_name": "planner.pcap",
        "raw": "1.0 IP 10.0.0.1.1 > 10.0.0.2.2: Flags [P.], length 4",
        "ip_version": "IP",
        "protocol": "TCP",
        "src_ip": "10.0.0.1",
        "src_port": 1,
        "dst_ip": "10.0.0.2",
        "dst_port": 2,
        "direction": "outbound",
        "traffic_class": "agent_peer",
        "src_agent": "planner",
        "dst_agent": "developer",
        "tcp_flags": "P.",
        "ip_payload_bytes": 4,
    }]
    fake_logs = FakeLogManager()
    monkeypatch.setattr("agent_network.capture_management.packet_store.query_packets", lambda **_kwargs: packets)
    monkeypatch.setattr("agent_network.capture_management.projection.get_log_manager", lambda: fake_logs)

    service = PacketProjectionService(files)
    first = service.project(session)
    second = service.project(session)

    assert first["status"] == "complete"
    assert first["packets_written"] == 1
    assert second["skipped"] is True
    assert len(fake_logs.records) == 1
    assert fake_logs.records[0]["context"]["capture_id"] == "capture-1"
    assert fake_logs.records[0]["network"]["pcap_resource_id"] == "pcap-planner"
