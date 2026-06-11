"""Tests for statusline/install/patch_settings.py.

Covers:
  - _hook_marker_for(): boundary contract — 11 cases from W.5 round-3
    verification (real install shapes, F1-fp rejections introduced by
    round-3, suffix-substring rejections, idx-0 edge) plus extras.
  - patch(): single-hook, double-hook, idempotency, command-update,
    foreign-hook preservation, quoted-command shapes (the W.5 round-2
    critical fix).
  - load_settings(): missing file, malformed JSON (sys.exit(2)), valid JSON.
  - main(): argparse happy path, atomic-write path (tempfile.mkstemp +
    os.replace), error path (missing required arg), idempotency (no
    spurious second write).

Import via sys.path.insert so the module is loaded without a package context,
mirroring how install.sh invokes it directly.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

_PATCHER_DIR = Path(__file__).resolve().parent.parent / "statusline" / "install"
if str(_PATCHER_DIR) not in sys.path:
    sys.path.insert(0, str(_PATCHER_DIR))

import patch_settings  # noqa: E402  (import after sys.path manipulation)


# ===========================================================================
# _hook_marker_for tests
# ===========================================================================


class TestHookMarkerFor:
    """Covers patch_settings._hook_marker_for() boundary contract."""

    # -----------------------------------------------------------------------
    # Real install shapes — must return the matching marker
    # -----------------------------------------------------------------------

    def test_unquoted_unix_path(self):
        """Bare unquoted path: python3 /path/inject-codex-budget.py → marker."""
        assert (
            patch_settings._hook_marker_for("python3 /path/inject-codex-budget.py")
            == "inject-codex-budget.py"
        )

    def test_single_quoted_unix_path(self):
        """Single-quoted path: python3 '/path/inject-codex-budget.py' → marker."""
        assert (
            patch_settings._hook_marker_for("python3 '/path/inject-codex-budget.py'")
            == "inject-codex-budget.py"
        )

    def test_double_quoted_unix_path(self):
        """Double-quoted path: python3 \"/path/inject-codex-budget.py\" → marker."""
        assert (
            patch_settings._hook_marker_for('python3 "/path/inject-codex-budget.py"')
            == "inject-codex-budget.py"
        )

    def test_double_quoted_windows_path(self):
        """Double-quoted Windows path with backslashes → marker."""
        cmd = 'python3 "C:\\\\path\\\\inject-codex-budget.py"'
        assert patch_settings._hook_marker_for(cmd) == "inject-codex-budget.py"

    def test_trailing_whitespace(self):
        """Trailing whitespace after the marker is allowed (_PATH_BOUNDARY contains space)."""
        assert (
            patch_settings._hook_marker_for("python3 /path/inject-codex-budget.py ")
            == "inject-codex-budget.py"
        )

    def test_wakeup_hook_single_quoted(self):
        """claude-wakeup-sessionstart.py single-quoted path → wakeup marker."""
        assert (
            patch_settings._hook_marker_for("python3 '/path/claude-wakeup-sessionstart.py'")
            == "claude-wakeup-sessionstart.py"
        )

    # -----------------------------------------------------------------------
    # F1-fp rejections introduced by round-3 — must return None
    # -----------------------------------------------------------------------

    def test_marker_as_single_quoted_arg_rejected(self):
        """Marker appearing as a single-quoted arg to a foreign script → None."""
        assert patch_settings._hook_marker_for("/opt/foo.py 'inject-codex-budget.py'") is None

    def test_marker_as_double_quoted_arg_rejected(self):
        """Marker appearing as a double-quoted arg to a foreign script → None."""
        assert patch_settings._hook_marker_for('/opt/foo.py "inject-codex-budget.py"') is None

    # -----------------------------------------------------------------------
    # Other rejections
    # -----------------------------------------------------------------------

    def test_trailing_args_rejected(self):
        """Marker followed by additional path args → None (tail contains path chars)."""
        assert (
            patch_settings._hook_marker_for("/opt/evil.sh --use inject-codex-budget.py-handler")
            is None
        )

    def test_marker_as_suffix_substring_rejected(self):
        """Marker embedded as a suffix of another filename → None."""
        assert patch_settings._hook_marker_for("/opt/inject-codex-budget.py-old.py") is None

    def test_empty_string_returns_none(self):
        """Empty command string → None."""
        assert patch_settings._hook_marker_for("") is None

    # -----------------------------------------------------------------------
    # idx-0 edge: bare filename at start of string
    # -----------------------------------------------------------------------

    def test_bare_filename_at_idx_zero(self):
        """Bare marker at idx==0 (no path separator needed) → marker returned."""
        assert patch_settings._hook_marker_for("inject-codex-budget.py") == "inject-codex-budget.py"


