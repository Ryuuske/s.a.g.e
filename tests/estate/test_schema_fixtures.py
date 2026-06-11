"""Phase-0 contract tests for the Estate Model.

Covers:
- fixture validates against estate-model.schema.json (Draft 2020-12)
- body field on a drawer is rejected (additionalProperties:false)
- wing_type parity: schema enum (minus 'unknown') == wing_config.json wing_types
- fixtures dir contains no secret-shaped strings and no home-path segments
- negative tests locking all hard schema constraints added in Phase 0
"""

import copy
import json
import pathlib
import re

import jsonschema
import pytest

# ── Paths ──────────────────────────────────────────────────────────────────
_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]  # repo root: tests/estate/ -> tests/ -> root
_SCHEMA_PATH = _ROOT / "docs" / "projects" / "sage-estate-dashboard" / "estate-model.schema.json"
_FIXTURE_PATH = _HERE / "fixtures" / "estate-model.sample.json"
_WING_CONFIG_PATH = _ROOT / "wing_config.json"
_FIXTURES_DIR = _HERE / "fixtures"

# ── Shared loads ────────────────────────────────────────────────────────────
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_SAMPLE = json.loads(_FIXTURE_PATH.read_text())


# ── Lookup helpers ──────────────────────────────────────────────────────────


def _find_building(data: dict, building_id: str) -> dict:
    """Return the building with the given id; raise if absent."""
    for b in data["buildings"]:
        if b["id"] == building_id:
            return b
    raise KeyError(f"building id={building_id!r} not found in fixture")


def _find_drawer_room(building: dict) -> dict:
    """Return the first room inside building that carries a non-empty drawers list."""
    for wing in building.get("wings", []):
        for room in wing.get("rooms", []):
            if room.get("drawers"):
                return room
    raise KeyError("no room with drawers found in building")


# ── 1. Fixture validates against the schema ─────────────────────────────────
def test_sample_fixture_validates():
    """The hand-authored sample fixture must be valid per the contract schema."""
    jsonschema.validate(_SAMPLE, _SCHEMA)


# ── 2. Body field on a drawer is rejected ──────────────────────────────────
def test_body_field_is_rejected():
    """additionalProperties:false on drawer must reject any body/content field."""
    bad = copy.deepcopy(_SAMPLE)
    nook = _find_building(bad, "nook")
    room = _find_drawer_room(nook)
    room["drawers"][0]["body"] = "leaked content"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _SCHEMA)


# ── 3. wing_type parity (PR-28 guard) ──────────────────────────────────────
def test_wing_type_parity():
    """Schema wing_type enum (minus 'unknown') must equal wing_config.json wing_types.

    Prevents a registered wing-type from being mislabeled 'unknown' due to
    an out-of-sync schema enum (PR-28 review finding).
    """
    wing_cfg = json.loads(_WING_CONFIG_PATH.read_text())
    registered_types = set(wing_cfg["wing_types"].keys())

    # Extract the enum from the schema definition.
    schema_enum = set(_SCHEMA["$defs"]["wing_type"]["enum"])
    # 'unknown' is the explicit catch-all bucket — it is intentionally absent
    # from wing_config.json and intentionally present in the schema.
    schema_enum.discard("unknown")

    assert schema_enum == registered_types, (
        f"Schema wing_type enum (minus 'unknown') does not match wing_config.json.\n"
        f"  In schema only: {schema_enum - registered_types}\n"
        f"  In wing_config only: {registered_types - schema_enum}"
    )


# ── 4. No secrets, no home paths in fixtures ───────────────────────────────
_SECRET_PATTERN = re.compile(r"key|token|secret|password|auth", re.IGNORECASE)
_HOME_PATH_PATTERN = re.compile(
    r"(/home/|\\Users\\|^/|^~[/\\]|[A-Za-z]:[/\\])",
    re.MULTILINE,
)


def _collect_string_values(obj) -> list[str]:
    """Recursively collect all string values from a JSON object."""
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


