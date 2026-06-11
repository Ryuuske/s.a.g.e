"""Phase 6 acceptance tests for the Stop / PreCompact session hooks.

Plan acceptance criteria covered here:

  - Hook does NOT auto-mine the transcript directory (the #1083 fix).
    The hook never imports or invokes any miner; structural assertion
    in test_no_miner_import.

  - Hook skips when the orchestrator dispatched the Keeper
    recently. Covered by test_skip_when_keeper_dispatched.

  - Hook files exactly one drawer with ``agents=["session-end-hook"]``
    (or ``pre-compact-hook``) to the wing the orchestrator advertised
    via the current_wing sentinel. Covered by
    test_stop_files_emergency_drawer + the pre-compact mirror.

  - Hook does nothing when no wing has been advertised. Covered by
    test_no_op_without_current_wing.

  - PreCompact NEVER raises out of the hook even when the drawer
    write fails (cancel-on-error must not propagate; a hook exception
    would cancel the compact and lose the session).
    Covered by test_precompact_swallows_failure.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# Make hooks/scripts importable. The plugin entry points add this
# at runtime; tests do the same so the shared body module loads.
HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks" / "scripts"
sys.path.insert(0, str(HOOKS_DIR))


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    """Redirect ~/.sage/ to a tmp dir and clear cached module state."""
    sp_dir = tmp_path / ".sage"
    sp_dir.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-import the shared body so it sees the new HOME.
    import _session_hook  # noqa: F401

    importlib.reload(_session_hook)
    return {"sp_dir": sp_dir, "tmp_path": tmp_path}


def _write_current_wing(hook_env, wing: str) -> None:
    (hook_env["sp_dir"] / "current_wing").write_text(wing, encoding="utf-8")


def _write_keeper_dispatch(hook_env, when=None) -> None:
    stamp = (when or datetime.now(timezone.utc)).isoformat()
    (hook_env["sp_dir"] / "last_keeper_dispatch").write_text(stamp, encoding="utf-8")


# ── Structural: the hook never imports a miner ────────────────────────


def test_no_miner_import():
    """The #1083 fix: the session hook must not pull in any miner.

    A static import check is the most reliable assertion — even a stub
    that doesn't *call* the miner would still pull its (heavy) import
    side effects into the session-end critical path.
    """
    import _session_hook

    src = Path(_session_hook.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "from sage_mcp.miner",
        "from sage_mcp.convo_miner",
        "from sage_mcp.format_miner",
        "import sage_mcp.miner",
    ):
        assert forbidden not in src, (
            f"session hook imports {forbidden!r} — the whole point of the "
            "rewrite was to NOT auto-mine on session end."
        )


# ── No-op paths ───────────────────────────────────────────────────────


def test_no_op_without_current_wing(hook_env, monkeypatch):
    """No current_wing sentinel ⇒ hook never writes anything."""
    import _session_hook

    # current_wing absent — hook should exit 0 without invoking tool_add_drawer.
    monkeypatch.setattr(sys, "stdin", _StubStdin("any payload at all"))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    assert rc == 0
    mock_add.assert_not_called()


def test_skip_when_keeper_dispatched(hook_env, monkeypatch):
    """Recent Keeper dispatch ⇒ no emergency drawer."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    _write_keeper_dispatch(hook_env)  # now

    monkeypatch.setattr(sys, "stdin", _StubStdin("session payload"))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    assert rc == 0
    mock_add.assert_not_called()


def test_no_op_when_payload_empty(hook_env, monkeypatch):
    """Wing set, Keeper skipped — but stdin had nothing to preserve."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    monkeypatch.setattr(sys, "stdin", _StubStdin(""))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    assert rc == 0
    mock_add.assert_not_called()


# ── The emergency-drawer write paths ──────────────────────────────────


def test_stop_files_emergency_drawer(hook_env, monkeypatch):
    """Wing set, no Keeper dispatch, payload present ⇒ exactly one
    tool_add_drawer call with the expected wing + agents tag."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    payload = "User: do the thing\nClaude: doing the thing"
    monkeypatch.setattr(sys, "stdin", _StubStdin(payload))

    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "did_x"}
        rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    assert rc == 0
    mock_add.assert_called_once()
    kwargs = mock_add.call_args.kwargs
    assert kwargs["wing"] == "sage"
    assert kwargs["room"] == "handoff"
    assert kwargs["agents"] == ["session-end-hook"]
    assert payload in kwargs["content"]


