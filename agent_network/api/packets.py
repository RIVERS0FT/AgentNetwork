from __future__ import annotations
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from agent_network.experiment_manifest import audit_session, build_bundle, load_manifest, write_analysis_result, write_quality_result
from agent_network.file_management import FileManagerError, get_file_manager
from agent_network.log_management import get_log_manager
from agent_network.capture_management import analyze_packets, packet_stats, pcap_resource, query_packets, wireshark_lines
router = APIRouter(); logger = get_log_manager(); files = get_file_manager()

def _event_epoch(value: str) -> float:
    try: parsed = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
    except ValueError: return 0.0
    if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.timestamp()

def _application_events(session_id: str, trace_id: str, agent_id: Optional[str]) -> list[dict]:
    try: events = logger.read_session_records(session_id, 'application')
    except (FileNotFoundError, ValueError, FileManagerError): events = []
    filtered = [event for event in events if event.get('trace_id') == trace_id and (not agent_id or event.get('agent_id') == agent_id)]
    return filtered or logger.query(log_type='application', trace_id=trace_id, agent_id=agent_id, limit=10000)

@router.get('/')
async def packets(agent_id: Optional[str]=Query(None), session_id: str=Query(default=''), limit: int=Query(default=100, le=1000)):
    records = await asyncio.to_thread(query_packets, session_id, agent_id, limit); capture_stats = await asyncio.to_thread(packet_stats, session_id)
    return {'source': 'real', 'capture_source': 'tcpdump_pcap', 'total': len(records), 'packets': records, 'stats': capture_stats}

@router.get('/stats')
async def stats(session_id: str=Query(default='')): return await asyncio.to_thread(packet_stats, session_id)

@router.get('/analysis')
async def analysis(session_id: str=Query(default=''), agent_id: Optional[str]=Query(None), max_packets: int=Query(default=100000, ge=1, le=1000000)):
    result = await asyncio.to_thread(analyze_packets, session_id, agent_id, max_packets)
    if session_id and not agent_id: await asyncio.to_thread(write_analysis_result, session_id, result)
    return result

@router.get('/experiment')
async def experiment(session_id: str=Query(...)):
    manifest = load_manifest(session_id)
    if not manifest: raise HTTPException(status_code=404, detail='Experiment manifest not found')
    return manifest

@router.get('/quality')
async def quality(session_id: str=Query(...), verify_hashes: bool=Query(default=True)):
    result = await asyncio.to_thread(audit_session, session_id, verify_hashes); await asyncio.to_thread(write_quality_result, session_id, result); return result

@router.get('/bundle')
async def bundle(session_id: str=Query(...)):
    try: resource = await asyncio.to_thread(build_bundle, session_id); descriptor = files.prepare_download(resource.resource_id)
    except (FileNotFoundError, ValueError, FileManagerError) as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(descriptor.internal_path, media_type=descriptor.media_type, filename=descriptor.logical_name)

@router.get('/download')
async def download(session_id: str=Query(...), agent_id: str=Query(...)):
    resource = pcap_resource(session_id=session_id, agent_id=agent_id)
    if resource is None: raise HTTPException(status_code=404, detail='PCAP not found')
    try: descriptor = files.prepare_download(resource.resource_id)
    except FileManagerError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(descriptor.internal_path, media_type=descriptor.media_type, filename=descriptor.logical_name)

@router.get('/correlate')
async def correlate(session_id: str=Query(...), trace_id: str=Query(...), agent_id: Optional[str]=Query(None), window_ms: int=Query(default=250, ge=0, le=60000), packet_limit: int=Query(default=10000, ge=1, le=100000)):
    events = await asyncio.to_thread(_application_events, session_id, trace_id, agent_id); packet_rows = await asyncio.to_thread(query_packets, session_id, agent_id, packet_limit); packets = [packet for packet in packet_rows if packet.get('parsed') and packet.get('timestamp_epoch')]; correlations = []; margin = window_ms / 1000
    for event in events:
        event_agent_id = event.get('agent_id', ''); ended_at = _event_epoch(event.get('timestamp', '')); duration_ms = (event.get('action') or {}).get('duration_ms') or (event.get('metrics') or {}).get('duration_ms') or 0; started_at = ended_at - float(duration_ms) / 1000
        matched = [packet for packet in packets if (not event_agent_id or packet.get('agent_id') == event_agent_id) and started_at - margin <= packet['timestamp_epoch'] <= ended_at + margin]; protocols = Counter(packet['protocol'] for packet in matched); directions = Counter(packet['direction'] for packet in matched)
        correlations.append({'event': event.get('event', ''), 'timestamp': event.get('timestamp', ''), 'agent_id': event_agent_id, 'started_at_epoch': started_at, 'ended_at_epoch': ended_at, 'duration_ms': duration_ms, 'matched_packets': len(matched), 'matched_ip_payload_bytes': sum(packet.get('ip_payload_bytes', 0) for packet in matched), 'by_protocol': dict(protocols), 'by_direction': dict(directions)})
    return {'session_id': session_id, 'trace_id': trace_id, 'agent_id': agent_id or '', 'method': 'temporal_window_inference', 'window_ms': window_ms, 'packet_candidates': len(packets), 'application_events': len(events), 'correlations': correlations, 'caveat': 'Temporal matches are evidence windows, not protocol-level causal proof.'}

@router.get('/stream')
async def stream(agent_id: Optional[str]=Query(None), session_id: str=Query(default=''), limit: int=Query(default=100, le=1000)):
    return {'lines': await asyncio.to_thread(wireshark_lines, session_id, agent_id, limit)}
