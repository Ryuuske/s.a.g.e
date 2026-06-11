"""Tests for sage.estate.adapter.nook — the Nook building adapter (Phase 3).

Covers:
1. Wing/room grouping from metadata rows.
2. Wing-type resolution (registered → correct type; unregistered → 'unknown').
3. The ? bucket is always carried, never hidden.
4. hall_counts correct per wing.
5. Redaction applied (home-path in a title is stripped).
6. Schema validation of the emitted nook building.
7. Slot stability: two builds with the same ledger produce identical slots.
8. Deleted wing leaves a gap (slot never reclaimed).
9. No drawer bodies in the output (the no-body contract).
10. Slot ledger persistence: load/save roundtrip; save is append-only.

WHERE: tests/estate/test_nook_adapter.py
"""

import copy
import json
import pathlib

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from sage_mcp.estate.adapter.nook import (
    _assign_slot,
    _resolve_wing_type,
    build_nook_building,
    load_ledger,
    save_ledger,
)

# ── Paths ──────────────────────────────────────────────────────────────────────

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_SCHEMA_PATH = _ROOT / "docs" / "projects" / "sage-estate-dashboard" / "estate-model.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_NOOK_DEF = _SCHEMA["$defs"]["nook_building"]
_BASE_URI = "https://sage.estate/estate-model"


# ── Schema validator helper ────────────────────────────────────────────────────


def _validate_nook(nook_dict: dict) -> None:
    """Validate *nook_dict* against the ``nook_building`` $def."""
    resource = Resource.from_contents(_SCHEMA, DRAFT202012)
    registry = Registry().with_resource(_BASE_URI, resource)
    wrapper_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": _BASE_URI + "/validate-nook",
        "$defs": _SCHEMA["$defs"],
        **_NOOK_DEF,
    }
    wrapper_resource = Resource.from_contents(wrapper_schema, DRAFT202012)
    registry = registry.with_resource(_BASE_URI + "/validate-nook", wrapper_resource)
    validator = jsonschema.Draft202012Validator(wrapper_schema, registry=registry)
    validator.validate(nook_dict)


# ── Fixtures ───────────────────────────────────────────────────────────────────

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

# Synthetic metadata rows — no real store, no real paths.
# Covers: ≥2 wing types, multiple rooms, multiple halls, ? bucket, strength values.
_METADATA_ROWS = [
    # dev wing — main room — handoff hall
    {"wing": "dev", "room": "main", "hall": "handoff", "strength": 0.9, "agent": "dev-architect"},
    {"wing": "dev", "room": "main", "hall": "handoff", "strength": 0.8, "agent": "dev-architect"},
    # dev wing — archive room — in-flight hall
    {"wing": "dev", "room": "archive", "hall": "in-flight", "strength": 0.5},
    # project wing — planning room — plans hall
    {"wing": "project", "room": "planning", "hall": "plans", "strength": 0.7},
    {"wing": "project", "room": "planning", "hall": "plans", "strength": 0.6},
    # project wing — planning room — facts hall
    {"wing": "project", "room": "planning", "hall": "facts", "strength": 0.4},
    # personal wing (registered in wings map) — core room
    {"wing": "Personal", "room": "core", "hall": "core", "strength": 1.0},
    # unregistered wing → unknown type
    {"wing": "xyzzy-unknown-wing", "room": "misc", "hall": "facts", "strength": 0.3},
    # ? bucket: malformed/missing wing
    {"wing": "?", "room": "?", "hall": "", "strength": 0.2},
    {"wing": None, "room": None, "hall": None, "strength": 0.1},
]

# A row with a home path in the wing title (redaction test).
_REDACT_ROWS = [
    {"wing": "/home/testuser/some-wing", "room": "main", "hall": "facts", "strength": 0.5},
]


# ── 1. Wing/room grouping ──────────────────────────────────────────────────────


def test_wings_present():
    """All distinct wing values produce wing nodes."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    wing_ids = {w["id"] for w in nook["wings"]}
    assert "wing:dev" in wing_ids
    assert "wing:project" in wing_ids
    assert "wing:Personal" in wing_ids
    assert "wing:xyzzy-unknown-wing" in wing_ids


def test_rooms_present():
    """Rooms are nested under the correct wing."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    by_wing = {w["id"]: w for w in nook["wings"]}
    dev_room_ids = {r["id"] for r in by_wing["wing:dev"]["rooms"]}
    assert "room:dev:main" in dev_room_ids
    assert "room:dev:archive" in dev_room_ids


