#!/usr/bin/env python3
"""Fail when a change set has no added or updated design document."""

from __future__ import annotations

import argparse
import subprocess
import sys


def git_paths(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def collect_changes(base: str | None) -> tuple[set[str], set[str]]:
    if base:
        comparison = f"{base}...HEAD"
        all_paths = git_paths("diff", "--name-only", "--diff-filter=ACDMRTUXB", comparison)
        record_paths = git_paths("diff", "--name-only", "--diff-filter=ACMRTUXB", comparison)
        return all_paths, record_paths

    untracked = git_paths("ls-files", "--others", "--exclude-standard")
    all_paths = (
        git_paths("diff", "--name-only", "--diff-filter=ACDMRTUXB")
        | git_paths("diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB")
        | untracked
    )
    record_paths = (
        git_paths("diff", "--name-only", "--diff-filter=ACMRTUXB")
        | git_paths("diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB")
        | untracked
    )
    return all_paths, record_paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require every Git change set to add or update a docs/*.md design record."
    )
    parser.add_argument(
        "--base",
        help="Compare BASE...HEAD for a committed branch or PR; omit to inspect the local worktree.",
    )
    args = parser.parse_args()

    try:
        all_paths, record_paths = collect_changes(args.base)
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"ERROR: unable to inspect Git changes: {message}", file=sys.stderr)
        return 2

    if not all_paths:
        print("PASS: no changes detected.")
        return 0

    design_records = sorted(
        path for path in record_paths if path.startswith("docs/") and path.lower().endswith(".md")
    )
    if not design_records:
        print("FAIL: the change set has no added or updated docs/*.md design record.", file=sys.stderr)
        print("Changed paths:", file=sys.stderr)
        for path in sorted(all_paths):
            print(f"  - {path}", file=sys.stderr)
        return 1

    print("PASS: design traceability record detected:")
    for path in design_records:
        print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
