"""Tests for sage_mcp.cli — the main CLI dispatcher."""

import argparse
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from sage_mcp.cli import (
    cmd_hook,
    cmd_init,
    cmd_instructions,
    cmd_mine,
    cmd_recall,
    cmd_registry,
    cmd_repair,
    cmd_search,
    cmd_split,
    cmd_status,
    cmd_wakeup,
    main,
)


# ── CLI entry point: PYTHONPATH stripping ────────────────────────────────


_LEAK_PREFIX = "/__sage_cli_leak_sentinel__"


def test_cli_main_strips_leaked_pythonpath_from_env():
    """sage_mcp.cli:main must drop PYTHONPATH from the process env so
    any subprocess the CLI spawns starts clean. Mirrors the
    sys.path-filter test in test_init.py but for the env half of the
    split fix. See #1423.

    Three assertions cover the full split contract:
    - ENV_MID (after import, before main) is preserved verbatim:
      regression detector for someone moving the env pop back into
      __init__.py.
    - SENTINEL_IN_PATH is False at import time: package-level sys.path
      filter half of the split actually ran.
    - ENV_AFTER (after main) is None: CLI entry-point env strip ran.

    SystemExit is caught with a narrowed exit-code check so a future
    argparse change that exits with a non-zero code (e.g. usage error)
    surfaces as a test failure instead of being swallowed."""
    expected_env = f"{_LEAK_PREFIX}/a{os.pathsep}{_LEAK_PREFIX}/b"
    env = os.environ.copy()
    env["PYTHONPATH"] = expected_env
    # Run main() with --version so it exits cleanly without entering any
    # subcommand. argparse raises SystemExit(0) on --version; the wrapper
    # asserts the exit code is clean and prints the post-main PYTHONPATH
    # so the assertion is observable.
    code = (
        "import os, sys\n"
        "from sage_mcp.cli import main\n"
        f"prefix = {_LEAK_PREFIX!r}\n"
        "print('ENV_MID:', repr(os.environ.get('PYTHONPATH')))\n"
        "print('SENTINEL_IN_PATH:', any(prefix in (p or '') for p in sys.path))\n"
        "sys.argv = ['sage', '--version']\n"
        "try:\n"
        "    main()\n"
        "except SystemExit as exc:\n"
        "    assert exc.code in (0, None), f'unexpected exit code: {exc.code!r}'\n"
        "print('ENV_AFTER:', repr(os.environ.get('PYTHONPATH')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    diag = f"rc={result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}"
    assert result.returncode == 0, f"subprocess failed: {diag}"
    assert f"ENV_MID: {expected_env!r}" in result.stdout, (
        f"package import unexpectedly stripped env (regression in __init__.py): {diag}"
    )
    assert "SENTINEL_IN_PATH: False" in result.stdout, (
        f"package import did not filter sys.path (regression in __init__.py): {diag}"
    )
    assert "ENV_AFTER: None" in result.stdout, f"CLI did not strip PYTHONPATH: {diag}"


# ── cmd_status ─────────────────────────────────────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_status_default_nook(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(nook=None)
    mock_miner = MagicMock()
    with patch.dict("sys.modules", {"sage_mcp.miner": mock_miner}):
        cmd_status(args)
        mock_miner.status.assert_called_once_with(nook_path="/fake/nook")


@patch("sage_mcp.cli.SageConfig")
def test_cmd_status_custom_nook(mock_config_cls):
    args = argparse.Namespace(nook="~/my_nook")
    mock_miner = MagicMock()
    with patch.dict("sys.modules", {"sage_mcp.miner": mock_miner}):
        cmd_status(args)
        import os

        expected = os.path.expanduser("~/my_nook")
        mock_miner.status.assert_called_once_with(nook_path=expected)


# ── cmd_search ─────────────────────────────────────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_search_calls_ops(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(
        nook=None, query="test query", wing="mywing", room="myroom", results=3, agent=None
    )
    with patch("sage_mcp.ops.search", return_value={"results": []}) as mock_search:
        cmd_search(args)
        mock_search.assert_called_once_with(
            query="test query",
            nook_path="/fake/nook",
            collection_name="nook_drawers",
            wing="mywing",
            room="myroom",
            n_results=3,
            agents=None,
        )


@patch("sage_mcp.cli.SageConfig")
def test_cmd_search_with_agent_passes_filter(mock_config_cls):
    """`--agent <name>` flows through cmd_search → ops.search(agents=[name])."""
    mock_config_cls.return_value.nook_path = "/fake/nook"
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(
        nook=None,
        query="q",
        wing=None,
        room=None,
        results=5,
        agent="aidev-code-reviewer",
    )
    with patch("sage_mcp.ops.search", return_value={"results": []}) as mock_search:
        cmd_search(args)
        mock_search.assert_called_once_with(
            query="q",
            nook_path="/fake/nook",
            collection_name="nook_drawers",
            wing=None,
            room=None,
            n_results=5,
            agents=["aidev-code-reviewer"],
        )


@patch("sage_mcp.cli.SageConfig")
def test_cmd_search_backend_error_exits_1(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None, query="q", wing=None, room=None, results=5, agent=None)

    with patch("sage_mcp.ops.search", return_value={"error": "boom", "error_kind": "backend"}):
        with pytest.raises(SystemExit) as exc_info:
            cmd_search(args)
        assert exc_info.value.code == 1


@patch("sage_mcp.cli.SageConfig")
def test_cmd_search_validation_error_exits_2(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None, query="q", wing="../bad", room=None, results=5, agent=None)

    with patch(
        "sage_mcp.ops.search", return_value={"error": "bad wing", "error_kind": "validation"}
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_search(args)
        assert exc_info.value.code == 2


@patch("sage_mcp.cli.SageConfig")
def test_cmd_search_prints_hint_on_error(mock_config_cls, capsys):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None, query="q", wing=None, room=None, results=5, agent=None)
    err = {"error": "No nook found", "hint": "Run: sage init <dir>", "error_kind": "backend"}
    with patch("sage_mcp.ops.search", return_value=err):
        with pytest.raises(SystemExit):
            cmd_search(args)
    captured = capsys.readouterr()
    assert "No nook found" in captured.err
    assert "Run: sage init <dir>" in captured.err