def test_drawer_count_per_room():
    """Room drawer_count matches the row count for that wing/room pair."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    by_wing = {w["id"]: w for w in nook["wings"]}
    dev_rooms = {r["id"]: r for r in by_wing["wing:dev"]["rooms"]}
    assert dev_rooms["room:dev:main"]["drawer_count"] == 2
    assert dev_rooms["room:dev:archive"]["drawer_count"] == 1


# ── 2. Wing-type resolution ────────────────────────────────────────────────────


def test_registered_wing_type_dev():
    """Wing named 'dev' resolves to wing_type 'dev'."""
    wtype = _resolve_wing_type("dev", _WING_CONFIG)
    assert wtype == "dev"


def test_registered_wing_type_via_wings_map():
    """Wing registered in 'wings' map uses its explicit 'type'."""
    wtype = _resolve_wing_type("Personal", _WING_CONFIG)
    assert wtype == "personal"


def test_unregistered_wing_resolves_to_unknown():
    """An unregistered wing name resolves to 'unknown'."""
    wtype = _resolve_wing_type("xyzzy-unknown-wing", _WING_CONFIG)
    assert wtype == "unknown"


def test_question_mark_bucket_resolves_to_unknown():
    """The ? bucket always resolves to 'unknown'."""
    wtype = _resolve_wing_type("?", _WING_CONFIG)
    assert wtype == "unknown"


def test_wing_types_in_emitted_model():
    """All wing type values in the emitted model are valid schema enum members."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    valid_types = {"dev", "project", "knowledge", "ops", "meta", "personal", "unknown"}
    for wing in nook["wings"]:
        assert wing["type"] in valid_types, f"wing {wing['id']} has invalid type {wing['type']!r}"


# ── 3. The ? bucket is always carried ─────────────────────────────────────────


def test_question_mark_bucket_is_present():
    """The ? bucket always appears when there are rows with missing/None wing."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    wing_ids = {w["id"] for w in nook["wings"]}
    # ? bucket (and None → ?) must be present
    assert "wing:?" in wing_ids, "? bucket must be carried, never hidden"


def test_question_mark_bucket_drawer_count():
    """The ? bucket has the correct drawer count (rows with None or '?' wing)."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    by_wing = {w["id"]: w for w in nook["wings"]}
    q_wing = by_wing["wing:?"]
    # 2 rows land in ?: {"wing": "?", ...} and {"wing": None, ...}
    assert q_wing["drawer_total"] == 2


# ── 4. hall_counts ────────────────────────────────────────────────────────────


def test_hall_counts_dev_wing():
    """hall_counts for the dev wing are correct."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    by_wing = {w["id"]: w for w in nook["wings"]}
    hall_counts = by_wing["wing:dev"]["hall_counts"]
    assert hall_counts.get("handoff") == 2
    assert hall_counts.get("in-flight") == 1


def test_hall_counts_project_wing():
    """hall_counts for the project wing are correct."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    by_wing = {w["id"]: w for w in nook["wings"]}
    hall_counts = by_wing["wing:project"]["hall_counts"]
    assert hall_counts.get("plans") == 2
    assert hall_counts.get("facts") == 1


def test_hall_counts_empty_for_no_hall_rows():
    """hall_counts is empty (not missing) for rows without a hall field."""
    rows = [{"wing": "dev", "room": "main"}]  # no hall key
    nook, _ = build_nook_building(rows, _WING_CONFIG)
    by_wing = {w["id"]: w for w in nook["wings"]}
    # hall_counts should be an empty dict (no halls for a missing hall field)
    assert by_wing["wing:dev"]["hall_counts"] == {}


# ── 5. Redaction ──────────────────────────────────────────────────────────────


def test_home_path_in_wing_name_is_stripped():
    """A home-path-like wing name has the username stripped from the title."""
    nook, _ = build_nook_building(_REDACT_ROWS, _WING_CONFIG)
    for wing in nook["wings"]:
        assert "/home/" not in wing["title"], f"Home path survived in wing title: {wing['title']!r}"
        assert "/home/" not in wing["id"], f"Home path survived in wing id: {wing['id']!r}"


def test_room_title_redacted():
    """Room titles are run through the redactor."""
    rows = [{"wing": "dev", "room": "/home/alice/project/session-42", "hall": "facts"}]
    nook, _ = build_nook_building(rows, _WING_CONFIG)
    for wing in nook["wings"]:
        for room in wing["rooms"]:
            assert "/home/" not in room["title"]


# ── 6. Schema validation ──────────────────────────────────────────────────────


