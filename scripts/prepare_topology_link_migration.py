#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"cannot locate {label} in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def make_channel_ids_unique() -> None:
    for path in sorted((ROOT / "scenes").rglob("network_topology.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        sub_networks = document.get("sub_networks")
        if not isinstance(sub_networks, list):
            continue
        edges = [
            edge
            for subnet in sub_networks
            if isinstance(subnet, dict)
            for edge in (subnet.get("edges") or [])
            if isinstance(edge, dict)
        ]
        counts = {}
        for edge in edges:
            channel_id = str(edge.get("channel_id") or "").strip()
            if channel_id:
                counts[channel_id] = counts.get(channel_id, 0) + 1
        used = set()
        for edge in edges:
            endpoint_a = str(edge.get("source") or edge.get("from") or "").strip().lower()
            endpoint_b = str(edge.get("target") or edge.get("to") or "").strip().lower()
            original = str(edge.get("channel_id") or f"ch_{endpoint_a}_{endpoint_b}").strip()
            candidate = original
            if counts.get(original, 0) > 1:
                candidate = f"{original}__{endpoint_a}__{endpoint_b}"
            base = candidate
            suffix = 2
            while candidate in used:
                candidate = f"{base}__{suffix}"
                suffix += 1
            used.add(candidate)
            edge["channel_id"] = candidate
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def flatten_network_profiles() -> None:
    path = ROOT / "agent_network" / "api" / "simulations.py"
    old = '''    def add_profile(source: str, target: str, network: dict):
        profile = {
            "target_agent": target,
            "target_host": agents[target].container_name,
            "target_ip": agents[target].container_ip,
            "network": network,
        }
        previous = profile_maps[source].get(target)
        if previous and previous["network"] != network:
            validation_errors.append(f"conflicting network profiles for {source}->{target}")
            return
        profile_maps[source][target] = profile
'''
    new = '''    def add_profile(source: str, target: str, network: dict):
        profile = {
            "target_agent": target,
            "target_host": agents[target].container_name,
            "target_ip": agents[target].container_ip,
            **network,
        }
        previous = profile_maps[source].get(target)
        if previous and any(
            previous.get(field) != network.get(field)
            for field in ("delay_ms", "jitter_ms", "loss_pct", "rate_mbit")
        ):
            validation_errors.append(f"conflicting network profiles for {source}->{target}")
            return
        profile_maps[source][target] = profile
'''
    replace_once(path, old, new, "network profile builder")


def remove_network_aliases() -> None:
    path = ROOT / "agent_network" / "network_emulation.py"
    replacements = {
        '("delay_ms", "latency_ms", "latency")': '("delay_ms",)',
        '("jitter_ms", "jitter")': '("jitter_ms",)',
        '("loss_pct", "loss_percent", "packet_loss_pct", "packet_loss")': '("loss_pct",)',
        '("rate_mbit", "bandwidth_mbps", "bandwidth")': '("rate_mbit",)',
    }
    text = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        if old not in text:
            raise RuntimeError(f"cannot locate network alias {old}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8", newline="\n")


def remove_dashboard_residuals() -> None:
    path = ROOT / "web" / "public" / "dashboard.js"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "const rf = rel.from.toLowerCase();",
        "const rf = String(rel.endpoint_a || '').toLowerCase();",
    )
    text = text.replace(
        "const rt = rel.to.toLowerCase();",
        "const rt = String(rel.endpoint_b || '').toLowerCase();",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def update_network_alias_test() -> None:
    path = ROOT / "tests" / "test_network_emulation.py"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"def test_normalize_network_profile_accepts_documented_aliases\(\):.*?(?=\n\ndef test_normalize_network_profile_rejects_invalid_loss)",
        re.S,
    )
    replacement = '''def test_normalize_network_profile_accepts_canonical_fields():
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
'''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError("cannot replace network alias test")
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    make_channel_ids_unique()
    flatten_network_profiles()
    remove_network_aliases()
    remove_dashboard_residuals()
    update_network_alias_test()


if __name__ == "__main__":
    main()
