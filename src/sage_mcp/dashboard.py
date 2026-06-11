"""Basic telemetry + governance + store-health dashboard for sage.

Phase-9 deliverable: a single read-only health view that surfaces two signals
the framework otherwise leaves invisible —

1. **Governance telemetry** from the verdict-log (``~/.sage/telemetry/turns.jsonl``,
   schema ``docs/specs/telemetry.md``): how many audit verdicts, the verdict mix,
   blocking-finding rate, per-lane verdict quality, and paired-auditor disagreement.
2. **Nook store health**: drawer count, room/wing distribution, and decay-strength
   stats (the ADR-0043 floor health).

This is the BASIC dashboard the run contract calls for — a text render over the
collected signals. The elaborate visualization surface is deliberately out of
scope (a later, separate effort). The collectors are pure functions over their
inputs so they are testable without a live store.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from typing import Optional

# Canonical verdict enum (docs/specs/verdict-schema.md). Blocking severity floor
# is the §16 threshold.
_VERDICTS = ("APPROVE", "REQUEST_CHANGES", "REJECT", "HOLD", "ABORT")
_BLOCKING_SEVERITY = 80


def _safe_int(value) -> int:
    """Coerce a telemetry value to int, returning 0 for missing/unparseable.

    ``read_recent`` filters invalid JSON but not schema-invalid rows; a corrupt
    or schema-drifted ``severity_top`` ("high", "85.5", null) must degrade the
    one row, not crash the whole dashboard.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def collect_governance(rows: list[dict]) -> dict:
    """Summarize verdict-log telemetry rows into governance signals.

    Pure function over the parsed ``turns.jsonl`` rows (see
    ``sage_mcp.telemetry.read_recent``). Returns a flat summary dict.
    """
    audit_rows = [r for r in rows if r.get("phase") == "audit"]
    by_verdict: Counter = Counter()
    by_lane: defaultdict = defaultdict(Counter)
    blocking = 0
    for r in audit_rows:
        v = r.get("verdict")
        if v:
            by_verdict[v] += 1
        agent = r.get("agent") or "unknown"
        if v:
            by_lane[agent][v] += 1
        if _safe_int(r.get("severity_top")) >= _BLOCKING_SEVERITY:
            blocking += 1

    # Paired-auditor signals, keyed by turn_id, counting DISTINCT LANES (not raw
    # rows): the orchestrator pins one turn_id across a paired-auditor dispatch,
    # but the append-only log + retries mean the SAME lane may log a turn_id more
    # than once. A turn is "paired" only when ≥2 DISTINCT auditor lanes logged it;
    # a "disagreement" is a paired turn whose distinct lanes emitted differing
    # verdicts. Within one lane on one turn, the LAST-logged verdict wins (latest
    # row in the append-only file), so a same-lane retry does not fake a pair.
    turn_lane_verdict: defaultdict = defaultdict(dict)  # turn_id -> {lane: verdict}
    for r in audit_rows:
        tid = r.get("turn_id")
        v = r.get("verdict")
        if tid and v:
            turn_lane_verdict[tid][r.get("agent") or "unknown"] = v
    paired_turns = sum(1 for lanes in turn_lane_verdict.values() if len(lanes) >= 2)
    disagreements = sum(
        1
        for lanes in turn_lane_verdict.values()
        if len(lanes) >= 2 and len(set(lanes.values())) > 1
    )

    total_verdicts = sum(by_verdict.values())
    approve = by_verdict.get("APPROVE", 0)
    return {
        "total_rows": len(rows),
        "audit_rows": len(audit_rows),
        "total_verdicts": total_verdicts,
        "by_verdict": dict(by_verdict),
        "by_lane": {lane: dict(c) for lane, c in by_lane.items()},
        "blocking_findings": blocking,
        "blocking_rate": (blocking / total_verdicts) if total_verdicts else None,
        "approve_rate": (approve / total_verdicts) if total_verdicts else None,
        "paired_turns": paired_turns,
        "disagreements": disagreements,
    }


