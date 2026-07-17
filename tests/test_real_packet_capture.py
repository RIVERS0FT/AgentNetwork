import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_network.capture_management import http_adapter as full_packet_capture
from agent_network.capture_management import packet_store as real_packet_store
from agent_network.agent_management import ContainerRuntime
from agent_network.capture_management import runtime as capture_runtime
from agent_network.file_management import get_file_manager, reset_file_manager


class _FakeCaptureProcess:
    def __init__(self, returncode=None, stderr=''):
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


@pytest.fixture
def managed_env(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    monkeypatch.setenv('DATA_DIR', str(data))
    monkeypatch.setenv('SCENE_DIR', str(tmp_path / 'scenes'))
    monkeypatch.setenv('LOG_DIR', str(data / 'logs'))
    monkeypatch.setenv('PCAP_DIR', str(data / 'pcap'))
    monkeypatch.setenv('ARCHIVE_DIR', str(data / 'archives'))
    monkeypatch.setenv('FILE_TEMP_DIR', str(data / 'tmp'))
    monkeypatch.setenv('FILE_REGISTRY_PATH', str(data / 'pcap/.file_registry.json'))
    reset_file_manager()
    capture_runtime._runtime = None
    yield get_file_manager()
    capture_runtime._runtime = None
    reset_file_manager()


def test_capture_registers_resources_without_exposing_paths(managed_env, monkeypatch):
    process = _FakeCaptureProcess()
    commands = []
    monkeypatch.setattr(
        capture_runtime.socket,
        'getaddrinfo',
        lambda *_args, **_kwargs: [(2, 1, 6, '', ('172.20.0.2', 0))],
    )

    def fake_popen(cmd, **_kwargs):
        commands.append(cmd)
        path = Path(cmd[cmd.index('-w') + 1])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'')
        return process

    monkeypatch.setattr(capture_runtime.subprocess, 'Popen', fake_popen)
    result = full_packet_capture.start_full_capture(
        agent_id='planner',
        runtime_container='ag-c1',
        session_id='session-1',
        trace_id='trace-1',
        pcap_dir=str(managed_env.root_path('pcap')),
        server_url='http://srv:8000',
    )

    assert result['status'] == 'started'
    assert 'pcap_path' not in result
    assert result['pcap_resource_id']
    assert commands[0][-1] == 'not host 172.20.0.2'

    managed_env.resolve_resource_path(result['pcap_resource_id']).write_bytes(b'0' * 24)
    stopped = full_packet_capture.stop_full_capture()
    assert stopped['status'] == 'stopped'
    assert stopped['sha256']


def test_packet_store_reads_registered_external_capture(managed_env, monkeypatch):
    session = managed_env.resolve_path('pcap', 'session-1')
    session.mkdir(parents=True)
    global_header = b'\xd4\xc3\xb2\xa1' + struct.pack(
        '<HHIIII', 2, 4, 0, 0, 65535, 1
    )
    packet = b'abcd'
    packet_header = struct.pack('<IIII', 1_700_000_000, 500_000, 4, 8)
    (session / 'planner.pcap').write_bytes(global_header + packet_header + packet)
    (session / 'planner.manifest.json').write_text(
        json.dumps({
            'agent_id': 'planner',
            'runtime_container': 'ag-c1',
            'runtime_ip': '172.20.0.3',
            'session_id': 'session-1',
            'trace_id': 'trace-1',
        }),
        encoding='utf-8',
    )
    decoded = (
        '2026-07-02 12:00:00.000000 IP '
        '172.20.0.3.50123 > 1.2.3.4.443: Flags [P.], length 120'
    )
    monkeypatch.setattr(
        real_packet_store,
        '_read_lines',
        lambda *_args, **_kwargs: ([decoded], '', 1),
    )

    packets = real_packet_store.query_packets(session_id='session-1')
    stats = real_packet_store.packet_stats(session_id='session-1')

    assert packets[0]['pcap_resource_id']
    assert 'pcap' not in packets[0]
    assert packets[0]['direction'] == 'outbound'
    assert stats['packet_count'] == 1
    assert stats['captured_bytes'] == 4


def test_dynamic_volumes_reuse_shared_pcap_mount(monkeypatch):
    monkeypatch.setattr(ContainerRuntime, '_init_docker', lambda self: None)
    runtime = ContainerRuntime()
    current = SimpleNamespace(attrs={'Mounts': [
        {'Destination': '/app/services/agent_server.py', 'Source': 'C:/repo/services/agent_server.py'},
        {'Destination': '/app/agent_network', 'Source': 'C:/repo/agent_network'},
        {'Destination': '/app/scenes', 'Source': 'C:/repo/scenes'},
        {'Destination': '/app/data/pcap', 'Source': 'C:/repo/data/pcap'},
    ]})
    runtime._docker_client = SimpleNamespace(
        containers=SimpleNamespace(get=lambda _name: current)
    )
    monkeypatch.setattr(
        'agent_network.agent_management.os.path.exists',
        lambda path: path == '/.dockerenv',
    )
    monkeypatch.setattr(
        'agent_network.agent_management.socket.gethostname',
        lambda: 'srv-id',
    )

    volumes = runtime._dynamic_volumes()

    assert volumes['C:/repo/data/pcap'] == {
        'bind': '/app/data/pcap',
        'mode': 'rw',
    }
