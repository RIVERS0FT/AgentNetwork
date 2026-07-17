import json
import struct
import zipfile


def _pcap_bytes():
    global_header = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
    payload = b"abcd"
    return global_header + struct.pack("<IIII", 1, 0, len(payload), len(payload)) + payload


def test_manifest_audit_and_bundle_use_managed_resources(tmp_path, monkeypatch):
    data = tmp_path / "data"; scenes = tmp_path / "scenes"
    monkeypatch.setenv("DATA_DIR", str(data)); monkeypatch.setenv("SCENE_DIR", str(scenes)); monkeypatch.setenv("LOG_DIR", str(data / "logs")); monkeypatch.setenv("PCAP_DIR", str(data / "pcap")); monkeypatch.setenv("ARCHIVE_DIR", str(data / "archives")); monkeypatch.setenv("FILE_TEMP_DIR", str(data / "tmp")); monkeypatch.setenv("FILE_REGISTRY_PATH", str(data / "pcap/.file_registry.json"))
    from agent_network.file_management import get_file_manager, reset_file_manager, stable_resource_id
    reset_file_manager()
    from agent_network.scene_management import SceneStorage
    folder = scenes / "demo"; folder.mkdir(parents=True)
    (folder / "meta_and_roles.json").write_text(json.dumps({"scenario_metadata":{"title":"Demo"},"roles":{"a1":{"name":"A1","identity":"worker","model_backbone":"openclaw"}}}))
    (folder / "instances_and_skills.json").write_text(json.dumps({"container_instances":{"a1":{"skill_refs":[],"tool_refs":[],"tasks":[]}}}))
    (folder / "network_topology.json").write_text(json.dumps({"topology":[]})); SceneStorage().get_resource("demo")
    from agent_network.log_management import LogManager
    logs = LogManager(log_dir=str(data / "logs")); logs.reset(); logs._log_dir=str(data / "logs"); session = logs.start_session("demo")
    from agent_network.experiment_manifest import create_manifest, finalize_manifest, audit_session, build_bundle
    manifest = create_manifest(session_id=session, scene_name="demo", scene_dir=folder, trace_id="trace-1", seed=1, agents=[{"agent_id":"a1","image_id":"sha256:image"}], llm_config={}, scheduler={"mode":"event_driven"})
    assert manifest["schema_version"] == "agent-traffic-experiment.v2"
    assert manifest["scheduler"] == {"mode": "event_driven"}
    assert "max_rounds" not in manifest["scheduler"]
    logs.emit_application_event("acting","a1",trace_id="trace-1",action={"name":"work"})
    manager=get_file_manager(); manager.ensure_directory(owner_type="capture_session",owner_id=session,resource_type="capture_session_directory",root_name="pcap",relative_path=session,resource_id=stable_resource_id("capture",session,"directory"))
    pcap=manager.write_bytes(_pcap_bytes(), owner_type="capture_session", owner_id=session, resource_type="pcap", root_name="pcap", relative_path=f"{session}/a1.pcap", logical_name="a1.pcap", media_type="application/vnd.tcpdump.pcap", resource_id=stable_resource_id("capture",session,"a1","pcap"))
    manager.write_json({"agent_id":"a1","session_id":session,"status":"stopped","runtime_container":"ag-o1","runtime_ip":"172.1.0.2","sha256":pcap.sha256}, owner_type="capture_session", owner_id=session, resource_type="capture_manifest", root_name="pcap", relative_path=f"{session}/a1.manifest.json", logical_name="a1.manifest.json", resource_id=stable_resource_id("capture",session,"a1","manifest"), overwrite=True)
    finalize_manifest(session,status="complete",stop_reason="completed"); quality=audit_session(session); assert quality["passed"] is True, quality
    import agent_network.capture_management.packet_store as store
    monkeypatch.setattr(store,"analyze_packets",lambda **kw:{"session_id":session,"packets_analyzed":1}); monkeypatch.setattr(store,"query_packets",lambda **kw:[{"packet":1}])
    bundle=build_bundle(session); descriptor=manager.prepare_download(bundle.resource_id)
    with zipfile.ZipFile(descriptor.internal_path) as archive: names=set(archive.namelist())
    assert {"pcap/a1.pcap", "logs/application.jsonl", "quality.json", "analysis.json", "packets.sample.jsonl", "SHA256SUMS.json"}.issubset(names)


def test_audit_rejects_session_path_traversal():
    from agent_network.experiment_manifest import audit_session
    quality = audit_session("../../outside")
    assert quality["passed"] is False
    assert quality["issues"] == ["invalid session path"]
