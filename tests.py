#!/usr/bin/env python3
"""Full zero-token test entrypoint for AgentNetwork.

Usage:
    python tests.py
    python tests.py -q
    python tests.py tests/test_mcp_server_boundary.py -q

The entrypoint forces zero-token defaults and delegates to pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


DEFAULT_TEST_TARGETS = [
    "tests/test_mcp_server_boundary.py",
    "tests/test_skill_md_loader_context_only.py",
    "tests/test_adapter_context_boundary.py",
    "tests/test_scene_building_boundary.py",
    "tests/test_agent_model_control_plane_only.py",
    "tests/test_container_runtime_boundary.py",
    "tests/test_agent_server_boundary.py",
    "tests/test_direct_tool_execute_debug_only.py",
]


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)

    os.environ.setdefault("MOCK_LLM", "1")
    os.environ.setdefault("NO_LLM", "1")
    os.environ.setdefault("RUN_LLM_TESTS", "0")
    os.environ.setdefault("ENABLE_DEBUG_TOOL_EXECUTE", "0")

    user_args = sys.argv[1:]
    if user_args:
        cmd = [sys.executable, "-m", "pytest", *user_args]
    else:
        existing_targets = [p for p in DEFAULT_TEST_TARGETS if Path(p).exists()]
        cmd = [sys.executable, "-m", "pytest", *existing_targets, "-q"]

    print("[tests.py] zero-token env: MOCK_LLM=1 NO_LLM=1 RUN_LLM_TESTS=0")
    print("[tests.py] running:", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
