"""Tests for sage.estate.adapter.estate_model — full Estate Model assembler (Phase 3).

Covers:
1. Full model validates against the schema.
2. property.health.governance matches collect_governance shape.
3. property.health.store matches collect_store_health shape.
4. Both buildings (nook + workshop) are present.
5. Slots are stable across two builds with a persisted ledger.
6. A deleted wing leaves a gap (slot never reclaimed).
7. No home paths or secrets in the emitted model.
8. validate=True raises on a deliberately broken model.
9. build_estate_model graceful: empty metadata rows produces available:false store.
10. nook building has wings with correct types incl. 'unknown' for unregistered wings.

WHERE: tests/estate/test_estate_model.py
"""

import json
import pathlib

import jsonschema
import pytest

from sage_mcp.estate.adapter.estate_model import (
    build_estate_model,
    validate_estate_model,
    _compute_store_health_from_rows,
)
from sage_mcp.estate.adapter.nook import load_ledger

# ── Paths + schema ─────────────────────────────────────────────────────────────

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_SCHEMA_PATH = _ROOT / "docs" / "projects" / "sage-estate-dashboard" / "estate-model.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_AGENTS_FIXTURES = _HERE / "fixtures" / "workshop" / "agents"

# ── Shared fixtures ────────────────────────────────────────────────────────────

_WING_CONFIG = {
    "version": 1,
    "wing_types": {
        "dev": {"halls": ["handoff", "in-flight", "audits", "decisions", "plans", "facts"]},
        "project": {"halls": ["handoff", "in-flight", "plans", "facts", "milestones"]},
        "knowledge": {"halls": ["facts"]},
        "ops": {"halls": ["facts"]},
        "meta": {"halls": ["turns"]},
        "personal": {"halls": ["core", "detail"]},
    },
    "wings": {
        "telemetry": {"type": "meta", "path": "~/.sage/telemetry"},
        "Personal": {"type": "personal", "path": "~/.sage/personal"},
    },
}

# Synthetic metadata rows (no live store, no real paths)
_METADATA_ROWS = [
    {"wing": "dev", "room": "main", "hall": "handoff", "strength": 0.9},
    {"wing": "dev", "room": "archive", "hall": "in-flight", "strength": 0.5},
    {"wing": "project", "room": "planning", "hall": "plans", "strength": 0.7},
    {"wing": "Personal", "room": "core", "hall": "core", "strength": 1.0},
    {"wing": "xyzzy-unknown-wing", "room": "misc", "hall": "facts", "strength": 0.3},
    {"wing": "?", "room": "?", "hall": "", "strength": 0.2},
    {"wing": None, "room": None, "hall": None, "strength": 0.1},
]

# Minimal telemetry rows for governance health
_TELEMETRY_ROWS = [
    {
        "phase": "audit",
        "verdict": "APPROVE",
        "agent": "dev-code-reviewer",
        "severity_top": 20,
        "turn_id": "t1",
    },
    {
        "phase": "audit",
        "verdict": "APPROVE",
        "agent": "sec-auditor",
        "severity_top": 30,
        "turn_id": "t1",
    },
    {
        "phase": "audit",
        "verdict": "REQUEST_CHANGES",
        "agent": "dev-code-reviewer",
        "severity_top": 85,
        "turn_id": "t2",
    },
    {
        "phase": "audit",
        "verdict": "APPROVE",
        "agent": "sec-auditor",
        "severity_top": 10,
        "turn_id": "t2",
    },
]


# ── Helper: build with tmp ledger ─────────────────────────────────────────────


def _build_with_tmp_ledger(tmp_path: pathlib.Path, **kwargs) -> dict:
    """Build an estate model using a fresh temp ledger."""
    ledger_path = tmp_path / "ledger.json"
    return build_estate_model(
        _METADATA_ROWS,
        _WING_CONFIG,
        telemetry_rows=_TELEMETRY_ROWS,
        agents_dir=_AGENTS_FIXTURES,
        ledger_path=ledger_path,
        **kwargs,
    )


# ── 1. Full model validates against the schema ────────────────────────────────


def test_full_model_validates(tmp_path):
    """The assembled estate model must be valid per the contract schema."""
    model = _build_with_tmp_ledger(tmp_path, schema=_SCHEMA)
    validate_estate_model(model, _SCHEMA)


# ── 2. Top-level shape ────────────────────────────────────────────────────────


