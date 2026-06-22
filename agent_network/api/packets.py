from typing import Optional
from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from agent_network.event_bus import PacketRecorder

router = APIRouter()

@router.get("/")
async def query_packets(
    agent_id: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    limit: int = Query(default=100, le=500),
):
    records = PacketRecorder.get_records(agent_id=agent_id, direction=direction, limit=limit)
    return {
        "total": PacketRecorder.get_stats()["total_packets"],
        "packets": records,
        "stats": PacketRecorder.get_stats(),
    }

@router.get("/stats")
async def packet_stats():
    return PacketRecorder.get_stats()

@router.get("/stream")
async def packet_stream(agent_id: Optional[str] = Query(None), limit: int = 100):
    lines = PacketRecorder.get_wireshark_view(agent_id=agent_id, limit=limit)
    return PlainTextResponse("\n".join(lines), media_type="text/plain")
