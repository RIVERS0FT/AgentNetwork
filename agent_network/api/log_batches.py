from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent_network.file_management import FileManagerError
from agent_network.log_management import get_log_manager


router = APIRouter()
log_manager = get_log_manager()


class LogResourceRef(BaseModel):
    session_id: str
    log_type: str


class LogResourceBatchRequest(BaseModel):
    items: List[LogResourceRef]


class LogParseBatchRequest(BaseModel):
    items: List[LogResourceRef]
    allow_hidden: bool = False
    max_errors_per_file: int = Field(default=100, ge=1, le=1000)


class LogVisibilityBatchRequest(BaseModel):
    items: List[LogResourceRef]
    visible: bool


class LogSessionBatchRequest(BaseModel):
    session_ids: List[str]


class LogSessionVisibilityBatchRequest(BaseModel):
    session_ids: List[str]
    visible: bool


def _file_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileManagerError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/batch/download")
async def batch_download_logs(req: LogResourceBatchRequest):
    result = await asyncio.to_thread(
        log_manager.batch_download_logs,
        [item.model_dump() for item in req.items],
    )
    payload = result.to_dict()
    if result.archive_resource_id:
        payload["download_url"] = (
            f"/api/logs/batch/download/{result.archive_resource_id}"
        )
    return payload


@router.get("/batch/download/{resource_id}")
async def download_log_batch(resource_id: str):
    try:
        descriptor = log_manager.prepare_log_batch_download(resource_id)
    except (ValueError, OSError, FileManagerError) as exc:
        raise _file_error(exc) from exc
    return FileResponse(
        descriptor.internal_path,
        media_type=descriptor.media_type,
        filename=descriptor.logical_name,
    )


@router.post("/batch/delete")
async def batch_delete_logs(req: LogResourceBatchRequest):
    result = await asyncio.to_thread(
        log_manager.batch_delete_logs,
        [item.model_dump() for item in req.items],
    )
    return result.to_dict()


@router.post("/batch/parse")
async def batch_parse_logs(req: LogParseBatchRequest):
    result = await asyncio.to_thread(
        log_manager.batch_parse_logs,
        [item.model_dump() for item in req.items],
        allow_hidden=req.allow_hidden,
        max_errors_per_file=req.max_errors_per_file,
    )
    return result.to_dict()


@router.post("/batch/visibility")
async def batch_set_log_visibility(req: LogVisibilityBatchRequest):
    result = await asyncio.to_thread(
        log_manager.batch_set_log_visibility,
        [item.model_dump() for item in req.items],
        req.visible,
    )
    return result.to_dict()


@router.post("/sessions/{session_id}/hide")
async def hide_log_session(session_id: str):
    try:
        return await asyncio.to_thread(
            log_manager.set_session_log_visibility,
            session_id,
            False,
        )
    except (ValueError, OSError, FileManagerError) as exc:
        raise _file_error(exc) from exc


@router.post("/sessions/{session_id}/show")
async def show_log_session(session_id: str):
    try:
        return await asyncio.to_thread(
            log_manager.set_session_log_visibility,
            session_id,
            True,
        )
    except (ValueError, OSError, FileManagerError) as exc:
        raise _file_error(exc) from exc


@router.post("/sessions/batch/visibility")
async def batch_set_session_visibility(req: LogSessionVisibilityBatchRequest):
    result = await asyncio.to_thread(
        log_manager.batch_set_session_log_visibility,
        req.session_ids,
        req.visible,
    )
    return result.to_dict()


@router.post("/sessions/batch/delete")
async def batch_delete_log_sessions(req: LogSessionBatchRequest):
    result = await asyncio.to_thread(
        log_manager.batch_delete_log_sessions,
        req.session_ids,
    )
    return result.to_dict()
