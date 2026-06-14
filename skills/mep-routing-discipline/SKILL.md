---
name: mep-routing-discipline
description: "Use when routing an MEP discipline (electrical/water/drainage/heating) on IFC layers, sizing a vent/chimney shaft, clash-checking a route, or scheduling fixtures/terminals. Do not use for: IFC unit/placement (→ ifc-geometry-discipline, co-load it); model mutation (→ freecad-architect); ifcopenshell API signatures (→ PAUSE research-docs-lookup); building-code clearance sufficiency (→ research-fact-checker)."
---

# MEP Routing Discipline

This skill encodes four procedures — per-discipline layer mapping, route-clash reasoning, vent/chimney shaft sizing, and fixture/terminal scheduling — that the consuming agent applies when deriving an MEP system layout for a parametric IFC BIM model. It is the MEP analog to `ifc-geometry-discipline` (for unit correctness) and `structural-design-discipline` (for structural sizing). This skill co-loads with `ifc-geometry-discipline` for coordinate correctness — the two skills are complementary, not substitutes. This skill has zero IFC unit/placement logic; `ifc-geometry-discipline` has zero MEP routing logic.

All four procedures are logic-heavy: per-discipline layer mapping requires classifying route segments by discipline and IFC entity type before assigning them to `IfcPresentationLayerAssignment` targets; clash-checking requires explicitly naming the structural and other-MEP elements tested against (untested "clear" is fabrication); shaft sizing requires reasoning from served-load inputs and cross-section rules; vertical continuity must be confirmed storey by storey. The consuming agent (`arch-mep-engineer`) applies the 5-link CoT chain per route segment throughout.

## When this skill binds

Fire this skill when any of these are true:

- You are routing an MEP discipline (electrical, water/supply, drainage, heating) on real IFC layers for a parametric BIM model.
- You are sizing a vent or chimney shaft from served-load inputs and minimum-clearance rules.
- You are assigning a flow segment or distribution element to a `IfcPresentationLayerAssignment` target.
- You are clash-checking an MEP route against structural elements or other MEP disciplines.
- You are scheduling fixtures or terminals by discipline and layer assignment.
- You are confirming vertical continuity of a shaft across storeys.

Do NOT fire this skill for:

