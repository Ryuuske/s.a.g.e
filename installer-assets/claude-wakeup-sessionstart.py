#!/usr/bin/env python3
"""SessionStart hook: inject the Tier-0 cached-prefix block at session start.

Assembles a deterministic Tier-0 block — identity core + current-wing L1
halls + compact skill/agent/script registry section — and emits it so it
sits in Claude Code's cacheable prefix region (WI-3, ADR-0040).

The block is STABLE: same wing + same nook state → byte-identical output.
This is what makes it a prompt-cache prefix in practice: the Claude Code
harness caches the prefix automatically when it is byte-identical across
turns.  We do not set API-level cache_control (that is not available to a
SessionStart hook script); stability IS the caching mechanism.

tier0_block_stability: the script compares the assembled block hash against the
hash stored at ~/.sage/tier0-last-hash.txt from the previous session.
If they match, tier0_block_stable=True is recorded in the telemetry log.
This is a BLOCK STABILITY indicator, NOT a measure of an Anthropic prompt-cache
read.  SessionStart-injected content has undocumented placement; the real
tier0_block_stable signal (cache_read_input_tokens) is API-level only (see ADR-0044).

Fail-open: every error path returns 0 so the hook cannot block session start.
Silent if sage is not installed or the nook has no identity/drawers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

# ── SAGE_HOOK_PROFILE kill-switch ─────────────────────────────────────────────
# Mirror of the same helper in _session_hook.py and autonomy_reporting_eval.py.
# Do NOT import from the sage package here; this script runs as a standalone
# SessionStart hook where the package / venv may be absent.
# Values: off / minimal / standard; unset or unknown → standard; fail-open.
_HOOK_PROFILE_ENV = "SAGE_HOOK_PROFILE"
_HOOK_PROFILE_OFF = "off"


def _read_hook_profile() -> str:
    """Return the active SAGE_HOOK_PROFILE value, resolved from the environment.

    Unset or unrecognised values resolve to 'standard' so the dial is opt-in.
    Fails open (returns 'standard') on any error so the hook never crashes.
    """
    try:
        raw = os.environ.get(_HOOK_PROFILE_ENV, "").strip().lower()
        if raw in ("off", "minimal", "standard"):
            return raw
    except Exception:  # noqa: BLE001 — fail open
        pass
    return "standard"


# ── SAGE_TIER0_MAX_CHARS cap ─────────────────────────────────────────────────
# Maximum character count for the assembled Tier-0 block before it is emitted.
# A bloated wing can produce a Tier-0 block that is too large to sit in the
# cacheable prefix region, burning tokens on every session start (CLAUDE.md §13).
# When the assembled text exceeds this ceiling it is truncated BEFORE emit.
# The stability hash is computed on the FINAL (possibly truncated) text so the
# cache stays stable for the same wing/state even after truncation.
# Truncation is deterministic: same input length → same truncation point.
# Default: 8000 chars (~2000 tokens), sensible ceiling for the cacheable prefix.
_TIER0_MAX_CHARS_ENV = "SAGE_TIER0_MAX_CHARS"
_TIER0_MAX_CHARS_DEFAULT = 8000


def _tier0_max_chars() -> int:
    """Return the configured Tier-0 character ceiling.

    Reads SAGE_TIER0_MAX_CHARS from the environment (positive integer).
    Falls back to _TIER0_MAX_CHARS_DEFAULT on missing, zero, or non-integer
    values.  A value of 0 explicitly disables the cap (returns sys.maxsize
    so downstream comparisons are always False).
    """
    import os
    import sys

    raw = os.environ.get(_TIER0_MAX_CHARS_ENV, "").strip()
    if not raw:
        return _TIER0_MAX_CHARS_DEFAULT
    try:
        val = int(raw)
    except ValueError:
        return _TIER0_MAX_CHARS_DEFAULT
    if val == 0:
        return sys.maxsize  # cap disabled
    if val < 0:
        return _TIER0_MAX_CHARS_DEFAULT
    return val


# ── Tier-0 block stability hash state ───────────────────────────────────────
# Hash file is keyed by wing slug so concurrent sessions in different wings
# do not clobber each other's tier0_block_stability proxy (FIX 3).
# _HASH_FILE is kept as the legacy single-wing path for backward compat;
# use _hash_file_for_wing() to get the correct path per session.
_HASH_DIR = Path.home() / ".sage"

# Safe charset for wing slugs in file names: alphanumerics, hyphen, underscore, dot.
# Everything else is replaced with '-' to prevent path traversal + null bytes.
_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_wing_slug(wing: str) -> str:
    """Return a filesystem-safe version of a wing slug.

    Strips null bytes and characters outside the safe charset
    (alphanumerics, hyphen, underscore, dot).  Collapses repeated
    replacement chars and strips leading/trailing hyphens.

    If the result is empty after sanitisation, returns the literal string
    'unknown' so the caller always gets a usable filename component.
    """
    # Reject null bytes outright (they are never valid in slugs).
    wing = wing.replace("\x00", "")
    # Replace unsafe chars (path separators, spaces, etc.) with '-'.
    safe = _SAFE_SLUG_RE.sub("-", wing)
    # Collapse repeated hyphens and strip leading/trailing hyphens.
    safe = re.sub(r"-{2,}", "-", safe).strip("-")
    return safe if safe else "unknown"


def _hash_file_for_wing(wing: str | None) -> Path:
    """Return the wing-keyed hash file path.

    Wing-keyed: ``~/.sage/tier0-last-hash-<wing>.txt``
    Unkeyed fallback (wing is None): ``~/.sage/tier0-last-hash.txt``

    The wing slug is sanitised via ``_sanitize_wing_slug`` before being
    embedded in the path, preventing path traversal and null-byte injection
    (F#3).
    """
    if wing:
        safe = _sanitize_wing_slug(wing)
        return _HASH_DIR / f"tier0-last-hash-{safe}.txt"
    return _HASH_DIR / "tier0-last-hash.txt"


# ── Wakeup state file (legacy readout — preserved for backward compat) ───────
STATE_FILE = Path.home() / ".cache" / "claude-wakeup" / "state.json"
REQUIRED_KEYS = ("last_fire_at", "last_resets_at", "next_fire_at")


def _format_ts(epoch: object) -> str | None:
    """Return ISO8601 (seconds precision) for an epoch int/float, or None."""
    try:
        return datetime.fromtimestamp(int(epoch)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _read_current_wing() -> str | None:
    """Read the current wing slug from ~/.sage/current_wing."""
    wing_file = Path.home() / ".sage" / "current_wing"
    try:
        return wing_file.read_text(encoding="utf-8").strip() or None
    except (FileNotFoundError, OSError):
        return None


def _read_last_hash(wing: str | None) -> str | None:
    """Return the stored Tier-0 block hash from the previous session, or None.

    Uses the wing-keyed hash file so concurrent sessions in different wings
    do not clobber each other's tier0_block_stability proxy (FIX 3).

    Catches all exceptions (not just FileNotFoundError/OSError) so that any
    residual error from path construction or I/O never propagates out of the
    fail-open contract (F#3).
    """
    try:
        hash_file = _hash_file_for_wing(wing)
        return hash_file.read_text(encoding="utf-8").strip() or None
    except Exception:  # noqa: BLE001
        return None


def _write_last_hash(block_hash: str, wing: str | None) -> None:
    """Persist the Tier-0 block hash for the next session's tier0_block_stability check.

    Uses the wing-keyed hash file so concurrent sessions in different wings
    do not clobber each other's tier0_block_stability proxy (FIX 3).

    Catches all exceptions (not just OSError) so that any residual error
    from path construction or I/O never propagates out of the fail-open
    contract (F#3).
    """
    try:
        hash_file = _hash_file_for_wing(wing)
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text(block_hash + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass  # Fail-open; hash write failure does not block session start.


def _block_hash(text: str) -> str:
    """SHA-256 hex digest of the block text (first 16 hex chars, enough for collision resistance)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _log_tier0_telemetry(
    tier0_tokens: int, tier0_block_stable: bool | None, wing: str | None
) -> None:
    """Record the Tier-0 wake-up event in the telemetry log via sage_mcp.telemetry.

    Fails silently — telemetry must never block session start.
    """
    try:
        from sage_mcp.telemetry import log_tier0_wake_up

        log_tier0_wake_up(
            tier0_tokens=tier0_tokens,
            tier0_block_stable=tier0_block_stable,
            wing=wing,
        )
    except Exception:  # noqa: BLE001
        pass


def _emit_wakeup_state_line() -> None:
    """Emit the legacy claude-wakeup state line if the state file exists.

    Preserved for backward compatibility with tooling that parses the
    ``claude_wakeup last_fire_at=... next_fire_at=...`` line.
    """
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return

    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return

    if not isinstance(state, dict):
        return

    parts: list[str] = ["claude_wakeup"]
    for key in REQUIRED_KEYS:
        formatted = _format_ts(state.get(key))
        if formatted is None:
            return
        parts.append(f"{key}={formatted}")

    print(" ".join(parts))


def main() -> int:
    # SAGE_HOOK_PROFILE kill-switch — checked before any stdout write.
    # 'off' means all hook side-effects no-op; return 0, emit nothing.
    if _read_hook_profile() == _HOOK_PROFILE_OFF:
        return 0

    # ── Assemble Tier-0 block ────────────────────────────────────────────
    wing = _read_current_wing()

    tier0_text: str | None = None
    try:
        from sage_mcp.layers import MemoryStack

        stack = MemoryStack()
        block = stack.assemble_tier0(wing=wing)
        tier0_text = block.text
    except Exception:  # noqa: BLE001
        tier0_text = None

    # ── SAGE_TIER0_MAX_CHARS cap ─────────────────────────────────────────
    # Truncate the assembled Tier-0 text to the configured ceiling BEFORE
    # computing the stability hash.  Truncation is deterministic (same text
    # + same cap → same result), so the cache stays stable for identical
    # wing/state even when the cap is active.  The hash is intentionally
    # computed on the post-truncation text (including the truncation notice
    # when appended) so it reflects what was actually emitted (serves
    # CLAUDE.md §13 cost-and-context discipline).
    # A short truncation notice is appended when truncation occurs so the
    # receiving session knows context was cut and does not assume completeness.
    if tier0_text is not None:
        cap = _tier0_max_chars()
        if len(tier0_text) > cap:
            notice = f"\n<!-- sage: tier0 truncated at {cap} chars -->"
            tier0_text = tier0_text[:cap] + notice

    # ── Block-stability check ────────────────────────────────────────────
    # Measures whether the Tier-0 block is byte-identical to the prior
    # recorded emission.  This is a STABILITY indicator — NOT a measure of
    # an Anthropic prompt-cache read.  SessionStart-injected content has
    # undocumented placement; the real cache signal (cache_read_input_tokens)
    # is API-level only (see ADR-0044).
    tier0_block_stable: bool | None = None
    if tier0_text is not None:
        current_hash = _block_hash(tier0_text)
        prior_hash = _read_last_hash(wing)
        if prior_hash is not None:
            tier0_block_stable = current_hash == prior_hash
        # Always update the stored hash for next session's comparison.
        _write_last_hash(current_hash, wing)

    # ── Telemetry ────────────────────────────────────────────────────────
    if tier0_text is not None:
        tier0_tokens = len(tier0_text) // 4
        _log_tier0_telemetry(
            tier0_tokens=tier0_tokens,
            tier0_block_stable=tier0_block_stable,
            wing=wing,
        )

    # ── Emit the Tier-0 block to stdout ─────────────────────────────────
    # The Claude Code hook harness reads the hook's stdout into context.
    # Emitting here places the Tier-0 block at the stable prefix position.
    if tier0_text:
        print("## TIER-0 CONTEXT (sage session start)")
        if wing:
            print(f"## wing: {wing}")
        if tier0_block_stable is not None:
            stable_label = "STABLE" if tier0_block_stable else "CHANGED (state changed)"
            print(f"## tier0_block_stable: {stable_label}")
        print(tier0_text)

    # ── Legacy state line ────────────────────────────────────────────────
    _emit_wakeup_state_line()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
