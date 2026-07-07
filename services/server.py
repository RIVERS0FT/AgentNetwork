import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from agent_network import state
from agent_network.logger import get_logger
from agent_network.agent_management import AgentRegistry
from agent_network.event_bus import PacketRecorder

# 导入路由模块
from agent_network.api import system, agents, simulations, logs, packets

logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.server_loop = asyncio.get_running_loop()
    yield


# ═══════════════════════════════════════════════
# FastAPI 应用初始化
# ═══════════════════════════════════════════════

app = FastAPI(
    title="AI Agent 仿真运行平台",
    description="企业级 AI Agent 仿真、推演与编排平台 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from agent_network.traffic_log import TrafficMiddleware, traffic_enabled
if traffic_enabled():
    app.add_middleware(TrafficMiddleware, component="srv", server_url="http://localhost:8000")


# ═══════════════════════════════════════════════
# 挂载 API 路由模块
# ═══════════════════════════════════════════════

app.include_router(system.router, prefix="/api", tags=["System"])
app.include_router(agents.router, prefix="/api/agents", tags=["Agents"])
app.include_router(simulations.router, prefix="/api", tags=["Simulations & Scenes"])
app.include_router(logs.router, prefix="/api/logs", tags=["Logs"])
app.include_router(packets.router, prefix="/api/packets", tags=["Packets"])


# ═══════════════════════════════════════════════
# WebSocket & 前端页面
# ═══════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点 — 实时推送 Agent 状态、日志和消息"""
    await websocket.accept()
    state.ws_clients.add(websocket)

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if data == "status":
                    agents_data = [a.get_status() for a in AgentRegistry.list_all()]
                    await websocket.send_json({
                        "type": "status",
                        "data": {
                            "agents": agents_data,
                            "stats": AgentRegistry.get_stats(),
                            "agent_logs": state.agent_logs[-50:],
                            "log_entries": logger.get_entries(50),
                            "topology": state.current_topology,
                        },
                    })
                elif data == "packets":
                    await websocket.send_json({
                        "type": "packets",
                        "data": {
                            "packets": PacketRecorder.get_records(limit=50),
                            "stats": PacketRecorder.get_stats(),
                        },
                    })
                elif data == "logs":
                    await websocket.send_json({
                        "type": "logs",
                        "data": {
                            "entries": logger.get_entries(50),
                            "stats": logger.get_index_stats(),
                        },
                    })
                elif data == "all":
                    agents_data = [a.get_status() for a in AgentRegistry.list_all()]
                    await websocket.send_json({
                        "type": "all",
                        "data": {
                            "agents": agents_data,
                            "agent_stats": AgentRegistry.get_stats(),
                            "agent_logs": state.agent_logs[-50:],
                            "log_entries": logger.get_entries(50),
                            "log_stats": logger.get_index_stats(),
                            "packet_stats": PacketRecorder.get_stats(),
                            "topology": state.current_topology,
                        },
                    })
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"WS error: {e}")
                break
    finally:
        state.ws_clients.discard(websocket)


# 静态文件挂载
if os.path.isdir("web/public"):
    app.mount("/static", StaticFiles(directory="web/public"), name="static")
if os.path.isdir("web/src"):
    app.mount("/src", StaticFiles(directory="web/src"), name="src")


@app.get("/", response_class=FileResponse)
async def serve_dashboard():
    return FileResponse("web/public/dashboard.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
