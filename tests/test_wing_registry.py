"""Phase 4 acceptance tests for the wing registry + .sageignore.

These tests bypass the test conftest's session-scoped
``SAGE_WING_CONFIG`` pointer at points where the production
behaviour is what's under test (an unregistered wing is the test
input, not a regression).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _isolated_registry(tmp_path: Path, monkeypatch) -> None:
    """Point the wing registry at an empty config in tmp_path.

    The session fixture in conftest.py sets SAGE_WING_CONFIG to a
    permissive test config. Tests that exercise the rejection path need
    to either override the env or shrink the registered set; this helper
    picks the second option by writing an empty registry to tmp.
    """
    cfg = {"version": 1, "wing_types": {"dev": {"halls": [], "l1": []}}, "wings": {}}
    cfg_path = tmp_path / "wing_config.json"
    cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setenv("SAGE_WING_CONFIG", str(cfg_path))
    from sage_mcp.extensions import wing_registry

    wing_registry._invalidate_cache()


# ── Phase 4 acceptance #1 — unregistered wings rejected ───────────────


def test_unregistered_wing_rejected(monkeypatch, tmp_path):
    """The plan's required smoke: tool_add_drawer with an unregistered
    wing must surface a clear error rather than creating a phantom wing."""
    _isolated_registry(tmp_path, monkeypatch)

    # Reach into the registry directly — same code path tool_add_drawer
    # uses via require_registered_wing, but unit-tested here without the
    # full MCP plumbing so the assertion is on the registry contract.
    from sage_mcp.extensions.wing_registry import (
        WingNotRegisteredError,
        require_registered_wing,
    )

    with pytest.raises(WingNotRegisteredError) as exc_info:
        require_registered_wing("not-a-real-wing")
    msg = str(exc_info.value)
    assert "not-a-real-wing" in msg
    assert "sage wing add not-a-real-wing" in msg
    assert "--type" in msg


def test_diary_wings_always_pass_through_registry(monkeypatch, tmp_path):
    """`wing_<agent_name>` is the diary auto-generated convention; it must
    validate True even when the registry has zero explicit wings."""
    _isolated_registry(tmp_path, monkeypatch)
    from sage_mcp.extensions.wing_registry import (
        is_registered,
        require_registered_wing,
    )

    assert is_registered("wing_aidev-code-reviewer") is True
    require_registered_wing("wing_aidev-code-reviewer")  # no exception


# ── Phase 4 acceptance #2 — .sageignore excludes runtime configs ─


def test_claude_dir_ignored_by_mine_walk(tmp_path):
    """A file under .claude/ must not be returned by ``scan_project``
    when the project root has a .sageignore that excludes .claude/.
    Mirrors the plan's smoke: ".claude/CLAUDE.md is invisible to mining."
    """
    from sage_mcp.miner import scan_project

    # Build a tiny project tree with both a normal file and a .claude/ file.
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("orchestrator config — never mine me")
    (tmp_path / "README.md").write_text("normal project file")
    (tmp_path / ".sageignore").write_text("# exclusions for mine\n.claude/\n.obsidian/\n")

    files = scan_project(str(tmp_path), respect_gitignore=False)
    names = {Path(f).name for f in files}
    paths = {str(Path(f).relative_to(tmp_path)) for f in files}

    assert "README.md" in names, f"normal file got dropped: {files}"
    assert all(".claude" not in p for p in paths), f".claude/ contents leaked into scan: {paths}"
    assert "CLAUDE.md" not in names, f".claude/CLAUDE.md was mined: {files}"


def test_obsidian_dir_ignored_by_mine_walk(tmp_path):
    """.obsidian/ exclusion sibling of the .claude/ test."""
    from sage_mcp.miner import scan_project

    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "config.json").write_text('{"theme": "dark"}')
    (tmp_path / "note.md").write_text("a real note")
    (tmp_path / ".sageignore").write_text(".claude/\n.obsidian/\n")

    files = scan_project(str(tmp_path), respect_gitignore=False)
    paths = {str(Path(f).relative_to(tmp_path)) for f in files}
    assert "note.md" in paths
    assert all(".obsidian" not in p for p in paths), paths


def test_sageignore_overrides_gitignore_independence(tmp_path):
    """.sageignore runs independently of .gitignore.

    When the project's .gitignore tracks .claude/ (so it ships in the
    repo) but .sageignore excludes it from mining, the file must
    still be skipped on the mine walk even with respect_gitignore=True.
    """
    from sage_mcp.miner import scan_project

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    (tmp_path / "README.md").write_text("intro")
    # .gitignore is empty — .claude/ would otherwise pass the gitignore check.
    (tmp_path / ".gitignore").write_text("")
    (tmp_path / ".sageignore").write_text(".claude/\n")

    files = scan_project(str(tmp_path), respect_gitignore=True)
    paths = {str(Path(f).relative_to(tmp_path)) for f in files}
    assert "README.md" in paths
    assert all(".claude" not in p for p in paths), paths


# ── Wing CLI smokes ───────────────────────────────────────────────────


def test_wing_add_registers_new_slug(monkeypatch, tmp_path):
    """`add_wing` writes the slug into wing_config.json and the next
    `load_config(force_reload=True)` sees it."""
    _isolated_registry(tmp_path, monkeypatch)
    from sage_mcp.extensions.wing_registry import (
        add_wing,
        is_registered,
        load_config,
    )

    assert is_registered("freshly-added") is False
    add_wing("freshly-added", "project", path="/tmp/freshly-added")
    cfg = load_config(force_reload=True)
    assert "freshly-added" in cfg["wings"]
    assert cfg["wings"]["freshly-added"]["type"] == "project"
    assert is_registered("freshly-added") is True


def test_wing_add_rejects_invalid_type(monkeypatch, tmp_path):
    """add_wing must refuse a type outside dev|project|knowledge|ops|meta."""
    _isolated_registry(tmp_path, monkeypatch)
    from sage_mcp.extensions.wing_registry import add_wing

    with pytest.raises(ValueError, match="Invalid wing type"):
        add_wing("invalid-type-wing", "not_a_type")


def test_wing_add_rejects_duplicate(monkeypatch, tmp_path):
    """A second add for the same slug must error rather than silently
    overwriting the existing registration."""
    _isolated_registry(tmp_path, monkeypatch)
    from sage_mcp.extensions.wing_registry import add_wing

    add_wing("once", "ops")
    with pytest.raises(ValueError, match="already registered"):
        add_wing("once", "ops")


# ── Additional wing_registry unit tests (relocated from test_bootstrap.py) ───


def test_wing_registry_env_var_path(tmp_path, monkeypatch):
    """SAGE_WING_CONFIG env var overrides all other config-path resolution."""
    import json
    from sage_mcp.extensions import wing_registry

    cfg_file = tmp_path / "my_wing_config.json"
    cfg_file.write_text(json.dumps({"wings": {"test_wing": {"type": "dev"}}}))
    monkeypatch.setenv("SAGE_WING_CONFIG", str(cfg_file))
    wing_registry._invalidate_cache()

    path = wing_registry._resolve_config_path()
    assert path == cfg_file

    wing_registry._invalidate_cache()


def test_resolve_write_path_seeds_empty_wings_not_maintainer_wings(tmp_path, monkeypatch):
    """First-run write seeds ONLY the taxonomy — never the template's registered wings.

    Regression for the codex Phase-8 HIGH: a fresh user (no ~/.sage/wing_config.json)
    must not inherit the repo-root template's maintainer-specific wings (which would
    leak names + skip the user's own repos on slug collision). The seed copies
    version + wing_types and forces an empty wings map.
    """
    import json
    from sage_mcp.extensions import wing_registry

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    # A repo-root-style template carrying a maintainer project wing (under ~/dev)
    # AND a framework-generic wing (under ~/.sage). The seed must drop the former
    # and keep the latter.
    template = tmp_path / "repo" / "wing_config.json"
    template.parent.mkdir(parents=True)
    template.write_text(
        json.dumps(
            {
                "version": 1,
                "wing_types": {"dev": {"halls": ["handoff"], "l1": []}},
                "wings": {
                    "maintainer-private-proj": {"type": "dev", "path": "/home/maint/secret"},
                    "telemetry": {"type": "meta", "path": str(fake_home / ".sage" / "telemetry")},
                },
            }
        )
    )
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)
    monkeypatch.setattr(wing_registry.Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(wing_registry, "_resolve_config_path", lambda: template)

    write_path = wing_registry._resolve_write_path()
    assert write_path == fake_home / ".sage" / "wing_config.json"
    seeded = json.loads(write_path.read_text())
    assert "maintainer-private-proj" not in seeded["wings"], (
        "maintainer project wing must be dropped"
    )
    assert "maintainer-private-proj" not in json.dumps(seeded), "no maintainer name/path leaks"
    assert "telemetry" in seeded["wings"], "framework-internal (~/.sage) wing must be kept"
    assert "dev" in seeded["wing_types"], "taxonomy must be preserved"
    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_resolve_write_path_hardens_permissions(tmp_path, monkeypatch):
    """First-run seed must create ~/.sage 0700 and wing_config.json 0600 (POSIX).

    Regression for codex Phase-8 HIGH: the privacy-sensitive registry (absolute
    repo paths) must not be world-readable under a default umask.
    """
    import json
    import os
    import stat as _stat
    import sys

    if sys.platform.startswith("win"):
        import pytest as _pytest

        _pytest.skip("POSIX permission semantics only")

    from sage_mcp.extensions import wing_registry

    template = tmp_path / "repo" / "wing_config.json"
    template.parent.mkdir(parents=True)
    template.write_text(json.dumps({"version": 1, "wing_types": {"dev": {}}, "wings": {}}))
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)
    monkeypatch.setattr(wing_registry.Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(wing_registry, "_resolve_config_path", lambda: template)
    # Force a permissive umask so the test would catch a missing chmod.
    old = os.umask(0o022)
    try:
        write_path = wing_registry._resolve_write_path()
    finally:
        os.umask(old)
    sage_dir = fake_home / ".sage"
    assert _stat.S_IMODE(sage_dir.stat().st_mode) == 0o700, "~/.sage must be owner-only"
    assert _stat.S_IMODE(write_path.stat().st_mode) == 0o600, "registry must be owner-only"
    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_load_config_file_not_found(tmp_path, monkeypatch):
    """load_config raises FileNotFoundError when wing_config.json does not exist."""
    from sage_mcp.extensions import wing_registry

    missing = tmp_path / "no_such_file.json"
    monkeypatch.setenv("SAGE_WING_CONFIG", str(missing))
    wing_registry._invalidate_cache()

    with pytest.raises(FileNotFoundError):
        wing_registry.load_config(force_reload=True)

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_is_registered_diary_prefix(monkeypatch):
    """is_registered returns True for wings with the diary prefix regardless of config."""
    from sage_mcp.extensions import wing_registry

    # Diary prefix "wing_" always returns True.
    result = wing_registry.is_registered("wing_aidev-keeper")
    assert result is True


def test_wing_registry_is_registered_file_not_found_fails_open(tmp_path, monkeypatch):
    """is_registered returns True (fail-open) when wing_config.json is absent."""
    from sage_mcp.extensions import wing_registry

    missing = tmp_path / "nonexistent.json"
    monkeypatch.setenv("SAGE_WING_CONFIG", str(missing))
    wing_registry._invalidate_cache()

    result = wing_registry.is_registered("any_slug")
    assert result is True  # fail-open behaviour

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_add_wing_writes_and_reads_back(tmp_path, monkeypatch):
    """add_wing persists a new entry that is then readable via load_config."""
    import json
    from sage_mcp.extensions import wing_registry

    cfg_file = tmp_path / "wing_config.json"
    cfg_file.write_text(json.dumps({"wings": {}}))
    monkeypatch.setenv("SAGE_WING_CONFIG", str(cfg_file))
    wing_registry._invalidate_cache()

    wing_registry.add_wing("my_new_wing", "dev", path=str(tmp_path))

    wing_registry._invalidate_cache()
    cfg = wing_registry.load_config(force_reload=True)
    assert "my_new_wing" in cfg["wings"]
    assert cfg["wings"]["my_new_wing"]["type"] == "dev"

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_load_config_invalid_json(tmp_path, monkeypatch):
    """load_config raises ValueError when wing_config.json is not valid JSON."""
    from sage_mcp.extensions import wing_registry

    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text("{not valid json")
    monkeypatch.setenv("SAGE_WING_CONFIG", str(cfg_file))
    wing_registry._invalidate_cache()

    with pytest.raises(ValueError, match="not valid JSON"):
        wing_registry.load_config(force_reload=True)

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_is_registered_true_for_known_wing(tmp_path, monkeypatch):
    """is_registered returns True for a slug present in wing_config.json."""
    import json
    from sage_mcp.extensions import wing_registry

    cfg_file = tmp_path / "wing_config.json"
    cfg_file.write_text(json.dumps({"wings": {"known_wing": {"type": "dev"}}}))
    monkeypatch.setenv("SAGE_WING_CONFIG", str(cfg_file))
    wing_registry._invalidate_cache()

    assert wing_registry.is_registered("known_wing") is True
    assert wing_registry.is_registered("unknown_wing") is False

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_add_wing_duplicate_raises(tmp_path, monkeypatch):
    """add_wing raises ValueError for a slug that's already registered."""
    import json
    from sage_mcp.extensions import wing_registry

    cfg_file = tmp_path / "wing_config.json"
    cfg_file.write_text(json.dumps({"wings": {"dup_wing": {"type": "dev"}}}))
    monkeypatch.setenv("SAGE_WING_CONFIG", str(cfg_file))
    wing_registry._invalidate_cache()

    with pytest.raises(ValueError, match="already registered"):
        wing_registry.add_wing("dup_wing", "dev")

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_is_registered_empty_string():
    """is_registered returns False for an empty string slug."""
    from sage_mcp.extensions import wing_registry

    assert wing_registry.is_registered("") is False
    assert wing_registry.is_registered(None) is False  # type: ignore[arg-type]


