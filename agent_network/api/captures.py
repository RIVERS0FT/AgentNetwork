from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent_network.capture_management import CaptureConfig, get_capture_coordinator
from agent_network.file_management import FileManagerError

router = APIRouter()
manager = get_capture_coordinator()


class CaptureTargetRequest(BaseModel):
    agent_id: str
    runtime_url: str
    container_id: str = ""
    container_name: str = ""
    runtime_ip: str = ""


class CaptureConfigRequest(BaseModel):
    interface: str = "any"
    snap_length: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=1024 * 1024 * 1024, ge=0)
    include_control_plane: bool = False
    bpf_filter: str = ""
    health_check_interval_seconds: float = Field(default=2.0, gt=0)
    stop_timeout_seconds: float = Field(default=5.0, gt=0)
    projection_mode: str = "finalize"

    def to_domain(self) -> CaptureConfig:
        return CaptureConfig(**self.dict())


class CaptureCreateRequest(BaseModel):
    simulation_id: str
    session_id: str
    trace_id: str = ""
    capture_id: str = ""
    targets: List[CaptureTargetRequest]
    config: CaptureConfigRequest = Field(default_factory=CaptureConfigRequest)


class CaptureStopRequest(BaseModel):
    reason: str = "requested"


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.post("")
async def create_capture(req: CaptureCreateRequest):
    try:
        session = manager.create_session(
            simulation_id=req.simulation_id,
            session_id=req.session_id,
            trace_id=req.trace_id,
            targets=[target.dict() for target in req.targets],
            config=req.config.to_domain(),
            capture_id=req.capture_id,
        )
    except (ValueError, FileManagerError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return session.to_dict()


@router.post("/{capture_id}/start")
async def start_capture(capture_id: str):
    try:
        return (await asyncio.to_thread(manager.start_session, capture_id)).to_dict()
    except KeyError as exc:
        raise _not_found(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{capture_id}")
async def capture_status(capture_id: str, refresh: bool = Query(default=False)):
    try:
        session = (
            await asyncio.to_thread(manager.check_health, capture_id)
            if refresh
            else manager.get_session(capture_id)
        )
    except KeyError as exc:
        raise _not_found(exc) from exc
    return session.to_dict()


@router.post("/{capture_id}/stop")
async def stop_capture(capture_id: str, req: CaptureStopRequest = None):
    req = req or CaptureStopRequest()
    try:
        return (
            await asyncio.to_thread(manager.stop_session, capture_id, req.reason)
        ).to_dict()
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/{capture_id}/artifacts")
async def capture_artifacts(capture_id: str):
    try:
        return {
            "capture_id": capture_id,
            "artifacts": manager.list_artifacts(capture_id),
        }
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/{capture_id}/packets")
async def capture_packets(
    capture_id: str,
    agent_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=100_000),
):
    try:
        packets = await asyncio.to_thread(
            manager.query_packets, capture_id, agent_id, limit
        )
    except KeyError as exc:
        raise _not_found(exc) from exc
    return {"capture_id": capture_id, "total": len(packets), "packets": packets}


@router.get("/{capture_id}/stats")
async def capture_stats(capture_id: str):
    try:
        return await asyncio.to_thread(manager.stats, capture_id)
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/{capture_id}/analysis")
async def capture_analysis(
    capture_id: str,
    agent_id: str = Query(default=""),
    max_packets: int = Query(default=100_000, ge=1, le=1_000_000),
):
    try:
        return await asyncio.to_thread(
            manager.analyze, capture_id, agent_id, max_packets
        )
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/{capture_id}/quality")
async def capture_quality(
    capture_id: str,
    verify_hashes: bool = Query(default=True),
):
    try:
        return await asyncio.to_thread(manager.audit, capture_id, verify_hashes)
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/{capture_id}/bundle")
async def capture_bundle(capture_id: str):
    try:
        resource = await asyncio.to_thread(manager.build_bundle, capture_id)
        descriptor = manager.repository.prepare_download(resource.resource_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except (FileNotFoundError, ValueError, FileManagerError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(
        descriptor.internal_path,
        media_type=descriptor.media_type,
        filename=descriptor.logical_name,
    )


@router.get("/{capture_id}/agents/{agent_id}/pcap")
async def download_agent_pcap(capture_id: str, agent_id: str):
    try:
        manager.get_session(capture_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    resource = manager.repository.get_pcap(capture_id, agent_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="PCAP not found")
    try:
        descriptor = manager.repository.prepare_download(resource.resource_id)
    except FileManagerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(
        descriptor.internal_path,
        media_type=descriptor.media_type,
        filename=descriptor.logical_name,
    )