def test_model_required_top_level_keys(tmp_path):
    """Model has all required top-level keys."""
    model = _build_with_tmp_ledger(tmp_path)
    for key in (
        "version",
        "revision",
        "captured_at",
        "property",
        "buildings",
        "grounds",
        "outbuildings",
    ):
        assert key in model, f"Missing required key: {key}"


def test_version_is_one_point_zero(tmp_path):
    """version is '1.0'."""
    model = _build_with_tmp_ledger(tmp_path)
    assert model["version"] == "1.0"


def test_revision_is_non_negative_int(tmp_path):
    """revision is a non-negative integer."""
    model = _build_with_tmp_ledger(tmp_path)
    assert isinstance(model["revision"], int)
    assert model["revision"] >= 0


# ── 3. property.health.governance ─────────────────────────────────────────────


def test_governance_health_shape(tmp_path):
    """property.health.governance has the exact collect_governance return shape."""
    from sage_mcp.dashboard import collect_governance

    model = _build_with_tmp_ledger(tmp_path)
    gov = model["property"]["health"]["governance"]
    expected_keys = set(collect_governance(_TELEMETRY_ROWS).keys())
    assert set(gov.keys()) == expected_keys, (
        f"governance shape mismatch.\n"
        f"  In model only: {set(gov.keys()) - expected_keys}\n"
        f"  In collector only: {expected_keys - set(gov.keys())}"
    )


def test_governance_values_match_collector(tmp_path):
    """property.health.governance values match collect_governance output."""
    from sage_mcp.dashboard import collect_governance

    model = _build_with_tmp_ledger(tmp_path)
    gov = model["property"]["health"]["governance"]
    expected = collect_governance(_TELEMETRY_ROWS)
    assert gov["total_verdicts"] == expected["total_verdicts"]
    assert gov["blocking_findings"] == expected["blocking_findings"]
    assert gov["paired_turns"] == expected["paired_turns"]


def test_governance_blocking_finding_detected(tmp_path):
    """A row with severity_top >= 80 is counted as a blocking finding."""
    model = _build_with_tmp_ledger(tmp_path)
    gov = model["property"]["health"]["governance"]
    assert gov["blocking_findings"] >= 1


# ── 4. property.health.store ──────────────────────────────────────────────────


def test_store_health_available_when_rows_present(tmp_path):
    """store.available is True when metadata rows are present."""
    model = _build_with_tmp_ledger(tmp_path)
    store = model["property"]["health"]["store"]
    assert store["available"] is True


def test_store_health_count_matches_rows(tmp_path):
    """store.count equals the number of metadata rows."""
    model = _build_with_tmp_ledger(tmp_path)
    store = model["property"]["health"]["store"]
    assert store["count"] == len(_METADATA_ROWS)


def test_store_health_by_wing_includes_question_mark(tmp_path):
    """store.by_wing includes the '?' bucket (never hidden)."""
    model = _build_with_tmp_ledger(tmp_path)
    by_wing = model["property"]["health"]["store"]["by_wing"]
    assert "?" in by_wing, "? bucket must be in by_wing, never hidden"


def test_store_health_strength_stats(tmp_path):
    """store.strength has min/max/mean/at_or_below_floor/below_floor when rows have strength."""
    model = _build_with_tmp_ledger(tmp_path)
    strength = model["property"]["health"]["store"]["strength"]
    assert strength is not None
    for key in ("min", "max", "mean", "at_or_below_floor", "below_floor"):
        assert key in strength, f"Missing strength key: {key}"


def test_store_health_available_true_when_empty_initialized(tmp_path):
    """An opened-but-empty store is HEALTHY with count 0 — not unavailable
    (PR #34 review; mirrors collect_store_health which returns available:true
    after a successful open regardless of count)."""
    ledger_path = tmp_path / "ledger.json"
    model = build_estate_model(
        [],
        _WING_CONFIG,
        telemetry_rows=[],
        agents_dir=_AGENTS_FIXTURES,
        ledger_path=ledger_path,
        schema=_SCHEMA,
    )
    store = model["property"]["health"]["store"]
    assert store["available"] is True
    assert store["count"] == 0
    assert store["by_wing"] == {} and store["by_room"] == {}


# ── 5. Buildings ──────────────────────────────────────────────────────────────


def test_two_buildings_present(tmp_path):
    """buildings array contains exactly 2 entries."""
    model = _build_with_tmp_ledger(tmp_path)
    assert len(model["buildings"]) == 2


