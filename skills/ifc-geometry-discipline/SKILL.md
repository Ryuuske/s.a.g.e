---
name: ifc-geometry-discipline
description: "Use when writing or editing an ifcopenshell builder, deriving a placement matrix or unit conversion, or reasoning about which unit domain a value belongs to (project-mm vs SI-metres). Do not use for: build-test failure (→ systematic-debugging); pre-completion (→ verification-before-completion); failing test first (→ test-driven-development); API signature lookup (→ PAUSE for research-docs-lookup — wrong arg order silently corrupts geometry)."
---

# IFC Geometry Discipline

This skill encodes four decision trees — unit-domain identification, placement-matrix correctness, vertex filtering, and IFC validity — that the consuming agent applies in both author-mode (writing an ifcopenshell builder) and audit-mode (reviewing geometry-producing diffs). It also encodes the geometry-from-spec invariant, Qto attachment rules, and IfcConvert output rules.

This skill co-loads with `test-driven-development` (no overlap) and contributes IFC-specific verification items to `verification-before-completion` without duplicating its general procedure. It does not narrow `systematic-debugging` — that skill's triggers are bug, test failure, unexpected behavior, and stack trace; the unit-domain question is the entry point here, not failure investigation.

All four decision trees are logic-heavy: unit-domain identification requires classifying the call site against three distinct domains with non-obvious boundaries; placement-matrix correctness requires tracing the translation vector through domain conversions; vertex filtering requires filtering with scope-aware zero-drop logic; IFC validity requires entity-level structural knowledge of SET constraints. The consuming agent (freecad-architect) should apply CoT throughout all four trees.

## When this skill binds

Fire this skill when any of these are true:

- You are writing a new ifcopenshell builder or modifying an existing geometry-producing script.
- You are calling `unit.assign_unit`, `add_door_representation`, `add_window_representation`, `create_shape`, or any `geometry.edit_object_placement` helper.
- You are reasoning about whether a numeric value is in project units (millimetres by convention) or SI metres.
- You are passing a `--section-height` value to IfcConvert.
- You are deriving a placement translation vector and are uncertain which domain it must be in.
- You are puzzled by a bounding-box or vertex count that doesn't match the spec, and the question is which unit domain the value arrived from.
- You are writing an IfcPresentationLayerAssignment or an IfcPolygonalFaceSet for a hipped roof.

Do NOT fire this skill for:

