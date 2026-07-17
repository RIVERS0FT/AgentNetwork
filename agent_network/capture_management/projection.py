from __future__ import annotations

import hashlib
from typing import Optional

from agent_network.file_management import FileManager, get_file_manager, stable_resource_id
from agent_network.log_management import get_log_manager

from .models import CaptureSession


class PacketProjectionService:
    """Idempotent final PCAP -> network.jsonl projection."""

    def __init__(self, files: Optional[FileManager] = None) -> None:
        self.files = files or get_file_manager()

    @staticmethod
    def projection_resource_id(capture_id: str) -> str:
        return stable_resource_id("capture", capture_id, "network_projection")

    def _source_fingerprint(self, session: CaptureSession) -> tuple[str, list[dict]]:
        sources = []
        digest = hashlib.sha256()
        resources = self.files.list_resources(
            owner_type="capture_session",
            owner_id=session.capture_id,
            resource_type="pcap",
            include_hidden=True,
        )
        for resource in sorted(resources, key=lambda item: item.logical_name):
            refreshed = self.files.refresh(resource.resource_id, compute_sha256=True)
            item = {
                "resource_id": refreshed.resource_id,
                "logical_name": refreshed.logical_name,
                "sha256": refreshed.sha256,
                "size_bytes": refreshed.size_bytes,
            }
            sources.append(item)
            digest.update(refreshed.resource_id.encode("utf-8"))
            digest.update(refreshed.sha256.encode("ascii"))
        return digest.hexdigest(), sources

    @staticmethod
    def _existing_log_ids(logger, session_id: str) -> set[str]:
        try:
            records = logger.read_session_records(session_id, "network")
        except Exception:
            return set()
        return {
            str(record.get("log_id"))
            for record in records
            if record.get("log_id")
        }

    def project(self, session: CaptureSession, *, force: bool = False) -> dict:
        fingerprint, sources = self._source_fingerprint(session)
        projection_id = self.projection_resource_id(session.capture_id)
        existing = self.files.find_resource(
            owner_type="capture_session",
            owner_id=session.capture_id,
            resource_type="capture_projection",
            include_deleted=False,
        )
        if existing and not force:
            try:
                value = self.files.read_json(existing.resource_id, allow_hidden=True)
            except (OSError, ValueError):
                value = {}
            if (
                value.get("source_fingerprint") == fingerprint
                and value.get("status") == "complete"
            ):
                return {**value, "skipped": True}

        from .packet_store import query_packets

        packets = query_packets(session_id=session.session_id, limit=1_000_000)
        logger = get_log_manager()
        try:
            logger.set_session_dir(
                str(self.files.resolve_path("logs", session.session_id))
            )
        except Exception:
            pass

        existing_log_ids = self._existing_log_ids(logger, session.session_id)
        written = 0
        already_present = 0
        errors = []
        per_agent_index: dict[str, int] = {}
        source_by_name = {item["logical_name"]: item for item in sources}
        for packet in packets:
            if not packet.get("parsed"):
                errors.append(
                    packet.get("error")
                    or f"unparsed packet in {packet.get('pcap_name', '')}"
                )
                continue
            agent_id = str(packet.get("agent_id") or "unknown")
            packet_index = per_agent_index.get(agent_id, 0)
            per_agent_index[agent_id] = packet_index + 1
            raw_line = str(packet.get("raw") or packet.get("line") or "")
            raw_bytes = raw_line.encode("utf-8")
            source = source_by_name.get(str(packet.get("pcap_name") or ""), {})
            log_id = stable_resource_id(
                session.capture_id,
                agent_id,
                source.get("sha256", ""),
                str(packet_index),
            )
            if log_id in existing_log_ids:
                already_present += 1
                continue
            logger.emit_network_event(
                timestamp=packet.get("timestamp", ""),
                log_id=log_id,
                context={
                    "trace_id": packet.get("trace_id") or session.trace_id,
                    "capture_id": session.capture_id,
                    "packet_index": packet_index,
                    "observer_agent_id": agent_id,
                    "runtime_container": packet.get("runtime_container", ""),
                    "interface": packet.get("interface", "any"),
                    "captured_length": int(
                        packet.get("captured_length")
                        or packet.get("ip_payload_bytes")
                        or 0
                    ),
                    "original_length": int(
                        packet.get("original_length")
                        or packet.get("ip_payload_bytes")
                        or 0
                    ),
                    "truncated": bool(packet.get("truncated", False)),
                },
                network={
                    "ip_version": packet.get("ip_version", ""),
                    "protocol": packet.get("protocol", ""),
                    "src_ip": packet.get("src_ip", ""),
                    "src_port": int(packet.get("src_port") or 0),
                    "dst_ip": packet.get("dst_ip", ""),
                    "dst_port": int(packet.get("dst_port") or 0),
                    "direction": packet.get("direction", "observed"),
                    "traffic_class": packet.get("traffic_class", ""),
                    "src_agent": packet.get("src_agent", ""),
                    "dst_agent": packet.get("dst_agent", ""),
                    "tcp_flags": packet.get("tcp_flags", ""),
                    "ip_payload_bytes": int(
                        packet.get("ip_payload_bytes") or 0
                    ),
                    "pcap_resource_id": packet.get("pcap_resource_id", ""),
                },
                raw={
                    "format": "tcpdump_text",
                    "encoding": "utf-8",
                    "data": raw_line,
                    "byte_length": len(raw_bytes),
                    "packet_count": 1,
                    "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                },
            )
            existing_log_ids.add(log_id)
            written += 1

        result = {
            "schema_version": "capture-projection.v1",
            "capture_id": session.capture_id,
            "session_id": session.session_id,
            "status": "complete" if not errors else "incomplete",
            "source_fingerprint": fingerprint,
            "sources": sources,
            "packets_written": written,
            "packets_already_present": already_present,
            "errors": errors,
            "skipped": False,
        }
        resource = self.files.write_json(
            result,
            owner_type="capture_session",
            owner_id=session.capture_id,
            resource_type="capture_projection",
            root_name="pcap",
            relative_path=f"{session.session_id}/network.projection.json",
            logical_name="network.projection.json",
            resource_id=projection_id,
            overwrite=True,
        )
        result["resource_id"] = resource.resource_id
        return result