# ===========================================================================
# patch() tests
# ===========================================================================


class TestPatch:
    """Covers patch_settings.patch(): single-hook, double-hook, idempotency,
    command-update, foreign-hook preservation, and quoted-command shapes.
    """

    _SL_CMD = "sage statusline"
    _CODEX_CMD = "python3 '/home/user/.claude/hooks/inject-codex-budget.py'"
    _WAKEUP_CMD = "python3 '/home/user/.claude/hooks/claude-wakeup-sessionstart.py'"

    def test_single_hook_patch_returns_true_and_sets_data(self):
        """patch() with no wakeup cmd → returns True; data has statusLine + one SessionStart entry."""
        data: dict = {}
        changed = patch_settings.patch(data, self._SL_CMD, self._CODEX_CMD)
        assert changed is True
        assert data["statusLine"] == {"type": "command", "command": self._SL_CMD}
        session_start = data["hooks"]["SessionStart"]
        assert isinstance(session_start, list) and len(session_start) == 1
        hook_entry = session_start[0]["hooks"][0]
        assert hook_entry["command"] == self._CODEX_CMD
        assert hook_entry["timeout"] == 10
        assert "claude-wakeup-sessionstart.py" not in json.dumps(data)

    def test_single_hook_idempotent(self):
        """Second patch() call with same args returns False (no change)."""
        data: dict = {}
        patch_settings.patch(data, self._SL_CMD, self._CODEX_CMD)
        changed_again = patch_settings.patch(data, self._SL_CMD, self._CODEX_CMD)
        assert changed_again is False

    def test_double_hook_patch_returns_true_and_sets_data(self):
        """patch() with wakeup cmd → returns True; two SessionStart entries."""
        data: dict = {}
        changed = patch_settings.patch(data, self._SL_CMD, self._CODEX_CMD, self._WAKEUP_CMD)
        assert changed is True
        session_start = data["hooks"]["SessionStart"]
        assert len(session_start) == 2
        commands = [e["hooks"][0]["command"] for e in session_start]
        assert self._CODEX_CMD in commands
        assert self._WAKEUP_CMD in commands
        for e in session_start:
            assert e["hooks"][0]["timeout"] == 10

    def test_double_hook_idempotency(self):
        """Second double-hook patch() with same args returns False."""
        data: dict = {}
        patch_settings.patch(data, self._SL_CMD, self._CODEX_CMD, self._WAKEUP_CMD)
        changed_again = patch_settings.patch(data, self._SL_CMD, self._CODEX_CMD, self._WAKEUP_CMD)
        assert changed_again is False

    def test_updates_existing_hook_command(self):
        """patch() replaces a stale codex-budget command without duplicating the entry."""
        old_cmd = "python3 /old/path/inject-codex-budget.py"
        new_cmd = self._CODEX_CMD
        # Pre-populate with the old command
        data: dict = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": old_cmd,
                                "timeout": 10,
                            }
                        ]
                    }
                ]
            }
        }
        changed = patch_settings.patch(data, self._SL_CMD, new_cmd)
        assert changed is True
        session_start = data["hooks"]["SessionStart"]
        # Exactly one codex-budget entry (no duplication)
        codex_entries = [
            e
            for e in session_start
            if any("inject-codex-budget.py" in h.get("command", "") for h in e.get("hooks", []))
        ]
        assert len(codex_entries) == 1, (
            f"expected exactly one codex-budget entry after update, got {len(codex_entries)}"
        )
        assert codex_entries[0]["hooks"][0]["command"] == new_cmd

    def test_preserves_foreign_hooks(self):
        """patch() leaves pre-existing foreign hook entries untouched."""
        foreign_hook = {
            "hooks": [
                {
                    "type": "command",
                    "command": "/opt/foreign/hook.sh",
                    "timeout": 5,
                }
            ]
        }
        data: dict = {
            "hooks": {
                "SessionStart": [dict(foreign_hook)]  # pre-populate foreign
            }
        }
        patch_settings.patch(data, self._SL_CMD, self._CODEX_CMD)
        session_start = data["hooks"]["SessionStart"]

        foreign_present = any(
            any(h.get("command") == "/opt/foreign/hook.sh" for h in e.get("hooks", []))
            for e in session_start
        )
        assert foreign_present, "foreign hook was removed or modified by patch()"

        codex_present = any(
            any("inject-codex-budget.py" in h.get("command", "") for h in e.get("hooks", []))
            for e in session_start
        )
        assert codex_present, "codex-budget hook was not inserted"

    def test_quoted_command_shapes_idempotent(self):
        """patch() with quoted install.sh-style commands is idempotent (W.5 round-2 fix)."""
        # These are exactly the command shapes install.sh emits.
        home = Path.home()
        quoted_codex = f"python3 '{home}/.claude/hooks/inject-codex-budget.py'"
        quoted_wakeup = f"python3 '{home}/.claude/hooks/claude-wakeup-sessionstart.py'"
        data: dict = {}
        changed_1 = patch_settings.patch(data, self._SL_CMD, quoted_codex, quoted_wakeup)
        assert changed_1 is True
        changed_2 = patch_settings.patch(data, self._SL_CMD, quoted_codex, quoted_wakeup)
        assert changed_2 is False, (
            "second patch() with quoted command shapes was not idempotent — "
            "this is the W.5 round-2 regression"
        )