def test_stop_drawer_sets_handoff_hall(hook_env, monkeypatch):
    """Plan: 'writes a single drawer to the current wing's handoff hall'.
    Asserts the hall metadata is set to 'handoff' so wake-up retrieval
    that filters by hall surfaces hook-written drawers."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    monkeypatch.setattr(sys, "stdin", _StubStdin("session payload"))

    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "did_h"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    kwargs = mock_add.call_args.kwargs
    assert kwargs.get("hall") == "handoff", f"hook drawer missing hall=handoff: kwargs={kwargs}"


def test_precompact_drawer_also_sets_handoff_hall(hook_env, monkeypatch):
    """PreCompact's room is 'handoff-precompact' but its hall is still
    'handoff' — both hooks file emergency drawers that wake-up sees as
    handoff content."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    monkeypatch.setattr(sys, "stdin", _StubStdin("session payload"))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "did_p"}
        _session_hook.run_session_hook(room="handoff-precompact", agent_tag="pre-compact-hook")
    kwargs = mock_add.call_args.kwargs
    assert kwargs.get("hall") == "handoff", kwargs
    assert kwargs.get("room") == "handoff-precompact", kwargs


def test_emergency_drawer_truncates_to_4000_chars(hook_env, monkeypatch):
    """Long sessions clip to the last EMERGENCY_DRAWER_CHARS chars."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    payload = "x" * 50_000  # 12.5× the 4000-char window
    monkeypatch.setattr(sys, "stdin", _StubStdin(payload))

    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "did_y"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    kwargs = mock_add.call_args.kwargs
    assert len(kwargs["content"]) == _session_hook.EMERGENCY_DRAWER_CHARS


def test_precompact_uses_distinct_room_and_agent_tag(hook_env, monkeypatch):
    """PreCompact files into 'handoff-precompact' with the matching tag."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "did"}
        rc = _session_hook.run_session_hook(room="handoff-precompact", agent_tag="pre-compact-hook")
    assert rc == 0
    kwargs = mock_add.call_args.kwargs
    assert kwargs["room"] == "handoff-precompact"
    assert kwargs["agents"] == ["pre-compact-hook"]


# ── PreCompact #856 — never raise out of the hook ─────────────────────


def test_precompact_swallows_drawer_write_failure(hook_env, monkeypatch):
    """If tool_add_drawer raises, the pre-compact hook must still return
    0 so compaction proceeds. A hook exception cancelling compaction
    would lose the session — never acceptable."""
    import _session_hook

    _write_current_wing(hook_env, "sage")
    monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
    with patch(
        "sage_mcp.mcp_server.tool_add_drawer",
        side_effect=RuntimeError("simulated chroma blowup"),
    ):
        rc = _session_hook.run_session_hook(room="handoff-precompact", agent_tag="pre-compact-hook")
    assert rc == 0


# ── Stale keeper dispatch is ignored ───────────────────────────────


def test_stale_keeper_dispatch_does_not_skip(hook_env, monkeypatch):
    """A Keeper dispatch from yesterday should NOT suppress today's
    emergency drawer."""
    import _session_hook
    from datetime import timedelta

    _write_current_wing(hook_env, "sage")
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    _write_keeper_dispatch(hook_env, stale)
    monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "did"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    mock_add.assert_called_once()


