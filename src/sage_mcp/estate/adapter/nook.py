"""sage_mcp.estate.adapter.nook — Nook (Palace) building adapter (Phase 3).

Reads drawer metadata from the live Nook store (via the in-process
``_get_collection`` path or a synthetic metadata-row list for tests) and emits
the ``nook`` building dict conforming to the ``nook_building`` $def in
``estate-model.schema.json``.

**Read-path safety contract (ADR-0003):**
- All reads go through the in-process guarded-open path; never a concurrent
  second client.
- ``create=False`` on any direct collection open — never mutate-on-open.
- No drawer bodies are included in the output.
- All titles and labels are run through ``sage.estate.redact``.
- Schema-validation is the caller's responsibility (done in ``estate_model.py``
  and in the MCP tool before return).

**Slot-ledger semantics (ADR-0005):**
- Each node id (wing, room) is assigned a slot at first-sight via a PERSISTENT
  ledger at ``~/.sage/estate-layout-ledger.json``.
- Deleted ids leave a gap; their slot is NEVER reclaimed.
- Assignment is deterministic: nodes are processed in lexicographic order
  within each group so a fresh ledger always produces the same slots.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from sage_mcp.estate.redact import redact_string

# ── Constants ──────────────────────────────────────────────────────────────────

# _LEDGER_PATH is resolved at CALL TIME (None-sentinel in function defaults)
# so a test-suite HOME redirect is honoured without a module reload.
# See ADR-0095 C5 isolation hardening.  The sentinel is kept as a module-level
# name so consumers that import it (estate_model.py) can be updated to call
# _default_ledger_path() instead.


def _default_ledger_path() -> Path:
    """Return the default ledger path resolved from the current HOME."""
    return Path(os.path.expanduser("~/.sage/estate-layout-ledger.json"))


# The ? bucket used by dashboard.py for drawers whose wing/room is malformed.
_UNKNOWN_BUCKET = "?"

# Wing-type enum values from the schema + wing_config.json
_KNOWN_WING_TYPES: frozenset[str] = frozenset(
    ["dev", "project", "knowledge", "ops", "meta", "personal"]
)


# ── Ledger persistence ────────────────────────────────────────────────────────


def load_ledger(ledger_path: Path | None = None) -> dict[str, int]:
    """Load the persisted slot ledger from disk.

    ``ledger_path`` defaults to ``None`` (sentinel) and is resolved at call
    time from the current HOME so a test-suite HOME redirect is honoured
    without a module reload (ADR-0095 C5).

    Returns an empty dict if the file does not exist or is unreadable.
    Never raises: a missing/corrupt ledger starts fresh.
    """
    if ledger_path is None:
        ledger_path = _default_ledger_path()
    try:
        if ledger_path.is_file():
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Coerce values to int (JSON may have floats)
                return {k: int(v) for k, v in data.items() if isinstance(k, str)}
    except Exception:
        pass
    return {}


def save_ledger(ledger: dict[str, int], ledger_path: Path | None = None) -> None:
    """Persist the slot ledger to disk (append-only semantics: never shrink).

    ``ledger_path`` defaults to ``None`` (sentinel) and is resolved at call
    time from the current HOME so a test-suite HOME redirect is honoured
    without a module reload (ADR-0095 C5).

    If the file already exists, merge: keys in the on-disk ledger that are
    absent from *ledger* are preserved (deleted-id gap semantics — once a slot
    is assigned it is never reclaimed, even if the caller didn't know about it).
    Atomic write via a temp-file rename so a crash mid-write doesn't corrupt.
    """
    if ledger_path is None:
        ledger_path = _default_ledger_path()
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        # Merge with existing (append-only: never remove existing entries)
        on_disk = load_ledger(ledger_path)
        merged = {**on_disk, **ledger}
        tmp_path = ledger_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
        # os.replace overwrites atomically on POSIX *and* Windows; Path.rename
        # raises FileExistsError on Windows when the target exists, which the
        # bare except below would silently swallow — freezing the ledger at its
        # first write and risking the duplicate-slot state ADR-0005 forbids.
        os.replace(tmp_path, ledger_path)
    except Exception:
        # Fail-soft: slot persistence is best-effort; model generation continues.
        # Best-effort cleanup so a failed write doesn't leave a .tmp sidecar.
        try:
            tmp_path.unlink()
        except (OSError, NameError):
            pass


# ── Ledger lock (cross-process slot-assignment serialization) ──────────────────


@contextlib.contextmanager
def estate_layout_lock(ledger_path: Path | None = None):
    """Blocking exclusive lock serializing the slot-ledger read-modify-write.

    Two concurrent estate builds (e.g. the in-process ``nook_estate`` MCP tool
    and a ``sage estate`` CLI run) would otherwise both ``load_ledger`` the same
    state, both assign the same first-sight slot to *different* new nodes, and
    merge to a DUPLICATE slot — which ADR-0005 forbids and the schema cannot
    catch (slots aren't constrained unique). Holding this lock across
    load → assign → save → revision makes first-sight assignment atomic across
    processes.

    Fails OPEN (yields without a lock) if the lock dir is unwritable — degraded
    determinism beats a crash for a read-only view.
    """
    if ledger_path is None:
        ledger_path = _default_ledger_path()
    lock_dir = ledger_path.parent / "locks"
    lf = None
    acquired = False
    try:
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
            lf = open(lock_dir / "estate-layout.lock", "w")
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lf, fcntl.LOCK_EX)
            acquired = True
        except Exception:
            pass  # fail-open
        yield
    finally:
        if lf is not None:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lf, fcntl.LOCK_UN)
                except Exception:
                    pass
            lf.close()


# ── Slot assignment ────────────────────────────────────────────────────────────


def _assign_slot(node_id: str, ledger: dict[str, int]) -> int:
    """Return the slot for *node_id*, assigning one at first-sight.

    The ledger is mutated in place.  A deleted node's slot is NEVER reclaimed:
    new nodes always take ``max(current_slots) + 1`` (or 0 on an empty ledger).
    """
    if node_id in ledger:
        return ledger[node_id]
    next_slot = max(ledger.values()) + 1 if ledger else 0
    ledger[node_id] = next_slot
    return next_slot


# ── Wing-type resolution ───────────────────────────────────────────────────────


def _resolve_wing_type(wing_name: str, wing_config: dict[str, Any]) -> str:
    """Resolve the wing_type for *wing_name* from wing_config.

    Checks the ``wings`` dict for an explicit type, then checks if the
    wing_name itself is a known type key.  Falls back to ``"unknown"`` for
    unregistered wings, matching ADR-0004 and dashboard.py `?` bucket handling.
    """
    # wing_config has a "wings" key mapping wing names to {type: ..., path: ...}
    wings_map = wing_config.get("wings", {})
    if wing_name in wings_map:
        wtype = wings_map[wing_name].get("type", "")
        if wtype in _KNOWN_WING_TYPES:
            return wtype
    # Try wing_name itself as a known type (e.g. wing name == "dev")
    if wing_name in _KNOWN_WING_TYPES:
        return wing_name
    # The ? bucket is always unknown
    if wing_name == _UNKNOWN_BUCKET:
        return "unknown"
    return "unknown"


# ── Sort key ─────────────────────────────────────────────────────────────────


def _bucket_sort_key(name: str) -> tuple[int, str]:
    """Sort lexicographically, with the ``?`` bucket always last."""
    return (1 if name == _UNKNOWN_BUCKET else 0, name)


# ── Tunnel mapping (shared by the MCP tool + the CLI) ───────────────────────────


def _derive_diaries(metadata_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-agent diary counts from drawer metadata (room == 'diary').

    Diary drawers carry ``agent`` + ``room == "diary"`` (the same fields
    ``tool_diary_read`` filters on), so counts come from the already-fetched
    metadata with no extra I/O. Agent names are redacted (export surface); the
    result is sorted by agent for deterministic output.
    """
    counts: Counter = Counter()
    for row in metadata_rows:
        row = row or {}
        if str(row.get("room") or "").strip() == "diary":
            agent = str(row.get("agent") or "").strip()
            if agent:
                counts[redact_string(agent)] += 1
    return [{"agent": a, "entries": n} for a, n in sorted(counts.items())]


def build_tunnels(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Map raw nook_graph tunnel records → schema-shaped, redacted, sorted edges.

    Records are ``{id, source:{wing,room}, target:{wing,room}, label}`` (the
    ``nook_graph.list_tunnels`` / ``tunnels.json`` shape). Endpoint wing ids
    mirror the nook builder's redacted ids so the renderer can link them; the
    name and the fallback id are redacted; the endpoint pair is canonical-sorted
    (schema: UNORDERED). Shared by BOTH the ``nook_estate`` MCP tool and the
    ``sage estate`` CLI so the two paths can't drift (PR #34 review).
    """
    out: list[dict[str, Any]] = []
    for t in records or []:
        if not isinstance(t, dict):
            continue
        # Coerce non-dict source/target to {} so ONE malformed record (e.g. a
        # hand-edited `"source": "dev"`) is skipped individually rather than
        # raising and dropping EVERY tunnel via the caller's outer except
        # (PR #34 review). `or {}` wouldn't help — a truthy non-dict survives it.
        src = t.get("source")
        tgt = t.get("target")
        src = src if isinstance(src, dict) else {}
        tgt = tgt if isinstance(tgt, dict) else {}
        src_w = redact_string(str(src.get("wing", "")))
        src_r = redact_string(str(src.get("room", "")))
        tgt_w = redact_string(str(tgt.get("wing", "")))
        tgt_r = redact_string(str(tgt.get("room", "")))
        if not (src_w and tgt_w):
            continue
        # If the two endpoints redact to the SAME wing id (e.g. /home/alice/proj
        # and /home/bob/proj → wing:~/proj), the tunnel is no longer a cross-wing
        # passage — the nook builder merges those wings into one node, so emitting
        # it would be a bogus self-loop ["wing:~/proj","wing:~/proj"]. Skip it
        # (PR #34 review — redaction-collapse follow-on).
        if src_w == tgt_w:
            continue
        # A custom/hand-edited id is user-derived → redact it too (the schema only
        # checks it's a string; generated hash ids are unaffected). ADR-0008.
        tid = t.get("id")
        tunnel_id = redact_string(str(tid)) if tid else f"tunnel:{src_w}:{src_r}__{tgt_w}:{tgt_r}"
        out.append(
            {
                "id": tunnel_id,
                "name": redact_string(str(t.get("label", ""))),
                "endpoints": sorted([f"wing:{src_w}", f"wing:{tgt_w}"]),
            }
        )
    return out


# ── Core builder ───────────────────────────────────────────────────────────────


def build_nook_building(
    metadata_rows: list[dict[str, Any]],
    wing_config: dict[str, Any],
    *,
    ledger: dict[str, int] | None = None,
    kg_entities: int = 0,
    kg_relations: int = 0,
    closets_consolidated: int = 0,
    closets_decayed: int = 0,
    diaries: list[dict[str, Any]] | None = None,
    tunnels: list[dict[str, Any]] | None = None,
    title: str = "The Nook",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Build the ``nook`` building dict from drawer metadata rows.

    Parameters
    ----------
    metadata_rows:
        List of metadata dicts, one per drawer, each with at least ``wing`` and
        ``room`` keys.  Additional keys ``hall``, ``agent``, ``strength``,
        ``source_file`` may be present.  NO ``document`` / ``body`` fields —
        those are never fetched.
    wing_config:
        Parsed ``wing_config.json`` dict.  Used for wing-type resolution.
    ledger:
        Existing id→slot mapping from a prior run.  Pass ``{}`` or ``None`` for
        a fresh run.  The ledger is updated in-place and returned.
    kg_entities:
        Knowledge-graph entity count (from ``nook_kg_stats`` or stub 0).
    kg_relations:
        Knowledge-graph relation count.
    closets_consolidated:
        Closets layer: number of consolidated drawers.
    closets_decayed:
        Closets layer: number of decayed drawers.
    diaries:
        List of ``{"agent": str, "entries": int}`` dicts, or None/[].
    tunnels:
        List of tunnel dicts conforming to the schema ``tunnel`` $def.
        If None, an empty list is used.
    title:
        Display title for the building.

    Returns
    -------
    (nook_dict, updated_ledger)
        *nook_dict* conforms to the ``nook_building`` $def.
        *updated_ledger* is the (mutated) slot ledger — persist for stable
        slots on the next call.

    Security notes (ADR-0003):
    - No document bodies are accessed or emitted.
    - All title strings are run through ``redact_string`` before emission.
    - The ? bucket is always carried, never hidden.
    """
    if ledger is None:
        ledger = {}
    if tunnels is None:
        tunnels = []
    if diaries is None:
        # Derive per-agent diary counts from the metadata we already have — diary
        # drawers carry agent + room="diary" (the filter tool_diary_read uses), so
        # no extra I/O is needed and it works for both the MCP and CLI paths
        # (PR #34 review). Agent names are user-derived → redacted. Pass an
        # explicit list to override (tests).
        diaries = _derive_diaries(metadata_rows)

    # ── 1. Group drawers by REDACTED wing, then REDACTED room ────────────────
    # Group by the SAFE (redacted) name so two raw names that redact to the same
    # id — e.g. ``/home/alice/proj`` and ``/home/bob/proj`` both → ``~/proj`` —
    # MERGE into one node instead of emitting duplicate ids/slots that a
    # renderer keyed on id would collide (PR #34 review). The raw wing names are
    # tracked per bucket because wing_config keys are raw (needed for type).
    wings_data: dict[str, dict[str, list[dict[str, Any]]]] = {}
    wing_raw_names: dict[str, list[str]] = {}
    for row in metadata_rows:
        row = row or {}
        raw_wing = str(row.get("wing") or _UNKNOWN_BUCKET).strip() or _UNKNOWN_BUCKET
        raw_room = str(row.get("room") or _UNKNOWN_BUCKET).strip() or _UNKNOWN_BUCKET
        safe_wing = redact_string(raw_wing)
        safe_room = redact_string(raw_room)
        wings_data.setdefault(safe_wing, {}).setdefault(safe_room, []).append(row)
        raws = wing_raw_names.setdefault(safe_wing, [])
        if raw_wing not in raws:
            raws.append(raw_wing)

    # ── 2. Build wings (sorted lex for determinism; ? bucket last) ───────────
    wing_nodes: list[dict[str, Any]] = []
    for safe_wing_name in sorted(wings_data.keys(), key=_bucket_sort_key):
        rooms_data = wings_data[safe_wing_name]
        # Already redacted (grouped by safe name); the id/title use it directly.
        wing_id = f"wing:{safe_wing_name}"
        wing_slot = _assign_slot(wing_id, ledger)
        # Resolve wing-type from the raw name(s) in this bucket — prefer a known
        # type over "unknown" when distinct raw names collided on redaction.
        wing_type = "unknown"
        for raw in sorted(wing_raw_names.get(safe_wing_name, [])):
            resolved = _resolve_wing_type(raw, wing_config)
            if resolved != "unknown":
                wing_type = resolved
                break

        # ── 2a. hall_counts (open map keyed by hall name) ────────────────────
        # Keys are user-derived → redact before emission (a hall name could carry
        # a home path / secret token; the open map can't be schema-constrained).
        hall_counter: Counter = Counter()
        for room_rows in rooms_data.values():
            for row in room_rows:
                hall = str(row.get("hall") or "").strip()
                if hall:
                    hall_counter[redact_string(hall)] += 1

        # ── 2b. Rooms (sorted lex; ? bucket last) ────────────────────────────
        room_nodes: list[dict[str, Any]] = []
        drawer_total = 0
        for safe_room_name in sorted(rooms_data.keys(), key=_bucket_sort_key):
            room_rows = rooms_data[safe_room_name]
            room_id = f"room:{safe_wing_name}:{safe_room_name}"
            room_slot = _assign_slot(room_id, ledger)
            drawer_count = len(room_rows)
            drawer_total += drawer_count
            room_node: dict[str, Any] = {
                "id": room_id,
                "title": safe_room_name,
                "slot": room_slot,
                "drawer_count": drawer_count,
            }
            room_nodes.append(room_node)

        wing_node: dict[str, Any] = {
            "id": wing_id,
            "type": wing_type,
            "title": safe_wing_name,
            "slot": wing_slot,
            "rooms": room_nodes,
            "hall_counts": dict(hall_counter),
            "drawer_total": drawer_total,
        }
        wing_nodes.append(wing_node)

    # ── 3. Assemble building dict ─────────────────────────────────────────────
    nook: dict[str, Any] = {
        "id": "nook",
        "kind": "palace",
        "title": redact_string(title),
        "wings": wing_nodes,
        "tunnels": tunnels,
        "closets": {
            "consolidated": closets_consolidated,
            "decayed": closets_decayed,
        },
        "kg": {
            "entities": kg_entities,
            "relations": kg_relations,
        },
    }

    # diaries is optional in the schema; only include if non-empty
    if diaries:
        nook["diaries"] = diaries

    return nook, ledger
