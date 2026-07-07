import json
import struct
from pathlib import Path
from types import SimpleNamespace

from agent_network import full_packet_capture, real_packet_store
from agent_network.agent_management import ContainerAgent, ContainerRuntime
from agent_network.api import simulations


class _FakeCaptureProcess:
    def __init__(self, returncode=None, stderr=""):
        self.returncode = returncode
        self.pid = 4321
        self.stderr = SimpleNamespace(read=lambda: stderr)

    def poll(self):
        return self.returncode

    def send_signal(self, _signal):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _reset_capture_globals():
    full_packet_capture._capture_process = None
    full_packet_capture._capture_metadata = {}


def test_capture_uses_logical_agent_id_excludes_srv_and_writes_manifest(tmp_path, monkeypatch):
    _reset_capture_globals()
    process = _FakeCaptureProcess()
    commands = []
    monkeypatch.setattr(
        full_packet_capture.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("172.20.0.2", 0))],
    )
    monkeypatch.setattr(
        full_packet_capture.subprocess,
        "Popen",
        lambda cmd, **_kwargs: commands.append(cmd) or process,
    )

    result = full_packet_capture.start_full_capture(
        agent_id="planner",
        runtime_container="ag-c1",
        session_id="session-1",
        trace_id="trace-1",
        pcap_dir=str(tmp_path),
        server_url="http://srv:8000",
        network_profiles=[{"target_agent": "peer", "delay_ms": 20}],
    )

    assert result["status"] == "started"
    assert Path(result["pcap_path"]).parts[-2:] == ("session-1", "planner.pcap")
    assert result["runtime_container"] == "ag-c1"
    assert result["capture_filter"] == "not host 172.20.0.2"
    assert commands[0][-1] == "not host 172.20.0.2"
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["agent_id"] == "planner"
    assert manifest["trace_id"] == "trace-1"
    assert manifest["network_profiles"][0]["delay_ms"] == 20
    Path(result["pcap_path"]).write_bytes(b"0" * 24)

    stopped = full_packet_capture.stop_full_capture()
    assert stopped["status"] == "stopped"
    assert full_packet_capture.capture_status()["status"] == "not_running"


def test_capture_reports_immediate_tcpdump_failure(tmp_path, monkeypatch):
    _reset_capture_globals()
    monkeypatch.setattr(
        full_packet_capture.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeCaptureProcess(1, "denied"),
    )

    result = full_packet_capture.start_full_capture(
        "planner",
        pcap_dir=str(tmp_path),
    )

    assert result["status"] == "error"
    assert result["error"] == "denied"


def test_packet_store_returns_structured_packets_and_binary_stats(tmp_path, monkeypatch):
    session = tmp_path / "session-1"
    session.mkdir()
    pcap = session / "planner.pcap"
    global_header = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
    packet = b"abcd"
    packet_header = struct.pack("<IIII", 1_700_000_000, 500_000, len(packet), 8)
    pcap.write_bytes(global_header + packet_header + packet)
    (session / "planner.manifest.json").write_text(
        json.dumps({
            "agent_id": "planner",
            "runtime_container": "ag-c1",
            "runtime_ip": "172.20.0.3",
            "session_id": "session-1",
            "trace_id": "trace-1",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(real_packet_store, "PCAP_ROOT", tmp_path)
    decoded = "2026-07-02 12:00:00.000000 IP 172.20.0.3.50123 > 1.2.3.4.443: Flags [P.], length 120"
    monkeypatch.setattr(
        real_packet_store,
        "_read_lines",
        lambda *_args, **_kwargs: ([decoded], "", 1),
    )

    packets = real_packet_store.query_packets(session_id="session-1")
    stats = real_packet_store.packet_stats(session_id="session-1")

    assert packets[0]["agent_id"] == "planner"
    assert packets[0]["protocol"] == "TCP"
    assert packets[0]["direction"] == "outbound"
    assert packets[0]["dst_port"] == 443
    assert stats["packet_count"] == 1
    assert stats["captured_bytes"] == 4
    assert stats["wire_bytes"] == 8
    assert stats["files"][0]["valid_pcap"] is True


def test_simulation_capture_passes_logical_identity_and_checks_body_status():
    assignment = ContainerAgent(
        agent_id="planner",
        name="Planner",
        role="planner",
        container_name="ag-c1",
        url="http://ag-c1:8000",
    )
    posted = {}

    class Response:
        status_code = 200

        def json(self):
            return {"status": "started", "pcap_path": "/app/data/pcap/s/planner.pcap"}

    class Requests:
        @staticmethod
        def post(url, json=None, timeout=None):
            posted.update({"url": url, "json": json, "timeout": timeout})
            return Response()

    result = simulations._capture(
        [(assignment, [])],
        True,
        Requests,
        session_id="s",
        trace_id="t",
    )

    assert result["success"] == 1
    assert posted["json"]["agent_id"] == "planner"
    assert posted["json"]["runtime_container"] == "ag-c1"
    assert posted["json"]["trace_id"] == "t"


def test_dynamic_volumes_reuse_srv_host_mount_sources(monkeypatch):
    monkeypatch.setattr(ContainerRuntime, "_init_docker", lambda self: None)
    runtime = ContainerRuntime()
    current = SimpleNamespace(attrs={"Mounts": [
        {"Destination": "/app/services/agent_server.py", "Source": "C:/repo/services/agent_server.py"},
        {"Destination": "/app/agent_network", "Source": "C:/repo/agent_network"},
        {"Destination": "/app/scenes", "Source": "C:/repo/scenes"},
        {"Destination": "/app/data/pcap", "Source": "C:/repo/data/pcap"},
    ]})
    runtime._docker_client = SimpleNamespace(
        containers=SimpleNamespace(get=lambda _name: current)
    )
    monkeypatch.setattr(
        "agent_network.agent_management.os.path.exists",
        lambda path: path == "/.dockerenv",
    )
    monkeypatch.setattr(
        "agent_network.agent_management.socket.gethostname",
        lambda: "srv-id",
    )

    volumes = runtime._dynamic_volumes()

    assert volumes["C:/repo/data/pcap"] == {"bind": "/app/data/pcap", "mode": "rw"}
    assert volumes["C:/repo/agent_network"] == {"bind": "/app/agent_network", "mode": "rw"}
