import importlib
import inspect
from pathlib import Path

import pytest

import agent_network.log_management.log_manager as log_manager_module
from agent_network.log_management import LogManager, infer_log_type, normalize_log_type


ROOT = Path(__file__).resolve().parents[1]
REMOVED_NAMES = {
    "AGENT_APPLICATION_LAYER",
    "AGENT_NETWORK_LAYER",
    "SYSTEM_LAYER",
    "LOG_TYPE_TO_LAYER",
    "LAYER_TO_LOG_TYPE",
    "APPLICATION_CATEGORIES",
    "NETWORK_CATEGORIES",
    "REMOVED_APPLICATION_EVENTS",
    "infer_log_layer",
    "LogLevel",
    "SimulationLogger",
    "get_logger",
    "system_log",
    "agent_log",
    "message_log",
    "normalize_application_record",
    "normalize_network_record",
    "normalize_system_record",
    "_trace",
}


@pytest.mark.not_llm
def test_legacy_log_symbols_are_removed():
    for name in REMOVED_NAMES:
        assert not hasattr(log_manager_module, name)
    assert not hasattr(LogManager, "record")


@pytest.mark.not_llm
def test_virtual_logger_module_is_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agent_network.logger")


@pytest.mark.not_llm
def test_network_schema_is_integrated_into_log_manager():
    package = ROOT / "agent_network"
    log_manager_text = (
        package / "log_management" / "log_manager.py"
    ).read_text(encoding="utf-8")
    init_text = (package / "__init__.py").read_text(encoding="utf-8")

    assert not (package / "_log_manager_core.py").exists()
    assert not (package / "network_log_v4.py").exists()
    assert '"schema_version": "network.v4"' in log_manager_text
    assert '"schema_version": "application.v12"' in log_manager_text
    assert "NETWORK_CONTEXT_FIELDS" in log_manager_text
    assert "NETWORK_RAW_FIELDS" in log_manager_text
    assert "_log_manager_core" not in log_manager_text
    assert "network_log_v4" not in init_text


@pytest.mark.not_llm
def test_repository_uses_log_manager_import_directly():
    paths = (
        ROOT / "services" / "agent_server.py",
        ROOT / "agent_network" / "api" / "packets.py",
        ROOT / "agent_network" / "api" / "simulations.py",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "from agent_network.logger import" not in text
        assert "from agent_network.log_management import get_log_manager" in text


@pytest.mark.not_llm
def test_log_manager_interfaces_have_top_level_application_agent_id():
    application_parameters = inspect.signature(
        LogManager.emit_application_event
    ).parameters
    network_parameters = inspect.signature(
        LogManager.emit_network_event
    ).parameters
    system_parameters = inspect.signature(
        LogManager.emit_system_event
    ).parameters

    for name in (
        "actor",
        "tick",
        "level",
        "component",
        "source",
        "debug",
        "policy",
        "decision",
        "links",
        "parent_event_id",
        "event_id",
    ):
        assert name not in application_parameters
    assert "agent_id" in application_parameters
    assert "trace_id" in application_parameters

    assert set(network_parameters) == {
        "self",
        "context",
        "network",
        "raw",
        "timestamp",
        "log_id",
    }
    for name in (
        "event",
        "actor",
        "agent_id",
        "target",
        "action",
        "payload",
        "result",
        "metrics",
        "links",
        "trace_id",
        "parent_event_id",
    ):
        assert name not in network_parameters

    for name in ("category", "parent_event_id", "tick", "component"):
        assert name not in system_parameters
    assert "actor" in system_parameters
    assert "kind" in system_parameters
    assert "source" in system_parameters


@pytest.mark.not_llm
def test_log_manager_query_interfaces_only_accept_log_type():
    query_parameters = inspect.signature(LogManager.query).parameters
    export_parameters = inspect.signature(LogManager.export).parameters
    export_file_parameters = inspect.signature(LogManager.export_file).parameters

    assert "log_type" in query_parameters
    assert "layer" not in query_parameters
    assert "category" not in query_parameters
    assert "layer" not in export_parameters
    assert "layer" not in export_file_parameters


@pytest.mark.not_llm
def test_log_type_accepts_only_canonical_names():
    assert normalize_log_type("application") == "application"
    assert normalize_log_type("network") == "network"
    assert normalize_log_type("system") == "system"

    for legacy in (
        "agent_application",
        "agent_network",
        "application.jsonl",
        "network.jsonl",
        "system.jsonl",
    ):
        with pytest.raises(ValueError, match="unknown log type"):
            normalize_log_type(legacy)


@pytest.mark.not_llm
def test_log_type_inference_uses_packet_contract_not_legacy_fields():
    assert infer_log_type({"event": "reasoning"}) == "application"
    assert infer_log_type({"event": "packet"}) == "network"
    assert infer_log_type({
        "log_id": "net_01JZ123456",
        "context": {},
        "network": {},
        "raw": {},
    }) == "network"
    assert infer_log_type({"event": "docker_http_outbound"}) == "system"
    assert infer_log_type({"event": "unknown"}) == "system"

    assert infer_log_type({"layer": "agent_application"}) == "system"
    assert infer_log_type({"category": "agent_application"}) == "system"
    assert infer_log_type({"category": "network_capture"}) == "system"
    assert infer_log_type({"log_type": "application"}) == "application"

    with pytest.raises(ValueError, match="unknown log type"):
        infer_log_type({"log_type": "application.jsonl"})