def test_wing_registry_add_wing_config_missing(tmp_path, monkeypatch):
    """add_wing raises FileNotFoundError when config file does not exist."""
    from sage_mcp.extensions import wing_registry

    missing_cfg = tmp_path / "no_config.json"
    monkeypatch.setenv("SAGE_WING_CONFIG", str(missing_cfg))
    wing_registry._invalidate_cache()

    with pytest.raises(FileNotFoundError):
        wing_registry.add_wing("new_wing", "dev")

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


def test_wing_registry_registered_wings_returns_dict(tmp_path, monkeypatch):
    """registered_wings() returns the wings mapping as a dict."""
    import json
    from sage_mcp.extensions import wing_registry

    cfg_file = tmp_path / "wing_config.json"
    cfg_file.write_text(json.dumps({"wings": {"w1": {"type": "dev"}, "w2": {"type": "project"}}}))
    monkeypatch.setenv("SAGE_WING_CONFIG", str(cfg_file))
    wing_registry._invalidate_cache()

    wings = wing_registry.registered_wings()
    assert "w1" in wings
    assert "w2" in wings

    wing_registry._invalidate_cache()
    monkeypatch.delenv("SAGE_WING_CONFIG", raising=False)


# ── E2E M1/F3 — add_wing sanitizes the slug ───────────────────────────


def test_add_wing_rejects_path_traversal_slug(monkeypatch, tmp_path):
    """sage wing add must reject traversal/metachar slugs (E2E M1/F3).

    Previously add_wing validated only wing_type, so '../../etc/evil' and
    'foo/bar' registered silently at exit 0 and polluted wing_config.json.
    """
    _isolated_registry(tmp_path, monkeypatch)
    from sage_mcp.extensions.wing_registry import add_wing, load_config

    for bad in ["../../etc/evil", "foo/bar", "a\\b", "..", "x;rm -rf y"]:
        with pytest.raises(ValueError):
            add_wing(bad, "dev")
    # None of the rejected slugs leaked into the registry.
    assert load_config().get("wings", {}) == {}


def test_add_wing_accepts_clean_slug(monkeypatch, tmp_path):
    """A normal slug still registers (no over-rejection regression)."""
    _isolated_registry(tmp_path, monkeypatch)
    from sage_mcp.extensions.wing_registry import add_wing, is_registered

    add_wing("my-project_1", "dev")
    assert is_registered("my-project_1")