# ===========================================================================
# load_settings tests
# ===========================================================================


class TestLoadSettings:
    """Covers patch_settings.load_settings(): missing file, malformed JSON, valid JSON."""

    def test_missing_file_returns_empty_dict(self, tmp_path):
        """load_settings() returns {} when the file does not exist."""
        missing = tmp_path / "settings.json"
        result = patch_settings.load_settings(missing)
        assert result == {}

    def test_malformed_json_exits_2(self, tmp_path):
        """load_settings() calls sys.exit(2) on malformed JSON."""
        bad_file = tmp_path / "settings.json"
        bad_file.write_text("{not: valid json}", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            patch_settings.load_settings(bad_file)
        assert exc_info.value.code == 2

    def test_valid_json_returns_parsed_dict(self, tmp_path):
        """load_settings() parses and returns a valid JSON object."""
        good_file = tmp_path / "settings.json"
        payload = {"statusLine": {"type": "command", "command": "sl"}, "hooks": {}}
        good_file.write_text(json.dumps(payload), encoding="utf-8")
        result = patch_settings.load_settings(good_file)
        assert result == payload


# ===========================================================================
# main() tests
# ===========================================================================


class TestPatchSettingsMain:
    """Covers patch_settings.main(): argparse happy path, atomic-write path,
    error path (missing required arg), and idempotency (no spurious second write).

    CLI shape: settings_path statusline_cmd hook_cmd [--hook-wakeup CMD] [--dry-run]
    """

    _SL_CMD = "sage statusline"
    _HOOK_CMD = "python3 '/home/user/.claude/hooks/inject-codex-budget.py'"
    _WAKEUP_CMD = "python3 '/home/user/.claude/hooks/claude-wakeup-sessionstart.py'"

    def test_happy_path_writes_settings(self, tmp_path):
        """main() with valid positional args loads settings, patches, and writes the file."""
        settings_file = tmp_path / "settings.json"
        # Start with empty settings
        settings_file.write_text("{}", encoding="utf-8")

        argv = [str(settings_file), self._SL_CMD, self._HOOK_CMD]
        with mock_patch("sys.argv", ["patch_settings.py"] + argv):
            result = patch_settings.main()

        assert result == 0
        written = json.loads(settings_file.read_text(encoding="utf-8"))
        assert written.get("statusLine") == {"type": "command", "command": self._SL_CMD}
        session_start = written.get("hooks", {}).get("SessionStart", [])
        assert len(session_start) == 1
        assert session_start[0]["hooks"][0]["command"] == self._HOOK_CMD

    def test_atomic_write_path_uses_mkstemp_then_replace(self, tmp_path, monkeypatch):
        """main() writes to a temp file first (mkstemp), then atomically renames (os.replace).

        This is the security-hardened path documented at patch_settings.py:178-182:
        using mkstemp avoids a predictable .tmp path that could be pre-created as a
        symlink by an attacker with write access to ~/.claude/.

        The test pins the symlink-race-safety property directly: it captures the
        exact temp path returned by tempfile.mkstemp and asserts that os.replace
        used THAT path as src — not any hardcoded or predictable .tmp suffix.
        A regression replacing mkstemp with a deterministic path would break the
        src == mkstemp_path assertion even though the rename pattern is identical.
        """
        import tempfile as _tempfile

        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}", encoding="utf-8")

        # Capture the (fd, path) tuple returned by mkstemp
        mkstemp_results = []
        original_mkstemp = _tempfile.mkstemp

        def _spy_mkstemp(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            mkstemp_results.append(result)
            return result

        # Capture (src, dst) pairs passed to os.replace
        replace_calls = []
        original_replace = os.replace

        def _spy_replace(src, dst):
            replace_calls.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(patch_settings.tempfile, "mkstemp", _spy_mkstemp)
        monkeypatch.setattr(patch_settings.os, "replace", _spy_replace)

        argv = [str(settings_file), self._SL_CMD, self._HOOK_CMD]
        with mock_patch("sys.argv", ["patch_settings.py"] + argv):
            result = patch_settings.main()

        assert result == 0
        # mkstemp must have been called exactly once
        assert len(mkstemp_results) == 1, (
            f"expected tempfile.mkstemp called once, got {len(mkstemp_results)}"
        )
        _, mkstemp_path = mkstemp_results[0]

        # The atomic rename must have been called exactly once
        assert len(replace_calls) == 1, f"expected os.replace called once, got {len(replace_calls)}"
        src_path, dst_path = replace_calls[0]
        # src must be the exact path mkstemp returned — pins the race-safe property.
        # Normalize both to str: mkstemp returns str, but the SUT wraps it in Path()
        # before passing to os.replace, so src_path may arrive as a PosixPath.
        assert str(src_path) == str(mkstemp_path), (
            f"os.replace src {src_path!r} does not match mkstemp path {mkstemp_path!r} — "
            "the atomic write must use the mkstemp-allocated random temp file, "
            "not a predictable suffix"
        )
        # Destination must be the settings file
        assert Path(dst_path) == settings_file, (
            f"os.replace destination should be {settings_file}, got {dst_path}"
        )
        # Source (temp file) should not still exist after the rename
        assert not Path(src_path).exists(), "temp file should be gone after os.replace"

    def test_error_path_missing_required_arg_exits_2(self, tmp_path):
        """main() exits with code 2 when required positional args are missing.

        argparse uses exit code 2 for missing/invalid arguments — consistent with
        TestLoadSettings.test_malformed_json_exits_2 in the same file.
        """
        settings_file = tmp_path / "settings.json"
        # Only settings_path provided — statusline_cmd and hook_cmd missing
        argv = [str(settings_file)]
        with mock_patch("sys.argv", ["patch_settings.py"] + argv):
            with pytest.raises(SystemExit) as exc_info:
                patch_settings.main()
        assert exc_info.value.code == 2, (
            f"main() must exit with code 2 (argparse default) on missing required args, "
            f"got {exc_info.value.code!r}"
        )
        # settings file must not have been created or modified
        assert not settings_file.exists() or settings_file.read_text() == ""

    def test_idempotency_second_invocation_is_noop(self, tmp_path, monkeypatch):
        """main() invoked twice on the same settings.json performs no write on the second call."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}", encoding="utf-8")

        replace_calls = []
        original_replace = os.replace

        def _spy_replace(src, dst):
            replace_calls.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(patch_settings.os, "replace", _spy_replace)

        argv = [str(settings_file), self._SL_CMD, self._HOOK_CMD]
        with mock_patch("sys.argv", ["patch_settings.py"] + argv):
            patch_settings.main()
        # First call should have written
        assert len(replace_calls) == 1

        with mock_patch("sys.argv", ["patch_settings.py"] + argv):
            result = patch_settings.main()
        assert result == 0
        # Second call must not trigger another atomic write (settings already patched)
        assert len(replace_calls) == 1, (
            "second main() invocation with same args should be a no-op — "
            "os.replace should not be called again"
        )
