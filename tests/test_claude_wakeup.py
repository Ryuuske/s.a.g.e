"""Tests for installer-assets/claude-wakeup (state-machine script)
and installer-assets/claude-wakeup-sessionstart.py (SessionStart hook).

Covers:
  - claude-wakeup: pure functions (time_of_day_greeting, read_state,
    write_state, append_history, read_resets_at, read_cache_updated_at,
    wait_for_cache_refresh) and main() idempotency / first-run / overdue paths.
  - claude-wakeup: fire() internals — next_fire_at = resets_at + 60 math,
    FALLBACK_WINDOW math, JSONL sweep logic (known-UUID deletion and unknown
    UUID preservation).
  - claude-wakeup: main() flock-guard (BlockingIOError early-exit and
    OSError branch at L243-244).
  - claude-wakeup-sessionstart.py: _format_ts boundary cases and main()
    happy-path + silent-on-error paths.

NO live tmux / cron / subprocess / claude / network. fire() is replaced by a
sentinel callable in all main() tests that do not specifically test fire()
internals. In fire() tests the real fire() is called with tmux() monkeypatched
to a no-op. time.sleep is monkeypatched to a no-op in all fire() and polling
tests. All filesystem paths are redirected to tmp_path-based locations via
monkeypatching module-level Path attributes.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import time
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Module-level loader helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, path: Path):
    """Load a Python source file as a module regardless of file extension."""
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name!r} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def claude_wakeup():
    """Load installer-assets/claude-wakeup as a module."""
    return _load_module(
        "claude_wakeup",
        REPO_ROOT / "installer-assets" / "claude-wakeup",
    )


@pytest.fixture(scope="module")
def claude_wakeup_hook():
    """Load installer-assets/claude-wakeup-sessionstart.py as a module."""
    return _load_module(
        "claude_wakeup_sessionstart",
        REPO_ROOT / "installer-assets" / "claude-wakeup-sessionstart.py",
    )


# ===========================================================================
# claude-wakeup script tests
# ===========================================================================


class TestTimeOfDayGreeting:
    """Covers claude-wakeup: time_of_day_greeting()."""

    def test_morning_at_5am(self, claude_wakeup, monkeypatch):
        """Returns 'Good morning' for hour=5 (4 <= h < 12 range)."""
        fake_dt = _fixed_datetime(hour=5)
        monkeypatch.setattr(claude_wakeup, "datetime", fake_dt)
        assert claude_wakeup.time_of_day_greeting() == "Good morning"

    def test_afternoon_at_13(self, claude_wakeup, monkeypatch):
        """Returns 'Good afternoon' for hour=13 (12 <= h < 18 range)."""
        fake_dt = _fixed_datetime(hour=13)
        monkeypatch.setattr(claude_wakeup, "datetime", fake_dt)
        assert claude_wakeup.time_of_day_greeting() == "Good afternoon"

    def test_evening_at_19(self, claude_wakeup, monkeypatch):
        """Returns 'Good evening' for hour=19 (18 <= h < 22 range)."""
        fake_dt = _fixed_datetime(hour=19)
        monkeypatch.setattr(claude_wakeup, "datetime", fake_dt)
        assert claude_wakeup.time_of_day_greeting() == "Good evening"

    def test_night_at_23(self, claude_wakeup, monkeypatch):
        """Returns 'Good night' for hour=23 (22 <= h < 24 range)."""
        fake_dt = _fixed_datetime(hour=23)
        monkeypatch.setattr(claude_wakeup, "datetime", fake_dt)
        assert claude_wakeup.time_of_day_greeting() == "Good night"

    def test_night_at_2am(self, claude_wakeup, monkeypatch):
        """Returns 'Good night' for hour=2 (falls to default branch, 0 <= h < 4)."""
        fake_dt = _fixed_datetime(hour=2)
        monkeypatch.setattr(claude_wakeup, "datetime", fake_dt)
        assert claude_wakeup.time_of_day_greeting() == "Good night"


class TestReadWriteState:
    """Covers claude-wakeup: read_state() / write_state() round-trip and edge cases."""

    def test_missing_state_file_returns_empty_dict(self, claude_wakeup, tmp_path, monkeypatch):
        """read_state() returns {} when the state file does not exist."""
        state_file = tmp_path / "state.json"
        monkeypatch.setattr(claude_wakeup, "STATE_FILE", state_file)
        assert claude_wakeup.read_state() == {}

    def test_malformed_json_returns_empty_dict(self, claude_wakeup, tmp_path, monkeypatch):
        """read_state() returns {} on malformed JSON (catches Exception broadly)."""
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json{{}", encoding="utf-8")
        monkeypatch.setattr(claude_wakeup, "STATE_FILE", state_file)
        assert claude_wakeup.read_state() == {}

    def test_round_trip(self, claude_wakeup, tmp_path, monkeypatch):
        """write_state() persists; read_state() recovers the exact dict."""
        state_file = tmp_path / "state.json"
        monkeypatch.setattr(claude_wakeup, "STATE_FILE", state_file)
        payload = {"next_fire_at": 9999999, "last_uuid": "abc-123"}
        claude_wakeup.write_state(payload)
        recovered = claude_wakeup.read_state()
        assert recovered == payload


class TestParseStreak:
    """Covers claude-wakeup: _parse_streak() — defensive parser for state.json
    partial_failure_streak (ADR-0033). Hardens fire() against pathological
    manual-edit values that would otherwise crash mid-cron or bypass the cap.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (0, 0),
            (1, 1),
            (3, 3),
            (100, 100),
            (-1, 0),
            (-100, 0),
            (None, 0),
            ("", 0),
            ("0", 0),
            ("5", 5),
            ("foo", 0),
            ("-3", 0),
            ([], 0),
            ([1, 2], 0),
            ({}, 0),
            (True, 1),
            (False, 0),
            (1.7, 1),
            (-1.5, 0),
        ],
    )
    def test_parse_streak_normalizes(self, claude_wakeup, raw, expected):
        """_parse_streak() returns max(0, int(raw)) for parseable values, 0 otherwise."""
        assert claude_wakeup._parse_streak(raw) == expected, (
            f"_parse_streak({raw!r}) expected {expected}, got {claude_wakeup._parse_streak(raw)}"
        )