def test_nook_building_present(tmp_path):
    """A nook building (id='nook', kind='palace') is present."""
    model = _build_with_tmp_ledger(tmp_path)
    nook = next((b for b in model["buildings"] if b["id"] == "nook"), None)
    assert nook is not None
    assert nook["kind"] == "palace"


def test_workshop_building_present(tmp_path):
    """A workshop building (id='workshop', kind='workshop') is present."""
    model = _build_with_tmp_ledger(tmp_path)
    workshop = next((b for b in model["buildings"] if b["id"] == "workshop"), None)
    assert workshop is not None
    assert workshop["kind"] == "workshop"


# ── 6. Wing types in nook building ────────────────────────────────────────────


def test_nook_wings_include_unknown_type(tmp_path):
    """Wings with unregistered names have type 'unknown'."""
    model = _build_with_tmp_ledger(tmp_path)
    nook = next(b for b in model["buildings"] if b["id"] == "nook")
    unknown_wings = [w for w in nook["wings"] if w["type"] == "unknown"]
    # xyzzy-unknown-wing and ? bucket should both be unknown
    assert len(unknown_wings) >= 1, "Expected at least one wing with type 'unknown'"


def test_nook_wings_include_registered_types(tmp_path):
    """Wings with registered names have correct types."""
    model = _build_with_tmp_ledger(tmp_path)
    nook = next(b for b in model["buildings"] if b["id"] == "nook")
    by_id = {w["id"]: w for w in nook["wings"]}
    if "wing:dev" in by_id:
        assert by_id["wing:dev"]["type"] == "dev"
    if "wing:project" in by_id:
        assert by_id["wing:project"]["type"] == "project"
    if "wing:Personal" in by_id:
        assert by_id["wing:Personal"]["type"] == "personal"


# ── 7. Slot stability across two builds ──────────────────────────────────────


def test_slot_stability_across_two_builds(tmp_path):
    """Two builds with the same persisted ledger produce identical wing slots."""
    ledger_path = tmp_path / "ledger.json"
    model_a = build_estate_model(
        _METADATA_ROWS,
        _WING_CONFIG,
        telemetry_rows=[],
        agents_dir=_AGENTS_FIXTURES,
        ledger_path=ledger_path,
        schema=_SCHEMA,
    )
    nook_a = next(b for b in model_a["buildings"] if b["id"] == "nook")
    slots_a = {w["id"]: w["slot"] for w in nook_a["wings"]}

    # Second build with same ledger file
    model_b = build_estate_model(
        _METADATA_ROWS,
        _WING_CONFIG,
        telemetry_rows=[],
        agents_dir=_AGENTS_FIXTURES,
        ledger_path=ledger_path,
        schema=_SCHEMA,
    )
    nook_b = next(b for b in model_b["buildings"] if b["id"] == "nook")
    slots_b = {w["id"]: w["slot"] for w in nook_b["wings"]}

    assert slots_a == slots_b, "Wing slots changed between two builds (ledger not stable)"


# ── 8. Deleted wing leaves a gap ──────────────────────────────────────────────


def test_deleted_wing_leaves_gap(tmp_path):
    """Removing a wing from rows does NOT reclaim its slot in the ledger."""
    ledger_path = tmp_path / "ledger.json"
    rows_full = [
        {"wing": "dev", "room": "main", "hall": "handoff"},
        {"wing": "project", "room": "planning", "hall": "plans"},
    ]
    model_a = build_estate_model(
        rows_full,
        _WING_CONFIG,
        telemetry_rows=[],
        agents_dir=_AGENTS_FIXTURES,
        ledger_path=ledger_path,
    )
    nook_a = next(b for b in model_a["buildings"] if b["id"] == "nook")
    dev_slot_a = next(w["slot"] for w in nook_a["wings"] if w["id"] == "wing:dev")
    project_slot_a = next(w["slot"] for w in nook_a["wings"] if w["id"] == "wing:project")

    # Remove dev from rows
    rows_partial = [{"wing": "project", "room": "planning", "hall": "plans"}]
    model_b = build_estate_model(
        rows_partial,
        _WING_CONFIG,
        telemetry_rows=[],
        agents_dir=_AGENTS_FIXTURES,
        ledger_path=ledger_path,
    )
    nook_b = next(b for b in model_b["buildings"] if b["id"] == "nook")
    project_slot_b = next(w["slot"] for w in nook_b["wings"] if w["id"] == "wing:project")

    # project slot must be unchanged (no reflow)
    assert project_slot_b == project_slot_a, "project slot changed after removing dev (reflow!)"

    # dev slot must still be in the on-disk ledger (gap preserved)
    ledger = load_ledger(ledger_path)
    assert "wing:dev" in ledger, "dev slot was removed from ledger (forbidden — gaps must persist)"
    assert ledger["wing:dev"] == dev_slot_a


