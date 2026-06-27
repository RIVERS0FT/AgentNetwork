import pytest
from fastapi.testclient import TestClient
from agent_network.api.logs import router
from agent_network.logger import get_logger
from fastapi import FastAPI

app = FastAPI()
app.include_router(router, prefix="/api/logs")

client = TestClient(app)

@pytest.fixture
def mock_logs():
    logger = get_logger()
    logger._entries.clear()
    
    # Insert dummy records
    records = [
        {
            "event": "agent_message",
            "category": "agent_application",
            "actor": {"id": "agent_A"},
            "message": "msg 1"
        },
        {
            "event": "decide",
            "category": "agent_application",
            "actor": {"id": "agent_B"},
            "message": "decide 1"
        },
        {
            "event": "act",
            "category": "agent_application",
            "actor": {"id": "agent_B"},
            "message": "act 1"
        },
        {
            "event": "agent_application",  # Random application event
            "category": "agent_application",
            "actor": {"id": "agent_C"},
            "message": "other 1"
        }
    ]
    
    for r in records:
        logger._entries.append(r)
        
    yield logger
    
    logger._entries.clear()

@pytest.mark.not_llm
def test_messages_api_view(mock_logs):
    response = client.get("/api/logs/messages")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["entries"][0]["event"] == "agent_message"

@pytest.mark.not_llm
def test_behavior_api_view(mock_logs):
    response = client.get("/api/logs/behavior")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    events = [e["event"] for e in data["entries"]]
    assert "decide" in events
    assert "act" in events
    assert "agent_message" not in events
