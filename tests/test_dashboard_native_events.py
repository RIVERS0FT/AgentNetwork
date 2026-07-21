from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_classifies_and_describes_native_audit_events():
    source = (ROOT / "web" / "public" / "dashboard_event_types.js").read_text(
        encoding="utf-8"
    )
    for event in (
        "policy_check",
        "tool_call_requested",
        "tool_result_received",
        "subagent_lifecycle",
    ):
        assert event in source
    for field in (
        "parent_agent_id",
        "child_session_id",
        "policy_sha256",
        "canonical_capability",
    ):
        assert field in source
