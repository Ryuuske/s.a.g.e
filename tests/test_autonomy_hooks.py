"""Regression tests for the Phase-5 autonomy hooks (ADR-0066).

The SessionStart continuation injector and the Stop reporting evaluator are
fail-open scripts: they must NEVER block a session (always exit 0) and must
no-op on absent / malformed / terminal / stale markers so a stale marker
cannot hijack a fresh session. Run as subprocesses with HOME pointed at a
temp dir so the ~/.sage marker contract is exercised in isolation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INJECTOR = REPO_ROOT / "installer-assets" / "autonomy-continuation-sessionstart.py"
EVALUATOR = REPO_ROOT / "hooks" / "scripts" / "autonomy_reporting_eval.py"


def _run(script: Path, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _write_marker(home: Path, marker: dict | str) -> Path:
    sage = home / ".sage"
    sage.mkdir(parents=True, exist_ok=True)
    p = sage / "autonomy-run.json"
    p.write_text(marker if isinstance(marker, str) else json.dumps(marker), encoding="utf-8")
    return p


def test_injector_silent_without_marker(tmp_path: Path) -> None:
    r = _run(INJECTOR, tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_injector_silent_on_malformed_marker(tmp_path: Path) -> None:
    _write_marker(tmp_path, "not json{")
    r = _run(INJECTOR, tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_injector_silent_on_terminal_status(tmp_path: Path) -> None:
    _write_marker(tmp_path, {"status": "terminal"})
    r = _run(INJECTOR, tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_injector_silent_when_run_log_missing(tmp_path: Path) -> None:
    # in-flight but the run_log file does not exist -> cannot verify -> no-op.
    _write_marker(
        tmp_path,
        {"run_log": str(tmp_path / "nope.md"), "phase": "5", "status": "in-flight"},
    )
    r = _run(INJECTOR, tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_injector_silent_on_stale_marker(tmp_path: Path) -> None:
    run_log = tmp_path / "run-log.md"
    run_log.write_text("# run\n", encoding="utf-8")
    marker = _write_marker(
        tmp_path,
        {"run_log": str(run_log), "phase": "5", "status": "in-flight"},
    )
    # Age the marker well past the 7-day staleness window.
    old = time.time() - (8 * 24 * 3600)
    os.utime(marker, (old, old))
    r = _run(INJECTOR, tmp_path)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_injector_emits_continue_on_fresh_valid_marker(tmp_path: Path) -> None:
    run_log = tmp_path / "run-log.md"
    run_log.write_text("# run\n", encoding="utf-8")
    _write_marker(
        tmp_path,
        {"run_log": str(run_log), "phase": "5", "status": "in-flight", "skills_changed": True},
    )
    r = _run(INJECTOR, tmp_path)
    assert r.returncode == 0
    assert "AUTONOMY RUN IN-FLIGHT" in r.stdout
    assert str(run_log) in r.stdout
    assert "/reload-plugins" in r.stdout  # skills_changed surfaced


def test_injector_silent_on_non_md_run_log(tmp_path: Path) -> None:
    # A non-.md path (e.g. /etc/passwd) is never printed verbatim.
    _write_marker(
        tmp_path,
        {"run_log": "/etc/passwd", "phase": "5", "status": "in-flight"},
    )
    r = _run(INJECTOR, tmp_path)
    assert r.returncode == 0
    assert "/etc/passwd" not in r.stdout


def test_evaluator_silent_without_marker(tmp_path: Path) -> None:
    r = _run(EVALUATOR, tmp_path)
    assert r.returncode == 0
    assert not (tmp_path / ".sage" / "autonomy-reporting-eval.log").exists()


def test_evaluator_records_on_valid_marker(tmp_path: Path) -> None:
    run_log = tmp_path / "run-log.md"
    run_log.write_text("PHASE 5 START\nSTATUS\nDECISION\nPHASE 5 COMPLETE\n", encoding="utf-8")
    _write_marker(
        tmp_path,
        {"run_log": str(run_log), "phase": "5", "status": "in-flight"},
    )
    r = _run(EVALUATOR, tmp_path)
    assert r.returncode == 0
    log = tmp_path / ".sage" / "autonomy-reporting-eval.log"
    assert log.exists()
    assert "phase=5" in log.read_text(encoding="utf-8")


def test_evaluator_silent_on_irregular_run_log(tmp_path: Path) -> None:
    # A directory passed as run_log is not a regular file -> no-op, no hang.
    d = tmp_path / "adir.md"
    d.mkdir()
    _write_marker(
        tmp_path,
        {"run_log": str(d), "phase": "5", "status": "in-flight"},
    )
    r = _run(EVALUATOR, tmp_path)
    assert r.returncode == 0