def collect_store_health(nook_path: str, collection_name: str) -> dict:
    """Read drawer-store health from the Nook. Degrades gracefully.

    Opens the collection through the project's GUARDED path
    (``nook._open_collection_or_explain``) rather than constructing a raw
    ``chromadb.PersistentClient`` — that helper rejects a missing
    ``chroma.sqlite3`` before any open (so a read-only health check never
    mutates an empty nook dir) and routes through the backend's stale-HNSW
    quarantine (so a corrupt store reports rather than segfaults). Returns
    ``{"available": False, ...}`` for every not-healthy state so the dashboard
    renders the governance half even without a live Nook.
    """
    try:
        from .nook import _open_collection_or_explain
    except ImportError:
        return {"available": False, "reason": "store backend unavailable"}

    messages: list[str] = []
    col = _open_collection_or_explain(
        nook_path, collection_name=collection_name, out=messages.append
    )
    if col is None:
        reason = " ".join(m.strip() for m in messages if m.strip()) or "nook not available"
        return {"available": False, "reason": reason}
    from .consolidation import DRAWER_STRENGTH_FLOOR

    by_room: Counter = Counter()
    by_wing: Counter = Counter()
    strengths: list[float] = []
    try:
        count = col.count()
        # Paginate (matching sage status) to avoid the SQLite "too many SQL
        # variables" / large-nook failure modes a single full fetch hits.
        batch_size = 5000
        offset = 0
        while offset < count:
            batch = (
                col.get(limit=batch_size, offset=offset, include=["metadatas"]).get("metadatas")
                or []
            )
            if not batch:
                break
            for m in batch:
                m = m or {}  # Chroma can return None metadata rows; normalize like status
                by_room[m.get("room", "?")] += 1  # "?" fallback bucket, matching sage status
                by_wing[m.get("wing", "?")] += 1
                s = m.get("strength")
                if isinstance(s, (int, float)):
                    strengths.append(s)
            offset += len(batch)
    except Exception as exc:  # noqa: BLE001 — any store-read failure degrades gracefully
        return {"available": False, "reason": str(exc)}

    strength_stats = None
    if strengths:
        strength_stats = {
            "min": round(min(strengths), 4),
            "max": round(max(strengths), 4),
            "mean": round(sum(strengths) / len(strengths), 4),
            # ADR-0043 drawer-level floor (0.1): drawers AT the floor are healthy
            # (decayed to but not past it); drawers BELOW it indicate corruption.
            "at_or_below_floor": sum(1 for s in strengths if s <= DRAWER_STRENGTH_FLOOR),
            "below_floor": sum(1 for s in strengths if s < DRAWER_STRENGTH_FLOOR),
        }
    return {
        "available": True,
        "count": count,
        # Return ALL room/wing buckets (sorted by count, like sage status) — never
        # truncate, so the "?" unknown/malformed bucket can never be hidden.
        "by_room": dict(by_room.most_common()),
        "by_wing": dict(by_wing.most_common()),
        "strength": strength_stats,
    }


def render(governance: dict, store: dict) -> str:
    """Render the collected signals as a text dashboard."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  sage — governance + store health dashboard")
    lines.append("=" * 60)

    lines.append("")
    lines.append("  GOVERNANCE (verdict-log telemetry)")
    g = governance
    lines.append(f"    rows: {g['total_rows']}  audit-verdicts: {g['total_verdicts']}")
    if g["total_verdicts"]:
        mix = "  ".join(
            f"{v}={g['by_verdict'].get(v, 0)}" for v in _VERDICTS if g["by_verdict"].get(v)
        )
        lines.append(f"    verdict mix: {mix or '(none)'}")
        rate = g["approve_rate"]
        lines.append(
            f"    approve-rate: {rate:.0%}" if rate is not None else "    approve-rate: n/a"
        )
        brate = g["blocking_rate"]
        brate_str = f" ({brate:.0%} of verdicts)" if brate is not None else ""
        lines.append(f"    blocking findings (sev>=80): {g['blocking_findings']}{brate_str}")
        lines.append(
            f"    paired-auditor turns: {g['paired_turns']}  disagreements: {g['disagreements']}"
        )
        if g["by_lane"]:
            lines.append("    by lane:")
            for lane, c in sorted(g["by_lane"].items()):
                summary = " ".join(f"{k}={v}" for k, v in c.items())
                lines.append(f"      {lane}: {summary}")
    else:
        lines.append("    (no audit verdicts logged yet — run the loop with `sage verdict log`)")

    lines.append("")
    lines.append("  STORE HEALTH (Nook)")
    if not store.get("available"):
        lines.append(f"    (store unavailable: {store.get('reason', 'unknown')})")
    else:
        lines.append(f"    drawers: {store['count']}")
        rooms = "  ".join(f"{k}={v}" for k, v in store["by_room"].items())
        lines.append(f"    rooms: {rooms or '(none)'}")
        wings = "  ".join(f"{k}={v}" for k, v in store["by_wing"].items())
        lines.append(f"    wings: {wings or '(none)'}")
        s = store.get("strength")
        if s:
            lines.append(
                f"    strength: min={s['min']} mean={s['mean']} max={s['max']}"
                f"  at-or-below-floor(0.1)={s['at_or_below_floor']} below-floor={s['below_floor']}"
            )
    lines.append("=" * 60)
    return "\n".join(lines)


def dashboard(nook_path: Optional[str] = None, telemetry_limit: int = 1000) -> str:
    """Collect + render + print the dashboard. Returns the rendered text."""
    from .config import SageConfig
    from .telemetry import read_recent

    cfg = SageConfig()
    resolved_nook = os.path.abspath(os.path.expanduser(nook_path)) if nook_path else cfg.nook_path
    rows = read_recent(limit=telemetry_limit)
    governance = collect_governance(rows)
    store = collect_store_health(resolved_nook, cfg.collection_name)
    text = render(governance, store)
    print(text)
    return text