# ── cmd_recall ─────────────────────────────────────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_recall_calls_ops(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None, query="q", wing="w", results=4, agent="aidev-keeper")
    with patch("sage_mcp.ops.search", return_value={"results": []}) as mock_search:
        cmd_recall(args)
        mock_search.assert_called_once_with(
            query="q",
            nook_path="/fake/nook",
            collection_name="nook_drawers",
            wing="w",
            n_results=4,
            agents=["aidev-keeper"],
        )


@patch("sage_mcp.cli.SageConfig")
def test_cmd_recall_validation_error_exits_2(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None, query="q", wing="../bad", results=5, agent=None)
    with patch(
        "sage_mcp.ops.search", return_value={"error": "bad wing", "error_kind": "validation"}
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_recall(args)
        assert exc_info.value.code == 2


# ── cmd_registry ───────────────────────────────────────────────────────


def test_cmd_registry_search_delegates_to_ops():
    args = argparse.Namespace(
        registry_command="search",
        registry_root="/repo",
        query="auth",
        kind="agent",
        limit=10,
        force_rebuild=False,
    )
    fake = {"results": [{"kind": "agent", "name": "a1", "one_line": "x"}]}
    with patch("sage_mcp.ops.registry_search", return_value=fake) as mock_reg:
        cmd_registry(args)
        mock_reg.assert_called_once_with(
            query="auth",
            kind="agent",
            limit=10,
            repo_root="/repo",
            force_rebuild=False,
        )


def test_cmd_registry_search_error_exits():
    args = argparse.Namespace(
        registry_command="search",
        registry_root="/repo",
        query="auth",
        kind=None,
        limit=10,
        force_rebuild=False,
    )
    err = {"error": "Registry build failed: boom", "error_kind": "backend"}
    with patch("sage_mcp.ops.registry_search", return_value=err):
        with pytest.raises(SystemExit) as exc_info:
            cmd_registry(args)
        assert exc_info.value.code == 1


# ── cmd_instructions ───────────────────────────────────────────────────


def test_cmd_instructions_calls_run_instructions():
    args = argparse.Namespace(name="help")
    with patch("sage_mcp.instructions_cli.run_instructions") as mock_run:
        cmd_instructions(args)
        mock_run.assert_called_once_with(name="help")


# ── cmd_hook ───────────────────────────────────────────────────────────


def test_cmd_hook_calls_run_hook():
    args = argparse.Namespace(hook="session-start", harness="claude-code")
    with patch("sage_mcp.hooks_cli.run_hook") as mock_run:
        cmd_hook(args)
        mock_run.assert_called_once_with(hook_name="session-start", harness="claude-code")


# ── cmd_init ───────────────────────────────────────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_init_no_entities(mock_config_cls, tmp_path):
    args = argparse.Namespace(dir=str(tmp_path), yes=True)
    with (
        patch("sage_mcp.entity_detector.scan_for_detection", return_value=[]),
        patch("sage_mcp.room_detector_local.detect_rooms_local") as mock_rooms,
        patch("sage_mcp.cli._maybe_run_mine_after_init"),
    ):
        cmd_init(args)
        mock_rooms.assert_called_once_with(project_dir=str(tmp_path), yes=True)
        mock_config_cls.return_value.init.assert_called_once()


@patch("sage_mcp.cli.SageConfig")
def test_cmd_init_with_entities(mock_config_cls, tmp_path):
    fake_files = [tmp_path / "a.txt"]
    detected = {"people": [{"name": "Alice"}], "projects": [], "uncertain": []}
    confirmed = {"people": ["Alice"], "projects": []}
    args = argparse.Namespace(dir=str(tmp_path), yes=True)
    with (
        patch("sage_mcp.entity_detector.scan_for_detection", return_value=fake_files),
        patch("sage_mcp.entity_detector.detect_entities", return_value=detected),
        patch("sage_mcp.entity_detector.confirm_entities", return_value=confirmed),
        patch("sage_mcp.room_detector_local.detect_rooms_local"),
        # Pass 0 (corpus_origin) needs real file IO; this test mocks
        # builtins.open globally for the entities.json write, which would
        # break Pass 0's file-reading path. Patch Pass 0 out — a separate
        # suite (tests/test_corpus_origin_integration.py) covers it directly.
        patch("sage_mcp.cli._run_pass_zero", return_value=None),
        patch("builtins.open", MagicMock()),
        patch("sage_mcp.cli._maybe_run_mine_after_init"),
    ):
        cmd_init(args)


@patch("sage_mcp.cli.SageConfig")
def test_cmd_init_normalizes_wing_name_for_topics_registry(mock_config_cls, tmp_path):
    """Regression for #1194: hyphenated dir names must be normalized to the
    same slug ``sage.yaml`` uses, otherwise ``topics_by_wing`` keys
    miss the miner's lookup at mine time and tunnels are silently dropped.
    """
    project = tmp_path / "my-cool-app"
    project.mkdir()
    fake_files = [project / "a.txt"]
    detected = {
        "people": [{"name": "Alice"}],
        "projects": [],
        "topics": [{"name": "Bun"}],
        "uncertain": [],
    }
    confirmed = {"people": ["Alice"], "projects": [], "topics": ["Bun"]}
    args = argparse.Namespace(dir=str(project), yes=True)
    with (
        patch("sage_mcp.entity_detector.scan_for_detection", return_value=fake_files),
        patch("sage_mcp.entity_detector.detect_entities", return_value=detected),
        patch("sage_mcp.entity_detector.confirm_entities", return_value=confirmed),
        patch("sage_mcp.miner.add_to_known_entities") as mock_register,
        patch("sage_mcp.room_detector_local.detect_rooms_local"),
        patch("builtins.open", MagicMock()),
        patch("sage_mcp.cli._maybe_run_mine_after_init"),
        # Pass-zero corpus-origin detection runs unconditionally inside
        # cmd_init now (#1221 / #1223). It accesses SageConfig fields
        # that don't survive MagicMock stringification, so stub it out —
        # this test only cares about the wing-slug write to the registry.
        patch("sage_mcp.cli._run_pass_zero", return_value=None),
    ):
        mock_register.return_value = "/tmp/known_entities.json"
        cmd_init(args)
        mock_register.assert_called_once()
        assert mock_register.call_args.kwargs["wing"] == "my_cool_app"


def test_cmd_init_honors_nook_flag(tmp_path, monkeypatch):
    """Regression for #1313: ``cmd_init`` must honor ``--nook`` instead of
    silently writing to ``~/.sage``. Mirrors the env-var pattern used
    by ``cmd_mine`` / ``cmd_status`` / ``mcp_server`` so every downstream
    read of ``cfg.nook_path`` (Pass 0, ``cfg.init()``, post-init mine)
    routes to the user-specified location.
    """
    project = tmp_path / "project"
    project.mkdir()
    nook = tmp_path / "custom_nook"

    # Make sure no leftover env var from another test leaks in — we want to
    # verify that --nook ALONE drives the resolution. Prime monkeypatch's
    # undo list with setenv first so that the env var ``cmd_init`` writes
    # below is rolled back at teardown (``delenv(raising=False)`` on a
    # missing key registers no undo entry, which would leak into the next
    # test).
    monkeypatch.setenv("SAGE_NOOK_PATH", "")
    monkeypatch.delenv("SAGE_NOOK_PATH")

    args = argparse.Namespace(
        dir=str(project),
        nook=str(nook),
        yes=True,
        auto_mine=False,
    )

    captured = {}

    def fake_pass_zero(project_dir, nook_dir, llm_provider):
        # Capture the nook_dir Pass 0 sees — this is the smoking-gun
        # value for the bug. Pre-fix it was always ~/.sage.
        captured["pass_zero_nook_dir"] = nook_dir
        return None

    with (
        patch("sage_mcp.entity_detector.scan_for_detection", return_value=[]),
        patch("sage_mcp.room_detector_local.detect_rooms_local"),
        patch("sage_mcp.cli._run_pass_zero", side_effect=fake_pass_zero),
        patch("sage_mcp.cli._maybe_run_mine_after_init"),
    ):
        cmd_init(args)

    expected = str(nook)
    # Pass 0 must have been handed the --nook location, not ~/.sage.
    assert captured["pass_zero_nook_dir"] == expected
    # And the env var must point at the custom nook so any downstream
    # ``cfg.nook_path`` read in this process resolves correctly too.
    import os

    assert os.environ.get("SAGE_NOOK_PATH") == os.path.abspath(expected)


@patch("sage_mcp.cli.SageConfig")
def test_cmd_init_with_entities_zero_total(mock_config_cls, tmp_path, capsys):
    """When entities detected but total is 0, prints 'No entities' message."""
    fake_files = [tmp_path / "a.txt"]
    detected = {"people": [], "projects": [], "uncertain": []}
    args = argparse.Namespace(dir=str(tmp_path), yes=False)
    with (
        patch("sage_mcp.entity_detector.scan_for_detection", return_value=fake_files),
        patch("sage_mcp.entity_detector.detect_entities", return_value=detected),
        patch("sage_mcp.room_detector_local.detect_rooms_local"),
        patch("sage_mcp.cli._maybe_run_mine_after_init"),
    ):
        cmd_init(args)
    out = capsys.readouterr().out
    assert "No entities detected" in out


# ── _maybe_run_mine_after_init (init → mine prompt, #1181) ─────────────


def _init_args(tmp_path, *, yes=False, auto_mine=False):
    return argparse.Namespace(dir=str(tmp_path), yes=yes, auto_mine=auto_mine)


def _fake_cfg(tmp_path):
    cfg = MagicMock()
    cfg.nook_path = str(tmp_path / "nook")
    return cfg


def _fake_scanned(tmp_path, n=3):
    """Build n real Path objects with stat()-able sizes for the scan estimate."""
    paths = []
    for i in range(n):
        p = tmp_path / f"f{i}.txt"
        p.write_text("x" * 1024)  # 1 KB each
        paths.append(p)
    return paths


def test_maybe_run_mine_prompt_accepted_runs_mine(tmp_path):
    """Empty / 'y' / 'yes' on the prompt triggers mine() in-process."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=False, auto_mine=False)
    cfg = _fake_cfg(tmp_path)
    scanned = _fake_scanned(tmp_path, n=3)
    with (
        patch("sage_mcp.miner.mine") as mock_mine,
        patch("sage_mcp.miner.scan_project", return_value=scanned),
        patch("builtins.input", return_value=""),
    ):
        _maybe_run_mine_after_init(args, cfg)
        mock_mine.assert_called_once_with(
            project_dir=str(tmp_path),
            nook_path=cfg.nook_path,
            files=scanned,
        )


def test_maybe_run_mine_prompt_yes_accepted_runs_mine(tmp_path):
    """Explicit 'y' answer also runs mine()."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=False, auto_mine=False)
    cfg = _fake_cfg(tmp_path)
    with (
        patch("sage_mcp.miner.mine") as mock_mine,
        patch("sage_mcp.miner.scan_project", return_value=[]),
        patch("builtins.input", return_value="Y"),
    ):
        _maybe_run_mine_after_init(args, cfg)
        mock_mine.assert_called_once()


def test_maybe_run_mine_prompt_declined_prints_hint(tmp_path, capsys):
    """'n' answer skips mine() and prints the resume hint."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=False, auto_mine=False)
    cfg = _fake_cfg(tmp_path)
    with (
        patch("sage_mcp.miner.mine") as mock_mine,
        patch("sage_mcp.miner.scan_project", return_value=[]),
        patch("builtins.input", return_value="n"),
    ):
        _maybe_run_mine_after_init(args, cfg)
        mock_mine.assert_not_called()
    out = capsys.readouterr().out
    # shlex.quote is a no-op on POSIX-safe paths but wraps Windows paths
    # (which contain backslashes) in single quotes, so the assertion has
    # to mirror what the production code actually emits.
    assert f"sage mine {shlex.quote(str(tmp_path))}" in out
    assert "Skipped" in out


def test_maybe_run_mine_yes_alone_still_prompts(tmp_path):
    """`--yes` is scoped to entity auto-accept and MUST still prompt for mine.

    Regression guard for the flag-overload review feedback on #1183: extending
    `--yes` to also auto-mine would silently change behaviour for scripted
    callers and turn a fast command into a minutes-long ChromaDB write.
    """
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=True, auto_mine=False)
    cfg = _fake_cfg(tmp_path)
    with (
        patch("sage_mcp.miner.mine") as mock_mine,
        patch("sage_mcp.miner.scan_project", return_value=[]),
        patch("builtins.input", return_value="n") as mock_input,
    ):
        _maybe_run_mine_after_init(args, cfg)
        mock_input.assert_called_once()  # the prompt MUST fire
        mock_mine.assert_not_called()


def test_maybe_run_mine_auto_mine_skips_prompt(tmp_path):
    """`--auto-mine` runs mine() automatically without calling input()."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=False, auto_mine=True)
    cfg = _fake_cfg(tmp_path)
    scanned = _fake_scanned(tmp_path, n=2)
    with (
        patch("sage_mcp.miner.mine") as mock_mine,
        patch("sage_mcp.miner.scan_project", return_value=scanned),
        patch("builtins.input", side_effect=AssertionError("input() must not be called")),
    ):
        _maybe_run_mine_after_init(args, cfg)
        mock_mine.assert_called_once_with(
            project_dir=str(tmp_path),
            nook_path=cfg.nook_path,
            files=scanned,
        )


def test_maybe_run_mine_yes_and_auto_mine_fully_noninteractive(tmp_path):
    """`--yes --auto-mine` together: never call input(), always mine."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=True, auto_mine=True)
    cfg = _fake_cfg(tmp_path)
    with (
        patch("sage_mcp.miner.mine") as mock_mine,
        patch("sage_mcp.miner.scan_project", return_value=[]),
        patch("builtins.input", side_effect=AssertionError("input() must not be called")),
    ):
        _maybe_run_mine_after_init(args, cfg)
        mock_mine.assert_called_once()


def test_maybe_run_mine_decline_quotes_path_with_spaces(tmp_path, capsys):
    """The resume hint must shell-quote the project dir so paths with
    spaces / metacharacters produce a copy-paste-safe command."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    spaced_dir = tmp_path / "my project dir"
    spaced_dir.mkdir()
    args = argparse.Namespace(dir=str(spaced_dir), yes=False, auto_mine=False)
    cfg = _fake_cfg(tmp_path)
    with (
        patch("sage_mcp.miner.mine"),
        patch("sage_mcp.miner.scan_project", return_value=[]),
        patch("builtins.input", return_value="n"),
    ):
        _maybe_run_mine_after_init(args, cfg)
    out = capsys.readouterr().out
    # shlex.quote wraps paths with spaces (and Windows backslashes) in
    # single quotes — the assertion must use the same shlex form so the
    # test passes on every platform's tmp_path layout.
    assert f"sage mine {shlex.quote(str(spaced_dir))}" in out
    # Bare unquoted form must NOT appear — that's the bug we're guarding.
    assert f"sage mine {spaced_dir} " not in out
    assert f"sage mine {spaced_dir}`" not in out


def test_maybe_run_mine_eof_on_stdin_treated_as_decline(tmp_path, capsys):
    """Piped / non-interactive stdin (EOFError) declines without crashing."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=False, auto_mine=False)
    cfg = _fake_cfg(tmp_path)
    with (
        patch("sage_mcp.miner.mine") as mock_mine,
        patch("sage_mcp.miner.scan_project", return_value=[]),
        patch("builtins.input", side_effect=EOFError),
    ):
        _maybe_run_mine_after_init(args, cfg)
        mock_mine.assert_not_called()
    assert "Skipped" in capsys.readouterr().out


def test_maybe_run_mine_failure_surfaces_via_exit(tmp_path, capsys):
    """Mine errors are not swallowed — they exit non-zero with an error line."""
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=False, auto_mine=True)
    cfg = _fake_cfg(tmp_path)
    with (
        patch("sage_mcp.miner.mine", side_effect=RuntimeError("boom")),
        patch("sage_mcp.miner.scan_project", return_value=[]),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _maybe_run_mine_after_init(args, cfg)
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "boom" in err


def test_maybe_run_mine_estimate_appears_before_prompt(tmp_path, capsys):
    """The file-count + size estimate line MUST render BEFORE the prompt.

    Required by the spec: hitting Enter on a default-Y prompt with no size
    info is a footgun on a real corpus where mine takes minutes. The user
    must see scope before being asked to confirm.
    """
    from sage_mcp.cli import _maybe_run_mine_after_init

    args = _init_args(tmp_path, yes=False, auto_mine=False)
    cfg = _fake_cfg(tmp_path)
    scanned = _fake_scanned(tmp_path, n=4)  # 4 files * 1 KB each
    captured_when_prompted = {}

    def fake_input(prompt):
        # Snapshot what stdout looked like at the moment the prompt fires.
        captured_when_prompted["stdout"] = capsys.readouterr().out
        return "n"

    with (
        patch("sage_mcp.miner.mine"),
        patch("sage_mcp.miner.scan_project", return_value=scanned),
        patch("builtins.input", side_effect=fake_input),
    ):
        _maybe_run_mine_after_init(args, cfg)

    pre_prompt = captured_when_prompted["stdout"]
    assert "4 files" in pre_prompt, f"file count missing from pre-prompt output: {pre_prompt!r}"
    assert "MB" in pre_prompt, f"size estimate missing from pre-prompt output: {pre_prompt!r}"
    assert "would be mined" in pre_prompt


# ── cmd_mine ───────────────────────────────────────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_projects_mode(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(
        dir="/tmp",
        nook=None,
        mode="projects",
        wing=None,
        agent="sage",
        limit=0,
        dry_run=False,
        no_gitignore=False,
        include_ignored=[],
        extract="exchange",
        agents=None,
    )
    with patch("sage_mcp.miner.mine") as mock_mine:
        cmd_mine(args)
        mock_mine.assert_called_once_with(
            project_dir="/tmp",
            nook_path="/fake/nook",
            wing_override=None,
            agent="sage",
            limit=0,
            dry_run=False,
            respect_gitignore=True,
            include_ignored=[],
            max_chunks_per_file=None,
            agents=[],
        )


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_convos_mode(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(
        dir="/tmp",
        nook=None,
        mode="convos",
        wing="mywing",
        agent="me",
        limit=10,
        dry_run=True,
        no_gitignore=False,
        include_ignored=[],
        extract="general",
        agents=None,
    )
    with patch("sage_mcp.convo_miner.mine_convos") as mock_mine:
        cmd_mine(args)
        mock_mine.assert_called_once_with(
            convo_dir="/tmp",
            nook_path="/fake/nook",
            wing="mywing",
            agent="me",
            limit=10,
            dry_run=True,
            extract_mode="general",
            agents=[],
        )


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_projects_mode_with_agents_flag(mock_config_cls):
    """`--agents X,Y` and repeated `--agents Z` both flow through to mine()."""
    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(
        dir="/tmp",
        nook=None,
        mode="projects",
        wing=None,
        agent="sage",
        limit=0,
        dry_run=False,
        no_gitignore=False,
        include_ignored=[],
        extract="exchange",
        agents=["aidev-code-reviewer,aidev-adversarial-auditor", "docs-keeper"],
    )
    with patch("sage_mcp.miner.mine") as mock_mine:
        cmd_mine(args)
        mock_mine.assert_called_once()
        kwargs = mock_mine.call_args.kwargs
        assert kwargs["agents"] == [
            "aidev-code-reviewer",
            "aidev-adversarial-auditor",
            "docs-keeper",
        ]


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_include_ignored_comma_split(mock_config_cls):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(
        dir="/tmp",
        nook=None,
        mode="projects",
        wing=None,
        agent="sage",
        limit=0,
        dry_run=False,
        no_gitignore=False,
        include_ignored=["a.txt,b.txt", "c.txt"],
        extract="exchange",
    )
    with patch("sage_mcp.miner.mine") as mock_mine:
        cmd_mine(args)
        mock_mine.assert_called_once()
        call_kwargs = mock_mine.call_args[1]
        assert call_kwargs["include_ignored"] == ["a.txt", "b.txt", "c.txt"]


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_exits_nonzero_on_lock_holder(mock_config_cls, capsys):
    """Regression #1264: lock contention must exit non-zero with a clear message.

    Before this fix the CLI silently returned 0 when another writer held
    the nook lock — operators using nohup/scripts had no way to detect
    the contention. The new behavior raises MineAlreadyRunning out of
    miner.mine() and cmd_mine catches it, printing the holder identity
    to stderr and exiting non-zero.
    """
    from sage_mcp.nook import MineAlreadyRunning

    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(
        dir="/tmp",
        nook=None,
        mode="projects",
        wing=None,
        agent="sage",
        limit=0,
        dry_run=False,
        no_gitignore=False,
        include_ignored=[],
        extract="exchange",
    )
    with patch(
        "sage_mcp.miner.mine",
        side_effect=MineAlreadyRunning(
            "nook /fake/nook is held by PID 12345 (sage mcp_server); wait for it to finish"
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            cmd_mine(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "PID 12345" in captured.err
    assert "mcp_server" in captured.err


# ── cmd_wakeup ─────────────────────────────────────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_wakeup(mock_config_cls, capsys):
    from sage_mcp.layers import Tier0Block

    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(nook=None, wing=None)
    mock_stack = MagicMock()
    fake_block = Tier0Block(text="Hello world context", budget=4500, registry_count=3)
    mock_stack.assemble_tier0.return_value = fake_block
    with patch("sage_mcp.layers.MemoryStack", return_value=mock_stack):
        cmd_wakeup(args)
    out = capsys.readouterr().out
    assert "Hello world context" in out
    assert "tokens" in out
    assert "budget" in out
    assert "registry" in out


# ── cmd_split ──────────────────────────────────────────────────────────


def test_cmd_split_basic():
    args = argparse.Namespace(dir="/chats", output_dir=None, dry_run=False, min_sessions=2)
    with patch("sage_mcp.split_mega_files.main") as mock_main:
        cmd_split(args)
        mock_main.assert_called_once()


def test_cmd_split_all_options():
    args = argparse.Namespace(dir="/chats", output_dir="/out", dry_run=True, min_sessions=5)
    with patch("sage_mcp.split_mega_files.main") as mock_main:
        cmd_split(args)
        mock_main.assert_called_once()
    # sys.argv should be restored
    assert sys.argv[0] != "sage split"


# ── main() argparse dispatch ──────────────────────────────────────────


def test_main_no_args_prints_help(capsys):
    with patch("sys.argv", ["sage"]):
        main()
    out = capsys.readouterr().out
    assert "sage" in out


def test_main_status_dispatches():
    with (
        patch("sys.argv", ["sage", "status"]),
        patch("sage_mcp.cli.cmd_status") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_main_search_dispatches():
    with (
        patch("sys.argv", ["sage", "search", "my query"]),
        patch("sage_mcp.cli.cmd_search") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_main_init_dispatches():
    with (
        patch("sys.argv", ["sage", "init", "/some/dir"]),
        patch("sage_mcp.cli.cmd_init") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_main_mine_dispatches():
    with (
        patch("sys.argv", ["sage", "mine", "/some/dir"]),
        patch("sage_mcp.cli.cmd_mine") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_main_wakeup_dispatches():
    with (
        patch("sys.argv", ["sage", "wake-up"]),
        patch("sage_mcp.cli.cmd_wakeup") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_main_split_dispatches():
    with (
        patch("sys.argv", ["sage", "split", "/chats"]),
        patch("sage_mcp.cli.cmd_split") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_mcp_command_prints_setup_guidance(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["sage", "mcp"])

    main()

    captured = capsys.readouterr()
    assert "sage MCP quick setup:" in captured.out
    assert "claude mcp add sage -- sage-mcp" in captured.out
    assert "codex mcp add sage -- sage-mcp" in captured.out
    assert "\nOptional custom nook:\n" in captured.out
    assert "sage-mcp --nook /path/to/nook" in captured.out
    assert "[--nook /path/to/nook]" not in captured.out
    assert captured.err == ""


def test_mcp_command_uses_custom_nook_path_when_provided(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["sage", "--nook", "~/tmp/my nook", "mcp"])

    main()

    captured = capsys.readouterr()
    expanded = str(Path("~/tmp/my nook").expanduser())

    assert "sage-mcp --nook" in captured.out
    assert expanded in captured.out
    assert "claude mcp add sage -- sage-mcp --nook" in captured.out
    assert "codex mcp add sage -- sage-mcp --nook" in captured.out
    assert "Optional custom nook:" not in captured.out
    assert "[--nook /path/to/nook]" not in captured.out
    assert captured.err == ""


def test_main_hook_no_subcommand_prints_help(capsys):
    with patch("sys.argv", ["sage", "hook"]):
        main()
    out = capsys.readouterr().out
    assert "hook" in out.lower() or "run" in out.lower()


def test_main_hook_run_dispatches():
    with (
        patch(
            "sys.argv",
            ["sage", "hook", "run", "--hook", "session-start", "--harness", "claude-code"],
        ),
        patch("sage_mcp.cli.cmd_hook") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_main_instructions_no_subcommand_prints_help(capsys):
    with patch("sys.argv", ["sage", "instructions"]):
        main()
    out = capsys.readouterr().out
    assert "instructions" in out.lower() or "init" in out.lower()


def test_main_instructions_dispatches():
    with (
        patch("sys.argv", ["sage", "instructions", "help"]),
        patch("sage_mcp.cli.cmd_instructions") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()


def test_main_repair_dispatches():
    with (
        patch("sys.argv", ["sage", "repair"]),
        patch("sage_mcp.cli.cmd_repair") as mock_cmd,
    ):
        main()
        mock_cmd.assert_called_once()
        mock_cmd.assert_called_once()


# ── cmd_repair ─────────────────────────────────────────────────────────


def _mock_backend_for(col=None, new_col=None):
    """Build a mock ChromaBackend whose get_collection/create_collection return *col* / *new_col*."""
    mock_backend = MagicMock()
    if col is not None:
        mock_backend.get_collection.return_value = col
    if new_col is not None:
        mock_backend.create_collection.return_value = new_col
    return mock_backend


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_no_nook(mock_config_cls, tmp_path, capsys):
    mock_config_cls.return_value.nook_path = str(tmp_path / "nonexistent")
    args = argparse.Namespace(nook=None)
    with patch("sage_mcp.backends.chroma.ChromaBackend"):
        cmd_repair(args)
    out = capsys.readouterr().out
    assert "No nook found" in out


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_requires_nook_database(mock_config_cls, tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    mock_config_cls.return_value.nook_path = str(nook_dir)
    args = argparse.Namespace(nook=None)
    with patch("sage_mcp.backends.chroma.ChromaBackend"):
        cmd_repair(args)
    out = capsys.readouterr().out
    assert "No nook database found" in out


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_error_reading(mock_config_cls, tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    sqlite3.connect(str(nook_dir / "chroma.sqlite3")).close()
    mock_config_cls.return_value.nook_path = str(nook_dir)
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None)
    mock_backend = MagicMock()
    mock_backend.get_collection.side_effect = Exception("corrupt db")
    with patch("sage_mcp.backends.chroma.ChromaBackend", return_value=mock_backend):
        cmd_repair(args)
    out = capsys.readouterr().out
    assert "Error reading nook" in out


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_zero_drawers(mock_config_cls, tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    sqlite3.connect(str(nook_dir / "chroma.sqlite3")).close()
    mock_config_cls.return_value.nook_path = str(nook_dir)
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None)
    mock_col = MagicMock()
    mock_col.count.return_value = 0
    mock_backend = _mock_backend_for(col=mock_col)
    with patch("sage_mcp.backends.chroma.ChromaBackend", return_value=mock_backend):
        cmd_repair(args)
    out = capsys.readouterr().out
    assert "Nothing to repair" in out


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_success(mock_config_cls, tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    sqlite3.connect(str(nook_dir / "chroma.sqlite3")).close()
    mock_config_cls.return_value.nook_path = str(nook_dir)
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None, yes=True)
    mock_col = MagicMock()
    mock_col.count.return_value = 2
    mock_col.get.return_value = {
        "ids": ["id1", "id2"],
        "documents": ["doc1", "doc2"],
        "metadatas": [{"wing": "a"}, {"wing": "b"}],
    }
    mock_temp_col = MagicMock()
    mock_temp_col.count.return_value = 2
    mock_new_col = MagicMock()
    mock_new_col.count.return_value = 2
    mock_backend = _mock_backend_for(col=mock_col, new_col=mock_new_col)
    mock_backend.create_collection.side_effect = [mock_temp_col, mock_new_col]
    with patch("sage_mcp.backends.chroma.ChromaBackend", return_value=mock_backend):
        cmd_repair(args)
    out = capsys.readouterr().out
    assert "Repair complete" in out
    assert "2 drawers rebuilt" in out
    assert mock_backend.delete_collection.call_args_list == [
        call(str(nook_dir), "nook_drawers__repair_tmp"),
        call(str(nook_dir), "nook_drawers"),
        call(str(nook_dir), "nook_drawers__repair_tmp"),
    ]
    mock_temp_col.upsert.assert_called_once()
    mock_new_col.upsert.assert_called_once()
    mock_new_col.add.assert_not_called()


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_uses_configured_collection(mock_config_cls, tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    sqlite3.connect(str(nook_dir / "chroma.sqlite3")).close()
    mock_config_cls.return_value.nook_path = str(nook_dir)
    mock_config_cls.return_value.collection_name = "custom_drawers"
    args = argparse.Namespace(nook=None, yes=True)
    mock_col = MagicMock()
    mock_col.count.return_value = 2
    mock_col.get.return_value = {
        "ids": ["id1", "id2"],
        "documents": ["doc1", "doc2"],
        "metadatas": [{"wing": "a"}, {"wing": "b"}],
    }
    mock_temp_col = MagicMock()
    mock_temp_col.count.return_value = 2
    mock_new_col = MagicMock()
    mock_new_col.count.return_value = 2
    mock_backend = _mock_backend_for(col=mock_col, new_col=mock_new_col)
    mock_backend.create_collection.side_effect = [mock_temp_col, mock_new_col]

    with patch("sage_mcp.backends.chroma.ChromaBackend", return_value=mock_backend):
        cmd_repair(args)

    out = capsys.readouterr().out
    assert "Repair complete" in out
    mock_backend.get_collection.assert_called_once_with(str(nook_dir), "custom_drawers")
    assert mock_backend.create_collection.call_args_list == [
        call(str(nook_dir), "custom_drawers__repair_tmp"),
        call(str(nook_dir), "custom_drawers"),
    ]
    assert mock_backend.delete_collection.call_args_list == [
        call(str(nook_dir), "custom_drawers__repair_tmp"),
        call(str(nook_dir), "custom_drawers"),
        call(str(nook_dir), "custom_drawers__repair_tmp"),
    ]


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_restores_backup_on_live_rebuild_failure(mock_config_cls, tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    sqlite3.connect(str(nook_dir / "chroma.sqlite3")).close()
    mock_config_cls.return_value.nook_path = str(nook_dir)
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None, yes=True)
    mock_col = MagicMock()
    mock_col.count.return_value = 2
    mock_col.get.return_value = {
        "ids": ["id1", "id2"],
        "documents": ["doc1", "doc2"],
        "metadatas": [{"wing": "a"}, {"wing": "b"}],
    }
    mock_temp_col = MagicMock()
    mock_temp_col.count.return_value = 2
    mock_backend = _mock_backend_for(col=mock_col)
    mock_backend.create_collection.side_effect = [mock_temp_col, RuntimeError("live build failed")]
    with patch("sage_mcp.backends.chroma.ChromaBackend", return_value=mock_backend):
        with pytest.raises(SystemExit) as excinfo:
            cmd_repair(args)
    out = capsys.readouterr().out
    assert excinfo.value.code == 1
    assert "Repair failed" in out
    assert "restoring from backup" in out
    mock_backend.close_nook.assert_called_once_with(str(nook_dir))
    assert mock_backend.delete_collection.call_args_list == [
        call(str(nook_dir), "nook_drawers__repair_tmp"),
        call(str(nook_dir), "nook_drawers"),
        call(str(nook_dir), "nook_drawers__repair_tmp"),
    ]


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_aborts_without_confirmation(mock_config_cls, tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    sqlite3.connect(str(nook_dir / "chroma.sqlite3")).close()
    mock_config_cls.return_value.nook_path = str(nook_dir)
    mock_config_cls.return_value.collection_name = "nook_drawers"
    args = argparse.Namespace(nook=None)
    mock_col = MagicMock()
    mock_col.count.return_value = 1
    mock_backend = _mock_backend_for(col=mock_col)
    with (
        patch("sage_mcp.backends.chroma.ChromaBackend", return_value=mock_backend),
        patch("builtins.input", return_value="n"),
    ):
        cmd_repair(args)
    out = capsys.readouterr().out
    assert "Aborted." in out
    mock_backend.create_collection.assert_not_called()


@patch("sage_mcp.cli.SageConfig")
def test_cmd_sync_no_nook_dir(mock_config_cls, tmp_path, capsys):
    """cmd_sync on a missing nook dir prints the State A message (#1498)."""
    from sage_mcp.cli import cmd_sync

    nook_path = tmp_path / "nonexistent"
    mock_config_cls.return_value.nook_path = str(nook_path)
    args = argparse.Namespace(nook=None, dir=None, root=[], wing=None, dry_run=False)
    cmd_sync(args)
    captured = capsys.readouterr()
    assert "No nook found" in captured.out + captured.err


@patch("sage_mcp.cli.SageConfig")
def test_cmd_sync_nook_dir_no_db(mock_config_cls, tmp_path, capsys):
    """cmd_sync on a nook dir without chroma.sqlite3 prints the State B
    message and does NOT trigger chromadb's lazy DB creation (#1498)."""
    from sage_mcp.cli import cmd_sync

    mock_config_cls.return_value.nook_path = str(tmp_path)
    args = argparse.Namespace(nook=None, dir=None, root=[], wing=None, dry_run=False)
    cmd_sync(args)
    captured = capsys.readouterr()
    assert "has no chroma.sqlite3 yet" in captured.out + captured.err
    # Side-effect-free: backend not invoked.
    assert list(tmp_path.iterdir()) == []


def test_cmd_repair_trailing_slash_does_not_recurse():
    """Repair with trailing slash should put backup outside nook dir (#395)."""
    import os

    args = argparse.Namespace(nook="/tmp/fake_nook/")
    with patch("sage_mcp.cli.os.path.isdir", return_value=False):
        cmd_repair(args)
    # Verify the rstrip logic: nook_path should not end with separator
    nook_path = os.path.expanduser(args.nook).rstrip(os.sep)
    backup_path = nook_path + ".backup"
    assert not backup_path.startswith(nook_path + os.sep)


# ── stdio reconfigure on Windows ─────────────────────────────────────


class _ReconfigurableStringIO:
    def __init__(self):
        self.reconfigure_calls = []

    def reconfigure(self, **kwargs):
        self.reconfigure_calls.append(kwargs)


def test_cli_reconfigures_stdio_to_utf8_on_windows():
    """Windows `sage` CLI must decode/encode stdio as UTF-8.

    Without this, piped non-ASCII input (`sage search ... < q.txt`)
    or piped non-ASCII output (`sage search "..." > out.txt`) is
    mojibaked through the system ANSI codepage on non-Latin Windows
    locales (cp1252/cp1251/cp950).
    """
    from sage_mcp.cli import _reconfigure_stdio_utf8_on_windows

    stdin = _ReconfigurableStringIO()
    stdout = _ReconfigurableStringIO()
    stderr = _ReconfigurableStringIO()
    with (
        patch.object(sys, "platform", "win32"),
        patch.object(sys, "stdin", stdin),
        patch.object(sys, "stdout", stdout),
        patch.object(sys, "stderr", stderr),
    ):
        _reconfigure_stdio_utf8_on_windows()

    # Per-stream errors policy: stdin survives bad bytes via
    # surrogateescape so a redirected non-UTF-8 file does not crash
    # the read; stdout/stderr use replace so a drawer carrying a
    # round-tripped surrogate half does not crash mid-print.
    assert stdin.reconfigure_calls == [{"encoding": "utf-8", "errors": "surrogateescape"}]
    assert stdout.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_cli_reconfigure_stdio_is_noop_off_windows():
    """Linux/macOS already default to UTF-8 stdio -- helper must not touch streams."""
    from sage_mcp.cli import _reconfigure_stdio_utf8_on_windows

    stdin = _ReconfigurableStringIO()
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(sys, "stdin", stdin),
    ):
        _reconfigure_stdio_utf8_on_windows()

    assert stdin.reconfigure_calls == []


# ── cmd_repair: from-sqlite mode exit codes ──────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_from_sqlite_validation_refusal_exits_nonzero(mock_config_cls, tmp_path, capsys):
    """When ``rebuild_from_sqlite`` returns ``{}`` for a validation
    refusal (missing source DB, in-place without --archive-existing,
    refusing to overwrite an existing dest), the CLI must surface a
    non-zero exit so unattended scripts and CI distinguish "invalid
    inputs" from "successful recovery that found zero rows."

    Catches: a regression where the CLI treats the validation-refusal
    sentinel as success, leaving CI green on a no-op repair that should
    have alerted an operator.
    """
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    mock_config_cls.return_value.nook_path = str(nook_dir)

    args = argparse.Namespace(
        nook=str(nook_dir),
        mode="from-sqlite",
        source=None,
        archive_existing=False,
        yes=True,
    )
    with patch("sage_mcp.repair.rebuild_from_sqlite", return_value={}):
        with pytest.raises(SystemExit) as excinfo:
            cmd_repair(args)
    assert excinfo.value.code == 1


@patch("sage_mcp.cli.SageConfig")
def test_cmd_repair_from_sqlite_success_does_not_exit(mock_config_cls, tmp_path):
    """A successful from-sqlite rebuild — even one that finds zero rows
    in a legitimately empty source nook — must NOT call ``sys.exit``.
    A populated counts dict (with ``0`` values) is the success signal;
    only the empty dict ``{}`` is reserved for validation refusal.

    Catches: a regression where ``if not counts`` is replaced by
    ``if not sum(counts.values())`` or similar, conflating "empty source"
    with "validation refused" and breaking idempotent recovery scripts.
    """
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    mock_config_cls.return_value.nook_path = str(nook_dir)

    args = argparse.Namespace(
        nook=str(nook_dir),
        mode="from-sqlite",
        source=None,
        archive_existing=False,
        yes=True,
    )
    # Zero rows but per-collection keys present → success, no exit.
    fake_counts = {"nook_drawers": 0, "nook_closets": 0}
    with patch("sage_mcp.repair.rebuild_from_sqlite", return_value=fake_counts):
        # Should return cleanly; no SystemExit raised.
        cmd_repair(args)


# ── Pass 3/4: cmd_mine source-directory guard ─────────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_refuses_missing_dir(mock_config_cls, capsys, tmp_path):
    mock_config_cls.return_value.nook_path = "/fake/nook"
    missing = str(tmp_path / "nope")
    args = argparse.Namespace(
        dir=missing,
        nook=None,
        mode="projects",
        wing=None,
        agent="sage",
        limit=0,
        dry_run=False,
        no_gitignore=False,
        include_ignored=[],
        extract="exchange",
        agents=None,
    )
    with pytest.raises(SystemExit) as exc:
        cmd_mine(args)
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_refuses_file_not_dir(mock_config_cls, capsys, tmp_path):
    """A regular file passes os.path.exists but os.walk yields zero entries —
    the silent 'Files: 0' bug. The guard uses isdir, not exists. (Pass 4 F4)"""
    mock_config_cls.return_value.nook_path = "/fake/nook"
    a_file = tmp_path / "README.md"
    a_file.write_text("not a directory")
    args = argparse.Namespace(
        dir=str(a_file),
        nook=None,
        mode="projects",
        wing=None,
        agent="sage",
        limit=0,
        dry_run=False,
        no_gitignore=False,
        include_ignored=[],
        extract="exchange",
        agents=None,
    )
    with pytest.raises(SystemExit) as exc:
        cmd_mine(args)
    assert exc.value.code == 1
    assert "not a directory" in capsys.readouterr().err


@patch("sage_mcp.cli.SageConfig")
def test_cmd_mine_surfaces_wing_not_registered(mock_config_cls, capsys, tmp_path):
    """WingNotRegisteredError must surface as a clean stderr message + exit 1,
    not a Python traceback. (Pass 3 Cat 15 F4)"""
    from sage_mcp.extensions.wing_registry import WingNotRegisteredError

    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(
        dir=str(tmp_path),
        nook=None,
        mode="projects",
        wing="unknown-wing",
        agent="sage",
        limit=0,
        dry_run=False,
        no_gitignore=False,
        include_ignored=[],
        extract="exchange",
        agents=None,
    )
    with patch(
        "sage_mcp.miner.mine",
        side_effect=WingNotRegisteredError(
            "Wing 'unknown-wing' is not registered. Run 'sage wing add unknown-wing --type dev' first."
        ),
    ):
        with pytest.raises(SystemExit) as exc:
            cmd_mine(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "unknown-wing" in err
    assert "wing add" in err


# ── Pass 3/4: cmd_migrate exit-code propagation ───────────────────────


@patch("sage_mcp.cli.SageConfig")
def test_cmd_migrate_propagates_failure_exit_code(mock_config_cls):
    """migrate() returns False on lock conflict / declined / refused / disk full.
    cmd_migrate must propagate as exit 1, not silently exit 0. (Pass 3 Cat 15 F1)"""
    from sage_mcp.cli import cmd_migrate

    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(nook=None, dry_run=False, yes=False)
    with patch("sage_mcp.migrate.migrate", return_value=False):
        with pytest.raises(SystemExit) as exc:
            cmd_migrate(args)
    assert exc.value.code == 1


@patch("sage_mcp.cli.SageConfig")
def test_cmd_migrate_success_exits_zero(mock_config_cls):
    from sage_mcp.cli import cmd_migrate

    mock_config_cls.return_value.nook_path = "/fake/nook"
    args = argparse.Namespace(nook=None, dry_run=False, yes=False)
    with patch("sage_mcp.migrate.migrate", return_value=True):
        # Should NOT raise SystemExit on success.
        cmd_migrate(args)


# ── Pass 3/4: --agent default ─────────────────────────────────────────


def test_mine_agent_default_is_sage():
    """--agent default must NOT be a personal handle; default is 'sage'."""
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "src" / "sage_mcp" / "cli.py").read_text(encoding="utf-8")
    match = re.search(r'p_mine\.add_argument\(\s*"--agent",\s*\n?\s*default="([^"]+)"', src)
    assert match is not None, "--agent default not found in cli.py"
    assert match.group(1) == "sage", f"got default={match.group(1)!r}"
