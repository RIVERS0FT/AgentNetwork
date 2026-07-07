#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PATCH_ROOT = Path(__file__).resolve().parent
EXCLUDED = {"README_PATCH.md", "apply_skill_refs_patch.py"}
LEGACY_TOKENS_BY_FILE = {
    "agent_network/agent_model.py": (
        "self.skills",
        "self.tags",
        '"skills"',
        '"tags"',
        "skills: List[str]",
        "tags: List[str]",
        "skill: str",
        "tag: str",
    ),
    "agent_network/api/agents.py": (
        'req.get("skills"',
        'req.get("tags"',
        "skill: str",
        "tag: str",
        "find_agent(role=role, skill=skill, tag=tag)",
    ),
    "agent_network/api/simulations.py": (
        "ad.skills",
        "ad.tags",
        'instance.get("skills")',
        '"allowed_skills"',
    ),
    "agent_network/container_runtime.py": (
        '"allowed_skills"',
        "allowed_skills",
    ),
    "services/agent_server.py": (
        "allowed_skills",
        "_skill_names_from_legacy",
        "req.skills",
        "skills: List[Dict",
    ),
    "agent_network/adapters/base.py": (
        "allowed_skills",
        "skills: List[Dict",
    ),
    "agent_network/adapters/openclaw.py": (
        "allowed_skills",
        "agent_context.skills",
        "_skill_names(",
    ),
    "agent_network/adapters/claude_code.py": (
        "allowed_skills",
        "agent_context.skills",
        "_skill_names(",
        "--allowed-skills",
    ),
    "agent_network/adapters/direct_llm.py": (
        "allowed_skills",
        "agent_context.skills",
        "_skill_names(",
    ),
    "agent_network/skill_md_loader.py": (
        "allowed_skills",
        '"skill_name"',
    ),
    "agent_network/mcp_server.py": (
        "_SKILLS_CACHE",
        "_ALLOWED_SKILLS",
        "parse_skill_md",
        "--allowed-skills",
    ),
}


def patch_files() -> list[Path]:
    return sorted(
        path.relative_to(PATCH_ROOT)
        for path in PATCH_ROOT.rglob("*")
        if path.is_file() and path.name not in EXCLUDED and "__pycache__" not in path.parts
    )


def validate_root(root: Path) -> None:
    required = (
        root / "agent_network",
        root / "services",
        root / "tests",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Not an AgentNetworkSimulation repository root: missing {missing}")


def apply(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / ".migration-backups" / f"skill-refs-{stamp}"
    for relative in patch_files():
        source = PATCH_ROOT / relative
        target = root / relative
        if target.exists():
            backup_target = backup / relative
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    for relative in (
        Path("scripts/remove_skill_compat.py"),
        Path(".github/workflows/remove-skill-compat.yml"),
    ):
        target = root / relative
        if target.exists():
            backup_target = backup / relative
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_target)
            target.unlink()
    return backup


def check(root: Path) -> None:
    # Only require patched paths to exist. Do not require byte-for-byte equality:
    # formatting, comments, line endings, or legitimate follow-up edits must not
    # invalidate the skill contract.
    for relative in patch_files():
        target = root / relative
        if not target.exists():
            raise RuntimeError(f"Missing patched file: {relative}")

    for relative, tokens in LEGACY_TOKENS_BY_FILE.items():
        path = root / relative
        if not path.exists():
            raise RuntimeError(f"Missing runtime file: {relative}")
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if token in text:
                raise RuntimeError(f"Legacy token {token!r} remains in {relative}")

    agent_model = (root / "agent_network/agent_model.py").read_text(encoding="utf-8")
    if '"skills"' in agent_model or '"tags"' in agent_model:
        raise RuntimeError("Legacy Agent status fields remain in agent_model.py")

    scene_def = (root / "agent_network/scene_def.py").read_text(encoding="utf-8")
    if "skill_refs: List[str]" not in scene_def:
        raise RuntimeError("AgentDef.skill_refs is missing")
    if re.search(r"^\s+tags\s*:", scene_def, flags=re.MULTILINE):
        raise RuntimeError("Legacy AgentDef.tags remains")
    if re.search(r"^\s+skills\s*:", scene_def, flags=re.MULTILINE):
        raise RuntimeError("Legacy AgentDef.skills remains")

    server = (root / "services/agent_server.py").read_text(encoding="utf-8")
    if "skill_refs: List[str]" not in server:
        raise RuntimeError("RunRequest.skill_refs is missing")

    mcp = (root / "agent_network/mcp_server.py").read_text(encoding="utf-8")
    if "parse_skill_md" in mcp or "--allowed-skills" in mcp:
        raise RuntimeError("MCP still contains Skill content handling")


def run_tests(root: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "agent_network", "services", "tests"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_scene_building_boundary.py",
            "tests/test_container_runtime_boundary.py",
            "tests/test_mcp_server_boundary.py",
            "tests/test_skill_md_loader_context_only.py",
            "tests/test_adapter_context_boundary.py",
            "tests/test_agent_server_boundary.py",
            "tests/test_agent_model_control_plane_only.py",
        ],
        cwd=root,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    validate_root(root)

    if args.apply:
        backup = apply(root)
        print(f"Backup: {backup}")

    check(root)
    print("Skill contract is skill_refs-only.")

    if args.test:
        run_tests(root)
        print("Compilation and boundary tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
