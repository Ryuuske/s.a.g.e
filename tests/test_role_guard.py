"""Deterministic decision-matrix tests for hooks/scripts/role_guard.py.

Covers every acceptance-criteria row from the plan (2026-06-20):

  - Orchestrator (no agent_id) + Write/Edit/MultiEdit/NotebookEdit → deny
  - Subagent (agent_id present) + any write tool → allow
  - Orchestrator + Bash write-pattern → deny
  - Orchestrator + git / gh Bash → allow (admin carve-out)
  - Orchestrator + Bash read-only / safe commands → allow
  - Malformed stdin (not JSON, wrong type, partial) → fail-open allow
  - SAGE_ROLE_GUARD=off → allow-all regardless of tool or agent_id
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

# ── Import the module under test ──────────────────────────────────────────────
HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks" / "scripts"
sys.path.insert(0, str(HOOKS_DIR))

import role_guard  # noqa: E402 — must be after sys.path.insert


def _reload(monkeypatch, role_guard_val: str | None = None) -> None:
    """Reload role_guard so env-var changes are picked up.

    Clears SAGE_ROLE_GUARD from the environment (or sets it) then
    reloads the module so the module-level _guard_enabled() result is fresh.
    """
    if role_guard_val is None:
        monkeypatch.delenv("SAGE_ROLE_GUARD", raising=False)
    else:
        monkeypatch.setenv("SAGE_ROLE_GUARD", role_guard_val)
    importlib.reload(role_guard)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _allow() -> dict:
    return {}


def _deny() -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": role_guard._DENY_REASON,
        }
    }


def _orchestrator_payload(tool_name: str, tool_input: dict | None = None) -> dict:
    """Build a hook payload that looks like an orchestrator call (no agent_id)."""
    p = {"tool_name": tool_name}
    if tool_input is not None:
        p["tool_input"] = tool_input
    return p


def _subagent_payload(tool_name: str, tool_input: dict | None = None) -> dict:
    """Build a hook payload that looks like a dispatched subagent call."""
    p = {"tool_name": tool_name, "agent_id": "sub-abc-123"}
    if tool_input is not None:
        p["tool_input"] = tool_input
    return p


# ── Orchestrator write tools → deny ──────────────────────────────────────────


class TestOrchestratorWriteToolsDenied:
    """Orchestrator calls to file-write tools are always denied."""

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_orchestrator_write_tool_denied(self, tool, monkeypatch):
        _reload(monkeypatch)
        result = role_guard.decide(_orchestrator_payload(tool))
        assert result == _deny(), f"Expected deny for orchestrator {tool}, got {result}"

    def test_deny_contains_implementer_hint(self, monkeypatch):
        """Deny reason must name aidev-code-implementer so the orchestrator knows what to do."""
        _reload(monkeypatch)
        result = role_guard.decide(_orchestrator_payload("Write"))
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        assert "aidev-code-implementer" in reason
        assert "dev-code-implementer" in reason

    def test_deny_contains_claude_md_reference(self, monkeypatch):
        """Deny reason must reference CLAUDE.md for traceability."""
        _reload(monkeypatch)
        result = role_guard.decide(_orchestrator_payload("Edit"))
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        assert "CLAUDE.md" in reason


# ── Subagent write tools → allow ─────────────────────────────────────────────


class TestSubagentWriteToolsAllowed:
    """Dispatched subagents (agent_id present) must always be allowed to write."""

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_subagent_write_tool_allowed(self, tool, monkeypatch):
        _reload(monkeypatch)
        result = role_guard.decide(_subagent_payload(tool))
        assert result == _allow(), f"Expected allow for subagent {tool}, got {result}"

    def test_subagent_bash_write_allowed(self, monkeypatch):
        """Subagents may also run Bash with write patterns."""
        _reload(monkeypatch)
        payload = _subagent_payload("Bash", {"command": "echo x > /some/file.txt"})
        result = role_guard.decide(payload)
        assert result == _allow()

    def test_empty_string_agent_id_treated_as_absent(self, monkeypatch):
        """An empty-string agent_id is falsy but technically present.

        The check is ``agent_id is not None`` — an empty string IS present
        and should allow (the docs say absent = orchestrator; if an agent_id
        field exists, even empty, it came from a subagent context).
        """
        _reload(monkeypatch)
        payload = {"tool_name": "Write", "agent_id": ""}
        # agent_id="" → present → allow
        result = role_guard.decide(payload)
        assert result == _allow()


# ── Orchestrator Bash write patterns → deny ───────────────────────────────────


class TestOrchestratorBashWritePatternsDenied:
    """Representative Bash write idioms are denied for the orchestrator."""

    @pytest.mark.parametrize(
        "command",
        [
            "echo x > /home/user/file.txt",
            "echo y >> /repo/src/foo.py",
            "tee /tmp_real/output.log",
            "tee myfile.md",
            "sed -i 's/foo/bar/' /path/to/file",
            "sed --in-place 's/a/b/' file.py",
            "dd if=/dev/zero of=/path/out bs=1M count=1",
            "cp source.py dest.py",
            "mv old.py new.py",
            "install -m 755 script.sh /usr/local/bin/",
            "truncate -s 0 logfile.txt",
            "cat <<EOF > /path/config.json",
        ],
    )
    def test_orchestrator_bash_write_pattern_denied(self, command, monkeypatch):
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _deny(), f"Expected deny for Bash command {command!r}, got {result}"


# ── Orchestrator Bash admin / safe carve-outs → allow ────────────────────────


class TestOrchestratorBashCarveoutsAllowed:
    """git and gh commands are always allowed (orchestrator admin lane)."""

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git add hooks/scripts/role_guard.py",
            "git commit -m 'feat(hooks): add role_guard'",
            "git push origin feat/orchestrator-write-block",
            "git diff origin/main...HEAD",
            "gh pr create --title 'x' --body 'y'",
            "gh pr view 42",
            "gh api repos/owner/repo/pulls",
        ],
    )
    def test_git_gh_always_allowed(self, command, monkeypatch):
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), f"Expected allow for admin command {command!r}, got {result}"


class TestOrchestratorBashReadOnlyAllowed:
    """Read-only Bash commands must not be blocked."""

    @pytest.mark.parametrize(
        "command",
        [
            "ls /repo",
            "cat /repo/pyproject.toml",
            "grep -r 'agent_id' hooks/",
            "find . -name '*.py'",
            "python3 -m pytest tests/ -q",
            "uv run ruff check .",
            "echo 'hello world'",
            "cat file.txt | grep foo",
            "echo something > /dev/null",
            "some_cmd > /tmp/scratch.txt",
        ],
    )
    def test_read_only_bash_allowed(self, command, monkeypatch):
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), f"Expected allow for read-only command {command!r}, got {result}"


# ── Orchestrator non-write tool calls → allow ─────────────────────────────────


class TestOrchestratorNonWriteToolsAllowed:
    """Read / view / search tools should not be blocked."""

    @pytest.mark.parametrize(
        "tool",
        ["Read", "WebSearch", "ListFiles", "Glob", "mcp__nook_query"],
    )
    def test_orchestrator_read_tools_allowed(self, tool, monkeypatch):
        _reload(monkeypatch)
        result = role_guard.decide(_orchestrator_payload(tool))
        assert result == _allow()


# ── SAGE_ROLE_GUARD=off → allow-all ──────────────────────────────────────────


class TestDialOff:
    """SAGE_ROLE_GUARD=off must disable all blocking."""

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_dial_off_allows_write_tools(self, tool, monkeypatch):
        _reload(monkeypatch, role_guard_val="off")
        result = role_guard.decide(_orchestrator_payload(tool))
        assert result == _allow(), f"Expected allow when dial=off for {tool}, got {result}"

    def test_dial_off_allows_bash_write(self, monkeypatch):
        _reload(monkeypatch, role_guard_val="off")
        payload = _orchestrator_payload("Bash", {"command": "echo x > /some/file.txt"})
        result = role_guard.decide(payload)
        assert result == _allow()

    def test_dial_off_case_insensitive(self, monkeypatch):
        """SAGE_ROLE_GUARD=OFF (uppercase) must also disable blocking."""
        _reload(monkeypatch, role_guard_val="OFF")
        result = role_guard.decide(_orchestrator_payload("Write"))
        assert result == _allow()

    def test_dial_on_is_default(self, monkeypatch):
        """Unset SAGE_ROLE_GUARD defaults to guard-on."""
        _reload(monkeypatch, role_guard_val=None)
        result = role_guard.decide(_orchestrator_payload("Write"))
        assert result == _deny()

    def test_dial_unknown_value_defaults_to_on(self, monkeypatch):
        """Any value other than 'off' leaves the guard active."""
        _reload(monkeypatch, role_guard_val="disabled")
        result = role_guard.decide(_orchestrator_payload("Write"))
        assert result == _deny()


# ── Fail-open on malformed / unexpected stdin ─────────────────────────────────


class TestFailOpen:
    """Guard must fail open (allow) on any input error."""

    def test_malformed_json_fails_open(self, monkeypatch, tmp_path, capsys):
        """Non-JSON stdin → allow and log to stderr."""
        _reload(monkeypatch)
        import io

        original_stdin = sys.stdin
        sys.stdin = io.StringIO("not valid json {{{{")
        try:
            rc = role_guard.main()
        finally:
            sys.stdin = original_stdin

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "{}"
        assert "fail-open" in captured.err or "malformed" in captured.err

    def test_non_dict_json_fails_open(self, monkeypatch, tmp_path, capsys):
        """JSON array (not dict) stdin → allow."""
        _reload(monkeypatch)
        import io

        original_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps([1, 2, 3]))
        try:
            rc = role_guard.main()
        finally:
            sys.stdin = original_stdin

        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_empty_stdin_fails_open(self, monkeypatch, capsys):
        """Empty stdin → allow."""
        _reload(monkeypatch)
        import io

        original_stdin = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            rc = role_guard.main()
        finally:
            sys.stdin = original_stdin

        assert rc == 0
        assert capsys.readouterr().out.strip() == "{}"

    def test_decide_called_with_valid_payload(self, monkeypatch, capsys):
        """Valid payload → decide() result emitted as JSON."""
        _reload(monkeypatch)
        import io

        payload = json.dumps({"tool_name": "Write"})
        original_stdin = sys.stdin
        sys.stdin = io.StringIO(payload)
        try:
            rc = role_guard.main()
        finally:
            sys.stdin = original_stdin

        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── Subprocess smoke test (script-as-standalone) ──────────────────────────────


class TestScriptSubprocess:
    """Verify the script runs as a standalone with correct exit code."""

    SCRIPT = HOOKS_DIR / "role_guard.py"

    def _run(self, stdin_json: dict, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        import os

        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(self.SCRIPT)],
            input=json.dumps(stdin_json),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

    def test_orchestrator_write_denied_subprocess(self):
        r = self._run({"tool_name": "Write"})
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_subagent_write_allowed_subprocess(self):
        r = self._run({"tool_name": "Write", "agent_id": "agent-xyz"})
        assert r.returncode == 0
        assert json.loads(r.stdout) == {}

    def test_dial_off_allows_subprocess(self):
        r = self._run({"tool_name": "Write"}, extra_env={"SAGE_ROLE_GUARD": "off"})
        assert r.returncode == 0
        assert json.loads(r.stdout) == {}

    def test_git_bash_allowed_subprocess(self):
        r = self._run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        assert r.returncode == 0
        assert json.loads(r.stdout) == {}

    def test_echo_redirect_denied_subprocess(self):
        r = self._run({"tool_name": "Bash", "tool_input": {"command": "echo x > /some/file.txt"}})
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_malformed_stdin_fails_open_subprocess(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(self.SCRIPT)],
            input="not json at all",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r.returncode == 0
        assert r.stdout.strip() == "{}"
