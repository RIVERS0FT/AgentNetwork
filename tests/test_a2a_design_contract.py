from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr" / "ADR-024-CommManager统一A2A通信与禁止广播.md"
TASK_ADR = ROOT / "docs" / "adr" / "ADR-025-Agent任务下发与A2A回调.md"


def test_a2a_adr_records_non_regression_contract():
    text = ADR.read_text(encoding="utf-8")

    for required in (
        "CommManager",
        "A2A 1.0",
        "禁止广播",
        "多目标必须顺序发送",
        "POST /a2a/v1/message:send",
        "POST /communication/configure",
        "TASK_STATE_COMPLETED",
        "不得恢复 `DirectBus`",
        "必须在同一变更中",
    ):
        assert required in text


def test_design_indexes_link_to_authoritative_a2a_adr():
    adr_name = ADR.name
    for relative_path in (
        "docs/README.md",
        "docs/adr/README.md",
        "docs/design/通信与网络仿真设计.md",
        "docs/guides/开发文档.md",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert adr_name in text


def test_task_adr_records_persistence_callback_and_no_broadcast_contract():
    text = TASK_ADR.read_text(encoding="utf-8")

    for required in (
        "delegate_task",
        "TaskManager",
        "SQLite",
        "X-A2A-Notification-Token",
        "sequence",
        "不提供广播",
        "MOCK_LLM=1",
    ):
        assert required in text

    for relative_path in (
        "docs/README.md",
        "docs/adr/README.md",
        "docs/design/通信与网络仿真设计.md",
        "docs/guides/开发文档.md",
    ):
        assert TASK_ADR.name in (ROOT / relative_path).read_text(encoding="utf-8")