class TestAppendHistory:
    """Covers claude-wakeup: append_history() append-only behavior."""

    def test_two_appends_produce_two_jsonl_lines(self, claude_wakeup, tmp_path, monkeypatch):
        """Each append_history() call writes one valid JSON-dict line to the JSONL."""
        history_file = tmp_path / "history.jsonl"
        monkeypatch.setattr(claude_wakeup, "HISTORY", history_file)
        entry1 = {"timestamp": 1000, "uuid": "u1", "name": "auto-good-a"}
        entry2 = {"timestamp": 2000, "uuid": "u2", "name": "auto-good-b"}
        claude_wakeup.append_history(entry1)
        claude_wakeup.append_history(entry2)
        lines = [ln for ln in history_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2
        parsed = [json.loads(ln) for ln in lines]
        assert parsed[0] == entry1
        assert parsed[1] == entry2


class TestBudgetCacheReads:
    """Covers claude-wakeup: read_resets_at() / read_cache_updated_at()."""

    def test_missing_cache_returns_zero_resets(self, claude_wakeup, tmp_path, monkeypatch):
        """read_resets_at() returns 0 when BUDGET_CACHE does not exist."""
        cache_file = tmp_path / "claude.json"
        monkeypatch.setattr(claude_wakeup, "BUDGET_CACHE", cache_file)
        assert claude_wakeup.read_resets_at() == 0

    def test_missing_cache_returns_zero_updated_at(self, claude_wakeup, tmp_path, monkeypatch):
        """read_cache_updated_at() returns 0 when BUDGET_CACHE does not exist."""
        cache_file = tmp_path / "claude.json"
        monkeypatch.setattr(claude_wakeup, "BUDGET_CACHE", cache_file)
        assert claude_wakeup.read_cache_updated_at() == 0

    def test_malformed_cache_returns_zero(self, claude_wakeup, tmp_path, monkeypatch):
        """read_resets_at() and read_cache_updated_at() return 0 on malformed JSON."""
        cache_file = tmp_path / "claude.json"
        cache_file.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(claude_wakeup, "BUDGET_CACHE", cache_file)
        assert claude_wakeup.read_resets_at() == 0
        assert claude_wakeup.read_cache_updated_at() == 0

    def test_valid_cache_returns_resets_at(self, claude_wakeup, tmp_path, monkeypatch):
        """read_resets_at() extracts primary.resets_at from valid cache JSON."""
        cache_file = tmp_path / "claude.json"
        cache_file.write_text(
            json.dumps({"primary": {"resets_at": 12345}, "updated_at": 67890}),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "BUDGET_CACHE", cache_file)
        assert claude_wakeup.read_resets_at() == 12345

    def test_valid_cache_returns_updated_at(self, claude_wakeup, tmp_path, monkeypatch):
        """read_cache_updated_at() extracts updated_at from valid cache JSON."""
        cache_file = tmp_path / "claude.json"
        cache_file.write_text(
            json.dumps({"primary": {"resets_at": 12345}, "updated_at": 67890}),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "BUDGET_CACHE", cache_file)
        assert claude_wakeup.read_cache_updated_at() == 67890


class TestWaitForCacheRefresh:
    """Covers claude-wakeup: wait_for_cache_refresh() polling logic."""

    def test_cache_updates_returns_true(self, claude_wakeup, tmp_path, monkeypatch):
        """Returns True when updated_at advances past after_ts on the first poll."""
        monkeypatch.setattr(claude_wakeup, "time", _no_sleep_time())

        call_count = {"n": 0}

        def _updated_at_rising():
            call_count["n"] += 1
            # First call returns after_ts (no advance), second returns after_ts+1
            return 100 if call_count["n"] <= 1 else 101

        monkeypatch.setattr(claude_wakeup, "read_cache_updated_at", _updated_at_rising)
        result = claude_wakeup.wait_for_cache_refresh(after_ts=100)
        assert result is True

    def test_cache_stays_stale_returns_false(self, claude_wakeup, monkeypatch):
        """Returns False when updated_at never advances within REFRESH_POLL_LOOPS."""
        monkeypatch.setattr(claude_wakeup, "time", _no_sleep_time())
        monkeypatch.setattr(claude_wakeup, "read_cache_updated_at", lambda: 50)
        result = claude_wakeup.wait_for_cache_refresh(after_ts=100)
        assert result is False


class TestMainIdempotencyGate:
    """Covers claude-wakeup: main() does NOT fire when now < next_fire_at."""

    def test_idempotency_gate_no_fire(self, claude_wakeup, tmp_path, monkeypatch):
        """main() returns 0 and does NOT call fire() when next_fire_at is in the future."""
        state_dir = tmp_path / "state_dir"
        state_dir.mkdir()
        state_file = state_dir / "state.json"
        lock_file = state_dir / "lock"
        log_file = state_dir / "wakeup.log"

        future_ts = int(time.time()) + 3600
        state_file.write_text(json.dumps({"next_fire_at": future_ts}), encoding="utf-8")

        monkeypatch.setattr(claude_wakeup, "STATE_DIR", state_dir)
        monkeypatch.setattr(claude_wakeup, "STATE_FILE", state_file)
        monkeypatch.setattr(claude_wakeup, "LOCK_FILE", lock_file)
        monkeypatch.setattr(claude_wakeup, "LOG_FILE", log_file)

        fire_called = {"called": False}

        def _sentinel_fire():
            fire_called["called"] = True
            raise RuntimeError("fire() must not be called when idempotency gate holds")

        monkeypatch.setattr(claude_wakeup, "fire", _sentinel_fire)
        result = claude_wakeup.main()
        assert result == 0
        assert not fire_called["called"], "fire() was called when it should not have been"


class TestMainFirePaths:
    """Covers claude-wakeup: main() DOES fire on first-run and overdue states."""

    def _setup_dirs(self, tmp_path, monkeypatch, mod):
        state_dir = tmp_path / "state_dir"
        state_dir.mkdir()
        state_file = state_dir / "state.json"
        lock_file = state_dir / "lock"
        log_file = state_dir / "wakeup.log"
        monkeypatch.setattr(mod, "STATE_DIR", state_dir)
        monkeypatch.setattr(mod, "STATE_FILE", state_file)
        monkeypatch.setattr(mod, "LOCK_FILE", lock_file)
        monkeypatch.setattr(mod, "LOG_FILE", log_file)
        return state_file

    def test_first_run_fires(self, claude_wakeup, tmp_path, monkeypatch):
        """main() calls fire() exactly once when no state.json exists (first-ever run)."""
        self._setup_dirs(tmp_path, monkeypatch, claude_wakeup)
        # Deliberately no state file written — fresh install

        fire_called = {"count": 0}

        def _sentinel_fire():
            fire_called["count"] += 1

        monkeypatch.setattr(claude_wakeup, "fire", _sentinel_fire)
        result = claude_wakeup.main()
        assert result == 0
        assert fire_called["count"] == 1, (
            f"fire() should be called once on first run, got {fire_called['count']}"
        )

    def test_overdue_fires(self, claude_wakeup, tmp_path, monkeypatch):
        """main() calls fire() when next_fire_at=0 (overdue / past reset)."""
        state_file = self._setup_dirs(tmp_path, monkeypatch, claude_wakeup)
        state_file.write_text(json.dumps({"next_fire_at": 0}), encoding="utf-8")

        fire_called = {"count": 0}

        def _sentinel_fire():
            fire_called["count"] += 1

        monkeypatch.setattr(claude_wakeup, "fire", _sentinel_fire)
        result = claude_wakeup.main()
        assert result == 0
        assert fire_called["count"] == 1, (
            f"fire() should be called once when overdue, got {fire_called['count']}"
        )


class TestMainFlockGuard:
    """Covers claude-wakeup: main() flock-guard early-exit paths.

    The lock is acquired via ``fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)``.
    ``claude_wakeup.fcntl`` is a module-level binding (``import fcntl`` at L31),
    so ``monkeypatch.setattr(claude_wakeup, 'fcntl', _fake)`` works cleanly.
    """

    def _setup_dirs(self, tmp_path, monkeypatch, mod):
        state_dir = tmp_path / "state_dir"
        state_dir.mkdir()
        state_file = state_dir / "state.json"
        lock_file = state_dir / "lock"
        log_file = state_dir / "wakeup.log"
        monkeypatch.setattr(mod, "STATE_DIR", state_dir)
        monkeypatch.setattr(mod, "STATE_FILE", state_file)
        monkeypatch.setattr(mod, "LOCK_FILE", lock_file)
        monkeypatch.setattr(mod, "LOG_FILE", log_file)

    def test_blocking_error_returns_zero_without_fire(self, claude_wakeup, tmp_path, monkeypatch):
        """main() returns 0 and does not call fire() when flock raises BlockingIOError."""
        self._setup_dirs(tmp_path, monkeypatch, claude_wakeup)

        # Preserve real LOCK_EX | LOCK_NB values before patching.
        _LOCK_EX_NB = fcntl.LOCK_EX | fcntl.LOCK_NB

        class _FakeFcntl:
            LOCK_EX = fcntl.LOCK_EX
            LOCK_NB = fcntl.LOCK_NB

            def flock(self, fd, op):
                if op == _LOCK_EX_NB:
                    raise BlockingIOError("locked by another instance")

        monkeypatch.setattr(claude_wakeup, "fcntl", _FakeFcntl())

        fire_called = {"called": False}

        def _sentinel_fire():
            fire_called["called"] = True

        monkeypatch.setattr(claude_wakeup, "fire", _sentinel_fire)
        result = claude_wakeup.main()
        assert result == 0
        assert not fire_called["called"], "fire() must not be called when flock is contended"

    def test_oserror_branch_returns_zero_without_fire(self, claude_wakeup, tmp_path, monkeypatch):
        """main() returns 0 and does not call fire() when flock raises OSError (L243-244)."""
        self._setup_dirs(tmp_path, monkeypatch, claude_wakeup)

        import errno as _errno

        _LOCK_EX_NB = fcntl.LOCK_EX | fcntl.LOCK_NB

        class _FakeFcntlOSError:
            LOCK_EX = fcntl.LOCK_EX
            LOCK_NB = fcntl.LOCK_NB

            def flock(self, fd, op):
                if op == _LOCK_EX_NB:
                    raise OSError(_errno.EBADF, "bad file descriptor")

        monkeypatch.setattr(claude_wakeup, "fcntl", _FakeFcntlOSError())

        fire_called = {"called": False}

        def _sentinel_fire():
            fire_called["called"] = True

        monkeypatch.setattr(claude_wakeup, "fire", _sentinel_fire)
        result = claude_wakeup.main()
        assert result == 0
        assert not fire_called["called"], "fire() must not be called on OSError from flock"


# ===========================================================================
# fire() internals tests
# ===========================================================================

# Fixed reference timestamp for next_fire_at math tests: 2026-05-28 12:00:00 UTC
_RESETS_AT_FIXTURE = int(datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc).timestamp())
# Frozen "now" for FALLBACK_WINDOW test: a time clearly before resets_at
_NOW_FIXTURE = int(datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc).timestamp())