# ── 9. No home paths or secrets ───────────────────────────────────────────────


def _collect_string_values(obj) -> list[str]:
    """Recursively collect all string values from a nested structure."""
    values: list[str] = []
    if isinstance(obj, str):
        values.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            values.extend(_collect_string_values(v))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(_collect_string_values(item))
    return values


def test_no_home_paths_in_model(tmp_path):
    """The emitted model contains no absolute home paths."""
    model = _build_with_tmp_ledger(tmp_path)
    for val in _collect_string_values(model):
        assert "/home/" not in val, f"Home path found in model value: {val!r}"
        assert "\\Users\\" not in val, f"Home path found in model value: {val!r}"


# ── 10. validate=True raises on bad model ─────────────────────────────────────


def test_validate_raises_on_leaked_body_field(tmp_path):
    """validate_estate_model raises ValidationError when a body field is present."""
    model = _build_with_tmp_ledger(tmp_path)
    # Inject a body field into a wing (additionalProperties:false on wing)
    nook = next(b for b in model["buildings"] if b["id"] == "nook")
    if nook["wings"]:
        nook["wings"][0]["body"] = "leaked content"
    with pytest.raises(jsonschema.ValidationError):
        validate_estate_model(model, _SCHEMA)


# ── 11. _compute_store_health_from_rows unit test ─────────────────────────────


def test_compute_store_health_basic():
    """_compute_store_health_from_rows returns correct counts."""
    rows = [
        {"wing": "dev", "room": "main", "strength": 0.9},
        {"wing": "dev", "room": "main", "strength": 0.5},
        {"wing": "project", "room": "planning", "strength": 0.7},
    ]
    health = _compute_store_health_from_rows(rows)
    assert health["available"] is True
    assert health["count"] == 3
    assert health["by_wing"]["dev"] == 2
    assert health["by_wing"]["project"] == 1
    assert health["by_room"]["main"] == 2


def test_redact_governance_sums_counts_on_key_collision():
    """Two lane/verdict keys that redact to the same string MERGE counts, so
    by_lane/by_verdict stay consistent with totals (PR #34 review)."""
    from sage_mcp.estate.adapter.estate_model import _redact_governance

    gov = {
        "by_verdict": {"/home/a/APPROVE": 2, "/home/b/APPROVE": 3},
        "by_lane": {
            "/home/a/lane": {"APPROVE": 1},
            "/home/b/lane": {"APPROVE": 4},
        },
    }
    out = _redact_governance(gov)
    # Both verdict keys redact to the same string → counts summed to 5.
    assert sum(out["by_verdict"].values()) == 5
    assert all("/home/" not in k for k in out["by_verdict"])
    # Both lanes collapse to one key, nested APPROVE counts summed to 5.
    assert len(out["by_lane"]) == 1
    lane_counts = next(iter(out["by_lane"].values()))
    assert sum(lane_counts.values()) == 5


def test_compute_store_health_null_room_wing_goes_to_question_bucket():
    """A present-but-null/empty room/wing lands in the ? bucket (matching the
    nook builder), not exported as "None"/"" keys (PR #34 review)."""
    rows = [
        {"wing": None, "room": "", "strength": 0.5},
        {"wing": "dev", "room": "main", "strength": 0.7},
    ]
    health = _compute_store_health_from_rows(rows)
    assert "?" in health["by_wing"] and "?" in health["by_room"]
    assert "None" not in health["by_wing"] and "" not in health["by_room"]


def test_compute_store_health_empty_rows():
    """Empty rows (opened-but-empty store) returns available:true, count 0 — it
    mirrors collect_store_health, which is available after a successful open
    regardless of count (PR #34 review)."""
    health = _compute_store_health_from_rows([])
    assert health["available"] is True
    assert health["count"] == 0
    assert health["by_wing"] == {} and health["by_room"] == {}
    assert health["strength"] is None


