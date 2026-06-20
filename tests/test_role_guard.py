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


# ── Round post-1 audit fixes: new required tests ──────────────────────────────


class TestSubagentKeyVariants:
    """Both agent_id (snake_case) and agentId (camelCase) must trigger allow.

    Covers audit finding #1 (sev 88): the exact field name emitted by Claude
    Code was not verified at authoring time.  Until sandbox proof (work item 7)
    asserts with a REAL captured payload, both key forms are accepted so that a
    camelCase/snake_case mismatch cannot silently block all implementer writes.
    """

    # Realistic Claude Code subagent PreToolUse payload shapes.
    # These mirror what the docs describe but have NOT been verified against a
    # live captured payload — that verification is work item 7 (sandbox proof).
    _SNAKE_PAYLOAD = {
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/agents/foo.md", "content": "# Foo\n"},
        "agent_id": "agent-abc-001",
        "session_id": "session-xyz-999",
        "hook_event_name": "PreToolUse",
    }
    _CAMEL_PAYLOAD = {
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/agents/foo.md", "content": "# Foo\n"},
        "agentId": "agent-abc-001",
        "sessionId": "session-xyz-999",
        "hookEventName": "PreToolUse",
    }

    @pytest.mark.parametrize(
        "payload",
        [_SNAKE_PAYLOAD, _CAMEL_PAYLOAD],
        ids=["snake_case_agent_id", "camelCase_agentId"],
    )
    def test_realistic_subagent_payload_allowed(self, payload, monkeypatch):
        """Realistic subagent payloads (both key variants) must be allowed."""
        _reload(monkeypatch)
        result = role_guard.decide(payload)
        assert result == _allow(), (
            f"Expected allow for subagent payload (key variant), got {result}"
        )

    def test_snake_case_agent_id_allow(self, monkeypatch):
        """agent_id (snake_case) → allow."""
        _reload(monkeypatch)
        result = role_guard.decide({"tool_name": "Write", "agent_id": "sub-001"})
        assert result == _allow()

    def test_camel_case_agentId_allow(self, monkeypatch):
        """agentId (camelCase) → allow."""
        _reload(monkeypatch)
        result = role_guard.decide({"tool_name": "Write", "agentId": "sub-001"})
        assert result == _allow()

    def test_both_keys_absent_is_orchestrator(self, monkeypatch):
        """Neither agent_id nor agentId → orchestrator → deny Write."""
        _reload(monkeypatch)
        result = role_guard.decide({"tool_name": "Write"})
        assert result == _deny()

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_camel_agent_id_allows_all_write_tools(self, tool, monkeypatch):
        """camelCase agentId must allow every write-tool variant."""
        _reload(monkeypatch)
        result = role_guard.decide({"tool_name": tool, "agentId": "sub-xyz"})
        assert result == _allow(), f"Expected allow for camelCase agentId + {tool}"

    def test_camel_agent_id_allows_bash_write(self, monkeypatch):
        """camelCase agentId must also allow Bash write patterns."""
        _reload(monkeypatch)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo x > /repo/file.txt"},
            "agentId": "sub-xyz",
        }
        result = role_guard.decide(payload)
        assert result == _allow()


class TestBashChainedBypassDenied:
    """Chaining operators cannot be used to hide a write after a git/gh command.

    Covers audit finding #2 (sev 85): the old prefix-only carve-out allowed
    ``git --version && echo x > f`` to slip through.
    """

    @pytest.mark.parametrize(
        "command",
        [
            # && chain: git prefix then write segment
            "git --version && echo x > /repo/file.txt",
            "git status && cat <<EOF > /path/config.json",
            # ; chain
            "git fetch; echo y > /repo/out.txt",
            # || chain: write in fallback branch
            "git pull || echo fallback > /repo/fallback.txt",
            # | pipe: write in rhs
            "git log | tee /repo/output.log",
            # gh + chain
            "gh pr view && cp source.py dest.py",
            # multiple segments, write buried in middle
            "git status; echo x > /f; git diff",
        ],
    )
    def test_chained_git_write_denied(self, command, monkeypatch):
        """Chained commands where any segment writes to a file → deny."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _deny(), (
            f"Expected deny for chained bypass attempt {command!r}, got {result}"
        )

    @pytest.mark.parametrize(
        "command",
        [
            # Pure git chain — no write — must allow
            "git fetch && git pull",
            "git add -A; git commit -m 'msg'",
            # gh + git chain — no write
            "gh pr view 42 && git log --oneline -5",
        ],
    )
    def test_pure_git_chain_allowed(self, command, monkeypatch):
        """Chains of only git/gh commands (no writes) must be allowed."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), f"Expected allow for pure git/gh chain {command!r}, got {result}"