def test_nook_validates_against_schema():
    """The emitted nook building validates against nook_building $def."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    _validate_nook(nook)


def test_nook_empty_rows_validates():
    """An empty row set still produces a schema-valid nook (no wings)."""
    nook, _ = build_nook_building([], _WING_CONFIG)
    _validate_nook(nook)


# ── 7. No drawer bodies ───────────────────────────────────────────────────────


def _collect_all_keys(obj) -> list[str]:
    """Recursively collect all dict keys from a nested structure."""
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append(k)
            keys.extend(_collect_all_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            keys.extend(_collect_all_keys(item))
    return keys


def test_no_body_fields_in_output():
    """The emitted nook building contains no body/content/document fields."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    all_keys = _collect_all_keys(nook)
    forbidden = {"body", "content", "document", "text"}
    overlap = set(all_keys) & forbidden
    assert not overlap, f"Forbidden body fields found in nook output: {overlap}"


# ── 8. Slot stability ─────────────────────────────────────────────────────────


def test_slots_are_integers():
    """Every wing and room has a non-negative integer slot."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    for wing in nook["wings"]:
        assert isinstance(wing["slot"], int) and wing["slot"] >= 0
        for room in wing["rooms"]:
            assert isinstance(room["slot"], int) and room["slot"] >= 0


def test_slots_are_unique_across_wings():
    """Wing slots are unique."""
    nook, _ = build_nook_building(_METADATA_ROWS, _WING_CONFIG)
    wing_slots = [w["slot"] for w in nook["wings"]]
    assert len(wing_slots) == len(set(wing_slots)), "Duplicate wing slots detected"


def test_slot_stability_across_two_builds():
    """Same ledger produces identical slots on two successive builds."""
    nook_a, ledger_a = build_nook_building(_METADATA_ROWS, _WING_CONFIG, ledger={})
    nook_b, ledger_b = build_nook_building(
        _METADATA_ROWS, _WING_CONFIG, ledger=copy.deepcopy(ledger_a)
    )
    slots_a = {w["id"]: w["slot"] for w in nook_a["wings"]}
    slots_b = {w["id"]: w["slot"] for w in nook_b["wings"]}
    assert slots_a == slots_b, "Wing slots are not stable across two builds with the same ledger"


def test_new_wing_appends_slot():
    """Adding a new wing fills the next available slot, does not reflow survivors."""
    small_rows = [
        {"wing": "dev", "room": "main", "hall": "handoff", "strength": 0.9},
        {"wing": "project", "room": "planning", "hall": "plans", "strength": 0.7},
    ]
    nook_a, ledger_a = build_nook_building(small_rows, _WING_CONFIG, ledger={})
    slots_before = {w["id"]: w["slot"] for w in nook_a["wings"]}

    # Add a new wing
    rows_with_new = small_rows + [
        {"wing": "knowledge", "room": "facts", "hall": "facts", "strength": 0.5}
    ]
    nook_b, ledger_b = build_nook_building(
        rows_with_new, _WING_CONFIG, ledger=copy.deepcopy(ledger_a)
    )
    slots_after = {w["id"]: w["slot"] for w in nook_b["wings"]}

    # Survivors keep their slots
    for wing_id, slot in slots_before.items():
        assert slots_after[wing_id] == slot, f"{wing_id} slot changed (reflow!)"

    # New wing gets a higher slot
    new_slot = slots_after["wing:knowledge"]
    assert new_slot > max(slots_before.values()), "New wing should have the highest slot"


# ── 9. Deleted wing leaves a gap ──────────────────────────────────────────────


def test_deleted_wing_leaves_gap_in_ledger():
    """Removing a wing from the rows does NOT reclaim its slot (ADR-0005)."""
    small_rows = [
        {"wing": "dev", "room": "main", "hall": "handoff"},
        {"wing": "project", "room": "planning", "hall": "plans"},
    ]
    nook_a, ledger_a = build_nook_building(small_rows, _WING_CONFIG, ledger={})
    dev_slot = ledger_a["wing:dev"]
    project_slot = ledger_a["wing:project"]

    # Remove dev wing
    rows_without_dev = [{"wing": "project", "room": "planning", "hall": "plans"}]
    nook_b, ledger_b = build_nook_building(
        rows_without_dev, _WING_CONFIG, ledger=copy.deepcopy(ledger_a)
    )

    # project wing's slot must be unchanged
    project_in_b = {w["id"]: w["slot"] for w in nook_b["wings"]}.get("wing:project")
    assert project_in_b == project_slot, "project slot changed after removing dev (reflow!)"

    # dev's slot is preserved in the ledger (gap, never reclaimed)
    assert ledger_b.get("wing:dev") == dev_slot, "dev slot was removed from ledger (forbidden!)"


# ── 10. Slot ledger persistence ────────────────────────────────────────────────


def test_ledger_save_and_load_roundtrip(tmp_path):
    """save_ledger then load_ledger returns the same mapping."""
    ledger_file = tmp_path / "ledger.json"
    ledger = {"wing:dev": 0, "wing:project": 1, "room:dev:main": 2}
    save_ledger(ledger, ledger_file)
    loaded = load_ledger(ledger_file)
    assert loaded == ledger


def test_ledger_save_is_append_only(tmp_path):
    """save_ledger preserves existing entries not in the new ledger (gap semantics)."""
    ledger_file = tmp_path / "ledger.json"
    # Write initial ledger with two entries
    initial = {"wing:dev": 0, "wing:project": 1}
    save_ledger(initial, ledger_file)

    # Save a new ledger that only contains 'wing:project'
    partial = {"wing:project": 1, "wing:knowledge": 2}
    save_ledger(partial, ledger_file)

    loaded = load_ledger(ledger_file)
    # 'wing:dev' must still be present (append-only / gap semantics)
    assert "wing:dev" in loaded, "wing:dev was removed from ledger (forbidden — gaps must persist)"
    assert loaded["wing:dev"] == 0
    assert loaded["wing:knowledge"] == 2


def test_ledger_load_missing_file_returns_empty(tmp_path):
    """load_ledger returns an empty dict when the file does not exist."""
    ledger_file = tmp_path / "no-such-file.json"
    assert load_ledger(ledger_file) == {}


def test_ledger_load_corrupt_file_returns_empty(tmp_path):
    """load_ledger returns an empty dict when the file is corrupt JSON."""
    ledger_file = tmp_path / "corrupt.json"
    ledger_file.write_text("{not valid json", encoding="utf-8")
    assert load_ledger(ledger_file) == {}


# ── 11. _assign_slot helper ────────────────────────────────────────────────────


def test_assign_slot_first_sight():
    """First-sight assignment: new id gets slot 0 on empty ledger."""
    ledger: dict[str, int] = {}
    slot = _assign_slot("wing:dev", ledger)
    assert slot == 0
    assert ledger["wing:dev"] == 0


def test_assign_slot_stable():
    """Known id returns the same slot (never reassigned)."""
    ledger = {"wing:dev": 5}
    slot = _assign_slot("wing:dev", ledger)
    assert slot == 5


def test_assign_slot_appends():
    """New id takes max(slots) + 1, not a recycled gap."""
    ledger = {"wing:dev": 0, "wing:project": 2}  # gap at 1
    slot = _assign_slot("wing:knowledge", ledger)
    assert slot == 3  # max(0, 2) + 1 = 3, never fills gap 1


# ── 12. Redaction of MAP KEYS (sec F2 fold) ────────────────────────────────────


def test_colliding_redacted_wings_merge_to_one_node():
    """Two raw wing names that redact to the same safe id MERGE into one node
    (not duplicate id/slot a renderer would collide) — PR #34 review."""
    rows = [
        {"wing": "/home/alice/proj", "room": "/home/alice/main", "hall": "facts"},
        {"wing": "/home/bob/proj", "room": "/home/bob/main", "hall": "facts"},
    ]
    nook, _ = build_nook_building(rows, _WING_CONFIG)
    wing_ids = [w["id"] for w in nook["wings"]]
    assert len(wing_ids) == len(set(wing_ids)), f"duplicate wing ids: {wing_ids}"
    assert len(nook["wings"]) == 1, "the two home-path wings must merge into one"
    merged = nook["wings"][0]
    assert "/home/" not in merged["id"] and "/home/" not in merged["title"]
    assert merged["drawer_total"] == 2, "counts must aggregate across the merged raws"
    assert len(merged["rooms"]) == 1
    assert merged["rooms"][0]["drawer_count"] == 2