def _collect_string_keys(obj) -> list[str]:
    """Recursively collect all string keys from a JSON object."""
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append(k)
            keys.extend(_collect_string_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            keys.extend(_collect_string_keys(item))
    return keys


@pytest.mark.parametrize(
    "fixture_path",
    list(_FIXTURES_DIR.glob("*.json")),
    ids=lambda p: p.name,
)
def test_fixture_no_secrets(fixture_path: pathlib.Path):
    """No fixture JSON file may contain secret-shaped strings or home paths."""
    if fixture_path.name.startswith("__"):
        pytest.skip("skipping internal python file")
    data = json.loads(fixture_path.read_text())
    string_values = _collect_string_values(data)
    string_keys = _collect_string_keys(data)

    # Check values for secret patterns
    violations: list[str] = []
    for val in string_values:
        if _SECRET_PATTERN.search(val):
            violations.append(f"secret-pattern value: {val!r}")

    # Check keys for secret patterns
    for key in string_keys:
        if _SECRET_PATTERN.search(key):
            violations.append(f"secret-pattern key: {key!r}")

    # Check values for home path patterns
    for val in string_values:
        if _HOME_PATH_PATTERN.search(val):
            violations.append(f"home-path value: {val!r}")

    assert not violations, f"Fixture {fixture_path.name} contains forbidden content:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


# ── 5. Plot path: absolute paths are rejected (sec-F2) ──────────────────────
@pytest.mark.parametrize(
    "bad_path",
    [
        "/home/alice/dev/sage",
        "~/dev/sage",
        "C:\\dev\\sage",
    ],
    ids=["unix-absolute", "home-tilde", "windows-drive"],
)
def test_plot_path_absolute_rejected(bad_path: str):
    """grounds.plots[].path must reject any absolute or home-rooted path (sec-F2).

    The schema `not`-pattern covers: leading `/`, `~`, Windows drive letters,
    `/home/` segments, and `\\Users\\` segments.
    """
    bad = copy.deepcopy(_SAMPLE)
    bad["grounds"]["plots"][0]["path"] = bad_path
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _SCHEMA)


# ── 6. store_health: available:true requires sizing fields (PR-28) ──────────
@pytest.mark.parametrize(
    "missing_field",
    ["count", "by_room", "by_wing", "strength"],
)
def test_store_health_available_true_requires_sizing_fields(missing_field: str):
    """When store.available is true, count/by_room/by_wing/strength are all required.

    Deleting any one of the four sizing fields must fail schema validation so an
    incomplete health payload is rejected source-side before the renderer tries
    to size the Palace from missing counts.
    """
    bad = copy.deepcopy(_SAMPLE)
    store = bad["property"]["health"]["store"]
    assert store["available"] is True, "fixture must have available:true for this test"
    del store[missing_field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _SCHEMA)


def test_store_health_available_false_no_sizing_fields_validates():
    """When store.available is false, sizing fields are NOT required (graceful degradation)."""
    ok = copy.deepcopy(_SAMPLE)
    ok["property"]["health"]["store"] = {"available": False, "reason": "nook offline"}
    jsonschema.validate(ok, _SCHEMA)


# ── 7. Tunnel: directed field is rejected (ADR-0006) ───────────────────────
def test_tunnel_directed_field_rejected():
    """A tunnel with a `directed` field must fail (additionalProperties:false, ADR-0006).

    Tunnels are undirected; the schema has no `directed` property and
    additionalProperties:false makes any extra field a hard validation error.
    """
    bad = copy.deepcopy(_SAMPLE)
    nook = _find_building(bad, "nook")
    nook["tunnels"][0]["directed"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _SCHEMA)


# ── 8. buildings: nook + workshop are both required ─────────────────────────
@pytest.mark.parametrize(
    "building_ids,label",
    [
        (["nook"], "nook-only"),
        (["workshop"], "workshop-only"),
        (["nook", "nook"], "two-nooks-no-workshop"),
    ],
)
def test_buildings_require_nook_and_workshop(building_ids: list[str], label: str):
    """buildings array must contain exactly one nook AND one workshop.

    Missing either, or supplying two of the same kind, must fail validation.
    The schema enforces this via minItems/maxItems + two `contains` constraints.
    """
    bad = copy.deepcopy(_SAMPLE)
    # Build the target list; for two-nooks case we need a copy of nook.
    nook = _find_building(bad, "nook")
    workshop = _find_building(bad, "workshop")
    by_id = {"nook": nook, "workshop": workshop}
    bad["buildings"] = [copy.deepcopy(by_id[bid]) for bid in building_ids]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _SCHEMA)


# ── 9. strength: null is accepted ──────────────────────────────────────────
def test_strength_null_accepted():
    """store_health.strength may be null (no strength rows yet in the store).

    The schema declares `"type": ["object", "null"]` for this field; a null
    value must pass validation so a freshly initialised store doesn't break
    the adapter.
    """
    ok = copy.deepcopy(_SAMPLE)
    ok["property"]["health"]["store"]["strength"] = None
    jsonschema.validate(ok, _SCHEMA)