- IFC unit domains, placement translations, Qto attachment, or `IfcPresentationLayerAssignment` schema validity → co-load `ifc-geometry-discipline`.
- Any model mutation or spec write → `freecad-architect`.
- ifcopenshell API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup` and stop.
- Building-code minimum clearance sufficiency verdict → `research-fact-checker`. This skill determines whether the geometric clearance is met; regulatory sufficiency is a distinct question for `research-fact-checker`.
- Genuine routing or inspection failure → `systematic-debugging`.

## 5-link CoT chain (required per route segment)

Every `@@MEP-ROUTE` row requires this chain written before the row is emitted:

1. **Discipline + endpoints** — name the discipline (electrical/water/drainage/heating) and the source and destination endpoints sourced from the spec or storey geometry (never invented).
2. **Candidate route through real room/storey geometry** — describe the candidate path through the named rooms and storey heights from the spec.
3. **Clash check vs structural + other MEP** — name every structural element and every other-MEP segment tested. A route declared "clear" without naming the tested set is fabrication (CLAUDE.md §4). A clash forces re-route before the row is written; an unresolved clash is a finding.
4. **Discipline-layer assignment** — state the target `IfcPresentationLayerAssignment` layer name and the IFC entity type (`IfcFlowSegment`, `IfcDistributionElement`, or `IfcBuildingElementProxy` for shafts).
5. **Sizing rationale** — state the pipe/duct/conduit size and the basis (served load from brief, cross-section rule, or → `research-fact-checker` for code-minimum).

## Procedure 1 — Per-discipline layer mapping

Each MEP discipline routes on a dedicated `IfcPresentationLayerAssignment` layer. The layer assignment is NOT optional — a flow segment with no layer assignment is a gap finding.

**Layer conventions (SAGE-GENERIC — actual layer names arrive via brief):**

- **Electrical** — conduit, cable tray, distribution boards: `IfcFlowSegment` (conduit/cable) + `IfcDistributionElement` (boards, panels). Layer assignment: electrical discipline layer named in the brief.
- **Water/supply** — supply pipes, manifolds: `IfcFlowSegment` (pipes) + `IfcDistributionElement` (fixtures). Layer: water discipline layer.
- **Drainage** — soil/waste/storm pipes, traps, inspection chambers: `IfcFlowSegment` + `IfcDistributionElement`. Layer: drainage discipline layer. Gravity-fed: slope direction confirmed in the route.
- **Heating** — radiator circuits, underfloor heating, manifolds, boiler connections: `IfcFlowSegment` + `IfcDistributionElement`. Layer: heating discipline layer.

**Procedure:**

1. From the brief, enumerate the target `IfcPresentationLayerAssignment` layer names for each discipline.
2. For each segment, classify the IFC entity type (`IfcFlowSegment` for conduit/pipe/duct; `IfcDistributionElement` for terminals, boards, fixtures; `IfcBuildingElementProxy` for shafts when no specific MEP entity applies).
3. Confirm the `IfcPresentationLayerAssignment.AssignedItems` is non-empty at write time (validity rule, per `ifc-geometry-discipline` Decision tree 4).
4. Emit the `@@MEP-ROUTE` block per segment.

## Procedure 2 — Route-clash reasoning

Clash-checking is mandatory before any `@@MEP-ROUTE` row is emitted. "Clear" without naming the tested elements is fabrication.

**Clash-check procedure:**

1. Identify every structural element in the route corridor (structural members, beams, slabs, walls). Source: `arch-structural-engineer` output or `IfcBeam`/`IfcSlab`/`IfcWall` enumeration from the spec.
2. Identify every other-MEP segment already routed in the same corridor (from prior `@@MEP-ROUTE` rows in the current CO or from the existing MEP spec state).
3. For each candidate route segment, check geometric clearance against both sets. Clearance is geometric (does the route fit in the available space given element cross-sections and offsets); regulatory sufficiency is `research-fact-checker`'s lane.
4. If a clash is found, re-route before writing the row. If re-route is not possible without changing geometry, surface a finding.
5. Emit `clash-status: clear (vs <named structural set> + <named MEP set>)` or `clash: <element class>@<location>`.

**Clash-status clear without named sets = fabrication.** The `@@MEP-ROUTE` block must carry the tested sets explicitly.

## Procedure 3 — Vent/chimney shaft sizing

Shafts serve defined loads (number of appliances, flue type, or ventilation volume). Size from served-load inputs and cross-section rules. Code-minimum cross-sections → `research-fact-checker`.

**Sizing procedure:**

1. Identify the shaft kind (supply ventilation, exhaust ventilation, sanitary soil-stack, chimney/flue).
2. State the served load from the brief (appliances connected, rooms ventilated, fuel type for flue).
3. Derive required cross-section from served-load basis. Minimum code-required cross-section → `pending research-fact-checker`.
4. Confirm provided cross-section (from spec geometry) meets the derived minimum.
5. Confirm vertical continuity: the shaft must pass through every storey from origin to termination without obstruction. For each storey boundary, confirm continuity status. A shaft broken at a storey boundary is `broken@<storey>` — a finding.
6. Minimum clearance between shaft and adjacent structure → `pending research-fact-checker`.
7. Emit `@@MEP-SHAFT` row per shaft.

## Procedure 4 — Fixture/terminal scheduling

Every fixture and terminal is scheduled by discipline and layer assignment. A fixture without a layer assignment is a gap finding.

**Scheduling procedure:**

1. From the brief and spec, enumerate all fixtures/terminals: light points, sockets, switches (electrical); taps, valves, water outlets (water); WCs, floor drains, traps (drainage); radiators, underfloor loops, manifolds (heating).
2. For each fixture, assign: discipline, IFC entity type, target layer (from Procedure 1 layer map), and connected segment (the `@@MEP-ROUTE` row it connects to).
3. Confirm no fixture is unscheduled — a fixture in the spec with no `@@MEP-TERMINAL` row is a gap finding.
4. Emit one `@@MEP-TERMINAL` row per fixture.

## Output blocks

The consuming agent emits structured blocks for each procedure applied.

**MEP route (one per segment):**

```
@@MEP-ROUTE BEGIN
discipline | segment id | from endpoint | to endpoint | route corridor | IFC entity type | target IfcPresentationLayerAssignment layer | clash-status (clear (vs <structural set> + <MEP set>) | clash:<element>@<location>) | size/section | sizing basis
@@MEP-ROUTE END
```

**Shaft (one per shaft):**

```
@@MEP-SHAFT BEGIN
shaft id | kind | served load | required cross-section | provided cross-section | min clearance (pending research-fact-checker) | clearance met (yes | no | pending research-fact-checker) | vertical continuity (continuous | broken@<storey>) | IFC entity type | finding
@@MEP-SHAFT END
```

**Terminal (one per fixture):**

```
@@MEP-TERMINAL BEGIN
fixture id | discipline | fixture type | IFC entity type | layer assignment | connected segment id | scheduled (yes | UNSCHEDULED)
@@MEP-TERMINAL END
```

Never collapse multiple segments, shafts, or fixtures into a single row.

## PAUSE routing

Three distinct PAUSE destinations — do not conflate:

- **Building-code clearance sufficiency** (minimum separation, fire-rated separation, mandated cross-section) → `PAUSE: need research-fact-checker for <subject>`.
- **ifcopenshell API signature** → `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- **Ambiguous brief** (missing endpoint, undefined discipline layer, unspecified served load) → `PAUSE: orchestrator must clarify <specific question>`.

