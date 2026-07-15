from __future__ import annotations

import pytest

from agent_network.capture_management.manager import CaptureManager
from agent_network.capture_management.models import CaptureConfig, CaptureState
from agent_network.capture_management.repository import CaptureRepository
from agent_network.file_management import FileManager


class FakeCaptureManager(CaptureManager):
    def __init__(self, repository, failing_agent=""):
        super().__init__(repository)
        self.failing_agent = failing_agent
        self.stopped = []

    @staticmethod
    def _body(target, status):
        return {
            "status": status,
            "state": "running" if status == "started" else "stopped",
            "pid": 100,
            "pcap_resource_id": f"pcap-{target.agent_id}",
            "manifest_resource_id": f"manifest-{target.agent_id}",
        }

    def _start_target(self, session, target):
        if target.agent_id == self.failing_agent:
            raise RuntimeError("start failed")
        return self._body(target, "started")

    def _stop_target(self, session, target, reason):
        self.stopped.append((target.agent_id, reason))
        return self._body(target, "stopped")


def _repository(tmp_path):
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
    return CaptureRepository(files)


def _targets():
    return [
        {"agent_id": "planner", "runtime_url": "http://planner:8000"},
        {"agent_id": "developer", "runtime_url": "http://developer:8000"},
    ]


def test_capture_config_rejects_invalid_projection_mode():
    with pytest.raises(ValueError):
        CaptureConfig(projection_mode="live").validate()


def test_capture_manager_starts_and_stops_all_targets(tmp_path):
    manager = FakeCaptureManager(_repository(tmp_path))
    session = manager.create_session(
        simulation_id="simulation-1",
        session_id="session-1",
        trace_id="trace-1",
        capture_id="capture-1",
        targets=_targets(),
    )

    started = manager.start_session(session.capture_id)
    assert started.state == CaptureState.RUNNING
    assert all(target.state == CaptureState.RUNNING for target in started.targets.values())

    stopped = manager.stop_session(session.capture_id, "complete")
    assert stopped.state == CaptureState.STOPPED
    assert sorted(manager.stopped) == [
        ("developer", "complete"),
        ("planner", "complete"),
    ]


def test_capture_manager_rolls_back_partial_start(tmp_path):
    manager = FakeCaptureManager(_repository(tmp_path), failing_agent="developer")
    session = manager.create_session(
        simulation_id="simulation-1",
        session_id="session-1",
        trace_id="trace-1",
        capture_id="capture-1",
        targets=_targets(),
    )

    failed = manager.start_session(session.capture_id)

    assert failed.state == CaptureState.FAILED
    assert failed.targets["developer"].state == CaptureState.FAILED
    assert manager.stopped == [("planner", "capture_start_failed")]
