"""Apply optional per-peer Linux traffic control profiles inside an Agent."""

import os
import shutil
import socket
import subprocess
from typing import Callable


def _number(data: dict, names: tuple[str, ...], default: float = 0.0) -> float:
    for name in names:
        value = data.get(name)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"network.{name} must be numeric")
    return default


def normalize_profile(raw: dict) -> dict:
    raw = raw or {}
    delay_ms = _number(raw, ("delay_ms",))
    jitter_ms = _number(raw, ("jitter_ms",))
    loss_pct = _number(raw, ("loss_pct",))
    rate_mbit = _number(raw, ("rate_mbit",))
    if not 0 <= delay_ms <= 60_000:
        raise ValueError("network delay must be between 0 and 60000 ms")
    if not 0 <= jitter_ms <= 60_000:
        raise ValueError("network jitter must be between 0 and 60000 ms")
    if not 0 <= loss_pct <= 100:
        raise ValueError("network loss must be between 0 and 100 percent")
    if not 0 <= rate_mbit <= 1_000_000:
        raise ValueError("network bandwidth must be between 0 and 1000000 Mbit/s")
    return {
        "delay_ms": delay_ms,
        "jitter_ms": jitter_ms,
        "loss_pct": loss_pct,
        "rate_mbit": rate_mbit,
    }


def _run(command: list[str]) -> dict:
    proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
    return {
        "command": command,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def clear_network_emulation(interface: str = "eth0", runner: Callable = _run) -> dict:
    if not shutil.which("tc"):
        return {"status": "unsupported", "interface": interface, "reason": "tc command is not installed"}
    result = runner(["tc", "qdisc", "del", "dev", interface, "root"])
    # tc returns 2 when no qdisc exists. Clearing is intentionally idempotent.
    return {"status": "cleared", "interface": interface, "command": result}


def configure_network_emulation(
    profiles: list[dict],
    interface: str = "eth0",
    runner: Callable = _run,
    resolver: Callable[[str], str] = socket.gethostbyname,
) -> dict:
    if os.environ.get("AGENT_NETWORK_EMULATION", "1") != "1":
        return {"status": "disabled", "reason": "AGENT_NETWORK_EMULATION!=1"}
    if not shutil.which("tc"):
        return {"status": "error", "error": "tc command is not installed"}

    commands = []
    runner(["tc", "qdisc", "del", "dev", interface, "root"])
    normalized = []
    for item in profiles or []:
        profile = normalize_profile(item)
        if not any(profile.values()):
            continue
        host = item.get("target_host", "")
        try:
            target_ip = item.get("target_ip") or resolver(host)
        except OSError as exc:
            return {"status": "error", "error": f"cannot resolve {host}: {exc}"}
        normalized.append({
            "target_agent": item.get("target_agent", ""),
            "target_host": host,
            "target_ip": target_ip,
            **profile,
        })

    if not normalized:
        return {"status": "cleared", "interface": interface, "profiles": [], "commands": []}

    setup = [
        ["tc", "qdisc", "add", "dev", interface, "root", "handle", "1:", "htb", "default", "1"],
        ["tc", "class", "add", "dev", interface, "parent", "1:", "classid", "1:1", "htb", "rate", "10000mbit", "ceil", "10000mbit"],
    ]
    for command in setup:
        result = runner(command)
        commands.append(result)
        if result["returncode"] != 0:
            clear_network_emulation(interface, runner)
            return {"status": "error", "error": result["stderr"] or "tc setup failed", "commands": commands}

    for index, profile in enumerate(normalized, start=10):
        rate = profile["rate_mbit"] or 10_000
        classid = f"1:{index}"
        class_command = [
            "tc", "class", "add", "dev", interface, "parent", "1:",
            "classid", classid, "htb", "rate", f"{rate:g}mbit", "ceil", f"{rate:g}mbit",
        ]
        commands.append(runner(class_command))

        netem = []
        if profile["delay_ms"] or profile["jitter_ms"]:
            effective_delay = profile["delay_ms"] or 0.1
            netem.extend(["delay", f"{effective_delay:g}ms"])
            if profile["jitter_ms"]:
                netem.append(f"{profile['jitter_ms']:g}ms")
        if profile["loss_pct"]:
            netem.extend(["loss", f"{profile['loss_pct']:g}%"])
        if netem:
            commands.append(runner([
                "tc", "qdisc", "add", "dev", interface, "parent", classid,
                "handle", f"{index}:", "netem", *netem,
            ]))

        commands.append(runner([
            "tc", "filter", "add", "dev", interface, "protocol", "ip", "parent", "1:",
            "prio", str(index), "u32", "match", "ip", "dst", f"{profile['target_ip']}/32",
            "flowid", classid,
        ]))

    failures = [result for result in commands if result["returncode"] != 0]
    if failures:
        clear_network_emulation(interface, runner)
        return {"status": "error", "error": failures[0]["stderr"] or "tc command failed", "commands": commands}
    return {
        "status": "configured",
        "interface": interface,
        "profiles": normalized,
        "commands": commands,
        "semantics": "delay and loss are applied to outbound packets; configure both edge directions for symmetric RTT",
    }
