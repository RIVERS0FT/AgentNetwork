from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .file_manager import FileManager


_default_manager: Optional[FileManager] = None


def create_project_file_manager() -> FileManager:
    data_root = Path(os.environ.get("DATA_DIR", "./data"))
    pcap_root = Path(os.environ.get("PCAP_DIR", str(data_root / "pcap")))
    roots = {
        "scenes": os.environ.get("SCENE_DIR", "./scenes"),
        "logs": os.environ.get("LOG_DIR", str(data_root / "logs")),
        "pcap": pcap_root,
        "archives": os.environ.get(
            "ARCHIVE_DIR", str(data_root / "archives")
        ),
        "temp": os.environ.get("FILE_TEMP_DIR", str(data_root / "tmp")),
    }
    # The PCAP volume is shared by srv and all Agent containers, including
    # dynamically created slots. Keeping the registry here provides one
    # cross-process catalog without adding another Docker mount contract.
    catalog_path = os.environ.get(
        "FILE_REGISTRY_PATH", str(pcap_root / ".file_registry.json")
    )
    return FileManager(roots, catalog_path=catalog_path)


def get_file_manager() -> FileManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = create_project_file_manager()
    return _default_manager


def reset_file_manager() -> None:
    global _default_manager
    _default_manager = None
