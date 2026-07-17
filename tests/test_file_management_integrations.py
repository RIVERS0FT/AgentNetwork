import json
import zipfile
from pathlib import Path


def test_log_manager_uses_file_manager(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path)); monkeypatch.setenv('LOG_DIR', str(tmp_path / 'logs')); monkeypatch.setenv('PCAP_DIR', str(tmp_path / 'pcap')); monkeypatch.setenv('SCENE_DIR', str(tmp_path / 'scenes')); monkeypatch.setenv('FILE_REGISTRY_PATH', str(tmp_path / 'pcap/.file_registry.json'))
    from agent_network.log_management import LogManager
    manager = LogManager(log_dir=str(tmp_path / 'logs')); manager.reset(); manager._log_dir = str(tmp_path / 'logs'); session = manager.start_session('demo'); manager.emit_application_event('acting', 'a1', action={'name': 'x'})
    assert manager.list_log_files()[0]['session'] == session; descriptor = manager.get_download_descriptor(session, 'application'); assert Path(descriptor.internal_path).is_file(); manager.hide_log(session, 'application')
    assert all(item['type'] != 'application' for group in manager.list_log_files() for item in group['files'])
    manager.show_log(session, 'application'); manager.delete_log(session, 'application'); assert all(item['type'] != 'application' for group in manager.list_log_files() for item in group['files'])


def _make_scene(root: Path, name='demo'):
    folder = root / name; folder.mkdir(parents=True)
    (folder / 'meta_and_roles.json').write_text(json.dumps({'scenario_metadata': {'title': 'Demo'}, 'roles': {'A': {'name': 'A', 'identity': 'planner', 'core_goal': 'Complete the plan', 'model_backbone': 'openclaw'}}}))
    (folder / 'instances_and_skills.json').write_text(json.dumps({'container_instances': {'A': {'skill_refs': [], 'tool_refs': [], 'tasks': ['go']}}}))
    (folder / 'network_topology.json').write_text(json.dumps({'topology': []})); return folder


def test_scene_storage_managed_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data')); monkeypatch.setenv('SCENE_DIR', str(tmp_path / 'scenes')); monkeypatch.setenv('PCAP_DIR', str(tmp_path / 'data/pcap')); monkeypatch.setenv('FILE_REGISTRY_PATH', str(tmp_path / 'data/pcap/.file_registry.json'))
    from agent_network.file_management import reset_file_manager
    reset_file_manager(); from agent_network.scene_management import SceneStorage
    _make_scene(tmp_path / 'scenes'); storage = SceneStorage(); assert storage.list_scenes()[0]['title'] == 'Demo'; assert storage.build_definition('demo').agents[0].tasks == ['go']; archive = storage.create_archive('demo')
    with zipfile.ZipFile(storage.files.resolve_resource_path(archive.resource_id)) as bundle: assert 'demo/meta_and_roles.json' in bundle.namelist()
    storage.set_visibility('demo', False); assert storage.list_scenes() == []; storage.set_visibility('demo', True); storage.delete('demo'); assert storage.list_scenes() == []
