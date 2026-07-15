import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from agent_network import state
from agent_network.agent_management import AgentRegistry
from agent_network.event_bus import PacketRecorder
from agent_network.log_manager import get_log_manager
from agent_network.api import agents, logs, managed_simulations, packets, system
log_manager = get_log_manager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    state.server_loop = asyncio.get_running_loop()
    yield
app = FastAPI(title='AI Agent 仿真运行平台', description='企业级 AI Agent 仿真、推演与编排平台 API', version='0.1.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(system.router, prefix='/api', tags=['System'])
app.include_router(agents.router, prefix='/api/agents', tags=['Agents'])
app.include_router(managed_simulations.router, prefix='/api', tags=['Simulations & Scenes'])
app.include_router(logs.router, prefix='/api/logs', tags=['Logs'])
app.include_router(packets.router, prefix='/api/packets', tags=['Packets'])

@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept(); state.ws_clients.add(websocket)
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if data == 'status':
                    agents_data = [agent.get_status() for agent in AgentRegistry.list_all()]; await websocket.send_json({'type': 'status', 'data': {'agents': agents_data, 'stats': AgentRegistry.get_stats(), 'agent_logs': state.agent_logs[-50:], 'log_entries': log_manager.get_entries(50), 'topology': state.current_topology}})
                elif data == 'packets': await websocket.send_json({'type': 'packets', 'data': {'packets': PacketRecorder.get_records(limit=50), 'stats': PacketRecorder.get_stats()}})
                elif data == 'logs': await websocket.send_json({'type': 'logs', 'data': {'entries': log_manager.get_entries(50), 'stats': log_manager.get_index_stats()}})
                elif data == 'all':
                    agents_data = [agent.get_status() for agent in AgentRegistry.list_all()]; await websocket.send_json({'type': 'all', 'data': {'agents': agents_data, 'agent_stats': AgentRegistry.get_stats(), 'agent_logs': state.agent_logs[-50:], 'log_entries': log_manager.get_entries(50), 'log_stats': log_manager.get_index_stats(), 'packet_stats': PacketRecorder.get_stats(), 'topology': state.current_topology}})
            except asyncio.TimeoutError: pass
            except Exception as exc:
                print(f'WS error: {exc}'); break
    finally: state.ws_clients.discard(websocket)
if os.path.isdir('web/public'): app.mount('/static', StaticFiles(directory='web/public'), name='static')
if os.path.isdir('web/src'): app.mount('/src', StaticFiles(directory='web/src'), name='src')

@app.get('/', response_class=FileResponse)
async def serve_dashboard(): return FileResponse('web/public/dashboard.html')
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
