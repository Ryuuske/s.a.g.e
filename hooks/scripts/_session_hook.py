"""Shared session-end / pre-compact hook body for sage.

Both ``stop.py`` and ``precompact.py`` route through ``run_session_hook``;
the only difference is the topic + drawer ``room`` label that identifies
the trigger. Keeping the body shared means there's exactly one place
where the wing-detection contract, the Keeper skip-check, and the
emergency-drawer write live.

``SAGE_HOOK_PROFILE=off`` short-circuits the hook entirely (kill-switch):
no drawer is written and the hook returns 0 immediately. ``minimal`` and
``standard`` both proceed (the emergency drawer is the safety floor for
``minimal``).

Contract:

  ~/.sage/current_wing
      Single-line file whose contents are the wing slug the
      orchestrator wants this hook to write to. Owned by the
      orchestrator — the User's CLAUDE.md instructs it to write the
      slug on session start (and on cd into a new destination).
      The hook NEVER infers a wing from cwd; absent file ⇒ no-op.

  ~/.sage/last_keeper_dispatch
      ISO-8601 timestamp the orchestrator updates each time it
      dispatches the ``aidev-keeper`` (or any Keeper-role)
      agent. Recency window KEEPER_WINDOW_SECONDS treats the
      Keeper as having handled the session, so the hook skips
      the emergency drawer. Absent ⇒ never dispatched ⇒ emergency
      drawer required.

  drawer agents tag
      Hook-written drawers carry ``agents=["session-end-hook"]``
      (Stop) or ``agents=["pre-compact-hook"]`` (PreCompact) so
      ``nook_search agents=["session-end-hook"]`` surfaces every
      drawer the hook ever filed.

The pre-compact path MUST return cleanly even on internal errors:
cancelling compaction on hook failure is unacceptable. Any exception
inside ``run_session_hook`` is logged to stderr and swallowed.

Secret-redaction (opt-in, DEFAULT OFF — rules/memory-hygiene.md):
  Enabled by ``SAGE_REDACT_SECRETS=1`` env var OR
  ``hooks.redact_secrets = true`` in sage config.
  When active, drawer content is passed through
  ``sage_mcp.secret_scrub.scrub_secrets()`` (high-confidence patterns only,
  NOT the aggressive hex≥40 Tier-0 pass) before filing.
  Default is OFF so existing behavior is unchanged unless the operator
  explicitly opts in.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── SAGE_HOOK_PROFILE kill-switch ─────────────────────────────────────────────
# Mirror of the same constant in src/sage_mcp/hooks_cli.py — do NOT import from
# the sage package here; the package may not be importable in the installed-hook
# context (virtual env absent, editable install not active, etc.).
# off      — all hook side-effects no-op; return early, no drawer written.
# minimal  — emergency drawer is the safety floor; proceed.
# standard — full current behavior (default for unset / unknown values).
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


# Recency window for the Keeper-dispatched skip. 30 minutes is
# generous enough that mid-session dispatches still suppress the
# session-end hook write, but tight enough that a Keeper call from
# the prior session doesn't suppress the next session's emergency
# write if no fresh dispatch happened.
KEEPER_WINDOW_SECONDS = 30 * 60

# Last-4000-chars window the plan prescribes for the emergency drawer.
EMERGENCY_DRAWER_CHARS = 4000

# ── Opt-in secret-redaction (DEFAULT OFF) ────────────────────────────────────
# See rules/memory-hygiene.md for the full surface description and rationale.
# Enabled by SAGE_REDACT_SECRETS=1 env var OR hooks.redact_secrets config key.
_REDACT_SECRETS_ENV = "SAGE_REDACT_SECRETS"


def _redaction_enabled() -> bool:
    """Return True when the operator has opted into secret-redaction.

    Checks SAGE_REDACT_SECRETS env var first (env overrides config).
    Falls back to the sage config ``hooks.redact_secrets`` key.
    Default is False when neither is set — existing behavior is unchanged.

    Fails open (returns False) on any import or config error so the hook
    never blocks on missing sage package.
    """
    env_val = os.environ.get(_REDACT_SECRETS_ENV, "").strip().lower()
    if env_val:
        return env_val in ("1", "true", "yes")
    try:
        from sage_mcp.config import SageConfig

        cfg = SageConfig()
        return bool(getattr(cfg, "hooks_redact_secrets", False))
    except Exception:  # noqa: BLE001
        return False


def _maybe_scrub(content: str) -> str:
    """Apply secret-redaction to ``content`` when the operator has opted in.

    When redaction is disabled (default), returns ``content`` unchanged.
    When enabled, applies the high-confidence write-boundary scrub
    (``scrub_secrets()``, ``aggressive=False``) from ``sage_mcp.secret_scrub``.
    The aggressive hex≥40 pattern is NOT applied here (that is Tier-0 only
    per ADR-0042 — it would redact legitimate git SHAs in drawer content).

    Fails open: if the scrubber import fails, returns ``content`` unchanged
    so the hook never blocks on a missing sage package.
    """
    if not _redaction_enabled():
        return content
    try:
        from sage_mcp.secret_scrub import scrub_secrets

        return scrub_secrets(content, aggressive=False)
    except Exception:  # noqa: BLE001
        return content


def _sage_dir() -> Path:
    return Path.home() / ".sage"


def _current_wing() -> str | None:
    """Read the wing the orchestrator last advertised, or None.

    The orchestrator writes this file (per the User's CLAUDE.md spine);
    the hook treats absence as 'no current wing known, do nothing'.
    """
    path = _sage_dir() / "current_wing"
    if not path.is_file():
        return None
    try:
        wing = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return wing or None


def _keeper_handled_this_session() -> bool:
    """Return True when the orchestrator dispatched the Keeper
    within KEEPER_WINDOW_SECONDS. False on absent file or stale
    timestamp."""
    path = _sage_dir() / "last_keeper_dispatch"
    if not path.is_file():
        return False
    try:
        stamp = path.read_text(encoding="utf-8").strip()
        when = datetime.fromisoformat(stamp)
    except (OSError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (datetime.now(timezone.utc) - when).total_seconds()
    # delta < 0 means the timestamp is in the future (clock skew or
    # corrupted file); treat as not-recent so the emergency drawer
    # still fires. This is the fail-safe direction: an extra drawer
    # is recoverable; a missed handoff under genuine missed-dispatch
    # is not.
    return 0 <= delta < KEEPER_WINDOW_SECONDS


def _read_session_payload() -> str:
    """Drain stdin and return the tail content to preserve in the drawer.

    Claude Code's Stop / PreCompact hook protocol pipes a JSON envelope to
    stdin: ``{"session_id": ..., "transcript_path": ..., "stop_hook_active": ...}``.
    The transcript itself is a JSONL file at ``transcript_path`` — that's
    where the actual conversation content lives.

    Earlier revisions treated stdin as raw transcript text and filed the
    JSON envelope verbatim into the drawer — useless content. (Pass 3 Cat 17)

    Fallback: if stdin is empty or not JSON (manual standalone debug
    invocation, or a future protocol change), fall back to treating stdin
    itself as the payload. The fail-safe direction is "file something."
    """
    try:
        raw = sys.stdin.read() or ""
    except (OSError, ValueError):
        return ""

    raw_stripped = raw.lstrip()
    if not raw_stripped.startswith("{"):
        # Not a JSON envelope — treat as raw text (legacy / debug shape).
        return raw

    try:
        envelope = json.loads(raw_stripped)
    except (json.JSONDecodeError, ValueError):
        return raw

    if not isinstance(envelope, dict):
        return raw

    transcript_path = envelope.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        # Envelope without transcript_path: surface the envelope JSON as
        # a small drawer rather than nothing.
        return raw

    try:
        path = Path(transcript_path).expanduser()
    except (OSError, ValueError):
        return raw

    if not path.is_file():
        return raw

    return _tail_messages_from_transcript(path) or raw


def _tail_messages_from_transcript(path: Path) -> str:
    """Read a Claude Code JSONL transcript and return the last ~4k chars
    of role-tagged messages.

    Best-effort: malformed lines are skipped silently; OSError returns "".
    """
    pieces: list[str] = []
    total = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""

    # Walk from the end so we collect the most recent messages first.
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") or entry.get("event_message") or {}
        role = msg.get("role") if isinstance(msg, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if not isinstance(content, str) or not content.strip():
            continue
        line_str = f"[{role or 'msg'}] {content.strip()}\n"
        pieces.append(line_str)
        total += len(line_str)
        if total >= EMERGENCY_DRAWER_CHARS:
            break

    # We collected newest-first; reverse to chronological order.
    pieces.reverse()
    joined = "".join(pieces)
    return _tail_chars(joined)


def _tail_chars(text: str, n: int = EMERGENCY_DRAWER_CHARS) -> str:
    if len(text) <= n:
        return text
    return text[-n:]


def _file_emergency_drawer(wing: str, room: str, agents: list, content: str) -> bool:
    """Best-effort: file a drawer through tool_add_drawer. Returns True
    on success, False on any failure (the failure is logged to stderr
    so it shows up in Claude Code's hook output but does NOT propagate
    — pre-compact in particular must never cancel compaction).
    """
    try:
        # Import lazily so import-time failure (e.g., nook not installed
        # yet) doesn't break the hook entry point.
        from sage_mcp import mcp_server

        result = mcp_server.tool_add_drawer(
            wing=wing,
            room=room,
            content=content,
            source_file="",
            added_by="session-hook",
            agents=agents,
            # Both Stop and PreCompact emergency drawers land in the
            # "handoff" hall per the plan: that's where wake-up
            # retrieval looks for "what happened at the end of the
            # prior session" content. Room differs between hooks
            # (handoff vs handoff-precompact) but hall is shared so a
            # hall-scoped wake-up query picks them up uniformly.
            hall="handoff",
        )
        if not result.get("success"):
            print(
                f"sage hook: drawer write failed: {result.get('error')}",
                file=sys.stderr,
            )
            return False
        return True
    except Exception as exc:  # pragma: no cover — logged, never raised
        print(f"sage hook: emergency drawer raised {exc!r}", file=sys.stderr)
        return False


def run_session_hook(*, room: str, agent_tag: str) -> int:
    """Execute the shared session-hook body.

    ``room`` distinguishes Stop ("handoff") vs PreCompact
    ("handoff-precompact") destination halls on the drawer.
    ``agent_tag`` is the single agent string written into the drawer's
    agents list so later filters can find hook-written drawers.

    Returns 0 unconditionally — pre-compact callers depend on this.
    """
    try:
        # SAGE_HOOK_PROFILE kill-switch — checked before any I/O.
        # 'off' means all hook side-effects no-op; 'minimal' and 'standard'
        # both proceed (the emergency drawer is the safety floor for minimal).
        if _read_hook_profile() == _HOOK_PROFILE_OFF:
            return 0

        payload = _read_session_payload()

        wing = _current_wing()
        if wing is None:
            # Orchestrator hasn't advertised a wing — do nothing per the
            # current_wing contract. Common at session start before the
            # orchestrator has had a chance to write the sentinel.
            return 0

        if _keeper_handled_this_session():
            # Orchestrator dispatched the Keeper; structured drawers
            # were already written from dispatch points.
            return 0

        if not payload.strip():
            # No content to preserve — nothing to do.
            return 0

        drawer_content = _tail_chars(payload)
        # Opt-in secret-redaction (DEFAULT OFF — rules/memory-hygiene.md).
        # Scrub before filing so credential-shaped content does not persist
        # verbatim in the nook. Enabled by SAGE_REDACT_SECRETS=1 or config.
        drawer_content = _maybe_scrub(drawer_content)

        _file_emergency_drawer(
            wing=wing,
            room=room,
            agents=[agent_tag],
            content=drawer_content,
        )
    except Exception as exc:  # pragma: no cover
        # Never raise out of the hook — pre-compact #856 fix.
        print(f"sage hook: unexpected error {exc!r}", file=sys.stderr)
    return 0


__all__ = [
    "run_session_hook",
    "EMERGENCY_DRAWER_CHARS",
    "KEEPER_WINDOW_SECONDS",
    "_HOOK_PROFILE_ENV",
    "_HOOK_PROFILE_OFF",
    "_read_hook_profile",
]


# Allow running the module's tests in-place (helpful in tightly-scoped
# Phase 6 debug sessions); the real hook entry points are stop.py and
# precompact.py.
if __name__ == "__main__":
    # Sentinel demo mode: print which path each contract resolves to.
    sp_dir = _sage_dir()
    print(f"sage dir: {sp_dir}")
    print(f"current_wing: {_current_wing()!r}")
    print(f"keeper_handled_recently: {_keeper_handled_this_session()}")
