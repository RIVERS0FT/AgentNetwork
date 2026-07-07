import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_SERVER = ROOT / "services" / "agent_server.py"


def _text() -> str:
    return AGENT_SERVER.read_text(encoding="utf-8")


def _tree():
    return ast.parse(_text())


def test_agent_server_has_no_legacy_decide_or_act_endpoints():
    text = _text()

    assert '@app.post("/decide")' not in text
    assert "@app.post('/decide')" not in text
    assert '@app.post("/act")' not in text
    assert "@app.post('/act')" not in text


def test_agent_server_does_not_import_brain_or_tool_registry_execution():
    text = _text()

    forbidden = [
        "equip_brain",
        "AgentBrain",
        "BoundedFactBoard",
        "ToolRegistry.execute",
        "Skill.tools",
        "_create_tool_function",
    ]
    for item in forbidden:
        assert item not in text


def test_run_request_contains_scene_key_and_run_agent_uses_backend_adapter():
    text = _text()

    assert "scene_key: str = \"default\"" in text
    assert "AgentContext(" in text
    assert "scene_key=req.scene_key" in text
    assert "adapter.run_agent_task" in text
    assert "The full ReAct loop is delegated" in text


def test_agent_server_uploads_backend_application_events_to_srv():
    text = _text()

    assert 'f"{SERVER_URL}/api/logs/ingest"' in text
    assert "record = logger.emit_application_event(" in text
    assert "_snapshot_inbox()" in text
    assert "_ack_inbox(pending_ids)" in text


def test_agent_server_supported_backends_are_explicit_and_exclude_brain():
    text = _text()

    assert 'SUPPORTED_BACKENDS = {"openclaw", "claude-code", "direct_llm"}' in text
    assert "The brain backend has been removed" in text


def test_agent_server_exposes_only_skill_refs_contract():
    text = _text()

    assert "skill_refs: List[str]" in text
    assert "allowed_skills" not in text
    assert "_skill_names_from_legacy" not in text
    assert "skills: List[Dict" not in text
