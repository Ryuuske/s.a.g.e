"""sage_mcp.estate.adapter.estate_model — Full Estate Model assembler (Phase 3).

Assembles the FULL schema-valid Estate Model from the Nook adapter (Phase 3)
and the Workshop adapter (Phase 1).  Schema-validates before returning.

The schema contract lives at:
  ``docs/projects/sage-estate-dashboard/estate-model.schema.json``

**Read-path safety (ADR-0003):**
- property.health mirrors collect_governance + collect_store_health directly.
- No drawer bodies; no absolute home paths; all titles redacted.
- Schema validation before return: a leaked field or new enum member is caught
  at the source (validation failure, not a silent emission).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import datetime, timezone
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from sage_mcp.estate.adapter.nook import (
    build_nook_building,
    estate_layout_lock,
    load_ledger,
    save_ledger,
    _default_ledger_path,
)
from sage_mcp.estate.adapter.workshop import build_workshop
from sage_mcp.estate.redact import redact_string


# ── Workshop slot ledger paths (call-time resolution, ADR-0095 C5) ────────────
# These are resolved at CALL TIME (None-sentinel in function signatures below)
# so a test-suite HOME redirect is honoured without a module reload.


def _default_workshop_ledger_path() -> pathlib.Path:
    """Return the default workshop agent-ledger path from the current HOME."""
    return pathlib.Path.home() / ".sage" / "estate-workshop-ledger.json"


def _default_workshop_bucket_path() -> pathlib.Path:
    """Return the default workshop bucket-ledger path from the current HOME."""
    return pathlib.Path.home() / ".sage" / "estate-workshop-buckets.json"


# ── Schema loading ─────────────────────────────────────────────────────────────

# The PACKAGED schema ships inside the wheel (src/sage_mcp/estate/), so installed
# users can validate without the repo's docs/ tree (which is NOT packaged —
# `packages = ["src/sage_mcp"]`). This is the runtime source of truth.
_PACKAGED_SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "estate-model.schema.json"
# The repo CONTRACT copy under docs/ (the human-facing + TS-side reference). Used
# only as a dev-tree fallback; kept byte-identical to the packaged copy by
# tests/estate/test_schema_packaging.py.
_REPO_SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "docs"
    / "projects"
    / "sage-estate-dashboard"
    / "estate-model.schema.json"
)
_SCHEMA_BASE_URI = "https://github.com/Ryuuske/s.a.g.e/docs/projects/sage-estate-dashboard/estate-model.schema.json"


def _load_schema() -> dict[str, Any]:
    """Load the estate model schema — packaged copy first, repo docs as fallback."""
    for path in (_PACKAGED_SCHEMA_PATH, _REPO_SCHEMA_PATH):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"estate-model schema not found at {_PACKAGED_SCHEMA_PATH} or {_REPO_SCHEMA_PATH}"
    )


def _build_validator(schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    """Build a Draft 2020-12 validator for *schema* with proper $ref resolution."""
    resource = Resource.from_contents(schema, DRAFT202012)
    registry = Registry().with_resource(_SCHEMA_BASE_URI, resource)
    return jsonschema.Draft202012Validator(schema, registry=registry)


def validate_estate_model(model: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
    """Validate *model* against the estate model schema.

    Raises ``jsonschema.ValidationError`` on any violation (leaked body field,
    missing required field, enum mismatch, etc.).  Called before the model is
    returned from any public entry-point.
    """
    if schema is None:
        schema = _load_schema()
    validator = _build_validator(schema)
    validator.validate(model)


# ── Revision counter ───────────────────────────────────────────────────────────

# Persist beside the slot ledger in the user's sage dir — NEVER in the package
# tree, which is read-only under a wheel install and would churn under git.
# Resolved at CALL TIME (None-sentinel) so a test-suite HOME redirect is
# honoured without a module reload (ADR-0095 C5).


def _default_revision_path() -> pathlib.Path:
    """Return the default revision-counter path from the current HOME."""
    return pathlib.Path.home() / ".sage" / "estate-revision.json"


# Module-level alias kept for backward compat with tests that monkeypatch
# ``em._REVISION_PATH`` directly.  _content_revision reads this attribute
# (not the inline call) so monkeypatched values propagate correctly.
_REVISION_PATH: pathlib.Path | None = None


def _content_revision(content: dict[str, Any], revision_path: pathlib.Path | None = None) -> int:
    """Return a revision that bumps ONLY when the semantic content changes.

    Phase-5 live-refresh compares ``revision`` (not ``captured_at``) to decide
    whether to re-fetch/re-layout. So an idle poll over unchanged state must
    return the SAME revision — incrementing every call made an idle dashboard
    re-layout continuously (PR #34 review). The revision is derived from a stable
    SHA-256 over *content* (everything except ``revision``/``captured_at``): if
    the fingerprint matches the persisted one, the prior revision is returned;
    otherwise it bumps by one and the new (revision, fingerprint) is persisted.

    Fails-soft to revision 0. ``revision_path`` defaults to ``None`` (sentinel)
    and is resolved at call time from the current HOME so a test-suite HOME
    redirect is honoured without a module reload (ADR-0095 C5).

    Resolution order: explicit arg → module-level ``_REVISION_PATH`` if set
    (kept for backward compat with tests that monkeypatch it) → call-time
    ``_default_revision_path()``.
    """
    if revision_path is None:
        # `is not None` (not truthiness): a monkeypatched Path must win even in
        # edge representations; only an unset (None) attr falls through (PMA-3).
        revision_path = _REVISION_PATH if _REVISION_PATH is not None else _default_revision_path()
    fingerprint = hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    try:
        prev: dict[str, Any] = {}
        if revision_path.is_file():
            loaded = json.loads(revision_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prev = loaded
        if prev.get("fingerprint") == fingerprint:
            return int(prev.get("revision", 0))  # unchanged → same revision
        rev = int(prev.get("revision", 0)) + 1 if prev else 0
        revision_path.parent.mkdir(parents=True, exist_ok=True)
        revision_path.write_text(
            json.dumps({"revision": rev, "fingerprint": fingerprint}), encoding="utf-8"
        )
        return rev
    except Exception:
        return 0


# ── Deep redaction ──────────────────────────────────────────────────────────────


def _deep_redact(obj: Any) -> Any:
    """Recursively run ``redact_string`` over every string VALUE in a nested
    structure (dict keys are structural and kept as-is).

    Scrubs home paths AND secret tokens (redact_string masks both). Used to
    harden the Workshop building before export — its adapter strips home paths
    from agent name/family/tools but does NOT run the secret-value redactor the
    rest of the export surface uses, so a hand-written agent ``name: sk-…`` or a
    tool entry containing ``ghp_…`` would otherwise leak (PR #34 review).
    """
    if isinstance(obj, str):
        return redact_string(obj)
    if isinstance(obj, dict):
        return {k: _deep_redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_redact(v) for v in obj]
    return obj


# ── Governance redaction ────────────────────────────────────────────────────────


def _redact_governance(gov: dict[str, Any]) -> dict[str, Any]:
    """Redact the user-derived open-map KEYS in the governance summary.

    ``collect_governance`` returns ``by_verdict: {verdict: count}`` and
    ``by_lane: {lane: {verdict: count}}``. A custom/hand-written audit lane name
    or a schema-drifted verdict string could carry a home path or token, and the
    schema allows arbitrary keys there — so, like store-health and hall maps
    (ADR-0008), these KEYS must be redacted before they reach the exported model
    (PR #34 review). Scalar fields are copied through unchanged.
    """
    from collections import Counter

    out = dict(gov)
    by_verdict = gov.get("by_verdict")
    if isinstance(by_verdict, dict):
        # SUM on collision — two keys that redact to the same string merge their
        # counts (a plain comprehension would drop the earlier one, making
        # total_verdicts disagree with by_verdict). ADR-0008 redaction-collapse.
        merged: Counter = Counter()
        for k, v in by_verdict.items():
            merged[redact_string(str(k))] += v
        out["by_verdict"] = dict(merged)
    by_lane = gov.get("by_lane")
    if isinstance(by_lane, dict):
        lanes: dict[str, Counter] = {}
        for lane, verdicts in by_lane.items():
            key = redact_string(str(lane))
            bucket = lanes.setdefault(key, Counter())
            if isinstance(verdicts, dict):
                for vk, vv in verdicts.items():
                    bucket[redact_string(str(vk))] += vv
        out["by_lane"] = {lane: dict(c) for lane, c in lanes.items()}
    return out


# ── Isolation flags ────────────────────────────────────────────────────────────


def _collect_isolation_flags() -> dict[str, bool]:
    """Collect WSL/distro isolation flags from the environment.

    Reads environment variables and /proc/version to detect WSL2 interop,
    Windows mount presence, and systemd state.  All booleans — never raw config.
    Fails soft: missing indicators return False.
    """

    flags: dict[str, bool] = {
        "windows_mounts": False,
        "interop": False,
        "systemd": False,
    }
    try:
        # Windows mounts: /mnt/c exists and resolves to a Windows drive
        flags["windows_mounts"] = pathlib.Path("/mnt/c").is_dir()
    except Exception:
        pass
    try:
        # WSL interop: check /proc/sys/fs/binfmt_misc/WSLInterop
        flags["interop"] = pathlib.Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    except Exception:
        pass
    try:
        # systemd: check if PID 1 is systemd
        pid1_comm = pathlib.Path("/proc/1/comm")
        flags["systemd"] = pid1_comm.is_file() and "systemd" in pid1_comm.read_text()
    except Exception:
        pass
    return flags


# ── Public API ─────────────────────────────────────────────────────────────────


def build_estate_model(
    metadata_rows: list[dict[str, Any]],
    wing_config: dict[str, Any],
    *,
    telemetry_rows: list[dict[str, Any]] | None = None,
    agents_dir: pathlib.Path | None = None,
    skills_dir: pathlib.Path | None = None,
    rules_dir: pathlib.Path | None = None,
    hooks_dir: pathlib.Path | None = None,
    kg_entities: int = 0,
    kg_relations: int = 0,
    closets_consolidated: int = 0,
    closets_decayed: int = 0,
    diaries: list[dict[str, Any]] | None = None,
    tunnels: list[dict[str, Any]] | None = None,
    ledger_path: pathlib.Path | None = None,
    workshop_ledger_path: pathlib.Path | None = None,
    workshop_bucket_path: pathlib.Path | None = None,
    validate: bool = True,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a complete, schema-valid Estate Model from live sage state.

    Parameters
    ----------
    metadata_rows:
        Drawer metadata rows from the Nook (no bodies).
    wing_config:
        Parsed ``wing_config.json``.
    telemetry_rows:
        Telemetry rows for governance health (``collect_governance`` input).
        Pass ``[]`` or ``None`` for an empty governance snapshot.
    agents_dir:
        Path to the agents directory for the Workshop building.
        None → empty workshop with no agents.
    skills_dir / rules_dir / hooks_dir:
        Paths for Workshop armory counts (None → 0).
    kg_entities / kg_relations:
        Knowledge-graph stats (stub 0 if unavailable).
    closets_consolidated / closets_decayed:
        Closets layer stats.
    diaries:
        Per-agent diary counts ``[{"agent": str, "entries": int}, ...]``.
    tunnels:
        List of tunnel dicts (schema ``tunnel`` $def).
    ledger_path:
        Path to the persisted slot ledger JSON.  Loaded at call time and
        saved after slot assignment.
    validate:
        If True (default), validate the assembled model before returning.
        Pass False only in tests that deliberately build invalid fixtures.
    schema:
        Override the schema dict (for test injection).

    Returns
    -------
    dict
        Complete schema-valid Estate Model.

    Raises
    ------
    jsonschema.ValidationError
        If ``validate=True`` and the assembled model violates the schema.
    """
    from sage_mcp.dashboard import collect_governance

    if telemetry_rows is None:
        telemetry_rows = []

    # ── Resolve None-sentinel paths at call time (ADR-0095 C5) ────────────────
    if ledger_path is None:
        ledger_path = _default_ledger_path()
    if workshop_ledger_path is None:
        workshop_ledger_path = _default_workshop_ledger_path()
    if workshop_bucket_path is None:
        workshop_bucket_path = _default_workshop_bucket_path()

    # ── Slot ledger + revision (load → assign → save → revision) ──────────────
    # Held under one cross-process lock so first-sight slot assignment AND the
    # revision read-modify-write are atomic — two concurrent builds can't assign
    # the same slot to different new nodes or emit a duplicate revision
    # (Codex adversarial review, fold round 2; ADR-0005).
    # Default the Workshop source dirs to the live ~/.claude tree (mirroring
    # agents_dir) so the armory counts reflect real skills/rules/hooks instead of
    # always reporting 0 on the MCP/CLI paths (PR #34 review). Under tests, HOME
    # is redirected so these resolve to an absent temp dir → 0, as before.
    _CLAUDE = pathlib.Path.home() / ".claude"
    resolved_agents_dir = agents_dir if agents_dir is not None else _CLAUDE / "agents"
    resolved_skills_dir = skills_dir if skills_dir is not None else _CLAUDE / "skills"
    resolved_rules_dir = rules_dir if rules_dir is not None else _CLAUDE / "rules"
    resolved_hooks_dir = hooks_dir if hooks_dir is not None else _CLAUDE / "hooks"

    with estate_layout_lock(ledger_path):
        # Nook slot ledger (atomic load → assign → save).
        ledger = load_ledger(ledger_path)
        nook_building, ledger = build_nook_building(
            metadata_rows,
            wing_config,
            ledger=ledger,
            kg_entities=kg_entities,
            kg_relations=kg_relations,
            closets_consolidated=closets_consolidated,
            closets_decayed=closets_decayed,
            diaries=diaries,
            tunnels=tunnels or [],
        )
        save_ledger(ledger, ledger_path)

        # Workshop building (Phase 1) — persist its agent + bucket ledgers under
        # the same lock so workshop slots are append-only stable too (ADR-0005;
        # PR #34 review). Rebuilding from a fresh ledger each call would reflow
        # the dashboard when an agent file is removed.
        wk_ledger = load_ledger(workshop_ledger_path)
        wk_buckets = load_ledger(workshop_bucket_path)
        workshop_building, wk_ledger, wk_buckets = build_workshop(
            resolved_agents_dir,
            skills_dir=resolved_skills_dir,
            rules_dir=resolved_rules_dir,
            hooks_dir=resolved_hooks_dir,
            ledger=wk_ledger,
            bucket_ledger=wk_buckets,
        )
        # Scrub Workshop strings (agent name/family/tools) through the full
        # redactor before export — the adapter strips home paths but not secret
        # tokens, and the model is a no-secret export surface (PR #34 review).
        workshop_building = _deep_redact(workshop_building)
        # Redaction can collapse two distinct agent ids to the same exported id
        # (e.g. /home/alice/agent and /home/bob/agent → ~/agent, or two sk-…
        # names → [REDACTED]). Dedup by final id so the export has no duplicate
        # Workshop node ids a renderer keyed on id would collide — mirroring the
        # Nook bucket merge (PR #34 review). Keeps the first (lowest-slot) node.
        _agents = workshop_building.get("agents")
        if isinstance(_agents, list):
            _seen: set = set()
            _deduped: list = []
            for _a in _agents:
                _aid = _a.get("id") if isinstance(_a, dict) else None
                if _aid in _seen:
                    continue
                _seen.add(_aid)
                _deduped.append(_a)
            workshop_building["agents"] = _deduped
        save_ledger(wk_ledger, workshop_ledger_path)
        save_ledger(wk_buckets, workshop_bucket_path)

        # ── property.health (governance map KEYS redacted — export surface) ──
        governance = _redact_governance(collect_governance(telemetry_rows))
        store_health = _compute_store_health_from_rows(metadata_rows)
        property_node: dict[str, Any] = {
            "name": "Sage",
            "isolation": _collect_isolation_flags(),
            "health": {"governance": governance, "store": store_health},
        }

        grounds: dict[str, Any] = {"plots": []}  # real repo scan is Phase 4
        outbuildings: dict[str, Any] = {
            "horrea": {"snapshots": []},
            "tablinum": {"config": {}},
            "gate": {"danger_actions": ["reset --hard", "push --force", "push --force-with-lease"]},
        }

        # Revision bumps ONLY when this content changes — derived under the lock
        # so the fingerprint read-modify-write is atomic with slot assignment.
        content: dict[str, Any] = {
            "property": property_node,
            "buildings": [nook_building, workshop_building],
            "grounds": grounds,
            "outbuildings": outbuildings,
        }
        revision = _content_revision(content)  # None sentinel → _default_revision_path()

    # ── Assemble (content + the two non-content fields) ───────────────────────
    model: dict[str, Any] = {
        "version": "1.0",
        "revision": revision,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **content,
    }

    # ── Schema validation ─────────────────────────────────────────────────────
    if validate:
        if schema is None:
            schema = _load_schema()
        validate_estate_model(model, schema)

    return model


def _compute_store_health_from_rows(metadata_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute a store_health dict from metadata rows already in memory.

    This avoids a second store open.  When called from the MCP tool the rows
    are already fetched; we compute the same shape as collect_store_health
    directly from them.  Matches the return SHAPE of collect_store_health
    (dashboard.py:166-174) — but the room/wing map KEYS are run through
    ``redact_string`` here because, unlike the local-terminal dashboard, the
    estate model is an exported/observed surface (ADR-0003: absolute home paths
    are stripped from the model JSON; the open maps can't be schema-constrained).
    """
    from collections import Counter

    if not metadata_rows:
        # An opened-but-empty store (e.g. after `sage init` before the first mine)
        # is HEALTHY, count 0 — mirroring collect_store_health, which returns
        # available:true after a successful open regardless of count (PR #34
        # review). build_estate_model is only reached after a successful open, so
        # empty rows mean an empty Nook, not an unavailable one.
        return {
            "available": True,
            "count": 0,
            "by_room": {},
            "by_wing": {},
            "strength": None,
        }

    by_room: Counter = Counter()
    by_wing: Counter = Counter()
    strengths: list[float] = []

    for row in metadata_rows:
        row = row or {}
        # `or "?"` (not `.get(.., "?")`) so a PRESENT-but-null/empty room/wing
        # lands in the ? bucket too — matching build_nook_building's
        # normalization, so malformed drawers aren't exported as "None"/"" keys
        # that the dashboard's unknown-bucket checks would miss (PR #34 review).
        by_room[redact_string(str(row.get("room") or "?"))] += 1
        by_wing[redact_string(str(row.get("wing") or "?"))] += 1
        s = row.get("strength")
        if isinstance(s, (int, float)):
            strengths.append(float(s))

    from sage_mcp.consolidation import DRAWER_STRENGTH_FLOOR

    strength_stats: dict[str, Any] | None = None
    if strengths:
        strength_stats = {
            "min": round(min(strengths), 4),
            "max": round(max(strengths), 4),
            "mean": round(sum(strengths) / len(strengths), 4),
            "at_or_below_floor": sum(1 for s in strengths if s <= DRAWER_STRENGTH_FLOOR),
            "below_floor": sum(1 for s in strengths if s < DRAWER_STRENGTH_FLOOR),
        }

    return {
        "available": True,
        "count": len(metadata_rows),
        "by_room": dict(by_room.most_common()),
        "by_wing": dict(by_wing.most_common()),
        "strength": strength_stats,
    }
