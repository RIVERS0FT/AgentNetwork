import importlib
import inspect

import pytest

import agent_network.log_manager as log_manager_module
from agent_network.log_manager import LogManager, infer_log_type, normalize_log_type


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
def test_log_manager_interfaces_only_accept_log_type():
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
def test_log_type_inference_uses_event_not_legacy_fields():
    assert infer_log_type({"event": "reasoning"}) == "application"
    assert infer_log_type({"event": "docker_http_outbound"}) == "network"
    assert infer_log_type({"event": "unknown"}) == "system"

    assert infer_log_type({"layer": "agent_application"}) == "system"
    assert infer_log_type({"category": "agent_application"}) == "system"
    assert infer_log_type({"category": "network_capture"}) == "system"
    assert infer_log_type({"log_type": "application"}) == "application"

    with pytest.raises(ValueError, match="unknown log type"):
        infer_log_type({"log_type": "application.jsonl"})