## Inline invariants

These hold unconditionally before any procedure is entered.

**Never emit a route row without its 5-link chain.** Each `@@MEP-ROUTE` row is preceded by the discipline+endpoints → candidate route → clash check → layer assignment → sizing rationale chain. A row without its chain is incomplete.

**Never declare clash-status clear without naming the tested sets.** Both the structural set and the other-MEP set must be named explicitly. An untested "clear" is fabrication (CLAUDE.md §4).

**Never invent endpoint/height/served-load/layer.** All inputs arrive via brief, spec, or storey geometry from the model. Every invented coordinate or layer name is a §4 violation.

**Shaft sizing names served-load basis.** The `@@MEP-SHAFT` row states the served-load basis explicitly. A shaft sized without a stated served load is incomplete.

**Vertical continuity confirmed per storey.** Continuity is assessed at every storey boundary from shaft origin to termination. `broken@<storey>` is a finding; a blanket "continuous" claim without per-storey confirmation is unverified.

**Geometric clearance vs regulatory sufficiency.** This skill determines geometric fit (does the route/shaft fit in the available space?). Whether that clearance satisfies a building-code minimum is `research-fact-checker`'s question. Conflating the two is a lane violation.

**Co-load ifc-geometry-discipline for coordinate correctness.** MEP coordinates, storey heights, and shaft cross-sections are expressed in the IFC unit domain per `ifc-geometry-discipline`. This skill has zero unit-domain logic — always co-load for unit questions.

**SAGE-GENERIC.** Example values are house-reference shapes. No client names, project paths, or real layer-name strings appear in this file.

## Anti-patterns

- **Routing on a wrong or unstated layer.** Every segment carries its `IfcPresentationLayerAssignment` target. A segment assigned to no layer, or to a layer not declared for its discipline, is a finding.
- **Routing without a clash check.** Clash-status "clear" without naming both the structural and other-MEP tested sets is fabrication.
- **Asserting code compliance.** This skill checks geometric clearance; regulatory sufficiency is `research-fact-checker`'s lane.
- **Speculative spare capacity.** Do not add capacity beyond the served-load basis unless the brief names a future-proofing requirement.
- **Guessing a coordinate, height, or endpoint.** All endpoint geometry arrives from the spec or brief. A guessed coordinate is a §4 fabrication. Emit `PAUSE: orchestrator must clarify <specific question>` instead.
- **Self-certifying built MEP.** This skill derives the MEP spec; model correctness is `freecad-model-auditor`'s verdict.
- **Guessing an ifcopenshell API signature.** Emit the PAUSE shape instead — `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- **Claiming vertical shaft continuity without per-storey confirmation.** Continuity must be verified at each storey boundary; a blanket claim is unverified.
- **Conflating geometric clearance with regulatory sufficiency.** Geometric fit is this lane; code-minimum clearance is `research-fact-checker`'s lane.

## When NOT to use this skill

- IFC unit domains, placement translations, `IfcPresentationLayerAssignment` schema validity → co-load `ifc-geometry-discipline`.
- Model mutation → `freecad-architect`.
- ifcopenshell API signatures → emit `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- Building-code clearance sufficiency → `research-fact-checker`.
- Genuine routing or inspection failure → `systematic-debugging`.
- Pre-completion verification → `verification-before-completion` (load this skill alongside it for MEP items).