class TestTeeAppendDenied:
    """tee -a (append mode) must be caught — covers audit finding #3."""

    @pytest.mark.parametrize(
        "command",
        [
            "tee -a /var/log/myapp.log",
            "tee -a output.txt",
            "some_command | tee -a logfile.log",
        ],
    )
    def test_tee_append_denied(self, command, monkeypatch):
        """tee -a <file> → deny (append is still a write)."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _deny(), f"Expected deny for tee -a command {command!r}, got {result}"


class TestFdRedirectFalsePositives:
    """fd-number redirects and /dev/null must not trigger false-positive denies.

    Covers audit finding #5: ``echo x >&2``, ``pytest 2> err.txt``, and
    ``tee /dev/null`` were incorrectly denied.
    """

    @pytest.mark.parametrize(
        "command",
        [
            # stderr to terminal fd
            "echo x >&2",
            "echo error >&2",
            # stderr redirect to file (fd-number redirect: 2> is N>, excluded)
            "pytest 2> err.txt",
            "make build 2> /dev/stderr",
            # stdout to /dev/null — already allowed in original; kept as regression
            "echo something > /dev/null",
            # combined: stdout to /dev/null, stderr to fd
            "cmd > /dev/null 2>&1",
            # fd swap
            "cmd 2>&1",
        ],
    )
    def test_fd_redirect_allowed(self, command, monkeypatch):
        """fd-number redirects and /dev/null writes must not be denied."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), (
            f"Expected allow for fd-redirect command {command!r}, got {result}"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "tee /dev/null",
            "some_command | tee /dev/null",
        ],
    )
    def test_tee_dev_null_allowed(self, command, monkeypatch):
        """tee /dev/null must not be denied — writing to the bit-bucket is safe."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), (
            f"Expected allow for tee /dev/null command {command!r}, got {result}"
        )


# ── Round post-2: shlex-based structural rewrite tests ───────────────────────


class TestNewlineStatementSplit:
    """Newline-separated statements are each evaluated independently.

    Covers post-2 blocking finding #1 (sev 85): the prior chain-split omitted
    ``\\n``, so ``git status\\necho x > /repo/evil`` was one git-prefixed
    segment that swallowed the trailing write.
    """

    def test_newline_write_after_git_denied(self, monkeypatch):
        """Newline-separated write after a git command → deny."""
        _reload(monkeypatch)
        command = "git status\necho x > /repo/evil"
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _deny(), f"Expected deny for newline-separated write, got {result}"

    def test_newline_pure_git_allowed(self, monkeypatch):
        """Newline-separated git commands with no write → allow."""
        _reload(monkeypatch)
        command = "git fetch\ngit pull\ngit log --oneline -5"
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), (
            f"Expected allow for newline-separated git commands, got {result}"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "git status\necho x > /repo/file",
            "git log\ntee output.log",
            "git fetch\nsed -i 's/a/b/' file.py",
        ],
    )
    def test_newline_write_in_any_line_denied(self, command, monkeypatch):
        """Write in any newline-separated line → deny."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _deny(), f"Expected deny for {command!r}, got {result}"


class TestGitGhInSegmentWriteDenied:
    """A git/gh-prefixed segment that contains a write idiom is DENIED.

    Covers post-2 blocking finding #2 (sev 84): the old implementation
    exempted the entire segment if it started with git/gh, regardless of
    a trailing redirect.  Write-idiom check now runs FIRST.
    """

    @pytest.mark.parametrize(
        "command",
        [
            # git command followed by redirect in same segment
            "git commit -m x > f.txt",
            "git log --oneline > commits.txt",
            "git show HEAD > patch.diff",
            # gh command followed by redirect in same segment
            "gh pr view > out.txt",
            "gh api repos/owner/repo > response.json",
        ],
    )
    def test_git_gh_segment_with_redirect_denied(self, command, monkeypatch):
        """git/gh segment with a trailing redirect → deny."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _deny(), f"Expected deny for git/gh+redirect {command!r}, got {result}"

    @pytest.mark.parametrize(
        "command",
        [
            # Pure git commands with no write — must still be allowed
            "git commit -m 'feat: add role guard'",
            "git log --oneline -10",
            "git show HEAD",
            "gh pr view 42",
            "gh api repos/owner/repo",
        ],
    )
    def test_git_gh_no_redirect_allowed(self, command, monkeypatch):
        """Pure git/gh commands without any redirect → allow."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), f"Expected allow for pure git/gh {command!r}, got {result}"


class TestQuotedOperatorsAllowed:
    """Quoted ``>`` characters must not trigger false-positive denies.

    Covers post-2 non-blocking finding #3: the prior regex-on-raw-string
    approach was quote-blind and incorrectly denied commands like
    ``grep '>' file`` where ``>`` is a shell argument, not a redirect.

    Uses shlex (posix=False) so quoted ``'>'`` retains quote wrappers
    and is distinguishable from a bare ``>`` redirect token.
    """

    @pytest.mark.parametrize(
        "command",
        [
            # '>' as a grep pattern argument — NOT a redirect
            "grep '>' file",
            "grep -n '>' /repo/agents/foo.md",
            # '>' embedded in a larger quoted string — NOT a redirect
            "echo 'a > b'",
            "echo 'input > output'",
            # '>' inside awk single-quoted program — NOT a redirect
            "awk '$1 > 5' f",
            "awk '{if ($2 > 0) print}' data.csv",
            # bash test expression — [ ... > ... ] treated as comparison idiom
            "[ 5 > 3 ]",
        ],
    )
    def test_quoted_operator_not_denied(self, command, monkeypatch):
        """Quoted ``>`` in a shell argument must not be denied as a redirect."""
        _reload(monkeypatch)
        payload = _orchestrator_payload("Bash", {"command": command})
        result = role_guard.decide(payload)
        assert result == _allow(), (
            f"Expected allow for quoted-operator command {command!r}, got {result}"
        )
