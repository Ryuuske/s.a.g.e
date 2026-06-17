"""Per-turn JSONL telemetry — the governance-metrics surface for the sage loop
(schema: ``docs/specs/telemetry.md``).

The orchestrator calls :func:`log_turn` after every specialist dispatch
or audit verdict. The log lives at ``~/.sage/telemetry/turns.jsonl``
by default (override via ``SAGE_TELEMETRY_PATH``). Each line is one
JSON object with the fields documented in
``docs/specs/telemetry.md``.

The file is append-only. The nook miner is taught to ingest it into the
``telemetry`` wing, ``turns`` hall, so existing recall paths (nook_recall,
sage recall) can query it like any other drawer.

This module never raises on I/O error — telemetry failures must not break
the orchestrator. Failures are silently dropped; the next session that
runs ``sage audit telemetry-health`` will surface them.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_LOG_PATH = Path.home() / ".sage" / "telemetry" / "turns.jsonl"

_lock = threading.Lock()


@dataclass
class TurnRecord:
    """One row in the telemetry log.

    Fields are deliberately flat — the file is meant to be mined into the
    nook and grep'd from the command line. Nested structures hurt both.
    """

    turn_id: str
    timestamp: str  # ISO 8601 UTC
    phase: str  # plan | dispatch | audit | implement | commit | self-check
    mode: str  # aidev | normal
    agent: str  # which agent produced the verdict, or "orchestrator"
    verdict: Optional[str] = None  # APPROVE | REQUEST_CHANGES | REJECT | HOLD | ABORT | None
    severity_top: int = 0  # max severity across findings in this turn
    findings_count: int = 0
    adr_produced: Optional[str] = None  # e.g., ".development/decisions/0017-foo.md" or None
    wing: Optional[str] = None  # current_wing at turn time, if known
    extras: dict = field(default_factory=dict)
    # extras is forward-compat opaque storage.  WI-3 / ADR-0039 registered keys:
    #   tier0_block_stable (bool | None):
    #                             True when the assembled Tier-0 block was
    #                             byte-identical to the prior recorded emission
    #                             (a determinism/stability indicator).
    #                             NOT a measure of an Anthropic prompt-cache read
    #                             — SessionStart-injected content is not documented
    #                             to receive prompt caching, and the real signal
    #                             (cache_read_input_tokens) is API-level only.
    #                             See ADR-0044.  None when no prior block is
    #                             available for comparison (first session for wing).
    #   tier0_tokens (int):       Estimated token count of the assembled Tier-0
    #                             block (len(block_text) // 4).  Measured by WI-7a.


def _log_path() -> Path:
    override = os.environ.get("SAGE_TELEMETRY_PATH")
    return Path(override) if override else DEFAULT_LOG_PATH


def _now_iso() -> str:
    """ISO 8601 UTC with millisecond precision.

    Millisecond resolution prevents same-second turns from collapsing to
    identical timestamps; ordering by ``timestamp`` then by ``turn_id``
    yields a stable sort across paired-auditor rows.
    """
    now = time.time()
    secs = int(now)
    msec = int((now - secs) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(secs)) + f".{msec:03d}Z"


def new_turn_id() -> str:
    """Short, unique-per-turn token. Not cryptographic."""
    return uuid.uuid4().hex[:12]


def log_turn(record: TurnRecord) -> bool:
    """Append one record to the telemetry JSONL.

    Returns ``True`` on success, ``False`` on any I/O or serialization
    failure. The orchestrator should not branch on the return value —
    it is for tests and the optional ``audit telemetry-health`` check.
    """
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(record), separators=(",", ":"), sort_keys=True)
        with _lock:
            # Telemetry rows carry verbatim verdict text (possibly secret-bearing);
            # restrict the dir + file to owner-only, umask-independent (ADR-0077).
            try:
                path.parent.chmod(0o700)
            except OSError:
                pass
            if not path.exists():
                path.touch()
            try:
                path.chmod(0o600)
            except OSError:
                pass
            with path.open("a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def log_from_verdict(
    parsed_verdict,  # sage.verdict_parser.Verdict
    *,
    phase: str,
    mode: str,
    wing: Optional[str] = None,
    adr_produced: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[str]:
    """Convenience: log a parsed Verdict directly.

    Returns the ``turn_id`` written, or ``None`` on failure. The
    orchestrator can pin the same ``turn_id`` across paired-auditor calls
    by passing it explicitly.
    """
    if turn_id is None:
        turn_id = new_turn_id()
    severity_top = max((f.severity for f in parsed_verdict.findings), default=0)
    record = TurnRecord(
        turn_id=turn_id,
        timestamp=_now_iso(),
        phase=phase,
        mode=mode,
        agent=parsed_verdict.lane or "?",
        verdict=parsed_verdict.verdict,
        severity_top=severity_top,
        findings_count=len(parsed_verdict.findings),
        adr_produced=adr_produced,
        wing=wing,
    )
    return turn_id if log_turn(record) else None


def log_tier0_wake_up(
    *,
    tier0_tokens: int,
    tier0_block_stable: Optional[bool],
    wing: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[str]:
    """Log one Tier-0 wake-up event with tier0_block_stable + tier0_tokens metrics.

    Records a ``phase=wake-up`` row so the WI-7a harness can retrieve
    the Tier-0 baseline from the telemetry log without parsing free text.

    Returns the ``turn_id`` written, or ``None`` on failure.

    Args:
        tier0_tokens:       Estimated token count of the assembled Tier-0 block.
        tier0_block_stable: True when the assembled Tier-0 block was byte-identical
                            to the prior recorded emission (a determinism/stability
                            indicator).  NOT a measure of an Anthropic prompt-cache
                            read — the real signal (cache_read_input_tokens) is
                            API-level and not exposed to SessionStart hooks.
                            See ADR-0044.  None when no prior block is available.
        wing:               Current wing slug at wake-up time, if known.
        turn_id:            Pin a specific turn_id; generated if None.
    """
    if turn_id is None:
        turn_id = new_turn_id()
    record = TurnRecord(
        turn_id=turn_id,
        timestamp=_now_iso(),
        phase="wake-up",
        mode="aidev",
        agent="sage",
        wing=wing,
        extras={
            "tier0_tokens": tier0_tokens,
            "tier0_block_stable": tier0_block_stable,
        },
    )
    return turn_id if log_turn(record) else None


def read_recent(limit: int = 50) -> list[dict]:
    """Tail the telemetry log. Returns at most ``limit`` records.

    Used by ``sage audit telemetry-health`` and by tests. Returns
    an empty list when the log is missing or unreadable.
    """
    path = _log_path()
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:]
