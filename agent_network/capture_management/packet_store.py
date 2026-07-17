"""Read and summarize managed tcpdump PCAP resources."""
from __future__ import annotations
import re
import struct
import subprocess
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from agent_network.file_management import FileResource, get_file_manager, stable_resource_id
_LINE_RE = re.compile('^(?P<timestamp>(?:\\d{4}-\\d{2}-\\d{2}\\s+\\S+)|(?:\\d+(?:\\.\\d+)?))\\s+(?P<ip_version>IP6?)\\s+(?P<src>\\S+)\\s+>\\s+(?P<dst>\\S+):\\s*(?P<details>.*)$')
_LENGTH_RE = re.compile('\\blength\\s+(?P<length>\\d+)\\b')
_FLAGS_RE = re.compile('\\bFlags\\s+\\[(?P<flags>[^]]*)\\]')

def _capture_manifest_id(session_id: str, agent_id: str) -> str:
    return stable_resource_id('capture', session_id, agent_id, 'manifest')

def sync_capture_session(session_id: str) -> None:
    if not session_id or Path(session_id).name != session_id:
        return
    manager = get_file_manager()
    session = manager.resolve_path('pcap', session_id)
    if not session.is_dir():
        return
    for path in sorted(session.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = f'{session_id}/{path.name}'
        if path.suffix == '.pcap':
            manager.register_existing(owner_type='capture_session', owner_id=session_id, resource_type='pcap', root_name='pcap', relative_path=relative, logical_name=path.name, media_type='application/vnd.tcpdump.pcap', resource_id=stable_resource_id('capture', session_id, path.stem, 'pcap'), upsert=True)
        elif path.name.endswith('.manifest.json'):
            resource_type = 'experiment_manifest' if path.name == 'experiment.manifest.json' else 'capture_manifest'
            agent_id = path.name.removesuffix('.manifest.json')
            resource_id = stable_resource_id('experiment', session_id, 'manifest') if resource_type == 'experiment_manifest' else _capture_manifest_id(session_id, agent_id)
            manager.register_existing(owner_type='capture_session', owner_id=session_id, resource_type=resource_type, root_name='pcap', relative_path=relative, logical_name=path.name, media_type='application/json', resource_id=resource_id, upsert=True)

def _pcap_resources(session_id: str='', agent_id: Optional[str]=None) -> list[FileResource]:
    manager = get_file_manager()
    if session_id:
        sync_capture_session(session_id)
    resources = manager.list_resources(owner_type='capture_session', owner_id=session_id or None, resource_type='pcap', include_hidden=True)
    if agent_id:
        resources = [item for item in resources if Path(item.logical_name).stem == agent_id]
    return resources

def pcap_resource(session_id: str, agent_id: str) -> Optional[FileResource]:
    resources = _pcap_resources(session_id=session_id, agent_id=agent_id)
    return resources[0] if len(resources) == 1 else None

def _load_manifest(resource: FileResource) -> dict:
    manager = get_file_manager()
    agent_id = Path(resource.logical_name).stem
    manifest = manager.find_resource(owner_type='capture_session', owner_id=resource.owner_id, resource_type='capture_manifest', logical_name=f'{agent_id}.manifest.json')
    if not manifest:
        return {}
    try:
        return manager.read_json(manifest.resource_id, allow_hidden=True)
    except (OSError, ValueError):
        return {}

def _read_lines(resource: FileResource, limit: int=1000):
    manager = get_file_manager()
    path = manager.resolve_resource_path(resource.resource_id, allow_hidden=True, allow_directory=False)
    cmd = ['tcpdump', '-tt', '-nn', '-r', str(path)]
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        lines = deque(maxlen=max(1, limit))
        scanned = 0
        for line in proc.stdout or []:
            if line.strip():
                lines.append(line.rstrip('\r\n'))
                scanned += 1
        proc.wait(timeout=30)
        stderr = (proc.stderr.read() if proc.stderr else '').strip()
    except Exception as exc:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return ([], f'pcap_parse_error {resource.logical_name}: {exc}', 0)
    return (list(lines), stderr if proc.returncode != 0 else '', scanned)

def _endpoint(value: str) -> tuple[str, int]:
    host, separator, port = value.rpartition('.')
    if separator and port.isdigit():
        return (host, int(port))
    return (value, 0)

def _timestamp(value: str) -> tuple[str, float]:
    try:
        epoch = float(value)
        return (datetime.fromtimestamp(epoch, timezone.utc).isoformat(), epoch)
    except ValueError:
        pass
    try:
        local = datetime.fromisoformat(value.replace(' ', 'T'))
        if local.tzinfo is None:
            local = local.replace(tzinfo=timezone(timedelta(hours=8)))
        return (local.astimezone(timezone.utc).isoformat(), local.timestamp())
    except ValueError:
        return (value, 0.0)

def _parse_line(line: str, resource: FileResource, manifest: dict, identities: dict=None) -> dict:
    record = {'capture_source': 'tcpdump_pcap', 'agent_id': manifest.get('agent_id') or Path(resource.logical_name).stem, 'runtime_container': manifest.get('runtime_container', ''), 'session_id': manifest.get('session_id') or resource.owner_id, 'trace_id': manifest.get('trace_id', ''), 'pcap_resource_id': resource.resource_id, 'pcap_name': resource.logical_name, 'line': line, 'raw': line}
    match = _LINE_RE.match(line)
    if not match:
        record.update({'parsed': False, 'protocol': 'unknown', 'ip_payload_bytes': 0})
        return record
    values = match.groupdict()
    timestamp, timestamp_epoch = _timestamp(values['timestamp'])
    src_ip, src_port = _endpoint(values['src'])
    dst_ip, dst_port = _endpoint(values['dst'])
    identities = identities or {}
    own_ip = manifest.get('runtime_ip', '')
    if own_ip and src_ip == own_ip:
        direction = 'outbound'
    elif own_ip and dst_ip == own_ip:
        direction = 'inbound'
    else:
        direction = 'observed'
    src_agent = identities.get(src_ip, '')
    dst_agent = identities.get(dst_ip, '')
    traffic_class = 'agent_peer' if src_agent and dst_agent else 'agent_non_peer'
    details = values['details']
    length_match = _LENGTH_RE.search(details)
    flags_match = _FLAGS_RE.search(details)
    if flags_match:
        protocol = 'TCP'
    elif details.startswith('UDP') or ' UDP,' in details:
        protocol = 'UDP'
    elif 'ICMP' in details:
        protocol = 'ICMP'
    else:
        protocol = 'IP'
    record.update({'parsed': True, 'timestamp': timestamp, 'timestamp_raw': values['timestamp'], 'timestamp_epoch': timestamp_epoch, 'ip_version': values['ip_version'], 'protocol': protocol, 'src_ip': src_ip, 'src_port': src_port, 'dst_ip': dst_ip, 'dst_port': dst_port, 'direction': direction, 'traffic_class': traffic_class, 'src_agent': src_agent, 'dst_agent': dst_agent, 'tcp_flags': flags_match.group('flags') if flags_match else '', 'ip_payload_bytes': int(length_match.group('length')) if length_match else 0})
    return record

def _pcap_metadata(resource: FileResource) -> dict:
    manager = get_file_manager()
    path = manager.resolve_resource_path(resource.resource_id, allow_hidden=True, allow_directory=False)
    result = {'pcap_resource_id': resource.resource_id, 'pcap_name': resource.logical_name, 'file_bytes': path.stat().st_size, 'packet_count': 0, 'captured_bytes': 0, 'wire_bytes': 0, 'first_packet_at': '', 'last_packet_at': '', 'valid_pcap': False}
    try:
        with path.open('rb') as stream:
            global_header = stream.read(24)
            if len(global_header) != 24:
                return result
            formats = {b'\xd4\xc3\xb2\xa1': ('<', 1000000), b'\xa1\xb2\xc3\xd4': ('>', 1000000), b'M<\xb2\xa1': ('<', 1000000000), b'\xa1\xb2<M': ('>', 1000000000)}
            if global_header[:4] not in formats:
                return result
            endian, fraction_scale = formats[global_header[:4]]
            first_timestamp = None
            last_timestamp = None
            while True:
                packet_header = stream.read(16)
                if not packet_header:
                    result['valid_pcap'] = True
                    break
                if len(packet_header) != 16:
                    break
                seconds, fraction, captured_length, original_length = struct.unpack(f'{endian}IIII', packet_header)
                payload = stream.read(captured_length)
                if len(payload) != captured_length:
                    break
                timestamp = seconds + fraction / fraction_scale
                first_timestamp = timestamp if first_timestamp is None else first_timestamp
                last_timestamp = timestamp
                result['packet_count'] += 1
                result['captured_bytes'] += captured_length
                result['wire_bytes'] += original_length
            if first_timestamp is not None:
                result['first_packet_at'] = datetime.fromtimestamp(first_timestamp, timezone.utc).isoformat()
                result['last_packet_at'] = datetime.fromtimestamp(last_timestamp, timezone.utc).isoformat()
    except OSError as exc:
        result['error'] = str(exc)
    return result

def query_packets(session_id: str='', agent_id: Optional[str]=None, limit: int=100):
    packets = []
    errors = []
    all_pcaps = _pcap_resources(session_id=session_id)
    identities = {}
    for resource in all_pcaps:
        manifest = _load_manifest(resource)
        if manifest.get('runtime_ip') and manifest.get('agent_id'):
            identities[manifest['runtime_ip']] = manifest['agent_id']
    selected = [resource for resource in all_pcaps if not agent_id or Path(resource.logical_name).stem == agent_id]
    for resource in selected:
        manifest = _load_manifest(resource)
        lines, error, _ = _read_lines(resource, limit=limit)
        packets.extend((_parse_line(line, resource, manifest, identities) for line in lines))
        if error:
            errors.append({'capture_source': 'tcpdump_pcap', 'agent_id': manifest.get('agent_id') or Path(resource.logical_name).stem, 'pcap_resource_id': resource.resource_id, 'pcap_name': resource.logical_name, 'parsed': False, 'error': error})
    packets.sort(key=lambda item: item.get('timestamp', ''))
    return (packets + errors)[-limit:]

def wireshark_lines(session_id: str='', agent_id: Optional[str]=None, limit: int=100):
    return [packet.get('raw') or packet.get('error', '') for packet in query_packets(session_id, agent_id, limit)]

def analyze_packets(session_id: str='', agent_id: Optional[str]=None, max_packets: int=100000):
    all_pcaps = _pcap_resources(session_id=session_id)
    identities = {}
    for resource in all_pcaps:
        manifest = _load_manifest(resource)
        if manifest.get('runtime_ip') and manifest.get('agent_id'):
            identities[manifest['runtime_ip']] = manifest['agent_id']
    selected = [resource for resource in all_pcaps if not agent_id or Path(resource.logical_name).stem == agent_id]
    protocols = Counter(); directions = Counter(); traffic_classes = Counter(); endpoints = Counter(); flows = {}
    payload_bytes = scanned = analyzed = retained = 0
    errors = []
    per_file_limit = max(1, max_packets // max(1, len(selected)))
    for resource in selected:
        manifest = _load_manifest(resource)
        lines, error, file_scanned = _read_lines(resource, limit=per_file_limit)
        scanned += file_scanned; retained += len(lines)
        if error:
            errors.append({'pcap_resource_id': resource.resource_id, 'pcap_name': resource.logical_name, 'error': error})
        for line in lines:
            packet = _parse_line(line, resource, manifest, identities)
            if not packet.get('parsed'):
                continue
            analyzed += 1; protocols[packet['protocol']] += 1; directions[packet['direction']] += 1; traffic_classes[packet['traffic_class']] += 1; payload_bytes += packet['ip_payload_bytes']
            endpoint = f"{packet['dst_ip']}:{packet['dst_port']}" if packet['direction'] != 'inbound' else f"{packet['src_ip']}:{packet['src_port']}"
            endpoints[endpoint] += 1
            left = f"{packet['src_ip']}:{packet['src_port']}"; right = f"{packet['dst_ip']}:{packet['dst_port']}"; flow_endpoints = tuple(sorted((left, right))); flow_key = (packet['protocol'], *flow_endpoints)
            flow = flows.setdefault(flow_key, {'protocol': packet['protocol'], 'endpoint_a': flow_endpoints[0], 'endpoint_b': flow_endpoints[1], 'packets': 0, 'ip_payload_bytes': 0, 'first_timestamp_epoch': packet.get('timestamp_epoch', 0), 'last_timestamp_epoch': packet.get('timestamp_epoch', 0)})
            flow['packets'] += 1; flow['ip_payload_bytes'] += packet['ip_payload_bytes']; timestamp_epoch = packet.get('timestamp_epoch', 0)
            if timestamp_epoch:
                if not flow['first_timestamp_epoch'] or timestamp_epoch < flow['first_timestamp_epoch']: flow['first_timestamp_epoch'] = timestamp_epoch
                if timestamp_epoch > flow['last_timestamp_epoch']: flow['last_timestamp_epoch'] = timestamp_epoch
    return {'capture_source': 'tcpdump_pcap', 'session_id': session_id, 'agent_id': agent_id or '', 'pcap_files': len(selected), 'packets_scanned': scanned, 'packets_analyzed': analyzed, 'sampled': scanned > retained, 'sample_limit': max_packets, 'ip_payload_bytes': payload_bytes, 'by_protocol': dict(protocols), 'by_direction': dict(directions), 'by_traffic_class': dict(traffic_classes), 'top_endpoints': [{'endpoint': endpoint, 'packets': count} for endpoint, count in endpoints.most_common(50)], 'top_flows': sorted(flows.values(), key=lambda flow: (flow['ip_payload_bytes'], flow['packets']), reverse=True)[:100], 'errors': errors, 'aggregation_scope': 'per_agent_observations', 'aggregation_note': 'Agent-to-Agent packets are visible in both endpoint PCAPs and are not deduplicated.'}

def packet_stats(session_id: str=''):
    files = []; totals = {'packet_count': 0, 'captured_bytes': 0, 'wire_bytes': 0, 'file_bytes': 0}
    for resource in _pcap_resources(session_id=session_id):
        metadata = _pcap_metadata(resource); metadata['agent_id'] = _load_manifest(resource).get('agent_id') or Path(resource.logical_name).stem; files.append(metadata)
        for key in totals: totals[key] += metadata[key]
    return {'capture_source': 'tcpdump_pcap', 'aggregation_scope': 'per_agent_observations', 'aggregation_note': 'Agent-to-Agent packets are visible in both endpoint PCAPs and are not deduplicated.', 'session_id': session_id, 'pcap_files': len(files), **totals, 'files': files}
