"""Experiment provenance, managed artifacts and capture quality checks."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agent_network.file_management import FileManagerError, ResourceNotFoundError, get_file_manager, stable_resource_id

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

def _validate_session_id(session_id: str) -> str:
    value = str(session_id or '').strip()
    if not value or value in {'.', '..'} or Path(value).name != value:
        raise ValueError('invalid session path')
    return value

def _experiment_id(session_id: str) -> str: return stable_resource_id('experiment', session_id, 'manifest')
def _quality_id(session_id: str) -> str: return stable_resource_id('experiment', session_id, 'quality')
def _analysis_id(session_id: str) -> str: return stable_resource_id('experiment', session_id, 'analysis')
def _sample_id(session_id: str) -> str: return stable_resource_id('experiment', session_id, 'packet_sample')
def _bundle_id(session_id: str) -> str: return stable_resource_id('experiment', session_id, 'bundle')

def sanitize_config(value: Any):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).lower()
            sanitized[key] = '***REDACTED***' if any(marker in lowered for marker in ('key', 'token', 'secret', 'password')) else sanitize_config(item)
        return sanitized
    if isinstance(value, list): return [sanitize_config(item) for item in value]
    return value

def _scene_provenance(scene_name: str, scene_dir: Path | None=None) -> dict:
    del scene_dir
    from agent_network.scene_management import get_scene_storage
    resource = get_scene_storage().get_resource(scene_name, allow_hidden=True)
    return get_file_manager().tree_manifest(resource.resource_id)

def _write_experiment_manifest(session_id: str, value: dict):
    session_id = _validate_session_id(session_id); manager = get_file_manager()
    manager.ensure_directory(owner_type='capture_session', owner_id=session_id, resource_type='capture_session_directory', root_name='pcap', relative_path=session_id, logical_name=session_id, resource_id=stable_resource_id('capture', session_id, 'directory'))
    return manager.write_json(value, owner_type='capture_session', owner_id=session_id, resource_type='experiment_manifest', root_name='pcap', relative_path=f'{session_id}/experiment.manifest.json', logical_name='experiment.manifest.json', resource_id=_experiment_id(session_id), overwrite=True)

def create_manifest(session_id: str, scene_name: str, scene_dir: Path, trace_id: str, seed: int, agents: list[dict], llm_config: dict, scheduler: dict=None) -> dict:
    session_id = _validate_session_id(session_id); sanitized_config = sanitize_config(llm_config)
    config_sha256 = hashlib.sha256(json.dumps(sanitized_config, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    manifest = {'schema_version': 'agent-traffic-experiment.v1', 'status': 'running', 'session_id': session_id, 'trace_id': trace_id, 'scene_name': scene_name, 'seed': seed, 'started_at': _now_iso(), 'scene': _scene_provenance(scene_name, scene_dir), 'agents': agents, 'llm_config': sanitized_config, 'llm_config_sha256': config_sha256, 'scheduler': scheduler or {}}
    resource = _write_experiment_manifest(session_id, manifest); manifest['resource_id'] = resource.resource_id; return manifest

def finalize_manifest(session_id: str, **updates) -> dict:
    session_id = _validate_session_id(session_id); manifest = load_manifest(session_id) or {'schema_version': 'agent-traffic-experiment.v1', 'session_id': session_id}; manifest.pop('resource_id', None); manifest.update(updates); manifest['finished_at'] = _now_iso(); resource = _write_experiment_manifest(session_id, manifest); manifest['resource_id'] = resource.resource_id; return manifest

def load_manifest(session_id: str) -> dict:
    try: session_id = _validate_session_id(session_id)
    except ValueError: return {}
    manager = get_file_manager(); from agent_network.real_packet_store import sync_capture_session; sync_capture_session(session_id)
    resource = manager.find_resource(owner_type='capture_session', owner_id=session_id, resource_type='experiment_manifest')
    if not resource: return {}
    try: value = manager.read_json(resource.resource_id, allow_hidden=True)
    except (OSError, ValueError, FileManagerError): return {}
    value['resource_id'] = resource.resource_id; return value

def _application_counts(session_id: str, trace_id: str) -> tuple[int, dict]:
    try: session_id = _validate_session_id(session_id)
    except ValueError: return (0, {})
    from agent_network.log_management import get_log_manager
    try: records = get_log_manager().read_session_records(session_id, 'application')
    except (FileNotFoundError, ValueError, FileManagerError): return (0, {})
    total = 0; by_agent: dict[str, int] = {}
    for record in records:
        if trace_id and record.get('trace_id') != trace_id: continue
        total += 1; source_id = record.get('agent_id'); target_id = (record.get('target') or {}).get('agent_id'); participants = {agent_id for agent_id in (source_id, target_id) if agent_id} or {'unknown'}
        for agent_id in participants: by_agent[agent_id] = by_agent.get(agent_id, 0) + 1
    return (total, by_agent)

def audit_session(session_id: str, verify_hashes: bool=True) -> dict:
    try: session_id = _validate_session_id(session_id)
    except ValueError: return {'status': 'failed', 'passed': False, 'session_id': str(session_id or ''), 'issues': ['invalid session path']}
    from agent_network.real_packet_store import sync_capture_session
    sync_capture_session(session_id); manager = get_file_manager(); experiment = load_manifest(session_id)
    if not experiment: return {'status': 'failed', 'passed': False, 'session_id': session_id, 'issues': ['experiment manifest missing or invalid']}
    issues = []; captures = []; expected_agents = {str(item.get('agent_id', '')) for item in experiment.get('agents', []) if item.get('agent_id')}
    for item in experiment.get('agents', []):
        if item.get('agent_id') and not item.get('image_id'): issues.append(f"{item['agent_id']}: container image identity missing")
    if not (experiment.get('scene') or {}).get('files'): issues.append('scene provenance is empty')
    manifests = manager.list_resources(owner_type='capture_session', owner_id=session_id, resource_type='capture_manifest', include_hidden=True); pcaps = {Path(item.logical_name).stem: item for item in manager.list_resources(owner_type='capture_session', owner_id=session_id, resource_type='pcap', include_hidden=True)}; observed_agents = set()
    for manifest_resource in manifests:
        try: capture = manager.read_json(manifest_resource.resource_id, allow_hidden=True)
        except (OSError, ValueError) as exc: issues.append(f'invalid capture manifest {manifest_resource.logical_name}: {exc}'); continue
        agent_id = capture.get('agent_id') or manifest_resource.logical_name.removesuffix('.manifest.json'); observed_agents.add(agent_id); pcap = pcaps.get(agent_id); pcap_size = pcap.size_bytes if pcap else 0
        checks = {'manifest_stopped': capture.get('status') == 'stopped', 'pcap_exists': pcap is not None, 'pcap_header_present': pcap_size >= 24, 'pcap_has_packets': pcap_size > 24, 'runtime_identity': bool(capture.get('runtime_container') and capture.get('runtime_ip')), 'sha256_matches': None}
        if verify_hashes and pcap:
            try: refreshed = manager.refresh(pcap.resource_id); checks['sha256_matches'] = bool(capture.get('sha256') and refreshed.sha256 == capture.get('sha256'))
            except (OSError, FileManagerError): checks['sha256_matches'] = False
        elif verify_hashes: checks['sha256_matches'] = False
        for name, passed in checks.items():
            if passed is False: issues.append(f'{agent_id}: {name} failed')
        captures.append({'agent_id': agent_id, 'pcap_resource_id': pcap.resource_id if pcap else '', 'capture_manifest_resource_id': manifest_resource.resource_id, 'checks': checks})
    missing = sorted(expected_agents - observed_agents); unexpected = sorted(observed_agents - expected_agents)
    if missing: issues.append(f"missing Agent captures: {', '.join(missing)}")
    if unexpected: issues.append(f"unexpected Agent captures: {', '.join(unexpected)}")
    event_total, events_by_agent = _application_counts(session_id, experiment.get('trace_id', ''))
    if event_total == 0: issues.append('no trace-matched application events were recorded')
    missing_application_agents = sorted(agent for agent in expected_agents if events_by_agent.get(agent, 0) == 0)
    if missing_application_agents: issues.append('Agents without application events: ' + ', '.join(missing_application_agents))
    if experiment.get('status') != 'complete': issues.append(f"experiment status is {experiment.get('status', 'unknown')}, not complete")
    return {'status': 'passed' if not issues else 'failed', 'passed': not issues, 'session_id': session_id, 'verified_hashes': verify_hashes, 'expected_agents': sorted(expected_agents), 'observed_agents': sorted(observed_agents), 'captures': captures, 'application_events': {'total': event_total, 'by_agent': events_by_agent}, 'issues': issues}

def write_quality_result(session_id: str, quality: dict): return get_file_manager().write_json(quality, owner_type='capture_session', owner_id=session_id, resource_type='quality_result', root_name='pcap', relative_path=f'{session_id}/quality.json', logical_name='quality.json', resource_id=_quality_id(session_id), overwrite=True)
def write_analysis_result(session_id: str, analysis: dict): return get_file_manager().write_json(analysis, owner_type='capture_session', owner_id=session_id, resource_type='analysis_result', root_name='pcap', relative_path=f'{session_id}/analysis.json', logical_name='analysis.json', resource_id=_analysis_id(session_id), overwrite=True)
def write_packet_sample(session_id: str, packets: list[dict]):
    content = ''.join(json.dumps(packet, ensure_ascii=False) + '\n' for packet in packets)
    return get_file_manager().write_text(content, owner_type='capture_session', owner_id=session_id, resource_type='packet_sample', root_name='pcap', relative_path=f'{session_id}/packets.sample.jsonl', logical_name='packets.sample.jsonl', media_type='application/x-ndjson', resource_id=_sample_id(session_id), overwrite=True)

def build_bundle(session_id: str):
    session_id = _validate_session_id(session_id); from agent_network.real_packet_store import analyze_packets, query_packets, sync_capture_session; sync_capture_session(session_id); manager = get_file_manager(); experiment = load_manifest(session_id)
    if not experiment: raise FileNotFoundError('experiment session not found')
    quality = audit_session(session_id, verify_hashes=True); analysis = analyze_packets(session_id=session_id, max_packets=100000); packet_sample = query_packets(session_id=session_id, limit=100000)
    quality_resource = write_quality_result(session_id, quality); analysis_resource = write_analysis_result(session_id, analysis); sample_resource = write_packet_sample(session_id, packet_sample)
    resources = manager.list_resources(owner_type='capture_session', owner_id=session_id, include_hidden=True); log_resources = manager.list_resources(owner_type='log_session', owner_id=session_id, include_hidden=True)
    included = [resource for resource in resources if resource.resource_type in {'pcap', 'capture_manifest', 'experiment_manifest', 'quality_result', 'analysis_result', 'packet_sample'}] + [resource for resource in log_resources if resource.resource_type.endswith('_log')]
    known = {item.resource_id for item in included}
    for item in (quality_resource, analysis_resource, sample_resource):
        if item.resource_id not in known: included.append(item)
    archive_names = {}; checksums = {}
    for resource in included:
        refreshed = manager.refresh(resource.resource_id)
        if refreshed.resource_type.endswith('_log'): archive_name = f'logs/{refreshed.logical_name}'
        elif refreshed.resource_type in {'pcap', 'capture_manifest', 'experiment_manifest'}: archive_name = f'pcap/{refreshed.logical_name}'
        else: archive_name = refreshed.logical_name
        archive_names[refreshed.resource_id] = archive_name; checksums[archive_name] = refreshed.sha256
    return manager.create_archive([item.resource_id for item in included], owner_type='capture_session', owner_id=session_id, root_name='archives', relative_path=f'experiments/{session_id}.bundle.zip', logical_name=f'{session_id}.bundle.zip', resource_id=_bundle_id(session_id), archive_names=archive_names, virtual_files={'SHA256SUMS.json': json.dumps(checksums, indent=2, sort_keys=True)}, overwrite=True)
