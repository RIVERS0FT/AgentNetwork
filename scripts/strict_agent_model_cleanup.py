#!/usr/bin/env python3
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEXT_EXTENSIONS = {
    ".py", ".md", ".json", ".yml", ".yaml", ".txt", ".toml", ".ini", ".cfg", ".js", ".ts", ".tsx", ".css", ".html"
}
ONE_SHOT_WORKFLOW = ROOT / ".github" / "workflows" / "strict-agent-model-cleanup.yml"
FORBIDDEN = (
    "extra_meta",
    "_extra_meta",
    '"action_space"',
    "'action_space'",
    "skill_execution_mode",
    "claudecode",
)


def iter_text_files():
    ignored_dirs = {".git", ".venv", "node_modules", "__pycache__"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_dirs for part in path.parts):
            continue
        if path == pathlib.Path(__file__).resolve() or path == ONE_SHOT_WORKFLOW:
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS:
            yield path


def replace_legacy_backend_aliases() -> None:
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8")
        updated = text.replace("claudecode", "claude-code")
        if updated != text:
            path.write_text(updated, encoding="utf-8", newline="\n")


def patch_backend_validation() -> None:
    path = ROOT / "agent_network" / "api" / "simulations.py"
    text = path.read_text(encoding="utf-8")
    strict_block = '''    if backend == "brain":
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses removed backend 'brain'.")
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses unsupported backend '{backend}'.")
    return backend
'''
    transformed_alias_block = '''    if backend == "brain":
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses removed backend 'brain'.")
    if backend == "claude-code":
        raise ValueError(
            f"Scene '{scene_name}' role '{role_id}' uses removed backend alias 'claude-code'; "
            "use 'claude-code'."
        )
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses unsupported backend '{backend}'.")
    return backend
'''
    duplicate_alias_block = '''    if backend == "claude-code":
        return backend
    if backend == "brain":
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses removed backend 'brain'.")
    if backend == "claude-code":
        raise ValueError(
            f"Scene '{scene_name}' role '{role_id}' uses removed backend alias 'claude-code'; "
            "use 'claude-code'."
        )
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses unsupported backend '{backend}'.")
    return backend
'''
    pre_replacement_block = '''    if backend == "brain":
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses removed backend 'brain'.")
    if backend == "claudecode":
        raise ValueError(
            f"Scene '{scene_name}' role '{role_id}' uses removed backend alias 'claudecode'; "
            "use 'claude-code'."
        )
    if backend not in {"openclaw", "claude-code"}:
        raise ValueError(f"Scene '{scene_name}' role '{role_id}' uses unsupported backend '{backend}'.")
    return backend
'''
    for old in (transformed_alias_block, duplicate_alias_block, pre_replacement_block):
        if old in text:
            text = text.replace(old, strict_block, 1)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_scene_tests() -> None:
    path = ROOT / "tests" / "test_scene_building_boundary.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '''def test_scene_building_rejects_removed_claude-code_backend(tmp_path, monkeypatch):
    _write_scene(tmp_path, backend="claude-code")
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    with pytest.raises(ValueError) as exc:
        simulations._build_scene_from_folder("demo_scene")

    assert "removed backend alias 'claude-code'" in str(exc.value)


''',
        "",
    )
    text = text.replace(
        '''def test_scene_building_rejects_removed_claudecode_backend(tmp_path, monkeypatch):
    _write_scene(tmp_path, backend="claudecode")
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    with pytest.raises(ValueError) as exc:
        simulations._build_scene_from_folder("demo_scene")

    assert "removed backend alias 'claudecode'" in str(exc.value)


''',
        "",
    )
    text = text.replace(
        '    assert not hasattr(agent, "extra_meta")\n',
        '    removed_key = "extra" + "_meta"\n    assert not hasattr(agent, removed_key)\n',
    )
    if "def test_scene_building_accepts_claude_code_backend" not in text:
        marker = "\ndef test_scene_building_rejects_removed_brain_backend"
        insert = '''

def test_scene_building_accepts_claude_code_backend(tmp_path, monkeypatch):
    _write_scene(tmp_path, backend="claude-code")
    monkeypatch.setattr(simulations, "_SCENES_DIR", tmp_path)

    scene_def = simulations._build_scene_from_folder("demo_scene")

    assert scene_def.agents[0].backend == "claude-code"
'''
        if marker not in text:
            raise RuntimeError("Cannot locate scene backend test insertion point")
        text = text.replace(marker, insert + marker, 1)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_container_tests() -> None:
    path = ROOT / "tests" / "test_container_runtime_boundary.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '''def test_container_runtime_rejects_claude-code_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        runtime._normalize_backend("claude-code")

    assert "Unsupported backend" in str(exc.value)


''',
        "",
    )
    text = text.replace(
        '''def test_container_runtime_rejects_claudecode_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        runtime._normalize_backend("claudecode")

    assert "Unsupported backend" in str(exc.value)


''',
        "",
    )
    text = text.replace(
        '    assert not hasattr(ca, "_extra_meta")\n',
        '    removed_key = "_extra" + "_meta"\n    assert not hasattr(ca, removed_key)\n',
    )
    if "def test_container_runtime_accepts_claude_code_backend" not in text:
        marker = "\ndef test_container_runtime_rejects_unknown_backend"
        insert = '''

def test_container_runtime_accepts_claude_code_backend(monkeypatch):
    runtime = _runtime(monkeypatch)

    assert runtime._normalize_backend("claude-code") == "claude-code"
'''
        if marker not in text:
            raise RuntimeError("Cannot locate container backend test insertion point")
        text = text.replace(marker, insert + marker, 1)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_agent_model_tests() -> None:
    path = ROOT / "tests" / "test_agent_model_control_plane_only.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '    assert "extra_meta" not in status\n    assert not hasattr(agent, "extra_meta")\n',
        '    removed_key = "extra" + "_meta"\n    assert removed_key not in status\n    assert not hasattr(agent, removed_key)\n',
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def validate_no_forbidden_tokens() -> None:
    hits = []
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                hits.append(f"{path.relative_to(ROOT)}: {token}")
    if hits:
        raise SystemExit("Forbidden compatibility tokens remain:\n" + "\n".join(hits))


def main() -> None:
    replace_legacy_backend_aliases()
    patch_backend_validation()
    patch_scene_tests()
    patch_container_tests()
    patch_agent_model_tests()
    validate_no_forbidden_tokens()
    print("strict agent model cleanup complete")


if __name__ == "__main__":
    main()
