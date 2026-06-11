"""Tests: SAGE_HOOK_PROFILE=off silences all 3 SessionStart injectors.

T1 whole-branch finding (sev 72): the kill-switch previously only reached
the Stop/PreCompact path (_session_hook.py). The 3 installed SessionStart
injectors ignored it, emitting context even under SAGE_HOOK_PROFILE=off.

Fix: each injector now checks _read_hook_profile() at the top of main()
before any stdout write. These tests use subprocess so env-var isolation
mirrors real installed-hook execution (same pattern as test_autonomy_hooks.py
and TestAutonomyReportingEvalProfile in test_session_hooks.py).

Covered:
  - inject-codex-budget.py      off → no stdout; standard/unset → proceeds
  - claude-wakeup-sessionstart.py  off → no stdout; standard/unset → proceeds
  - autonomy-continuation-sessionstart.py  off → no stdout; standard/unset → proceeds
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INJECTOR_CODEX = REPO_ROOT / "installer-assets" / "inject-codex-budget.py"
INJECTOR_WAKEUP = REPO_ROOT / "installer-assets" / "claude-wakeup-sessionstart.py"
INJECTOR_AUTONOMY = REPO_ROOT / "installer-assets" / "autonomy-continuation-sessionstart.py"


def _run(script: Path, home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _write_valid_autonomy_marker(home: Path) -> Path:
    """Write a valid in-flight autonomy-run.json + run_log; returns run_log."""
    sage_dir = home / ".sage"
    sage_dir.mkdir(parents=True, exist_ok=True)
    run_log = home / "run-log.md"
    run_log.write_text(
        "PHASE 5 START\nSTATUS ok\nDECISION yes\nPHASE 5 COMPLETE\n", encoding="utf-8"
    )
    marker = sage_dir / "autonomy-run.json"
    marker.write_text(
        json.dumps({"run_log": str(run_log), "phase": "5", "status": "in-flight"}),
        encoding="utf-8",
    )
    return run_log


# ── inject-codex-budget.py ────────────────────────────────────────────────────


class TestInjectCodexBudgetProfile:
    """SAGE_HOOK_PROFILE=off must silence inject-codex-budget.py."""

    def test_profile_off_no_stdout(self, tmp_path):
        """SAGE_HOOK_PROFILE=off → no stdout emitted, exit 0."""
        r = _run(INJECTOR_CODEX, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "off"})
        assert r.returncode == 0
        assert r.stdout.strip() == "", (
            f"inject-codex-budget must emit nothing under profile=off; got: {r.stdout!r}"
        )

    def test_profile_standard_proceeds(self, tmp_path):
        """SAGE_HOOK_PROFILE=standard → injector proceeds (exits 0; may or may not emit)."""
        r = _run(INJECTOR_CODEX, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "standard"})
        assert r.returncode == 0

    def test_profile_unset_proceeds(self, tmp_path):
        """Unset SAGE_HOOK_PROFILE defaults to standard → injector proceeds."""
        env = {k: v for k, v in os.environ.items() if k != "SAGE_HOOK_PROFILE"}
        env["HOME"] = str(tmp_path)
        r = subprocess.run(
            [sys.executable, str(INJECTOR_CODEX)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert r.returncode == 0

    def test_profile_minimal_proceeds(self, tmp_path):
        """SAGE_HOOK_PROFILE=minimal → injector proceeds (off is the only silencer)."""
        r = _run(INJECTOR_CODEX, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "minimal"})
        assert r.returncode == 0


# ── claude-wakeup-sessionstart.py ─────────────────────────────────────────────


class TestClaudeWakeupSessionstartProfile:
    """SAGE_HOOK_PROFILE=off must silence claude-wakeup-sessionstart.py."""

    def test_profile_off_no_stdout(self, tmp_path):
        """SAGE_HOOK_PROFILE=off → no stdout emitted, exit 0."""
        r = _run(INJECTOR_WAKEUP, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "off"})
        assert r.returncode == 0
        assert r.stdout.strip() == "", (
            f"claude-wakeup-sessionstart must emit nothing under profile=off; got: {r.stdout!r}"
        )

    def test_profile_standard_proceeds(self, tmp_path):
        """SAGE_HOOK_PROFILE=standard → injector proceeds, exit 0."""
        r = _run(INJECTOR_WAKEUP, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "standard"})
        assert r.returncode == 0

    def test_profile_unset_proceeds(self, tmp_path):
        """Unset SAGE_HOOK_PROFILE defaults to standard → injector proceeds."""
        env = {k: v for k, v in os.environ.items() if k != "SAGE_HOOK_PROFILE"}
        env["HOME"] = str(tmp_path)
        r = subprocess.run(
            [sys.executable, str(INJECTOR_WAKEUP)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert r.returncode == 0

    def test_profile_minimal_proceeds(self, tmp_path):
        """SAGE_HOOK_PROFILE=minimal → injector proceeds (off is the only silencer)."""
        r = _run(INJECTOR_WAKEUP, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "minimal"})
        assert r.returncode == 0


# ── autonomy-continuation-sessionstart.py ────────────────────────────────────


class TestAutonomyContinuationSessionstartProfile:
    """SAGE_HOOK_PROFILE=off must silence autonomy-continuation-sessionstart.py."""

    def test_profile_off_no_stdout(self, tmp_path):
        """SAGE_HOOK_PROFILE=off → no stdout emitted even when a valid in-flight marker exists."""
        _write_valid_autonomy_marker(tmp_path)
        r = _run(INJECTOR_AUTONOMY, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "off"})
        assert r.returncode == 0
        assert r.stdout.strip() == "", (
            "autonomy-continuation-sessionstart must emit nothing under profile=off; "
            f"got: {r.stdout!r}"
        )

    def test_profile_standard_emits_on_valid_marker(self, tmp_path):
        """SAGE_HOOK_PROFILE=standard + valid marker → pointer injected, exit 0."""
        run_log = _write_valid_autonomy_marker(tmp_path)
        r = _run(INJECTOR_AUTONOMY, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "standard"})
        assert r.returncode == 0
        assert "AUTONOMY RUN IN-FLIGHT" in r.stdout
        assert str(run_log) in r.stdout

    def test_profile_unset_emits_on_valid_marker(self, tmp_path):
        """Unset SAGE_HOOK_PROFILE defaults to standard → pointer injected."""
        run_log = _write_valid_autonomy_marker(tmp_path)
        env = {k: v for k, v in os.environ.items() if k != "SAGE_HOOK_PROFILE"}
        env["HOME"] = str(tmp_path)
        r = subprocess.run(
            [sys.executable, str(INJECTOR_AUTONOMY)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert r.returncode == 0
        assert "AUTONOMY RUN IN-FLIGHT" in r.stdout
        assert str(run_log) in r.stdout

    def test_profile_minimal_proceeds(self, tmp_path):
        """SAGE_HOOK_PROFILE=minimal → injector proceeds (off is the only silencer)."""
        _write_valid_autonomy_marker(tmp_path)
        r = _run(INJECTOR_AUTONOMY, tmp_path, extra_env={"SAGE_HOOK_PROFILE": "minimal"})
        assert r.returncode == 0
