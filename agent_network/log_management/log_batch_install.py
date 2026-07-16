from __future__ import annotations

from agent_network.log_management.log_batch import LogBatchMixin, _LOG_BATCH_METHODS


def install_log_batch_manager() -> None:
    """Install batch methods while preserving staticmethod descriptors."""
    from agent_network.log_management import log_manager as legacy

    if getattr(legacy, "_LOG_BATCH_MANAGER_INSTALLED", False):
        return
    target_classes = {legacy.LogManager}
    active = getattr(legacy, "_log_manager", None)
    if active is not None:
        target_classes.add(type(active))
    for target_class in target_classes:
        for name in _LOG_BATCH_METHODS:
            setattr(target_class, name, LogBatchMixin.__dict__[name])
    legacy._LOG_BATCH_MANAGER_INSTALLED = True