- Geometry build test went red, investigate → `systematic-debugging`.
- About to claim the build is done → `verification-before-completion`.
- Write a failing test first → `test-driven-development`.
- Exact ifcopenshell 0.8.5 API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup` and stop; do not WebFetch or WebSearch directly. Wrong argument order in an ifcopenshell call silently corrupts geometry rather than raising a visible error.
- Designing an agent that writes IFC builders → `agent-creation` via `aidev-agent-creator`.

## Decision tree 1 — Unit-domain identification

Three distinct unit domains exist in an ifcopenshell workflow. They are not interchangeable and the API does not warn on domain crossing.

**The three domains:**

1. **PROJECT units** — defined by `unit.assign_unit`. The conventional default is MILLIMETRES. When calling `unit.assign_unit`, pass length, area, and volume explicitly: `length=MILLIMETERS`, `area=SQUARE_METERS`, `volume=CUBIC_METERS`. Omitting the explicit call leaves the project unit implementation-defined, which is a silent correctness risk.
   - `add_door_representation` and `add_window_representation` take arguments in PROJECT units. A 900 mm door passes as `900`. Under a MILLIMETRES project unit, `0.9` produces a 0.9 mm door.
   - Spec JSON dimensions are authoritative in PROJECT units. Read them from spec; do not hardcode.

2. **Geometry iterator / shape domain — always SI metres** — `create_shape` and the geometry iterator (`ifcopenshell.geom.iterate`) always output coordinates in SI metres regardless of the project unit assignment. Downstream bounding-box and area calculations on iterator output must operate in metres.
   - A bbox computed in metres on an iterator result is correct. Treating those metre values as millimetres inflates dimensions by 1 000×.

3. **IfcConvert domain — always SI metres** — `--section-height` for plan-cut output is SI metres. `--section-height 1.5` cuts at 1.5 m above the storey origin. `--section-height 1500` cuts 1 500 m above origin, above the roof of any ordinary building, producing an empty plan.

**Audit procedure:**

1. Identify every site that passes a numeric length/height/offset to an ifcopenshell or IfcConvert API.
2. Classify the site into one of the three domains.
3. Confirm the value is expressed in the correct unit for that domain.
4. Emit `@@IFC-UNIT-DOMAIN BEGIN` block.

```
@@IFC-UNIT-DOMAIN BEGIN
call site | domain (project-mm | iterator-metres | ifcconvert-metres) | value as written | correct for domain (yes | no) | finding
@@IFC-UNIT-DOMAIN END
```

## Decision tree 2 — Placement-matrix correctness

Placement translations passed to `geometry.edit_object_placement` are in SI metres, not in project units, regardless of the project unit assignment. This domain boundary is a common corruption site.

**Placement rules:**

- `geometry.edit_object_placement` translation components are SI metres. A raw millimetre value (e.g., 5 500 mm read from the spec) passed as the translation x-component places the element at x = 5 500 m. At metre scale the bounding-box fit logic shrinks neighbouring elements by a factor of ~1 000.
- Correct form: divide the spec mm value by 1 000 inside the placement helper before passing it to `geometry.edit_object_placement`.
- `create_shape` for doors and windows needs `settings.set("use-world-coords", True)` or the resulting bbox starts at (0, 0, 0) rather than the element's world position. Walls modelled in world coordinates with an identity placement do not require this flag — they already express vertices in world space.

**Audit procedure:**

1. Locate every call to `geometry.edit_object_placement` or equivalent placement mutation.
2. Trace the origin of each translation component back to the spec value and confirm the /1000 conversion is present.
3. Locate every `create_shape` call for doors or windows and confirm `use-world-coords` is set.
4. Emit `@@IFC-PLACEMENT-DECISION BEGIN` block.

```
@@IFC-PLACEMENT-DECISION BEGIN
element type | translation source (spec-mm / spec-metres / computed) | /1000 conversion present (yes | no | n/a) | use-world-coords required (yes | no) | use-world-coords set (yes | no | n/a) | finding
@@IFC-PLACEMENT-DECISION END
```

### Worked example — placement translation domain

A door element spec carries `x_offset: 1200` (project-mm). The placement helper writes:

```python
# WRONG — passes raw mm as if it were metres
matrix[0][3] = spec["x_offset"]          # 1200 → element at x=1200 m
```

Corrected form:

```python
# CORRECT — converts to SI metres before passing to geometry.edit_object_placement
matrix[0][3] = spec["x_offset"] / 1000   # 1200 mm → 1.2 m
```

The `@@IFC-PLACEMENT-DECISION` block for the corrected form:

```
@@IFC-PLACEMENT-DECISION BEGIN
element type | translation source | /1000 conversion | use-world-coords required | use-world-coords set | finding
door         | spec-mm (1200)     | yes              | yes                       | yes                  | none
@@IFC-PLACEMENT-DECISION END
```

## Decision tree 3 — Vertex filter

ifcopenshell 0.8.5 emits one garbage vertex per shape with magnitude ~1e72 (a sentinel / uninitialised value). It also emits exact (0, 0, 0) degenerate vertices that pass a naive `|v| < 1e6` finite check. Both must be excluded before bbox or area calculations.

**Filter rules:**

- Filter 1 — finiteness: discard any vertex where any coordinate is not finite (`math.isfinite`). This removes the ~1e72 garbage vertex.
- Filter 2 — degenerate zero: discard the exact origin (0, 0, 0) — but scope this exclusion to the geometry extraction context. A floor slab legitimately has vertices at (0, 0, 0) if it is modelled at the origin. Apply the zero-drop only when processing shape-iterator output, not when processing structural slab vertex lists directly.

**Audit procedure:**

1. Locate every site that computes a bbox, area, or centroid from iterator vertex output.
2. Confirm both filters are applied in sequence: finite first, then zero-drop with the scope note.
3. Emit `@@IFC-VERTEX-FILTER BEGIN` block.

```
@@IFC-VERTEX-FILTER BEGIN
calculation site | finite-filter present (yes | no) | zero-drop present (yes | no) | zero-drop scoped to iterator output (yes | no | n/a) | finding
@@IFC-VERTEX-FILTER END
```

## Decision tree 4 — IFC validity

**Validity rules:**

- `IfcPresentationLayerAssignment` requires `AssignedItems` to be `SET[1:?]` (non-empty). An empty `IfcPresentationLayerAssignment` is schema-invalid. Use `IfcGroup` as the placeholder entity when a logical group has no assigned items at creation time.
- An empty or unused representation context is droppable without validity impact.
- A hipped roof geometry must be a **closed** `IfcPolygonalFaceSet`: the vertex point list covers all ridge, hip, eave, and gable corners, and every face is an indexed face in the face set. An unclosed face set produces rendering gaps and may fail downstream validation.

**Audit procedure:**

1. Locate every `IfcPresentationLayerAssignment` creation. Confirm `AssignedItems` is populated before the entity is written.
2. Locate every `IfcPolygonalFaceSet` used for roof geometry. Confirm it is closed.
3. Emit `@@IFC-VALIDITY-DECISION BEGIN` block.

```
@@IFC-VALIDITY-DECISION BEGIN
entity | validity rule | compliant (yes | no | n/a) | finding
IfcPresentationLayerAssignment | AssignedItems SET[1:?] non-empty | ? | ?
IfcPolygonalFaceSet (roof) | closed face set | ? | ?
@@IFC-VALIDITY-DECISION END
```

## Inline invariants

These hold unconditionally and are not subject to the decision-tree audit procedure — they apply before any tree is entered.

**Geometry-from-spec.** The spec JSON is the single source of truth for all geometry. Never hardcode a dimension, offset, or coordinate in a builder. Never hand-edit a generated `.ifc` file — the generated file is an output, not a source. Re-run the full builder VERIFY step after any spec edit including a comment change, because comments adjacent to values have been known to shift downstream parsing in YAML/JSON-with-comments toolchains.

**Qto attachment.** When a builder attaches an `IfcElementQuantity` / `BaseQuantities` set to an element, the set must satisfy all of the following: (a) non-empty — an `IfcElementQuantity` with no quantity items is schema-invalid; (b) the `IfcElementQuantity` is attached via an `IfcRelDefinesByProperties` where the `IfcElementQuantity` is the `RelatingPropertyDefinition` and `RelatedObjects` includes the element being quantified; (c) quantity names are schema-valid for that element type (e.g., `NetSideArea` is valid for `IfcWall`, not `GrossFloorArea`); (d) quantity values are sourced from the spec JSON — geometry-from-spec applies here identically to placement and dimension values, never hardcoded; (e) each quantity value is expressed in the unit its quantity kind takes from the project's `IfcUnitAssignment` — and these are NOT a single domain: the `unit.assign_unit` call sets `length=MILLIMETERS`, `area=SQUARE_METERS`, `volume=CUBIC_METERS`, so an `IfcQuantityLength` is in mm, an `IfcQuantityArea` in m², and an `IfcQuantityVolume` in m³. The finding is a quantity value whose magnitude implies a unit other than its kind's declared assignment — e.g. a length quantity written in SI metres under the mm length unit (1000× wrong), or an area written in mm² under the m² area unit (1,000,000× wrong). These quantity units are distinct again from placement translations, which are always SI metres (Decision tree 2 / `edit_object_placement`): three separate unit considerations — placement in SI metres, length quantities in mm, area/volume quantities in m²/m³. Conflating them is the error.

**IfcConvert SVG includes.** IfcConvert SVG output includes only `IfcSpace` entities by default. To include walls, slabs, doors, or windows in a plan SVG, pass an explicit `--include` filter. Omitting it and expecting walls to appear is a silent omission.

## Function-reference verification

Never guess an ifcopenshell API signature. If uncertain whether `add_door_representation` takes `width` before `height`, or whether `geometry.edit_object_placement` expects a column-major or row-major matrix, emit:

```
PAUSE: need research-docs-lookup for <subject> reference lookup
```

Stop there. Do not attempt the call with a guessed signature. Wrong argument order in ifcopenshell calls silently corrupts geometry rather than raising a visible error — a silent wrong-dimension door is far harder to diagnose than an exception.

### When this skill PAUSEs

The PAUSE shape above is the ADR-0027 pattern. `research-docs-lookup` is in the active roster. When the PAUSE fires, the orchestrator dispatches `research-docs-lookup` to resolve the API signature. ADR-0027 cites ADR-0024 as directional precedent for the gap-naming-with-user-action-remediation pattern (ADR-0024 established explicit user documentation over auto-magic as the preference for bounded-and-shrinking gaps; ADR-0027 applied that directionality to name the PAUSE gap and document user-action remediation while research-docs-lookup was pending).

## Output blocks

The consuming agent emits structured blocks for each decision tree applied. All blocks use the delimiter pattern established across the agent roster.

**Unit-domain audit:**
```
@@IFC-UNIT-DOMAIN BEGIN
call site | domain (project-mm | iterator-metres | ifcconvert-metres) | value as written | correct for domain (yes | no) | finding (none | wrong-domain)
@@IFC-UNIT-DOMAIN END
```

**Placement-matrix decision:**
```
@@IFC-PLACEMENT-DECISION BEGIN
element type | translation source (spec-mm | spec-metres | computed) | /1000 conversion present (yes | no | n/a) | use-world-coords required (yes | no) | use-world-coords set (yes | no | n/a) | finding
@@IFC-PLACEMENT-DECISION END
```

**Vertex filter:**
```
@@IFC-VERTEX-FILTER BEGIN
calculation site | finite-filter present (yes | no) | zero-drop present (yes | no) | zero-drop scoped to iterator output (yes | no | n/a) | finding
@@IFC-VERTEX-FILTER END
```

**IFC validity:**
```
@@IFC-VALIDITY-DECISION BEGIN
entity | validity rule | compliant (yes | no | n/a) | finding
@@IFC-VALIDITY-DECISION END
```

API-signature uncertainty surfaces as a standalone `PAUSE:` line before any code is emitted.

## Anti-patterns

- **Passing a raw project-mm value to `geometry.edit_object_placement`.** The translation domain is SI metres. A raw millimetre value places the element ~1 000× too far; the resulting bounding box shrinks all neighbours when fit logic runs.
- **Treating geometry-iterator output as project units.** The iterator always outputs SI metres. Bbox / area math on those values in millimetres inflates dimensions by 1 000×.
- **Passing `--section-height` in millimetres to IfcConvert.** IfcConvert expects SI metres. A millimetre value produces a section height above the roof of any ordinary building, returning an empty SVG plan.
- **Omitting `use-world-coords` for door/window `create_shape` calls.** The resulting bbox starts at (0, 0, 0) regardless of the element's placement in the model.
- **Applying the zero-drop filter without scoping it to iterator output.** Floor slabs legitimately have vertices at (0, 0, 0). A global zero-drop corrupts slab geometry.
- **Hardcoding geometry in a builder.** Any builder that writes a literal coordinate not read from the spec is a geometry-from-spec violation. All dimensions, offsets, and extents come from the spec JSON.
- **Hand-editing a generated `.ifc` file.** The file is an output. Edits are overwritten on the next builder run and leave the spec and model out of sync.
- **Creating `IfcPresentationLayerAssignment` with an empty `AssignedItems`.** Schema-invalid. Use `IfcGroup` as a placeholder.
- **Guessing an ifcopenshell API signature.** Emit the PAUSE shape instead; wrong signatures produce silent geometry corruption.
- **Omitting `--include` when expecting non-IfcSpace entities in IfcConvert SVG output.** IfcConvert includes only IfcSpace by default; walls and doors are silently absent without an explicit include filter.

## Output guidance

### Semantic guidance

- Never claim a builder is correct without naming the unit-domain classification for every numeric value passed to an API call.
- Never insert a placement translation without naming whether it is expressed in SI metres (required) or project-mm (must convert).
- Never compute a bbox or area from iterator output without confirming both vertex filters (finite + scoped zero-drop) are applied.
- Never write an `IfcPresentationLayerAssignment` without confirming `AssignedItems` is non-empty at write time.
- In author-mode: every spec-sourced dimension is read from the spec JSON before work is considered done. In audit-mode: any hardcoded literal dimension in a builder is a finding.

### Tool guidance

- **Read** — view the builder script in full before applying any decision tree (CLAUDE.md §4; do not edit a file you have not read).
- **Grep** — scan for `edit_object_placement`, `add_door_representation`, `add_window_representation`, `create_shape`, `assign_unit`, `section-height`, `IfcPresentationLayerAssignment`, `IfcPolygonalFaceSet`, and numeric literals that might be unit-domain violations.
- **Glob** — locate builder scripts when the brief names a geometry area without an exact path.
- **No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.
- **No WebFetch or WebSearch** — API-signature uncertainty emits a `PAUSE:` line only (ADR-0027 shape); the orchestrator dispatches `research-docs-lookup`.

## When NOT to use this skill

- Geometry build test went red, investigate → `systematic-debugging`.
- Writing a failing test for a builder first → `test-driven-development`.
- General pre-completion verification → `verification-before-completion` (load this skill alongside it for the IFC-specific items, but `verification-before-completion` governs the overall procedure).
- Exact ifcopenshell API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup`; the orchestrator dispatches `research-docs-lookup` (ADR-0027).
- Any non-IFC geometry pipeline.