def test_compute_store_health_strength_stats():
    """strength stats are computed correctly from rows with strength values."""
    rows = [
        {"wing": "dev", "room": "main", "strength": 1.0},
        {"wing": "dev", "room": "main", "strength": 0.0},  # below floor
    ]
    health = _compute_store_health_from_rows(rows)
    s = health["strength"]
    assert s is not None
    assert s["min"] == 0.0
    assert s["max"] == 1.0
    assert s["mean"] == 0.5


def test_compute_store_health_strength_none_when_no_strength():
    """strength is None when no rows have a strength field."""
    rows = [{"wing": "dev", "room": "main"}]  # no strength key
    health = _compute_store_health_from_rows(rows)
    assert health["strength"] is None


def test_store_health_keys_redacted():
    """by_room/by_wing KEYS are redacted — a home path never leaks as a JSON key
    (sec F1 fold). The local dashboard collector does not redact; the exported
    estate model must."""
    rows = [
        {"wing": "/home/secretuser/dev-wing", "room": "/home/secretuser/main", "strength": 0.5},
    ]
    health = _compute_store_health_from_rows(rows)
    for key in health["by_wing"]:
        assert "/home/" not in key, f"home path leaked in by_wing key: {key!r}"
    for key in health["by_room"]:
        assert "/home/" not in key, f"home path leaked in by_room key: {key!r}"


def test_concurrent_builds_never_assign_duplicate_slots(tmp_path, monkeypatch):
    """Concurrent builds against the same ledger never produce duplicate slots
    (Codex fold: estate_layout_lock serializes load→assign→save). Each thread
    introduces a distinct new wing from the same starting ledger; without the
    lock two threads could both assign slot 0."""
    import threading

    from sage_mcp.estate.adapter import estate_model as em

    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")

    n = 8
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def _worker(i: int):
        try:
            rows = [{"wing": f"wing-{i}", "room": "main", "hall": "handoff"}]
            barrier.wait()  # maximize contention on the ledger
            build_estate_model(
                rows,
                _WING_CONFIG,
                telemetry_rows=[],
                agents_dir=_AGENTS_FIXTURES,
                ledger_path=ledger_path,
                validate=False,
            )
        except Exception as exc:  # noqa: BLE001 — surface to the assertion
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker errors: {errors}"
    final = load_ledger(ledger_path)
    wing_slots = [v for k, v in final.items() if k.startswith("wing:")]
    assert len(wing_slots) == len(set(wing_slots)), (
        f"duplicate slots under concurrency: {sorted(wing_slots)}"
    )


def test_deep_redact_scrubs_secrets_and_paths():
    """_deep_redact masks secret tokens AND home paths in nested values, keeping
    structural keys (PR #34 review — Workshop export hardening)."""
    from sage_mcp.estate.adapter.estate_model import _deep_redact

    out = _deep_redact(
        {
            "id": "workshop",
            "agents": [
                {"name": "ghp_aaaaaaaaaaaaaaaaaaaa", "family": "dev", "tools": ["sk-secret123"]},
                {"name": "/home/alice/agent", "family": "ops", "tools": []},
            ],
        }
    )
    assert out["id"] == "workshop"  # structural key/value untouched (no secret)
    blob = json.dumps(out)
    assert "ghp_aaaaaaaaaaaaaaaaaaaa" not in blob
    assert "sk-secret123" not in blob
    assert "/home/alice" not in blob


def test_workshop_ids_dedup_after_redaction_collapse(tmp_path, monkeypatch):
    """Two agents whose names collapse to the same redacted id export as ONE
    Workshop node (no duplicate id a renderer would collide) — PR #34 review."""
    from sage_mcp.estate.adapter import estate_model as em

    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    agents = tmp_path / "agents"
    agents.mkdir()
    # Two distinct files whose names strip_home_path to the same id.
    (agents / "a.md").write_text(
        "---\nname: /home/alice/agent\nfamily: dev\n---\n", encoding="utf-8"
    )
    (agents / "b.md").write_text("---\nname: /home/bob/agent\nfamily: dev\n---\n", encoding="utf-8")
    model = build_estate_model(
        _METADATA_ROWS,
        _WING_CONFIG,
        telemetry_rows=[],
        agents_dir=agents,
        skills_dir=tmp_path / "no-s",
        rules_dir=tmp_path / "no-r",
        hooks_dir=tmp_path / "no-h",
        ledger_path=tmp_path / "ledger.json",
        workshop_ledger_path=tmp_path / "wk.json",
        workshop_bucket_path=tmp_path / "wkb.json",
        validate=False,
    )
    ws = next(b for b in model["buildings"] if b["id"] == "workshop")
    ids = [a["id"] for a in ws.get("agents", [])]
    assert len(ids) == len(set(ids)), f"duplicate Workshop agent ids: {ids}"
    assert "/home/" not in json.dumps(ws)