def test_future_keeper_dispatch_does_not_skip(hook_env, monkeypatch):
    """Clock-skew safety: a future timestamp in `last_keeper_dispatch`
    (clock skew, corrupted file, or the operator's machine has the clock
    set wrong) must be treated as not-recent — the emergency drawer
    still fires. Regression lock for the Pass 1 Cat 8 clock-skew fix
    that changed `_keeper_recent` from `delta < N` to
    `0 <= delta < N`."""
    import _session_hook
    from datetime import timedelta

    _write_current_wing(hook_env, "sage")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    _write_keeper_dispatch(hook_env, future)
    monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "did"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
    mock_add.assert_called_once()


# ── Helper ────────────────────────────────────────────────────────────


class _StubStdin:
    """Minimal sys.stdin replacement for the hook test surface."""

    def __init__(self, payload: str):
        self._payload = payload
        self._read = False

    def read(self, *_args, **_kwargs):
        if self._read:
            return ""
        self._read = True
        return self._payload


# ── Pass 3 Cat 17 F2: Claude Code JSON envelope handling ──────────────


def test_envelope_with_transcript_path_reads_messages(hook_env, monkeypatch, tmp_path):
    """When stdin is a Claude Code JSON envelope, the hook must read the
    JSONL transcript and file the last few role-tagged messages — NOT the
    envelope JSON verbatim."""
    import json
    import _session_hook

    _write_current_wing(hook_env, "sage")

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"message": {"role": "user", "content": "decided to ship now"}}),
                json.dumps({"message": {"role": "assistant", "content": "ack"}}),
            ]
        ),
        encoding="utf-8",
    )

    envelope = json.dumps(
        {
            "session_id": "abc",
            "transcript_path": str(transcript),
            "stop_hook_active": False,
        }
    )

    monkeypatch.setattr(sys, "stdin", _StubStdin(envelope))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "d1"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")

    mock_add.assert_called_once()
    content = mock_add.call_args.kwargs["content"]
    assert "decided to ship now" in content
    # The envelope JSON itself must NOT appear (the bug being regressed).
    assert "transcript_path" not in content
    assert '"session_id"' not in content


def test_envelope_with_missing_transcript_path_falls_back_to_raw(hook_env, monkeypatch):
    """If the JSON envelope lacks transcript_path, fall back to the raw stdin
    (the envelope JSON itself) so SOMETHING is preserved."""
    import json
    import _session_hook

    _write_current_wing(hook_env, "sage")

    envelope = json.dumps({"session_id": "x"})
    monkeypatch.setattr(sys, "stdin", _StubStdin(envelope))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "d1"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")

    mock_add.assert_called_once()
    assert "session_id" in mock_add.call_args.kwargs["content"]


def test_non_json_stdin_uses_legacy_raw_path(hook_env, monkeypatch):
    """Stdin not starting with '{' must be treated as raw text (legacy /
    debug invocation)."""
    import _session_hook

    _write_current_wing(hook_env, "sage")

    monkeypatch.setattr(sys, "stdin", _StubStdin("plain transcript text"))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "d1"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")

    mock_add.assert_called_once()
    assert mock_add.call_args.kwargs["content"] == "plain transcript text"


def test_envelope_with_empty_transcript_falls_back_to_envelope(hook_env, monkeypatch, tmp_path):
    """If the transcript file exists but contains no role-tagged messages, the
    hook falls back to filing the raw envelope so SOMETHING is preserved.
    (Pass 5 Cat 25 F7)"""
    import json
    import _session_hook

    _write_current_wing(hook_env, "sage")

    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("", encoding="utf-8")

    envelope = json.dumps(
        {"session_id": "x", "transcript_path": str(transcript), "stop_hook_active": False}
    )
    monkeypatch.setattr(sys, "stdin", _StubStdin(envelope))
    with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
        mock_add.return_value = {"success": True, "drawer_id": "d1"}
        _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")

    mock_add.assert_called_once()
    # Fallback files raw envelope JSON.
    assert "session_id" in mock_add.call_args.kwargs["content"]


# ── P3.4: Opt-in secret-redaction tests ──────────────────────────────────────