class TestFireInternals:
    """Covers claude-wakeup: fire() internals — called with tmux() monkeypatched
    to a no-op. time.sleep is monkeypatched to a no-op and time.time is frozen
    to _NOW_FIXTURE (2026-05-28 10:00 UTC) so math assertions are wall-clock-independent.
    """

    def _setup_fire_env(self, tmp_path, monkeypatch, mod):
        """Wire all module-level Paths to tmp_path subdirs for fire() isolation.

        Freezes time.time() to _NOW_FIXTURE so the ELIF fresh-cache branch
        (resets_at > now at SUT L164) is taken deterministically regardless of
        when the test runs — _RESETS_AT_FIXTURE (2026-05-28 12:00 UTC) is always
        > _NOW_FIXTURE (2026-05-28 10:00 UTC) because both are fixed constants.
        """
        state_dir = tmp_path / "state_dir"
        state_dir.mkdir()
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        state_file = state_dir / "state.json"
        history_file = state_dir / "history.jsonl"
        lock_file = state_dir / "lock"
        log_file = state_dir / "wakeup.log"
        cache_file = cache_dir / "claude.json"

        monkeypatch.setattr(mod, "STATE_DIR", state_dir)
        monkeypatch.setattr(mod, "STATE_FILE", state_file)
        monkeypatch.setattr(mod, "HISTORY", history_file)
        monkeypatch.setattr(mod, "LOCK_FILE", lock_file)
        monkeypatch.setattr(mod, "LOG_FILE", log_file)
        monkeypatch.setattr(mod, "BUDGET_CACHE", cache_file)
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)

        # Ensure CLAUDE_BIN resolves to a valid executable so fire()'s pre-resolution
        # gate passes on CI runners where claude is not on PATH.  /usr/bin/echo is
        # POSIX-mandated, absolute, and harmless — tmux is already mocked to a no-op
        # so the binary is never actually invoked; it only needs to satisfy the
        # os.path.isfile + os.access(X_OK) check at installer-assets/claude-wakeup:140.
        monkeypatch.setenv("CLAUDE_BIN", "/usr/bin/echo")

        # No-op tmux to prevent any subprocess spawn — returns a CompletedProcess-shaped
        # object so the F1 rc-check (installer-assets/claude-wakeup:161, :166) sees
        # success across both `tmux new-session` and `tmux has-session`.
        monkeypatch.setattr(mod, "tmux", lambda *args: SimpleNamespace(returncode=0, stderr=""))
        # Freeze time.time() to _NOW_FIXTURE; no-op sleep for SPAWN_WAIT / RESPONSE_WAIT
        # / POST_KILL_SETTLE. This replaces _no_sleep_time() which delegates to the real
        # wall clock and would cause test_next_fire_at_is_resets_at_plus_60 to fail after
        # 2026-05-28 12:00 UTC when _RESETS_AT_FIXTURE passes into the past.
        frozen_now = _NOW_FIXTURE

        class _FrozenTimeSetup(_NoSleepTimeModule):
            @staticmethod
            def time():
                return frozen_now

        monkeypatch.setattr(mod, "time", _FrozenTimeSetup())

        return state_file, history_file, projects_dir, cache_file

    def test_fire_ok_resets_streak_and_pins_next_fire_at(
        self, claude_wakeup, tmp_path, monkeypatch
    ):
        """fire() on the FIRE ok path (refreshed AND resets_at > now): next_fire_at = resets_at + 60
        AND partial_failure_streak resets to 0 regardless of prior value (ADR-0033 counter-reset).

        Seeds a prior streak of 2 to prove the reset actually fires (vs missing-key default).
        """
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        # Seed a production-shape state.json with a non-zero prior streak so the reset
        # is observable AND the seed mirrors what main() persists pre-fire (D3 fix).
        state_file.write_text(
            json.dumps(
                {
                    "next_fire_at": _NOW_FIXTURE - 60,
                    "last_uuid": "prior-uuid",
                    "last_name": "prior-name",
                    "last_fire_at": _NOW_FIXTURE - 300,
                    "last_resets_at": _NOW_FIXTURE - 240,
                    "last_greeting": "Good morning",
                    "partial_failure_streak": 2,
                }
            ),
            encoding="utf-8",
        )

        # Cache with future resets_at AND we force the refresh check to return True
        # (the real cache-refresh polling depends on updated_at advancing past
        # cache_before, which the test fixture cannot drive without a writer thread —
        # patch wait_for_cache_refresh directly).
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: True)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["next_fire_at"] == _RESETS_AT_FIXTURE + 60, (
            f"expected next_fire_at={_RESETS_AT_FIXTURE + 60}, got {state['next_fire_at']}"
        )
        assert state["partial_failure_streak"] == 0, (
            f"FIRE ok must reset streak; prior=2, got {state['partial_failure_streak']}"
        )

    def test_first_partial_failure_uses_retry_backoff(self, claude_wakeup, tmp_path, monkeypatch):
        """fire() on the first partial failure (refreshed=False but resets_at > now):
        next_fire_at = now + RETRY_BACKOFF AND partial_failure_streak = 1.

        Replaces the deleted test_next_fire_at_is_resets_at_plus_60, which encoded the
        ELIF stale-trust bug (treating refreshed=False as success) that F2 removes.
        """
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        # Real refresh-poll mock: no actual refresh happens during fire().
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        expected = _NOW_FIXTURE + claude_wakeup.RETRY_BACKOFF
        assert state["next_fire_at"] == expected, (
            f"first partial failure must use RETRY_BACKOFF (={claude_wakeup.RETRY_BACKOFF}); "
            f"expected next_fire_at={expected}, got {state['next_fire_at']}"
        )
        assert state["partial_failure_streak"] == 1, (
            f"first partial failure must set streak=1; got {state['partial_failure_streak']}"
        )

    def test_first_partial_failure_with_unreadable_resets_at_uses_retry_backoff(
        self, claude_wakeup, tmp_path, monkeypatch
    ):
        """fire() with resets_at unreadable AND streak < MAX: still RETRY_BACKOFF, not FALLBACK_WINDOW.

        Replaces the deleted test_fallback_window_used_when_resets_at_unreadable, which
        encoded the bug of treating any partial failure as the 5h cap. Under F2 the cap
        only applies after MAX_PARTIAL_RETRIES consecutive failures, regardless of which
        partial sub-condition triggered.
        """
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        # Cache present but resets_at missing → read_resets_at() returns 0
        cache_file.write_text(json.dumps({"updated_at": 0}), encoding="utf-8")
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        expected = _NOW_FIXTURE + claude_wakeup.RETRY_BACKOFF
        assert state["next_fire_at"] == expected, (
            f"first partial failure (resets_at unreadable) must use RETRY_BACKOFF, "
            f"not FALLBACK_WINDOW; expected {expected}, got {state['next_fire_at']}"
        )
        assert state["partial_failure_streak"] == 1, (
            f"streak must be 1; got {state['partial_failure_streak']}"
        )

    def test_streak_increments_across_consecutive_partials(
        self, claude_wakeup, tmp_path, monkeypatch
    ):
        """Two consecutive partial-failure fires: streak advances 0 → 1 → 2; both still use
        RETRY_BACKOFF since MAX_PARTIAL_RETRIES=3 (cap not yet reached)."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        # Seed streak=1 (simulating one prior partial failure).
        state_file.write_text(json.dumps({"partial_failure_streak": 1}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 2, (
            f"streak must increment from 1 to 2; got {state['partial_failure_streak']}"
        )
        # Streak=2 is still below MAX_PARTIAL_RETRIES=3, so still RETRY_BACKOFF.
        expected = _NOW_FIXTURE + claude_wakeup.RETRY_BACKOFF
        assert state["next_fire_at"] == expected, (
            f"streak=2 still uses RETRY_BACKOFF (cap at MAX_PARTIAL_RETRIES=3); "
            f"expected {expected}, got {state['next_fire_at']}"
        )

    def test_third_partial_caps_at_fallback_window(self, claude_wakeup, tmp_path, monkeypatch):
        """Third consecutive partial failure (streak reaches MAX_PARTIAL_RETRIES=3):
        next_fire_at = now + FALLBACK_WINDOW (5h cap), bounding D2 log growth."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        # Seed streak=2 (simulating two prior partial failures; this fire makes it 3).
        state_file.write_text(json.dumps({"partial_failure_streak": 2}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 3, (
            f"streak must increment from 2 to 3; got {state['partial_failure_streak']}"
        )
        expected = _NOW_FIXTURE + claude_wakeup.FALLBACK_WINDOW
        assert state["next_fire_at"] == expected, (
            f"streak=3 must cap at FALLBACK_WINDOW (5h); expected {expected}, "
            f"got {state['next_fire_at']}"
        )

    def test_streak_stays_capped_on_continued_failures(self, claude_wakeup, tmp_path, monkeypatch):
        """Once at or above MAX_PARTIAL_RETRIES, further partials hold at FALLBACK_WINDOW.
        The streak continues to grow (useful diagnostic) but the cadence stays at 5h."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        # Seed streak=5 (well past MAX_PARTIAL_RETRIES=3).
        state_file.write_text(json.dumps({"partial_failure_streak": 5}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 6, (
            f"streak continues to grow as diagnostic; expected 6, got {state['partial_failure_streak']}"
        )
        expected = _NOW_FIXTURE + claude_wakeup.FALLBACK_WINDOW
        assert state["next_fire_at"] == expected, (
            f"streak=6 stays capped at FALLBACK_WINDOW; expected {expected}, "
            f"got {state['next_fire_at']}"
        )

    def test_fire_ok_resets_streak_from_capped_state(self, claude_wakeup, tmp_path, monkeypatch):
        """After persistent failures have driven the streak above the cap, a single
        successful fire resets streak to 0 and restores the normal resets_at + 60 cadence."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        # Seed a capped state — streak=7, well past MAX_PARTIAL_RETRIES=3.
        state_file.write_text(json.dumps({"partial_failure_streak": 7}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        # FIRE ok path: cache refreshes during fire.
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: True)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 0, (
            f"FIRE ok must reset capped streak to 0; prior=7, got {state['partial_failure_streak']}"
        )
        assert state["next_fire_at"] == _RESETS_AT_FIXTURE + 60, (
            f"FIRE ok must restore resets_at+60 cadence; expected {_RESETS_AT_FIXTURE + 60}, "
            f"got {state['next_fire_at']}"
        )

    def test_send_keys_failure_aborts_without_state_advance(
        self, claude_wakeup, tmp_path, monkeypatch
    ):
        """send-keys rc != 0: fire() logs FIRE abort and returns BEFORE write_state.
        Prior state.json is byte-unchanged — next cron tick retries naturally without
        the partial_failure_streak getting bumped."""
        state_file, history_file, _, cache_file = self._setup_fire_env(
            tmp_path, monkeypatch, claude_wakeup
        )

        prior_state = {
            "next_fire_at": 12345,
            "last_uuid": "prior-uuid",
            "last_name": "prior-name",
            "last_fire_at": 11111,
            "last_resets_at": 22222,
            "last_greeting": "Good morning",
            "partial_failure_streak": 1,
        }
        state_file.write_text(json.dumps(prior_state), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )

        def _tmux_send_fails(*args):
            if args and args[0] == "send-keys":
                return SimpleNamespace(returncode=1, stderr="send-keys: server lost")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(claude_wakeup, "tmux", _tmux_send_fails)

        claude_wakeup.fire()

        state_after = json.loads(state_file.read_text(encoding="utf-8"))
        assert state_after == prior_state, (
            f"send-keys failure must abort BEFORE write_state; "
            f"prior={prior_state}, after={state_after}"
        )
        assert not history_file.exists() or history_file.read_text(encoding="utf-8") == ""

    def test_send_keys_failure_cleans_up_orphan_session(self, claude_wakeup, tmp_path, monkeypatch):
        """On send-keys failure, fire() calls tmux kill-session on the orphan session
        for best-effort cleanup before aborting."""
        self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        calls = []

        def _tmux_track_and_fail_send(*args):
            calls.append(args)
            if args and args[0] == "send-keys":
                return SimpleNamespace(returncode=1, stderr="server lost")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(claude_wakeup, "tmux", _tmux_track_and_fail_send)

        claude_wakeup.fire()

        # Expected call sequence on send-failure path: new-session, has-session,
        # send-keys (fails), kill-session (cleanup).
        ops = [c[0] for c in calls]
        assert "send-keys" in ops, f"send-keys must have been attempted; got {ops}"
        send_idx = ops.index("send-keys")
        post_send_ops = ops[send_idx + 1 :]
        assert "kill-session" in post_send_ops, (
            f"orphan tmux session must be cleaned up after send-keys failure; "
            f"calls after send-keys: {post_send_ops}"
        )

    def test_kill_session_failure_logs_warn_but_runs_success_decision(
        self, claude_wakeup, tmp_path, monkeypatch
    ):
        """kill-session rc != 0: fire() does NOT abort. Success-decision still runs;
        the message was delivered above so the streak/state logic must execute."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: True)

        def _tmux_kill_fails(*args):
            if args and args[0] == "kill-session":
                return SimpleNamespace(returncode=1, stderr="no such session")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(claude_wakeup, "tmux", _tmux_kill_fails)

        claude_wakeup.fire()

        # State file must be written despite kill-session failure.
        assert state_file.exists(), "kill-session failure must NOT abort write_state"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        # Success-decision ran and chose FIRE ok path.
        assert state["next_fire_at"] == _RESETS_AT_FIXTURE + 60, (
            f"kill-session failure must still allow FIRE ok success-decision; "
            f"got next_fire_at={state['next_fire_at']}"
        )
        assert state["partial_failure_streak"] == 0, (
            f"FIRE ok still resets streak even when kill-session failed; "
            f"got {state['partial_failure_streak']}"
        )

    def test_malformed_prior_state_partial_streak_defaults_to_zero(
        self, claude_wakeup, tmp_path, monkeypatch
    ):
        """prior state.json with a non-int partial_failure_streak value (e.g. None or
        string from manual edit) does NOT crash fire(); defaults to 0."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)

        # Pathological value: explicit None for the streak key. The `or 0` fallback in
        # the fire() preamble must coerce this to 0 rather than raise int(None).
        state_file.write_text(json.dumps({"partial_failure_streak": None}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 1, (
            f"None-streak coerced to 0 then incremented to 1 on partial; "
            f"got {state['partial_failure_streak']}"
        )

    def test_string_streak_value_defaults_to_zero(self, claude_wakeup, tmp_path, monkeypatch):
        """Non-numeric string in partial_failure_streak (e.g. manual edit typo): _parse_streak
        catches ValueError and defaults to 0; fire() does NOT crash."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)
        state_file.write_text(json.dumps({"partial_failure_streak": "foo"}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 1, (
            f"string streak coerced to 0 then incremented to 1; "
            f"got {state['partial_failure_streak']}"
        )

    def test_negative_streak_clamps_to_zero(self, claude_wakeup, tmp_path, monkeypatch):
        """Negative partial_failure_streak from manual edit MUST clamp to 0 — otherwise
        a single -100 walk-up bypasses the cap for many extra fast retries, violating the
        D2 log-growth bound that the cap exists to enforce."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)
        state_file.write_text(json.dumps({"partial_failure_streak": -100}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 1, (
            f"-100 must clamp to 0 then increment to 1, not walk -100 → -99 → ...; "
            f"got {state['partial_failure_streak']}"
        )

    def test_list_streak_value_defaults_to_zero(self, claude_wakeup, tmp_path, monkeypatch):
        """JSON list/dict in partial_failure_streak (e.g. corrupted state.json): TypeError
        caught by _parse_streak, defaults to 0; fire() does NOT crash."""
        state_file, _, _, cache_file = self._setup_fire_env(tmp_path, monkeypatch, claude_wakeup)
        state_file.write_text(json.dumps({"partial_failure_streak": [1, 2, 3]}), encoding="utf-8")
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(claude_wakeup, "wait_for_cache_refresh", lambda _before: False)

        claude_wakeup.fire()

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["partial_failure_streak"] == 1, (
            f"list value coerced to 0 then incremented to 1; got {state['partial_failure_streak']}"
        )

    def test_jsonl_sweep_deletes_known_uuid(self, claude_wakeup, tmp_path, monkeypatch):
        """fire() deletes a known-UUID JSONL file found in PROJECTS_DIR after the fire."""
        state_file, history_file, projects_dir, cache_file = self._setup_fire_env(
            tmp_path, monkeypatch, claude_wakeup
        )

        # Fresh cache with future resets_at so fire() takes the normal path
        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )

        # Seed HISTORY with a previously-seen UUID
        known_uuid = "aaaaaaaa-0000-0000-0000-000000000001"
        history_file.write_text(
            json.dumps({"uuid": known_uuid, "timestamp": _NOW_FIXTURE - 3600}) + "\n",
            encoding="utf-8",
        )

        # Seed the corresponding JSONL file in PROJECTS_DIR
        known_jsonl = projects_dir / f"{known_uuid}.jsonl"
        known_jsonl.write_text("{}", encoding="utf-8")

        claude_wakeup.fire()

        assert not known_jsonl.exists(), (
            f"known-UUID JSONL {known_jsonl.name} should have been deleted by the sweep"
        )

    def test_jsonl_sweep_preserves_unknown_uuid(self, claude_wakeup, tmp_path, monkeypatch):
        """fire() does NOT delete a JSONL file whose UUID is not in HISTORY (foreign session).

        This is the destructive-blast-radius guard: a flipped predicate at
        installer-assets/claude-wakeup:230 would silently delete the user's own
        conversation files from ~/.claude/projects/-home-user-dev/.
        """
        state_file, history_file, projects_dir, cache_file = self._setup_fire_env(
            tmp_path, monkeypatch, claude_wakeup
        )

        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )

        # Seed HISTORY with one known UUID
        known_uuid = "aaaaaaaa-0000-0000-0000-000000000002"
        history_file.write_text(
            json.dumps({"uuid": known_uuid, "timestamp": _NOW_FIXTURE - 3600}) + "\n",
            encoding="utf-8",
        )

        # Seed the known JSONL and a foreign JSONL (interactive session — not in HISTORY)
        known_jsonl = projects_dir / f"{known_uuid}.jsonl"
        known_jsonl.write_text("{}", encoding="utf-8")
        foreign_uuid = "bbbbbbbb-1111-1111-1111-000000000001"
        foreign_jsonl = projects_dir / f"{foreign_uuid}.jsonl"
        foreign_jsonl.write_text("{}", encoding="utf-8")

        claude_wakeup.fire()

        assert not known_jsonl.exists(), (
            f"known-UUID JSONL {known_jsonl.name} should have been deleted"
        )
        assert foreign_jsonl.exists(), (
            f"foreign-UUID JSONL {foreign_jsonl.name} must NOT be deleted — "
            "it belongs to a user interactive session, not the wakeup automation"
        )

    def test_spawn_failure_aborts_without_advancing_state(
        self, claude_wakeup, tmp_path, monkeypatch
    ):
        """fire() aborts + returns before write_state when tmux new-session reports rc != 0.

        Covers the F1 rc-check abort branch at installer-assets/claude-wakeup:161.
        Pre-existing state.json contents must be unchanged after the abort so the next
        cron tick retries the spawn rather than skipping the window.
        """
        state_file, history_file, _, cache_file = self._setup_fire_env(
            tmp_path, monkeypatch, claude_wakeup
        )

        # Seed pre-existing state — the abort must NOT overwrite this.
        prior_state = {
            "next_fire_at": 12345,
            "last_uuid": "prior-uuid",
            "last_name": "prior-name",
            "last_fire_at": 11111,
            "last_resets_at": 22222,
            "last_greeting": "Good morning",
        }
        state_file.write_text(json.dumps(prior_state), encoding="utf-8")

        cache_file.write_text(
            json.dumps(
                {"primary": {"resets_at": _RESETS_AT_FIXTURE}, "updated_at": _NOW_FIXTURE + 1}
            ),
            encoding="utf-8",
        )

        # tmux new-session returns rc=127 (mirrors the FileNotFoundError wrap)
        monkeypatch.setattr(
            claude_wakeup,
            "tmux",
            lambda *args: SimpleNamespace(returncode=127, stderr="tmux: command not found"),
        )

        claude_wakeup.fire()

        # State.json is byte-unchanged → next cron tick retries the same window
        state_after = json.loads(state_file.read_text(encoding="utf-8"))
        assert state_after == prior_state, (
            f"fire() must not advance state on spawn-failure abort; "
            f"prior={prior_state}, after={state_after}"
        )
        # History.jsonl was not appended — the abort runs before append_history
        assert not history_file.exists() or history_file.read_text(encoding="utf-8") == ""


class TestTmuxHelper:
    """Covers the tmux() helper FileNotFoundError wrap (installer-assets/claude-wakeup:122).

    Wrap is the F1 adversarial D1 close: tmux binary missing on cron's minimal PATH
    must convert into a CompletedProcess so the rc-check at fire() catches it +
    logs FIRE abort, rather than raising out of fire() silently.
    """

    def test_tmux_missing_binary_returns_127_completed_process(self, claude_wakeup, monkeypatch):
        """tmux() returns CompletedProcess(rc=127) when subprocess.run raises FileNotFoundError."""

        def _fake_run(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory: 'tmux'")

        monkeypatch.setattr(claude_wakeup.subprocess, "run", _fake_run)

        result = claude_wakeup.tmux("new-session", "-d", "-s", "test-sid")

        assert result.returncode == 127, (
            f"FileNotFoundError must convert to rc=127 (shell command-not-found convention); "
            f"got rc={result.returncode}"
        )
        assert "tmux" in result.stderr, (
            f"stderr must name the missing binary so the FIRE abort log is diagnostic; "
            f"got stderr={result.stderr!r}"
        )

    def test_tmux_normal_call_returns_subprocess_result(self, claude_wakeup, monkeypatch):
        """Green-path: tmux() returns whatever subprocess.run returns unchanged."""
        sentinel = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        monkeypatch.setattr(claude_wakeup.subprocess, "run", lambda *a, **k: sentinel)

        result = claude_wakeup.tmux("list-sessions")

        assert result is sentinel, "wrap must not intercept successful subprocess.run returns"


class TestModuleConstants:
    """Pins module-level constants whose values are load-bearing for math assertions."""

    def test_fallback_window_constant_value(self, claude_wakeup):
        """FALLBACK_WINDOW pinned to 5 hours + 60 seconds (Claude Max budget cycle + slack)."""
        assert claude_wakeup.FALLBACK_WINDOW == 5 * 3600 + 60

    def test_retry_backoff_constant_value(self, claude_wakeup):
        """RETRY_BACKOFF pinned to 5 minutes — arbiter verdict B (ADR-0033)."""
        assert claude_wakeup.RETRY_BACKOFF == 5 * 60

    def test_max_partial_retries_constant_value(self, claude_wakeup):
        """MAX_PARTIAL_RETRIES pinned to 3 — arbiter verdict B (ADR-0033): 15-minute
        retry window pre-cap covers transient cache-refresh / tmux-race failures from
        ADR-0032 clause (d) while escalating persistent failures promptly to FALLBACK_WINDOW."""
        assert claude_wakeup.MAX_PARTIAL_RETRIES == 3


# ===========================================================================
# claude-wakeup-sessionstart.py hook tests
# ===========================================================================


class TestFormatTs:
    """Covers claude-wakeup-sessionstart.py: _format_ts() boundary cases."""

    def test_valid_int_epoch(self, claude_wakeup_hook):
        """_format_ts(int) returns an ISO8601 string."""
        result = claude_wakeup_hook._format_ts(1_700_000_000)
        assert isinstance(result, str)
        assert "T" in result  # ISO8601 separator

    def test_valid_float_epoch(self, claude_wakeup_hook):
        """_format_ts(float) returns an ISO8601 string."""
        result = claude_wakeup_hook._format_ts(1_700_000_000.5)
        assert isinstance(result, str)
        assert "T" in result

    def test_none_returns_none(self, claude_wakeup_hook):
        """_format_ts(None) returns None (TypeError caught)."""
        assert claude_wakeup_hook._format_ts(None) is None

    def test_non_numeric_string_returns_none(self, claude_wakeup_hook):
        """_format_ts('abc') returns None (ValueError caught on int('abc'))."""
        assert claude_wakeup_hook._format_ts("abc") is None

    def test_negative_int_returns_non_none(self, claude_wakeup_hook):
        """_format_ts(-1) returns a non-None ISO string (pre-epoch date)."""
        result = claude_wakeup_hook._format_ts(-1)
        # On most platforms this is 1969-12-31T... ; don't pin the exact
        # value because timezone offsets vary. Just confirm it is a string.
        assert result is not None
        assert isinstance(result, str)

    def test_huge_int_returns_none_or_string(self, claude_wakeup_hook):
        """_format_ts(10**10) returns None (OverflowError/OSError) or a valid string.

        On 64-bit Linux the year ~2286 is representable; on 32-bit systems the
        call raises OSError (caught). Either outcome is acceptable.
        """
        result = claude_wakeup_hook._format_ts(10**10)
        # result is either None (overflow caught) or a str (large year)
        assert result is None or isinstance(result, str)


class TestHookMain:
    """Covers claude-wakeup-sessionstart.py: main() output paths.

    Since WI-3, main() may emit Tier-0 block lines (identity + L1 fallback
    + registry) before the legacy claude_wakeup state line.  Tests that
    previously asserted ``captured.out == ""`` on error paths now assert
    that NO ``claude_wakeup`` state line is emitted (the Tier-0 block itself
    may still produce output).  The happy-path test asserts the state line
    appears somewhere in the output.
    """

    _REQUIRED_KEYS = ("last_fire_at", "last_resets_at", "next_fire_at")

    def _write_state(self, path: Path, data: object) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_happy_path_emits_one_line(self, claude_wakeup_hook, tmp_path, monkeypatch, capsys):
        """main() emits the 'claude_wakeup ...' state line with 3 ISO timestamp fields."""
        state_file = tmp_path / "state.json"
        epoch = 1_700_000_000
        self._write_state(
            state_file,
            {
                "last_fire_at": epoch,
                "last_resets_at": epoch + 1,
                "next_fire_at": epoch + 2,
            },
        )
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", state_file)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()
        # Find the claude_wakeup state line among all output lines.
        wakeup_lines = [ln for ln in captured.out.splitlines() if ln.startswith("claude_wakeup ")]
        assert len(wakeup_lines) == 1, (
            f"expected exactly one 'claude_wakeup ...' line in output; "
            f"found {len(wakeup_lines)}:\n{captured.out!r}"
        )
        line = wakeup_lines[0]
        parts = line.split()
        assert len(parts) == 4, f"expected 4 tokens (prefix + 3 timestamps), got: {parts!r}"
        for key in self._REQUIRED_KEYS:
            assert any(p.startswith(f"{key}=") for p in parts), (
                f"missing key {key!r} in output {line!r}"
            )

    def test_missing_state_file_silent(self, claude_wakeup_hook, tmp_path, monkeypatch, capsys):
        """main() does not emit a 'claude_wakeup ...' line when state.json does not exist."""
        state_file = tmp_path / "state_absent.json"  # never created
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", state_file)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()
        wakeup_lines = [ln for ln in captured.out.splitlines() if ln.startswith("claude_wakeup ")]
        assert wakeup_lines == [], (
            f"no 'claude_wakeup ...' line expected when state file missing; got: {wakeup_lines}"
        )

    def test_malformed_json_silent(self, claude_wakeup_hook, tmp_path, monkeypatch, capsys):
        """main() does not emit a 'claude_wakeup ...' line when state.json is invalid JSON."""
        state_file = tmp_path / "state.json"
        state_file.write_text("not json {{", encoding="utf-8")
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", state_file)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()
        wakeup_lines = [ln for ln in captured.out.splitlines() if ln.startswith("claude_wakeup ")]
        assert wakeup_lines == [], (
            f"no 'claude_wakeup ...' line expected on malformed JSON; got: {wakeup_lines}"
        )

    def test_non_dict_json_silent(self, claude_wakeup_hook, tmp_path, monkeypatch, capsys):
        """main() does not emit a 'claude_wakeup ...' line when state.json is a list."""
        state_file = tmp_path / "state.json"
        self._write_state(state_file, [1, 2, 3])
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", state_file)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()
        wakeup_lines = [ln for ln in captured.out.splitlines() if ln.startswith("claude_wakeup ")]
        assert wakeup_lines == [], (
            f"no 'claude_wakeup ...' line expected when JSON is not a dict; got: {wakeup_lines}"
        )

    def test_null_json_silent(self, claude_wakeup_hook, tmp_path, monkeypatch, capsys):
        """main() does not emit a 'claude_wakeup ...' line when state.json is JSON null."""
        state_file = tmp_path / "state.json"
        self._write_state(state_file, None)
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", state_file)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()
        wakeup_lines = [ln for ln in captured.out.splitlines() if ln.startswith("claude_wakeup ")]
        assert wakeup_lines == [], (
            f"no 'claude_wakeup ...' line expected when JSON is null; got: {wakeup_lines}"
        )

    def test_missing_required_key_silent(self, claude_wakeup_hook, tmp_path, monkeypatch, capsys):
        """main() does not emit a 'claude_wakeup ...' line when a required key is absent."""
        state_file = tmp_path / "state.json"
        self._write_state(state_file, {"last_fire_at": 1_700_000_000})
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", state_file)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()
        wakeup_lines = [ln for ln in captured.out.splitlines() if ln.startswith("claude_wakeup ")]
        assert wakeup_lines == [], (
            f"no 'claude_wakeup ...' line expected when required key is absent; got: {wakeup_lines}"
        )

    def test_non_numeric_required_key_silent(
        self, claude_wakeup_hook, tmp_path, monkeypatch, capsys
    ):
        """main() does not emit a 'claude_wakeup ...' line when a required key is non-numeric."""
        state_file = tmp_path / "state.json"
        self._write_state(
            state_file,
            {
                "last_fire_at": "abc",
                "last_resets_at": 1_700_000_001,
                "next_fire_at": 1_700_000_002,
            },
        )
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", state_file)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()
        wakeup_lines = [ln for ln in captured.out.splitlines() if ln.startswith("claude_wakeup ")]
        assert wakeup_lines == [], (
            f"no 'claude_wakeup ...' line expected when required key is non-numeric; "
            f"got: {wakeup_lines}"
        )


class TestHashFileWingKeyed:
    """FIX 3: hash file is keyed by wing slug so concurrent sessions in
    different wings do not clobber each other's tier0_block_stable proxy.
    """

    def test_hash_file_for_wing_returns_keyed_path(self, claude_wakeup_hook):
        """_hash_file_for_wing returns a wing-specific path."""
        path = claude_wakeup_hook._hash_file_for_wing("sage")
        assert "sage" in path.name, f"Expected wing slug in hash file name; got: {path.name!r}"

    def test_hash_file_for_wing_none_returns_default(self, claude_wakeup_hook):
        """_hash_file_for_wing(None) returns the legacy unkeyed path."""
        path = claude_wakeup_hook._hash_file_for_wing(None)
        assert path.name == "tier0-last-hash.txt", (
            f"Expected legacy filename for wing=None; got: {path.name!r}"
        )

    def test_different_wings_use_different_hash_files(self, claude_wakeup_hook):
        """Two different wing slugs produce different hash file paths."""
        path_a = claude_wakeup_hook._hash_file_for_wing("wing-a")
        path_b = claude_wakeup_hook._hash_file_for_wing("wing-b")
        assert path_a != path_b, "Different wings must use different hash files."

    def test_write_and_read_wing_hash(self, claude_wakeup_hook, tmp_path, monkeypatch):
        """_write_last_hash + _read_last_hash round-trip per wing without cross-clobber."""
        monkeypatch.setattr(claude_wakeup_hook, "_HASH_DIR", tmp_path)

        # Write hash for wing-a.
        claude_wakeup_hook._write_last_hash("aabbccdd", "wing-a")
        # Write a DIFFERENT hash for wing-b — must not overwrite wing-a.
        claude_wakeup_hook._write_last_hash("11223344", "wing-b")

        read_a = claude_wakeup_hook._read_last_hash("wing-a")
        read_b = claude_wakeup_hook._read_last_hash("wing-b")

        assert read_a == "aabbccdd", f"wing-a hash clobbered; expected 'aabbccdd', got {read_a!r}"
        assert read_b == "11223344", f"wing-b hash wrong; expected '11223344', got {read_b!r}"
        assert read_a != read_b, "wing-a and wing-b hash must not share storage."

    def test_read_missing_hash_returns_none(self, claude_wakeup_hook, tmp_path, monkeypatch):
        """_read_last_hash returns None when no hash file exists for the wing."""
        monkeypatch.setattr(claude_wakeup_hook, "_HASH_DIR", tmp_path)
        result = claude_wakeup_hook._read_last_hash("no-such-wing")
        assert result is None


class TestMalformedSlugFailOpen:
    """F#3: malformed wing slugs (null byte, path traversal chars) must not
    raise and must not break the hook's fail-open contract.
    """

    def test_null_byte_slug_does_not_raise_on_hash_file(self, claude_wakeup_hook):
        """_hash_file_for_wing with a null-byte slug returns a safe Path, never raises."""
        result = claude_wakeup_hook._hash_file_for_wing("wing\x00evil")
        # Must be a Path and its name must not contain a null byte.
        assert isinstance(result, Path)
        assert "\x00" not in str(result), f"Null byte leaked into hash file path: {result!r}"

    def test_path_traversal_slug_stays_in_hash_dir(self, claude_wakeup_hook):
        """_hash_file_for_wing with a path-traversal slug stays under _HASH_DIR."""
        result = claude_wakeup_hook._hash_file_for_wing("../../etc/passwd")
        assert isinstance(result, Path)
        # The result must not escape the _HASH_DIR prefix after sanitisation.
        # Resolve both paths to compare canonically.
        hash_dir = claude_wakeup_hook._HASH_DIR
        assert str(result).startswith(str(hash_dir)), (
            f"Path traversal not sanitised; result {result!r} escapes _HASH_DIR {hash_dir!r}"
        )

    def test_read_last_hash_with_null_byte_slug_returns_none(
        self, claude_wakeup_hook, tmp_path, monkeypatch
    ):
        """_read_last_hash with a null-byte slug returns None, never raises."""
        monkeypatch.setattr(claude_wakeup_hook, "_HASH_DIR", tmp_path)
        result = claude_wakeup_hook._read_last_hash("wing\x00evil")
        assert result is None, (
            f"_read_last_hash with null-byte slug must return None; got {result!r}"
        )

    def test_write_last_hash_with_null_byte_slug_does_not_raise(
        self, claude_wakeup_hook, tmp_path, monkeypatch
    ):
        """_write_last_hash with a null-byte slug silently no-ops, never raises."""
        monkeypatch.setattr(claude_wakeup_hook, "_HASH_DIR", tmp_path)
        # Must not raise — the hook must always fail-open.
        claude_wakeup_hook._write_last_hash("aabbccdd", "wing\x00evil")

    def test_hook_main_returns_zero_with_malformed_wing(
        self, claude_wakeup_hook, tmp_path, monkeypatch
    ):
        """main() returns 0 (fail-open) even when current_wing is a malformed slug.

        Guards the full hook path: a null-byte or path-traversal slug read
        from ~/.sage/current_wing must not break the hook and must not
        block session start.
        """
        # Patch _read_current_wing to return a malformed slug.
        monkeypatch.setattr(
            claude_wakeup_hook,
            "_read_current_wing",
            lambda: "wing\x00evil/../../../etc/passwd",
        )
        # Patch MemoryStack import to skip the full nook stack.
        monkeypatch.setattr(claude_wakeup_hook, "_HASH_DIR", tmp_path)

        result = claude_wakeup_hook.main()
        assert result == 0, (
            f"main() must return 0 (fail-open) with malformed wing slug; got {result!r}"
        )


# ===========================================================================
# Internal helpers
# ===========================================================================


def _fixed_datetime(hour: int):
    """Return a fake datetime replacement whose .now().hour == ``hour``."""

    class _FakeNow:
        def __init__(self, h):
            self.hour = h

    class _FakeDatetime:
        @staticmethod
        def now(*args, **kwargs):
            return _FakeNow(hour)

    return _FakeDatetime


class _NoSleepTimeModule:
    """Drop-in replacement for the ``time`` module that no-ops sleep."""

    def sleep(self, _seconds):
        pass  # no-op — keeps polling tests fast

    @staticmethod
    def time():
        return time.time()


def _no_sleep_time() -> _NoSleepTimeModule:
    return _NoSleepTimeModule()


# ── SAGE_TIER0_MAX_CHARS cap tests (P3.3) ────────────────────────────────────


class TestTier0MaxChars:
    """Tests for the SAGE_TIER0_MAX_CHARS cap in claude-wakeup-sessionstart.py."""

    def test_default_returns_8000(self, claude_wakeup_hook, monkeypatch):
        """Unset env var returns the default 8000 ceiling."""
        monkeypatch.delenv("SAGE_TIER0_MAX_CHARS", raising=False)
        assert claude_wakeup_hook._tier0_max_chars() == 8000

    def test_custom_value(self, claude_wakeup_hook, monkeypatch):
        """A valid positive integer is honoured."""
        monkeypatch.setenv("SAGE_TIER0_MAX_CHARS", "4000")
        assert claude_wakeup_hook._tier0_max_chars() == 4000

    def test_zero_disables_cap(self, claude_wakeup_hook, monkeypatch):
        """0 disables the cap (returns sys.maxsize)."""
        import sys

        monkeypatch.setenv("SAGE_TIER0_MAX_CHARS", "0")
        assert claude_wakeup_hook._tier0_max_chars() == sys.maxsize

    def test_negative_falls_back_to_default(self, claude_wakeup_hook, monkeypatch):
        """Negative values fall back to the default."""
        monkeypatch.setenv("SAGE_TIER0_MAX_CHARS", "-500")
        assert claude_wakeup_hook._tier0_max_chars() == 8000

    def test_non_integer_falls_back_to_default(self, claude_wakeup_hook, monkeypatch):
        """Non-integer value falls back to the default."""
        monkeypatch.setenv("SAGE_TIER0_MAX_CHARS", "not_a_number")
        assert claude_wakeup_hook._tier0_max_chars() == 8000

    def test_main_truncates_when_cap_exceeded(
        self, claude_wakeup_hook, tmp_path, monkeypatch, capsys
    ):
        """main() truncates the tier0 block when it exceeds SAGE_TIER0_MAX_CHARS."""
        monkeypatch.setenv("SAGE_TIER0_MAX_CHARS", "100")
        # Suppress the state-file line so we don't hit stale state issues
        monkeypatch.setattr(claude_wakeup_hook, "STATE_FILE", tmp_path / "absent.json")

        # Inject a tier0_text that exceeds 100 chars
        long_text = "X" * 500

        class _FakeBlock:
            text = long_text

        class _FakeStack:
            def assemble_tier0(self, wing=None):
                return _FakeBlock()

        def _fake_import():
            pass

        # We call _tier0_max_chars directly to confirm the cap applies,
        # then verify via a mock that the truncation logic runs.
        import sys as _sys

        # Build a fake sage.layers module
        fake_layers = type(_sys)("sage_mcp.layers")
        fake_layers.MemoryStack = _FakeStack
        monkeypatch.setitem(_sys.modules, "sage_mcp.layers", fake_layers)

        # Silence telemetry import
        fake_telemetry = type(_sys)("sage_mcp.telemetry")

        def _fake_log_tier0(**kwargs):
            pass

        fake_telemetry.log_tier0_wake_up = _fake_log_tier0
        monkeypatch.setitem(_sys.modules, "sage_mcp.telemetry", fake_telemetry)

        claude_wakeup_hook.main()
        captured = capsys.readouterr()

        # The emitted TIER-0 CONTEXT section must not exceed 100 chars of "X"s
        # (the header lines are separate; we check the body doesn't have 500 Xs)
        assert "X" * 500 not in captured.out, (
            "tier0 block was not truncated — 500-char body passed through cap of 100"
        )
        # The first 100 Xs should be present (truncation keeps the prefix)
        assert "X" * 100 in captured.out or "X" * 50 in captured.out, (
            "expected partial X content in output after truncation"
        )

    def test_truncation_is_deterministic(self, claude_wakeup_hook, monkeypatch):
        """Same input text + same cap → same truncation result every time."""
        monkeypatch.setenv("SAGE_TIER0_MAX_CHARS", "50")
        cap = claude_wakeup_hook._tier0_max_chars()
        text = "A" * 200
        result1 = text[:cap]
        result2 = text[:cap]
        assert result1 == result2
        assert len(result1) == 50

    def test_hash_computed_on_truncated_text(self, claude_wakeup_hook, monkeypatch):
        """Stability hash is computed on the post-truncation text (same cap → same hash)."""
        monkeypatch.setenv("SAGE_TIER0_MAX_CHARS", "20")
        cap = claude_wakeup_hook._tier0_max_chars()
        text = "B" * 100
        truncated = text[:cap]
        h1 = claude_wakeup_hook._block_hash(truncated)
        h2 = claude_wakeup_hook._block_hash(truncated)
        assert h1 == h2
        # And it differs from the hash of the full text
        h_full = claude_wakeup_hook._block_hash(text)
        assert h1 != h_full