def test_build_tunnels_redacts_custom_id():
    """A custom/hand-edited tunnel id is redacted (ADR-0008; PR #34 review)."""
    from sage_mcp.estate.adapter.nook import build_tunnels

    out = build_tunnels(
        [
            {
                "id": "/home/secretuser/my-tunnel",
                "label": "ref",
                "source": {"wing": "dev", "room": "main"},
                "target": {"wing": "project", "room": "planning"},
            }
        ]
    )
    assert len(out) == 1
    assert "/home/" not in out[0]["id"], f"home path leaked in tunnel id: {out[0]['id']!r}"


def test_build_tunnels_skips_self_loop_after_redaction():
    """A tunnel whose two endpoints redact to the SAME wing id is dropped — it's
    a bogus self-loop, not a cross-wing passage (PR #34 review)."""
    from sage_mcp.estate.adapter.nook import build_tunnels

    out = build_tunnels(
        [
            {
                "id": "t",
                "source": {"wing": "/home/alice/proj", "room": "main"},
                "target": {"wing": "/home/bob/proj", "room": "main"},
            },
            {
                "id": "ok",
                "source": {"wing": "dev", "room": "main"},
                "target": {"wing": "project", "room": "planning"},
            },
        ]
    )
    # The collapsing tunnel is dropped; the genuine cross-wing one survives.
    assert len(out) == 1
    assert out[0]["endpoints"] == ["wing:dev", "wing:project"]
    for t in out:
        assert t["endpoints"][0] != t["endpoints"][1], "self-loop emitted"