class TestSecretRedaction:
    """Tests for the opt-in default-off secret-redaction on drawer writes (P3.4).

    Acceptance criteria:
      - default OFF = content unchanged (no redaction even if credential-shaped)
      - opt-in ON via SAGE_REDACT_SECRETS=1 = credential-shaped content scrubbed
      - opt-in ON via config = credential-shaped content scrubbed
      - config import failure fails open (returns content unchanged)
    """

    def test_default_off_content_unchanged(self, hook_env, monkeypatch):
        """With SAGE_REDACT_SECRETS unset and config default, content passes through unmodified."""
        import _session_hook

        monkeypatch.delenv("SAGE_REDACT_SECRETS", raising=False)

        # Patch SageConfig to return redaction=False (the default)
        class _FakeConfig:
            hooks_redact_secrets = False

        monkeypatch.setattr(
            _session_hook,
            "SageConfig" if hasattr(_session_hook, "SageConfig") else "_FakeConfig",
            _FakeConfig,
            raising=False,
        )

        credential_text = "sk-ant-api03-FAKE_KEY_FOR_TESTING_PURPOSES"
        result = _session_hook._maybe_scrub(credential_text)
        # When off, content passes through unchanged
        assert result == credential_text

    def test_env_opt_in_scrubs_credentials(self, hook_env, monkeypatch):
        """SAGE_REDACT_SECRETS=1 causes credential-shaped content to be scrubbed."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "1")
        importlib.reload(_session_hook)

        credential_text = "sk-ant-api03-FAKE_KEY_FOR_TESTING_PURPOSES_ONLY"
        result = _session_hook._maybe_scrub(credential_text)
        # Credential should be scrubbed
        assert "[REDACTED]" in result
        assert "sk-ant-api03" not in result

    def test_env_opt_in_true_scrubs_credentials(self, hook_env, monkeypatch):
        """SAGE_REDACT_SECRETS=true also enables redaction."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "true")
        importlib.reload(_session_hook)

        credential_text = "gh_token ghp_FAKEGITHUBTOKEN12345678901234567890"
        result = _session_hook._maybe_scrub(credential_text)
        assert "[REDACTED]" in result

    def test_default_off_normal_text_unchanged(self, hook_env, monkeypatch):
        """Non-credential content is never modified even when redaction is on."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "1")
        importlib.reload(_session_hook)

        normal_text = "User decided to refactor the auth module today."
        result = _session_hook._maybe_scrub(normal_text)
        assert result == normal_text

    def test_drawer_content_unchanged_when_redaction_off(self, hook_env, monkeypatch):
        """End-to-end: drawer content contains credential-shaped text unmodified
        when SAGE_REDACT_SECRETS is not set (default off)."""
        import _session_hook

        monkeypatch.delenv("SAGE_REDACT_SECRETS", raising=False)
        importlib.reload(_session_hook)

        _write_current_wing(hook_env, "sage")
        # Include credential-shaped text in the payload
        credential_payload = "User: my key is sk-ant-api03-FAKE_KEY_FOR_TEST"
        monkeypatch.setattr(sys, "stdin", _StubStdin(credential_payload))

        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            mock_add.return_value = {"success": True, "drawer_id": "d_redact_off"}
            _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")

        mock_add.assert_called_once()
        content = mock_add.call_args.kwargs["content"]
        # When off, the credential-shaped string must be present verbatim
        assert "sk-ant-api03" in content

    def test_drawer_content_scrubbed_when_redaction_on(self, hook_env, monkeypatch):
        """End-to-end: drawer content has credentials scrubbed when
        SAGE_REDACT_SECRETS=1."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "1")
        importlib.reload(_session_hook)

        _write_current_wing(hook_env, "sage")
        credential_payload = "User: my key is sk-ant-api03-FAKE_KEY_FOR_TEST_ONLY"
        monkeypatch.setattr(sys, "stdin", _StubStdin(credential_payload))

        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            mock_add.return_value = {"success": True, "drawer_id": "d_redact_on"}
            _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")

        mock_add.assert_called_once()
        content = mock_add.call_args.kwargs["content"]
        # When on, the credential-shaped string must be redacted
        assert "sk-ant-api03" not in content
        assert "[REDACTED]" in content

    def test_scrubber_import_failure_fails_open(self, hook_env, monkeypatch):
        """If the secret_scrub module is unavailable, _maybe_scrub returns
        content unchanged (fail-open — hook never blocks on missing sage)."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "1")
        importlib.reload(_session_hook)

        # Use monkeypatch to override the import inside _maybe_scrub
        import builtins

        original = builtins.__import__

        def patched_import(name, *args, **kwargs):
            if name == "sage_mcp.secret_scrub":
                raise ImportError("sage not installed")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", patched_import)

        credential_text = "sk-ant-api03-FAKE"
        result = _session_hook._maybe_scrub(credential_text)
        # Fail-open: returns unchanged content
        assert result == credential_text

    def test_config_opt_in_enables_redaction(self, hook_env, monkeypatch):
        """env UNSET + SageConfig.hooks_redact_secrets=True → _redaction_enabled True.

        P3 fold (code-reviewer F2): the config opt-in branch of _redaction_enabled
        in _session_hook.py was not covered — only the env branch was tested.
        """
        import _session_hook

        monkeypatch.delenv("SAGE_REDACT_SECRETS", raising=False)
        importlib.reload(_session_hook)

        # Patch SageConfig inside _session_hook to return redaction=True.
        class _FakeConfig:
            hooks_redact_secrets = True

        # _session_hook imports SageConfig lazily inside _redaction_enabled;
        # patch via the sage.config module path that the import resolves to.
        with patch("sage_mcp.config.SageConfig", _FakeConfig):
            # _redaction_enabled reads env first (unset), then config.
            result = _session_hook._redaction_enabled()

        assert result is True, (
            "_redaction_enabled must return True when env is unset but config opt-in is True"
        )

    def test_config_opt_out_disables_redaction(self, hook_env, monkeypatch):
        """env UNSET + SageConfig.hooks_redact_secrets=False → _redaction_enabled False."""
        import _session_hook

        monkeypatch.delenv("SAGE_REDACT_SECRETS", raising=False)
        importlib.reload(_session_hook)

        class _FakeConfig:
            hooks_redact_secrets = False

        with patch("sage_mcp.config.SageConfig", _FakeConfig):
            result = _session_hook._redaction_enabled()

        assert result is False

    # ── New-F1 (Codex PR#24): case-normalize SAGE_REDACT_SECRETS ───────────

    def test_env_uppercase_TRUE_enables_redaction(self, hook_env, monkeypatch):
        """SAGE_REDACT_SECRETS=TRUE (all-caps) must enable redaction.

        Before the fix, .strip() but not .lower() meant TRUE/YES/True were
        NOT matched against ('1','true','yes') → redaction silently stayed OFF.
        Codex PR#24 New-F1, issue #25.
        """
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "TRUE")
        importlib.reload(_session_hook)
        assert _session_hook._redaction_enabled() is True, (
            "SAGE_REDACT_SECRETS=TRUE must enable redaction (case-normalize fix)"
        )

    def test_env_uppercase_YES_enables_redaction(self, hook_env, monkeypatch):
        """SAGE_REDACT_SECRETS=YES (all-caps) must enable redaction."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "YES")
        importlib.reload(_session_hook)
        assert _session_hook._redaction_enabled() is True, (
            "SAGE_REDACT_SECRETS=YES must enable redaction (case-normalize fix)"
        )

    def test_env_titlecase_True_enables_redaction(self, hook_env, monkeypatch):
        """SAGE_REDACT_SECRETS=True (title-case) must enable redaction."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "True")
        importlib.reload(_session_hook)
        assert _session_hook._redaction_enabled() is True, (
            "SAGE_REDACT_SECRETS=True must enable redaction (case-normalize fix)"
        )

    def test_env_zero_stays_off(self, hook_env, monkeypatch):
        """SAGE_REDACT_SECRETS=0 must leave redaction OFF regardless of case fix."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "0")
        importlib.reload(_session_hook)
        assert _session_hook._redaction_enabled() is False

    def test_env_empty_stays_off(self, hook_env, monkeypatch):
        """SAGE_REDACT_SECRETS='' (empty) must leave redaction OFF."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "")
        importlib.reload(_session_hook)
        assert _session_hook._redaction_enabled() is False

    def test_env_uppercase_TRUE_scrubs_credentials(self, hook_env, monkeypatch):
        """End-to-end: SAGE_REDACT_SECRETS=TRUE scrubs credential-shaped content."""
        import _session_hook

        monkeypatch.setenv("SAGE_REDACT_SECRETS", "TRUE")
        importlib.reload(_session_hook)

        credential_text = "sk-ant-api03-FAKE_KEY_FOR_TESTING_PURPOSES_ONLY"
        result = _session_hook._maybe_scrub(credential_text)
        assert "[REDACTED]" in result, (
            "SAGE_REDACT_SECRETS=TRUE must scrub credentials (case-normalize fix)"
        )
        assert "sk-ant-api03" not in result


# ── F1 (Codex PR): SAGE_HOOK_PROFILE=off short-circuits run_session_hook ────


class TestHookProfileKillSwitch:
    """SAGE_HOOK_PROFILE=off must disable the installed Stop/PreCompact path.

    Codex PR finding F1: the profile dial was only wired in hooks_cli.py;
    _session_hook.run_session_hook had no profile check, so SAGE_HOOK_PROFILE=off
    did NOT suppress the emergency-drawer write for the installed hooks.
    """

    def test_profile_off_no_ops_run_session_hook(self, hook_env, monkeypatch):
        """SAGE_HOOK_PROFILE=off → run_session_hook returns 0 and never writes a drawer."""
        import _session_hook

        _write_current_wing(hook_env, "sage")
        monkeypatch.setenv("SAGE_HOOK_PROFILE", "off")
        importlib.reload(_session_hook)

        monkeypatch.setattr(sys, "stdin", _StubStdin("some payload that would normally file"))
        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
        assert rc == 0
        mock_add.assert_not_called()

    def test_profile_off_precompact_no_ops(self, hook_env, monkeypatch):
        """SAGE_HOOK_PROFILE=off suppresses PreCompact drawer too."""
        import _session_hook

        _write_current_wing(hook_env, "sage")
        monkeypatch.setenv("SAGE_HOOK_PROFILE", "off")
        importlib.reload(_session_hook)

        monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            rc = _session_hook.run_session_hook(
                room="handoff-precompact", agent_tag="pre-compact-hook"
            )
        assert rc == 0
        mock_add.assert_not_called()

    def test_profile_minimal_still_files_drawer(self, hook_env, monkeypatch):
        """SAGE_HOOK_PROFILE=minimal proceeds (emergency drawer is the safety floor)."""
        import _session_hook

        _write_current_wing(hook_env, "sage")
        monkeypatch.setenv("SAGE_HOOK_PROFILE", "minimal")
        importlib.reload(_session_hook)

        monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            mock_add.return_value = {"success": True, "drawer_id": "d_min"}
            rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
        assert rc == 0
        mock_add.assert_called_once()

    def test_profile_standard_still_files_drawer(self, hook_env, monkeypatch):
        """SAGE_HOOK_PROFILE=standard (explicit) proceeds normally."""
        import _session_hook

        _write_current_wing(hook_env, "sage")
        monkeypatch.setenv("SAGE_HOOK_PROFILE", "standard")
        importlib.reload(_session_hook)

        monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            mock_add.return_value = {"success": True, "drawer_id": "d_std"}
            rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
        assert rc == 0
        mock_add.assert_called_once()

    def test_profile_unset_proceeds_as_standard(self, hook_env, monkeypatch):
        """Unset SAGE_HOOK_PROFILE defaults to standard — drawer still filed."""
        import _session_hook

        _write_current_wing(hook_env, "sage")
        monkeypatch.delenv("SAGE_HOOK_PROFILE", raising=False)
        importlib.reload(_session_hook)

        monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            mock_add.return_value = {"success": True, "drawer_id": "d_unset"}
            rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
        assert rc == 0
        mock_add.assert_called_once()

    def test_profile_unknown_value_proceeds_as_standard(self, hook_env, monkeypatch):
        """An unrecognised SAGE_HOOK_PROFILE value is treated as 'standard' (fail-open)."""
        import _session_hook

        _write_current_wing(hook_env, "sage")
        monkeypatch.setenv("SAGE_HOOK_PROFILE", "bogus-value")
        importlib.reload(_session_hook)

        monkeypatch.setattr(sys, "stdin", _StubStdin("payload"))
        with patch("sage_mcp.mcp_server.tool_add_drawer") as mock_add:
            mock_add.return_value = {"success": True, "drawer_id": "d_bogus"}
            rc = _session_hook.run_session_hook(room="handoff", agent_tag="session-end-hook")
        assert rc == 0
        mock_add.assert_called_once()


# ── autonomy_reporting_eval SAGE_HOOK_PROFILE=off off-check ──────────────────


class TestAutonomyReportingEvalProfile:
    """SAGE_HOOK_PROFILE=off must also silence autonomy_reporting_eval.

    Part-0 audit finding (sev 50): autonomy_reporting_eval.py is a second
    Stop hook registered in hooks/hooks.json alongside stop.py, but it had
    no SAGE_HOOK_PROFILE=off check — so the advertised kill-switch did not
    silence it. Tests use subprocess (same as test_autonomy_hooks.py) so the
    env-var isolation mirrors real installed-hook execution.
    """

    # Path constants shared across tests in this class.
    EVALUATOR = HOOKS_DIR / "autonomy_reporting_eval.py"

    def _run_eval(self, home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=str(home))
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(self.EVALUATOR)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def _write_valid_marker(self, home: Path) -> Path:
        """Write a valid in-flight autonomy-run.json + run_log; returns run_log path."""
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

    def test_profile_off_no_log_written(self, tmp_path):
        """SAGE_HOOK_PROFILE=off → evaluator returns 0 and writes no log."""
        self._write_valid_marker(tmp_path)
        r = self._run_eval(tmp_path, extra_env={"SAGE_HOOK_PROFILE": "off"})
        assert r.returncode == 0
        log = tmp_path / ".sage" / "autonomy-reporting-eval.log"
        assert not log.exists(), f"log must not exist when profile=off, but found: {log}"

    def test_profile_standard_log_written(self, tmp_path):
        """SAGE_HOOK_PROFILE=standard (explicit) → evaluator proceeds and writes log."""
        self._write_valid_marker(tmp_path)
        r = self._run_eval(tmp_path, extra_env={"SAGE_HOOK_PROFILE": "standard"})
        assert r.returncode == 0
        log = tmp_path / ".sage" / "autonomy-reporting-eval.log"
        assert log.exists(), "log must exist when profile=standard"
        assert "phase=5" in log.read_text(encoding="utf-8")

    def test_profile_unset_log_written(self, tmp_path):
        """Unset SAGE_HOOK_PROFILE defaults to standard → evaluator proceeds."""
        self._write_valid_marker(tmp_path)
        # Explicitly remove the var if it happens to be set in the environment.
        env = {k: v for k, v in os.environ.items() if k != "SAGE_HOOK_PROFILE"}
        env["HOME"] = str(tmp_path)
        r = subprocess.run(
            [sys.executable, str(self.EVALUATOR)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert r.returncode == 0
        log = tmp_path / ".sage" / "autonomy-reporting-eval.log"
        assert log.exists(), "log must exist when SAGE_HOOK_PROFILE is unset (defaults to standard)"

    def test_profile_minimal_log_written(self, tmp_path):
        """SAGE_HOOK_PROFILE=minimal → evaluator proceeds (off is the only silencer)."""
        self._write_valid_marker(tmp_path)
        r = self._run_eval(tmp_path, extra_env={"SAGE_HOOK_PROFILE": "minimal"})
        assert r.returncode == 0
        log = tmp_path / ".sage" / "autonomy-reporting-eval.log"
        assert log.exists(), "log must exist when profile=minimal"
