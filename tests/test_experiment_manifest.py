import json
import struct
import zipfile

from agent_network import experiment_manifest


def test_experiment_manifest_redacts_secrets_and_quality_verifies_hashes(tmp_path, monkeypatch):
    pcap_root = tmp_path / "pcap"
    log_root = tmp_path / "logs"
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "meta_and_roles.json").write_text('{"name":"demo"}', encoding="utf-8")
    monkeypatch.setattr(experiment_manifest, "PCAP_ROOT", pcap_root)
    monkeypatch.setattr(experiment_manifest, "LOG_ROOT", log_root)

    experiment_manifest.create_manifest(
        session_id="session-1",
        scene_name="demo",
        scene_dir=scene,
        trace_id="trace-1",
        seed=1234,
        agents=[{
            "agent_id": "planner",
            "runtime_container": "ag-c1",
            "runtime_ip": "172.20.0.3",
            "image_id": "sha256:image",
        }],
        llm_config={"LLM_MODEL": "model", "LLM_API_KEY": "secret"},
    )
    session = pcap_root / "session-1"
    pcap = session / "planner.pcap"
    pcap.write_bytes(
        b"\xd4\xc3\xb2\xa1"
        + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
        + struct.pack("<IIII", 1_700_000_000, 0, 4, 4)
        + b"data"
    )
    (session / "planner.manifest.json").write_text(json.dumps({
        "agent_id": "planner",
        "runtime_container": "ag-c1",
        "runtime_ip": "172.20.0.3",
        "status": "stopped",
        "sha256": experiment_manifest.sha256_file(pcap),
    }), encoding="utf-8")
    application_dir = log_root / "session-1"
    application_dir.mkdir(parents=True)
    (application_dir / "application.jsonl").write_text(json.dumps({
        "trace": {"trace_id": "trace-1"},
        "actor": {"agent_id": "planner"},
    }) + "\n", encoding="utf-8")
    experiment_manifest.finalize_manifest("session-1", status="complete", stop_reason="hard_limit")

    manifest = experiment_manifest.load_manifest("session-1")
    quality = experiment_manifest.audit_session("session-1", verify_hashes=True)

    assert manifest["llm_config"]["LLM_API_KEY"] == "***REDACTED***"
    assert manifest["seed"] == 1234
    assert manifest["scene"]["sha256"]
    assert quality["passed"] is True
    assert quality["captures"][0]["checks"]["sha256_matches"] is True

    from agent_network import real_packet_store
    monkeypatch.setattr(real_packet_store, "PCAP_ROOT", pcap_root)
    monkeypatch.setattr(real_packet_store, "analyze_packets", lambda **_kwargs: {"packets_analyzed": 1})
    monkeypatch.setattr(real_packet_store, "query_packets", lambda **_kwargs: [{"protocol": "TCP"}])
    bundle = experiment_manifest.build_bundle("session-1")
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert "pcap/planner.pcap" in names
        assert "pcap/experiment.manifest.json" in names
        assert "logs/application.jsonl" in names
        assert "quality.json" in names
        assert "analysis.json" in names
        assert "packets.sample.jsonl" in names
        assert "SHA256SUMS.json" in names


def test_application_audit_ignores_legacy_trace_and_actor_fields(tmp_path, monkeypatch):
    log_root = tmp_path / "logs"
    application_dir = log_root / "session-legacy"
    application_dir.mkdir(parents=True)
    (application_dir / "application.jsonl").write_text(
        json.dumps({
            "trace_id": "trace-legacy",
            "actor": {"id": "planner"},
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(experiment_manifest, "LOG_ROOT", log_root)

    total, by_agent = experiment_manifest._application_counts(
        "session-legacy",
        "trace-legacy",
    )

    assert total == 0
    assert by_agent == {}


def test_audit_rejects_session_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_manifest, "PCAP_ROOT", tmp_path / "pcap")

    quality = experiment_manifest.audit_session("../../outside")

    assert quality["passed"] is False
    assert quality["issues"] == ["invalid session path"]