def test_workshop_secret_strings_scrubbed_in_model(tmp_path, monkeypatch):
    """A custom agent file with a secret-shaped name never leaks into the
    exported model (PR #34 review)."""
    from sage_mcp.estate.adapter import estate_model as em

    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    agents = tmp_path / "agents"
    agents.mkdir()
    # An agent whose name carries a secret-shaped token.
    (agents / "leaky.md").write_text(
        "---\nname: ghp_zzzzzzzzzzzzzzzzzzzz\nfamily: dev\n---\n# x\n", encoding="utf-8"
    )
    model = build_estate_model(
        _METADATA_ROWS,
        _WING_CONFIG,
        telemetry_rows=[],
        agents_dir=agents,
        skills_dir=tmp_path / "no-s",
        rules_dir=tmp_path / "no-r",
        hooks_dir=tmp_path / "no-h",
        ledger_path=tmp_path / "ledger.json",
        workshop_ledger_path=tmp_path / "wk.json",
        workshop_bucket_path=tmp_path / "wkb.json",
        validate=False,
    )
    assert "ghp_zzzzzzzzzzzzzzzzzzzz" not in json.dumps(model), "secret leaked via Workshop"


def test_workshop_ledger_persists_no_reflow_on_agent_removal(tmp_path, monkeypatch):
    """Removing an agent file does not reflow surviving agents' slots across two
    builds with a persisted workshop ledger (PR #34 review; ADR-0005). Without
    persistence build_workshop repacks from the current file list each call."""
    from sage_mcp.estate.adapter import estate_model as em

    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    wk_ledger = tmp_path / "wk.json"
    wk_buckets = tmp_path / "wkb.json"

    # Build a temp agents dir with 3 agents.
    agents = tmp_path / "agents"
    agents.mkdir()
    for name in ("dev-alpha", "dev-beta", "dev-gamma"):
        (agents / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")

    def _build():
        return build_estate_model(
            _METADATA_ROWS,
            _WING_CONFIG,
            telemetry_rows=[],
            agents_dir=agents,
            ledger_path=tmp_path / "ledger.json",
            workshop_ledger_path=wk_ledger,
            workshop_bucket_path=wk_buckets,
            validate=False,
        )

    model_a = _build()
    ws_a = next(b for b in model_a["buildings"] if b["id"] == "workshop")
    # Capture surviving agents' slots (find slot field on agent nodes).
    persisted = load_ledger(wk_ledger)
    beta_slot = persisted.get("dev-beta")
    gamma_slot = persisted.get("dev-gamma")
    assert beta_slot is not None and gamma_slot is not None

    # Remove the first agent and rebuild.
    (agents / "dev-alpha.md").unlink()
    _build()
    persisted2 = load_ledger(wk_ledger)

    assert persisted2.get("dev-beta") == beta_slot, "surviving agent slot reflowed"
    assert persisted2.get("dev-gamma") == gamma_slot, "surviving agent slot reflowed"
    assert "dev-alpha" in persisted2, "removed agent's slot must persist as a gap"
    assert ws_a is not None


def test_governance_map_keys_redacted(tmp_path):
    """Governance by_lane/by_verdict KEYS are redacted — a hand-written audit
    lane carrying a home path never leaks into the exported model (PR #34)."""
    rows = [
        {
            "phase": "audit",
            "verdict": "APPROVE",
            "agent": "/home/secretuser/custom-lane",
            "severity_top": 10,
            "turn_id": "t1",
        },
    ]
    model = build_estate_model(
        _METADATA_ROWS,
        _WING_CONFIG,
        telemetry_rows=rows,
        agents_dir=_AGENTS_FIXTURES,
        ledger_path=tmp_path / "ledger.json",
        workshop_ledger_path=tmp_path / "wk.json",
        workshop_bucket_path=tmp_path / "wkb.json",
        validate=False,
    )
    by_lane = model["property"]["health"]["governance"]["by_lane"]
    for lane_key in by_lane:
        assert "/home/" not in lane_key, f"home path leaked in by_lane key: {lane_key!r}"


def test_diaries_derived_from_metadata_rows():
    """Per-agent diary counts are derived from room=='diary' rows (no extra I/O),
    with agent names redacted (PR #34 review)."""
    rows = [
        {"wing": "dev", "room": "diary", "agent": "dev-architect"},
        {"wing": "dev", "room": "diary", "agent": "dev-architect"},
        {"wing": "ops", "room": "diary", "agent": "sec-auditor"},
        {"wing": "dev", "room": "main", "agent": "dev-architect"},  # not a diary
        {"wing": "x", "room": "diary", "agent": "/home/secretuser/agent"},  # redacted
    ]
    from sage_mcp.estate.adapter.nook import build_nook_building

    nook, _ = build_nook_building(rows, _WING_CONFIG)
    diaries = {d["agent"]: d["entries"] for d in nook.get("diaries", [])}
    assert diaries.get("dev-architect") == 2
    assert diaries.get("sec-auditor") == 1
    assert all("/home/" not in a for a in diaries), "agent name leaked a home path"


def test_armory_counts_from_default_dirs(tmp_path, monkeypatch):
    """When skills/rules/hooks dirs aren't passed, they default to ~/.claude/*
    and are counted (PR #34 review). HOME is pointed at a temp tree with one of
    each so the default resolution is observable."""
    fake_home = tmp_path / "home"
    claude = fake_home / ".claude"
    (claude / "agents").mkdir(parents=True)
    (claude / "agents" / "dev-a.md").write_text("# a", encoding="utf-8")
    (claude / "skills").mkdir()
    (claude / "skills" / "s1").mkdir()
    (claude / "skills" / "s1" / "SKILL.md").write_text("# s", encoding="utf-8")
    (claude / "rules").mkdir()
    (claude / "rules" / "r1.md").write_text("# r", encoding="utf-8")
    (claude / "hooks").mkdir()
    (claude / "hooks" / "h1.py").write_text("x", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))

    model = build_estate_model(
        _METADATA_ROWS,
        _WING_CONFIG,
        telemetry_rows=[],
        ledger_path=tmp_path / "ledger.json",
        workshop_ledger_path=tmp_path / "wk.json",
        workshop_bucket_path=tmp_path / "wkb.json",
        validate=False,
    )
    workshop = next(b for b in model["buildings"] if b["id"] == "workshop")
    armory = workshop["armory"]
    assert armory["skills"] >= 1 and armory["rules"] >= 1 and armory["hooks"] >= 1


