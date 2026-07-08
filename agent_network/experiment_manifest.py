"""Experiment provenance and capture quality checks."""

import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PCAP_ROOT = Path(os.environ.get("PCAP_DIR", "data/pcap"))
LOG_ROOT = Path(os.environ.get("LOG_DIR", "data/logs"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_scene(scene_dir: Path) -> dict:
    files = []
    digest = hashlib.sha256()
    if scene_dir.is_dir():
        for path in sorted(
            p
            for p in scene_dir.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        ):
            relative = path.relative_to(scene_dir).as_posix()
            file_hash = sha256_file(path)
            files.append(
                {
                    "path": relative,
                    "sha256": file_hash,
                    "bytes": path.stat().st_size,
                }
            )
            digest.update(relative.encode("utf-8"))
            digest.update(file_hash.encode("ascii"))
    return {"sha256": digest.hexdigest(), "files": files}


def sanitize_config(value: Any):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(
                marker in lowered
                for marker in ("key", "token", "secret", "password")
            ):
                sanitized[key] = "***REDACTED***"
            else:
                sanitized[key] = sanitize_config(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_config(item) for item in value]
    return value


def create_manifest(
    session_id: str,
    scene_name: str,
    scene_dir: Path,
    trace_id: str,
    seed: int,
    agents: list[dict],
    llm_config: dict,
    scheduler: dict = None,
) -> dict:
    sanitized_config = sanitize_config(llm_config)
    config_sha256 = hashlib.sha256(
        json.dumps(
            sanitized_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "agent-traffic-experiment.v1",
        "status": "running",
        "session_id": session_id,
        "trace_id": trace_id,
        "scene_name": scene_name,
        "seed": seed,
        "started_at": _now_iso(),
        "scene": hash_scene(scene_dir),
        "agents": agents,
        "llm_config": sanitized_config,
        "llm_config_sha256": config_sha256,
        "scheduler": scheduler or {},
    }
    _atomic_json(PCAP_ROOT / session_id / "experiment.manifest.json", manifest)
    return manifest


def finalize_manifest(session_id: str, **updates) -> dict:
    path = PCAP_ROOT / session_id / "experiment.manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {
            "schema_version": "agent-traffic-experiment.v1",
            "session_id": session_id,
        }
    manifest.update(updates)
    manifest["finished_at"] = _now_iso()
    _atomic_json(path, manifest)
    return manifest


def load_manifest(session_id: str) -> dict:
    base = PCAP_ROOT.resolve()
    path = (PCAP_ROOT / session_id / "experiment.manifest.json").resolve()
    try:
        path.relative_to(base)
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _application_counts(session_id: str, trace_id: str) -> tuple[int, dict]:
    base = LOG_ROOT.resolve()
    path = (LOG_ROOT / session_id / "application.jsonl").resolve()
    total = 0
    by_agent = {}
    try:
        path.relative_to(base)
    except ValueError:
        return total, by_agent
    if not path.is_file():
        return total, by_agent
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            record_trace = record.get("trace_id") or (
                record.get("trace") or {}
            ).get("trace_id")
            if trace_id and record_trace != trace_id:
                continue
            total += 1
            actor_id = (record.get("actor") or {}).get("agent_id") or (
                record.get("actor") or {}
            ).get("id")
            target_id = (record.get("target") or {}).get("agent_id") or (
                record.get("target") or {}
            ).get("id")
            participants = {
                agent_id for agent_id in (actor_id, target_id) if agent_id
            } or {"unknown"}
            for agent_id in participants:
                by_agent[agent_id] = by_agent.get(agent_id, 0) + 1
    return total, by_agent


def audit_session(session_id: str, verify_hashes: bool = True) -> dict:
    base = PCAP_ROOT.resolve()
    session_dir = (PCAP_ROOT / session_id).resolve()
    try:
        session_dir.relative_to(base)
    except ValueError:
        return {
            "status": "failed",
            "passed": False,
            "session_id": session_id,
            "issues": ["invalid session path"],
        }
    experiment = load_manifest(session_id)
    if not experiment:
        return {
            "status": "failed",
            "passed": False,
            "session_id": session_id,
            "issues": ["experiment manifest missing or invalid"],
        }

    issues = []
    captures = []
    expected_agents = {
        str(item.get("agent_id", ""))
        for item in experiment.get("agents", [])
        if item.get("agent_id")
    }
    for item in experiment.get("agents", []):
        if item.get("agent_id") and not item.get("image_id"):
            issues.append(f"{item['agent_id']}: container image identity missing")
    if not (experiment.get("scene") or {}).get("files"):
        issues.append("scene provenance is empty")
    observed_agents = set()
    for path in sorted(session_dir.glob("*.manifest.json")):
        if path.name == "experiment.manifest.json":
            continue
        try:
            capture = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            issues.append(f"invalid capture manifest {path.name}: {exc}")
            continue
        agent_id = capture.get("agent_id") or path.name.removesuffix(
            ".manifest.json"
        )
        observed_agents.add(agent_id)
        pcap = session_dir / f"{agent_id}.pcap"
        checks = {
            "manifest_stopped": capture.get("status") == "stopped",
            "pcap_exists": pcap.is_file(),
            "pcap_header_present": pcap.is_file() and pcap.stat().st_size >= 24,
            "pcap_has_packets": pcap.is_file() and pcap.stat().st_size > 24,
            "runtime_identity": bool(
                capture.get("runtime_container") and capture.get("runtime_ip")
            ),
            "sha256_matches": None,
        }
        if verify_hashes and checks["pcap_exists"] and capture.get("sha256"):
            checks["sha256_matches"] = sha256_file(pcap) == capture["sha256"]
        elif verify_hashes:
            checks["sha256_matches"] = False
        for name, passed in checks.items():
            if passed is False:
                issues.append(f"{agent_id}: {name} failed")
        captures.append({"agent_id": agent_id, "pcap": str(pcap), "checks": checks})

    missing = sorted(expected_agents - observed_agents)
    unexpected = sorted(observed_agents - expected_agents)
    if missing:
        issues.append(f"missing Agent captures: {', '.join(missing)}")
    if unexpected:
        issues.append(f"unexpected Agent captures: {', '.join(unexpected)}")

    event_total, events_by_agent = _application_counts(
        session_id,
        experiment.get("trace_id", ""),
    )
    if event_total == 0:
        issues.append("no trace-matched application events were recorded")
    missing_application_agents = sorted(
        agent for agent in expected_agents if events_by_agent.get(agent, 0) == 0
    )
    if missing_application_agents:
        issues.append(
            "Agents without application events: "
            + ", ".join(missing_application_agents)
        )
    if experiment.get("status") != "complete":
        issues.append(
            f"experiment status is {experiment.get('status', 'unknown')}, not complete"
        )

    return {
        "status": "passed" if not issues else "failed",
        "passed": not issues,
        "session_id": session_id,
        "verified_hashes": verify_hashes,
        "expected_agents": sorted(expected_agents),
        "observed_agents": sorted(observed_agents),
        "captures": captures,
        "application_events": {"total": event_total, "by_agent": events_by_agent},
        "issues": issues,
    }


def build_bundle(session_id: str) -> Path:
    base = PCAP_ROOT.resolve()
    session_dir = (PCAP_ROOT / session_id).resolve()
    try:
        session_dir.relative_to(base)
    except ValueError as exc:
        raise ValueError("invalid session path") from exc
    if not session_dir.is_dir() or not load_manifest(session_id):
        raise FileNotFoundError("experiment session not found")

    log_base = LOG_ROOT.resolve()
    log_dir = (LOG_ROOT / session_id).resolve()
    try:
        log_dir.relative_to(log_base)
    except ValueError:
        log_dir = None

    bundle_path = session_dir / f"{session_id}.bundle.zip"
    temporary = bundle_path.with_suffix(".zip.tmp")
    members = []
    for path in sorted(session_dir.iterdir()):
        if not path.is_file() or path == bundle_path or path == temporary:
            continue
        members.append((path, f"pcap/{path.name}"))
    if log_dir and log_dir.is_dir():
        for name in ("application.jsonl", "network.jsonl", "system.jsonl"):
            path = log_dir / name
            if path.is_file():
                members.append((path, f"logs/{name}"))

    checksums = {
        archive_name: sha256_file(path) for path, archive_name in members
    }
    quality = audit_session(session_id, verify_hashes=True)
    from agent_network.real_packet_store import analyze_packets, query_packets

    analysis = analyze_packets(session_id=session_id, max_packets=100_000)
    packet_sample = query_packets(session_id=session_id, limit=100_000)
    quality_bytes = json.dumps(quality, ensure_ascii=False, indent=2).encode("utf-8")
    analysis_bytes = json.dumps(analysis, ensure_ascii=False, indent=2).encode("utf-8")
    packet_sample_bytes = "".join(
        json.dumps(packet, ensure_ascii=False) + "\n" for packet in packet_sample
    ).encode("utf-8")
    checksums["quality.json"] = hashlib.sha256(quality_bytes).hexdigest()
    checksums["analysis.json"] = hashlib.sha256(analysis_bytes).hexdigest()
    checksums["packets.sample.jsonl"] = hashlib.sha256(packet_sample_bytes).hexdigest()
    with zipfile.ZipFile(temporary, "w") as archive:
        for path, archive_name in members:
            compression = (
                zipfile.ZIP_STORED
                if path.suffix == ".pcap"
                else zipfile.ZIP_DEFLATED
            )
            archive.write(path, archive_name, compress_type=compression)
        archive.writestr(
            "quality.json",
            quality_bytes,
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "analysis.json",
            analysis_bytes,
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "packets.sample.jsonl",
            packet_sample_bytes,
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "SHA256SUMS.json",
            json.dumps(checksums, indent=2, sort_keys=True),
            compress_type=zipfile.ZIP_DEFLATED,
        )
    temporary.replace(bundle_path)
    return bundle_path
