import pytest

from agent_network import network_emulation
from agent_network.api import simulations
from agent_network.container_runtime import ContainerAgent


def test_normalize_network_profile_accepts_canonical_fields():
    profile = network_emulation.normalize_profile({
        "delay_ms": 20,
        "jitter_ms": 5,
        "loss_pct": 0.5,
        "rate_mbit": 100,
    })
    assert profile == {
        "delay_ms": 20,
        "jitter_ms": 5,
        "loss_pct": 0.5,
        "rate_mbit": 100,
    }


def test_normalize_network_profile_does_not_accept_legacy_aliases():
    profile = network_emulation.normalize_profile({
        "latency_ms": 20,
        "jitter": 5,
        "packet_loss_pct": 0.5,
        "bandwidth_mbps": 100,
    })
    assert profile == {
        "delay_ms": 0,
        "jitter_ms": 0,
        "loss_pct": 0,
        "rate_mbit": 0,
    }


def test_normalize_network_profile_rejects_invalid_loss():
    with pytest.raises(ValueError):
        network_emulation.normalize_profile({"loss_pct": 101})


def test_configure_network_emulation_builds_per_peer_tc_rules(monkeypatch):
    commands = []

    def runner(command):
        commands.append(command)
        return {"command": command, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(network_emulation.shutil, "which", lambda _name: "/sbin/tc")
    result = network_emulation.configure_network_emulation(
        profiles=[{
            "target_agent": "agent_b",
            "target_host": "ag-c2",
            "delay_ms": 20,
            "jitter_ms": 5,
            "loss_pct": 1,
            "rate_mbit": 100,
        }],
        runner=runner,
        resolver=lambda _host: "172.20.0.4",
    )

    assert result["status"] == "configured"
    assert result["profiles"][0]["target_ip"] == "172.20.0.4"
    assert any("netem" in command for command in commands)
    assert any(command[-2:] == ["flowid", "1:10"] for command in commands)


def test_simulation_translates_topology_link_into_two_agent_profiles():
    a = ContainerAgent("a", "A", "role", container_name="ag-a", container_ip="172.20.0.3", url="http://ag-a:8000")
    b = ContainerAgent("b", "B", "role", container_name="ag-b", container_ip="172.20.0.4", url="http://ag-b:8000")
    posted = []

    class Response:
        status_code = 200

        def json(self):
            return {"status": "configured"}

    class Requests:
        @staticmethod
        def post(url, json=None, timeout=None):
            posted.append({"url": url, "json": json, "timeout": timeout})
            return Response()

    result = simulations._configure_network(
        [(a, []), (b, [])],
        [{
            "endpoint_a": "a",
            "endpoint_b": "b",
            "channel_id": "ch_a_b",
            "delay_ms": 20,
            "jitter_ms": 0,
            "loss_pct": 0,
            "rate_mbit": 0,
        }],
        Requests,
    )

    assert result["requested_profiles"] == 2
    assert result["failed"] == 0
    assert posted[0]["json"]["profiles"][0]["target_ip"] == "172.20.0.4"
    assert posted[1]["json"]["profiles"][0]["target_ip"] == "172.20.0.3"