def test_content_revision_stable_until_content_changes(tmp_path, monkeypatch):
    """Revision bumps ONLY when the content fingerprint changes; an unchanged
    poll keeps the same revision (PR #34 review — Phase-5 live-refresh compares
    revision, so idle polls must not force a re-layout)."""
    from sage_mcp.estate.adapter import estate_model as em

    rev_file = tmp_path / "rev.json"
    monkeypatch.setattr(em, "_REVISION_PATH", rev_file)
    c1 = {"buildings": [{"id": "nook", "drawer": 1}]}
    r0 = em._content_revision(c1)
    r1 = em._content_revision(c1)  # identical content
    assert rev_file.is_file()
    assert r1 == r0, "identical content must keep the same revision"
    c2 = {"buildings": [{"id": "nook", "drawer": 2}]}  # changed
    r2 = em._content_revision(c2)
    assert r2 == r0 + 1, "changed content must bump the revision by one"


def test_build_estate_model_revision_stable_across_identical_builds(tmp_path, monkeypatch):
    """Two identical builds emit the SAME revision (captured_at differs but is
    excluded from the fingerprint)."""
    from sage_mcp.estate.adapter import estate_model as em

    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    ledger_path = tmp_path / "ledger.json"

    def _build():
        return build_estate_model(
            _METADATA_ROWS,
            _WING_CONFIG,
            telemetry_rows=[],
            agents_dir=_AGENTS_FIXTURES,
            skills_dir=tmp_path / "none-skills",
            rules_dir=tmp_path / "none-rules",
            hooks_dir=tmp_path / "none-hooks",
            ledger_path=ledger_path,
            workshop_ledger_path=tmp_path / "wk.json",
            workshop_bucket_path=tmp_path / "wkb.json",
            validate=False,
        )

    a = _build()
    b = _build()
    assert a["revision"] == b["revision"], "identical content must not bump revision"
