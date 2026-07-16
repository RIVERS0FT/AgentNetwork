"""Unified log management package."""

from .log_batch import LogBatchItemResult, LogBatchResult
from .log_manager import (
    LOG_SCHEMAS,
    LOG_TYPE_TO_FILENAME,
    get_log_manager,
    infer_log_type,
    normalize_log_timestamp,
    normalize_log_type,
)

__all__ = [
    "LOG_SCHEMAS",
    "LOG_TYPE_TO_FILENAME",
    "LogBatchItemResult",
    "LogBatchResult",
    "LogManager",
    "get_log_manager",
    "infer_log_type",
    "normalize_log_timestamp",
    "normalize_log_type",
]


def __getattr__(name: str):
    """Resolve the installable LogManager class after package bootstrap."""
    if name == "LogManager":
        from . import log_manager

        return log_manager.LogManager
    raise AttributeError(name)