def test_build_tunnels_skips_malformed_endpoint_individually():
    """One record with a non-dict source/target is skipped alone — it must NOT
    raise and drop every valid tunnel (PR #34 review)."""
    from sage_mcp.estate.adapter.nook import build_tunnels

    out = build_tunnels(
        [
            {"id": "bad", "source": "dev", "target": {"wing": "project"}},  # source is a str
            {
                "id": "good",
                "source": {"wing": "dev", "room": "main"},
                "target": {"wing": "project", "room": "planning"},
            },
        ]
    )
    assert len(out) == 1, "valid tunnel must survive a malformed sibling record"
    assert out[0]["endpoints"] == ["wing:dev", "wing:project"]


def test_hall_counts_keys_redacted():
    """hall_counts KEYS are redacted — a home path in a hall name never leaks."""
    rows = [
        {"wing": "dev", "room": "main", "hall": "/home/secretuser/private-hall"},
        {"wing": "dev", "room": "main", "hall": "/home/secretuser/private-hall"},
    ]
    nook, _ = build_nook_building(rows, _WING_CONFIG)
    dev = next(w for w in nook["wings"] if w["id"] == "wing:dev")
    for hall_key in dev["hall_counts"]:
        assert "/home/" not in hall_key, f"home path leaked in hall_counts key: {hall_key!r}"
    # The count is preserved even after redaction merges identical raw names.
    assert sum(dev["hall_counts"].values()) == 2


# ── 13. diaries branch (test F3 fold) ──────────────────────────────────────────


def test_diaries_branch_roundtrips_and_validates():
    """A non-empty diaries list is emitted and the nook still validates."""
    rows = [{"wing": "dev", "room": "main", "hall": "handoff"}]
    diaries = [{"agent": "dev-architect", "entries": 3}, {"agent": "sec-auditor", "entries": 1}]
    nook, _ = build_nook_building(rows, _WING_CONFIG, diaries=diaries)
    assert nook["diaries"] == diaries
    _validate_nook(nook)


# ── 14. No-body strip is real, not tautological (test F5 fold) ─────────────────


def test_body_field_in_row_is_not_carried():
    """Even if an upstream row carries a body/document, it never reaches output."""
    rows = [
        {
            "wing": "dev",
            "room": "main",
            "hall": "handoff",
            "strength": 0.9,
            "document": "SECRET DRAWER BODY that must never appear",
            "body": "another body",
            "text": "more text",
        }
    ]
    nook, _ = build_nook_building(rows, _WING_CONFIG)
    all_keys = set(_collect_all_keys(nook))
    assert not (all_keys & {"body", "content", "document", "text"})
    # And the body string itself appears nowhere in the emitted structure.
    blob = json.dumps(nook)
    assert "SECRET DRAWER BODY" not in blob


# ── C5: Lazy ledger path — default None sentinel resolves at call time ────────


def test_save_load_ledger_default_resolves_at_call_time(tmp_path, monkeypatch):
    """save_ledger / load_ledger with default path must use call-time HOME.

    When the default ledger_path argument is None (sentinel), the function
    must call Path(os.path.expanduser(...)) at call time — not at module-import
    time.  A monkeypatched HOME must therefore redirect I/O to the new HOME
    without a module reload.  ADR-0095 C5 isolation hardening.
    """
    import sage_mcp.estate.adapter.nook as _nook_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    # Call save_ledger with no explicit path — must resolve to the patched HOME.
    _nook_mod.save_ledger({"wing:test": 0})
    expected_path = tmp_path / ".sage" / "estate-layout-ledger.json"
    assert expected_path.exists(), (
        "save_ledger() with default path did not write to monkeypatched HOME/.sage/; "
        "the ledger path is resolved at module-import time (must be None-sentinel)."
    )

    loaded = _nook_mod.load_ledger()
    assert loaded == {"wing:test": 0}, (
        f"load_ledger() with default path returned {loaded!r}; "
        "expected the data written by save_ledger() in the patched HOME."
    )
