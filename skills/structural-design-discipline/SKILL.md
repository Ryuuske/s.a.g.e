---
name: structural-design-discipline
description: "Use when selecting a foundation system (piles/grillage/plinth/precast slab), tracing a load path roof-to-footing, sizing structural elements by limit-state, or scheduling lintels per opening. Not for: code/norm VALUES (→ PAUSE research-fact-checker; never invent); IFC unit/placement (→ ifc-geometry-discipline); PDF dim extraction (→ pdf-vector-extraction-discipline); ifcopenshell API (→ PAUSE research-docs-lookup)."
---

# Structural Design Discipline

This skill encodes four decision trees — foundation-system selection, load-path tracing, limit-state checking, and lintel sizing — that the consuming agent applies in both author-mode (deriving a structural spec from a parametric BIM model) and audit-mode (reviewing a structural spec against a CO). It encodes METHOD, not TABLES: every code value, allowable stress, load combination, bearing capacity, or soil parameter routes to a `PAUSE: need research-fact-checker for <subject>` before the cell is filled. The consuming agent (`arch-structural-engineer`) applies CoT throughout all four trees.

This skill co-loads with `ifc-geometry-discipline` (no overlap — that skill has zero structural logic; this skill has zero IFC-expression logic) and with `pdf-vector-extraction-discipline` (which supplies span, opening, and level inputs; this skill never re-extracts them). It complements `verification-before-completion` without duplicating its general procedure.

All four decision trees are logic-heavy: foundation-system selection requires reasoning over soil, load, constructability, and differential-settlement risk simultaneously; load-path tracing requires geometric accumulation of tributary loads through a multi-storey hierarchy; limit-state checking requires classifying governing states (strength vs deflection vs bearing) before sizing; lintel sizing requires accumulating load-above from masonry-triangle geometry before specifying a section. The consuming agent should apply CoT throughout.

## When this skill binds

Fire this skill when any of these are true:

- You are selecting a foundation type (piles, grillage, plinth/strip, precast slab/raft) from soil, load, and span inputs.
- You are tracing the load path from roof-to-footing through a multi-storey assembly.
- You are sizing a beam, slab, column, or wall element by limit-state reasoning (strength, deflection, or bearing).
- You are scheduling lintels over openings — one row per opening, never collapsed.
- You are mapping structural elements to IFC entities (IfcPile, IfcFooting, IfcBeam, IfcMember, IfcSlab, IfcColumn) for `arch-structural-engineer`.
- You are confirming whether a span, opening, or level value arriving from `pdf-vector-extraction-discipline` is sufficient to drive a sizing decision.

Do NOT fire this skill for:

- Any code/norm VALUE (allowable stress, load factors, load combinations, bearing capacity, deflection limits, mandated bearing lengths, cover requirements, imposed-load categories, material unit weights) → emit `PAUSE: need research-fact-checker for <subject>` and stop; never invent or recall a norm value (CLAUDE.md §4).
- IFC entity creation, unit domains, placement translations, or Qto attachment → `ifc-geometry-discipline`.
- Extracting a span, opening, or level from a source drawing → `pdf-vector-extraction-discipline`.
- ifcopenshell API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup` and stop.
- A genuine BUILD/VERIFY failure on the structural spec → `systematic-debugging`.

## Hard boundary — no norm value from memory

**NEVER encode, recall, or invent a code/norm VALUE.** This boundary has no exceptions and no threshold below which a "well-known" value is safe to use. Values affected include (non-exhaustive): allowable compressive or tensile stress; imposed load categories and magnitudes; load combination factors; soil-bearing capacity (allowable or ultimate); pile capacity estimates; deflection limits (span fractions); mandated bearing lengths for lintels; concrete/steel/masonry unit weights; required cover.

Every cell in every output block that would hold a norm value instead reads:

```
pending research-fact-checker
```

The PAUSE fires before the row is written:

```
PAUSE: need research-fact-checker for <code/norm subject>
```

Encoding METHOD here while routing VALUES to `research-fact-checker` is the contract. Violating it is a CLAUDE.md §4 fabrication.

## CoT contract

Every output row — `@@FOUNDATION-SELECTION`, `@@STRUCTURAL-ELEMENT`, `@@LINTEL-SCHEDULE` — requires its four-beat chain-of-thought before the row is written:

1. **Governing inputs** — the span, load, soil parameter, or opening dimension sourced from the spec or from `pdf-vector-extraction-discipline` output.
2. **Applicable rule / limit state** — which limit state governs (strength, deflection, or bearing) or which foundation decision axis governs (bearing capacity, differential settlement, constructability).
3. **Tradeoff across candidate sizes/systems** — name at least two candidates and the axis on which one is rejected.
4. **Selected element + size + rationale** — the single selected option with the governing constraint named.

Beat 4's governing constraint becomes the `rationale` field in the output block. No row without its chain.

## Decision tree 1 — Foundation-system selection

Select the appropriate foundation type from soil, load, and constructability inputs. The decision axes are bearing capacity, differential-settlement risk, span/column-grid layout, and site constructability.

**Decision tree:**

- Soft soil or deep competent stratum, high differential-settlement risk → **piles** (driven or bored to the competent stratum; pile type and capacity → `research-fact-checker`).
- Moderate but variable bearing, differential-settlement risk elevated → **grillage** (reinforced concrete beam grid distributes load; grid dimensions and reinforcement → `research-fact-checker`).
- Adequate shallow bearing, predominantly linear/wall loads → **plinth / strip** (continuous under load-bearing walls; width and depth → `research-fact-checker`).
- Adequate shallow bearing, distributed/stiff-plane loads, low differential-settlement risk → **precast slab / raft** (precast-slab units or cast raft; thickness and spans → `research-fact-checker`).
- Constructability constraint can override any of the above and is named when it governs.

**Procedure:**

1. State the governing soil/bearing parameter sourced from the brief (never invented). If absent, surface `PAUSE: orchestrator must clarify soil/bearing data`.
2. Apply the four axes in order; record the axis at which a candidate is eliminated.
3. Name the constructability override if applicable.
4. Emit `@@FOUNDATION-SELECTION` row per zone. Every allowable value reads `pending research-fact-checker`.

## Decision tree 2 — Load-path tracing

Trace the structural load path from the origin (roof/snow/imposed) to the footing, accumulating tributary load geometrically at each level. Every support node on the path is queued for Decision tree 3. A support on the path with no sizing is a finding.

**Procedure:**

1. Identify the load origin: roof structure (dead + snow/imposed), floor slabs, live loads by occupancy.
2. Trace the bears-on chain: slab → beam/wall → column → footing → stratum.
3. At each node, accumulate tributary load: tributary area × load intensity (intensity = `pending research-fact-checker` unless supplied via brief).
4. Record the accumulated reaction at each support node.
5. Queue every support node (beam, column, wall, footing) for Decision tree 3.
6. Flag any support on the path that lacks a sizing decision — it is a gap finding.

Unit weight values (masonry, concrete, steel) read `pending research-fact-checker` unless the brief supplies them from a `research-fact-checker` output. Never assume a unit weight.

## Decision tree 3 — Limit-state checks

Size each element by the governing limit state: strength (capacity ≥ demand), deflection (often governs for house spans), or bearing (reaction / contact area ≤ allowable).

**Procedure:**

1. **Strength check.** Derive demand (bending moment, shear, axial force) from the tributary reaction and span from Decision tree 2. Allowable capacity → `pending research-fact-checker`. Name the governing section or element class.
2. **Deflection check.** For beams and slabs, deflection often governs for residential spans. Deflection limit → `pending research-fact-checker`. Flag when deflection is the likely governing state (name basis: span/depth ratios or expected loading).
3. **Bearing check.** Derived reaction from Decision tree 2, contact area from the section selected in the strength check. Allowable bearing → `pending research-fact-checker`.
4. **Name the governing state.** The output block carries the governing limit state (strength | deflection | bearing) as a field.

For each element, the selected size enters the `@@STRUCTURAL-ELEMENT` block. The allowable/limit value always reads `pending research-fact-checker`.

## Decision tree 4 — Lintel sizing per opening

Size a lintel for every opening in the structural envelope. One row per opening; never collapsed. The lintel spans the clear width plus bearing lengths each side.

**Procedure:**

1. **Load above.** Compute load-above from the masonry-triangle geometry (45° spread or as supplied by brief). Unit weight of masonry → `pending research-fact-checker`. Include any floor/roof landing load if the opening is below a bearing line.
2. **Effective span.** Effective span = clear opening width + bearing length each side. Bearing length → `pending research-fact-checker`.
3. **Required section.** Apply Decision tree 3 (strength and deflection checks) to the lintel as a simply-supported beam over the effective span with the load-above as the imposed load.
4. **Bearing check.** Derive the lintel end reaction, divide by the bearing area, compare to allowable bearing stress on the supporting masonry. Allowable bearing → `pending research-fact-checker`.
5. Emit one `@@LINTEL-SCHEDULE` row per opening.

## Output blocks

The consuming agent emits structured blocks for each decision tree applied.

**Foundation selection:**

```
@@FOUNDATION-SELECTION BEGIN
zone | governing soil/bearing input | selected system | governing axis (bearing|settlement|constructability) | ruling out alternative | allowable bearing (pending research-fact-checker) | IFC entity
@@FOUNDATION-SELECTION END
```

**Structural element:**

```
@@STRUCTURAL-ELEMENT BEGIN
element | zone/member | tributary reaction | governing limit state (strength|deflection|bearing) | selected size | allowable value (pending research-fact-checker) | IFC entity | rationale
@@STRUCTURAL-ELEMENT END
```

**Lintel schedule:**

```
@@LINTEL-SCHEDULE BEGIN
opening id | clear width mm | load above basis | effective span mm | selected section | bearing each side mm (pending research-fact-checker) | end reaction | allowable bearing (pending research-fact-checker) | IFC entity | governing state
@@LINTEL-SCHEDULE END
```

All allowable/limit fields that hold a norm value read `pending research-fact-checker`.

## PAUSE routing

Three distinct PAUSE destinations — do not conflate:

- **Code/norm VALUE** (allowable stress, load combination, bearing capacity, deflection limit, unit weight, mandated bearing, cover) → `PAUSE: need research-fact-checker for <subject>`.
- **ifcopenshell API signature** → `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- **Ambiguous brief** (missing soil data, unclear span, undefined load) → `PAUSE: orchestrator must clarify <specific question>`.

## Inline invariants

These hold unconditionally and are not subject to the decision-tree procedure.

**No norm value from memory.** See Hard boundary above. No exception.

**Method here; expression elsewhere.** This skill encodes structural reasoning method. IFC expression (unit domains, placement translations, entity creation) is `ifc-geometry-discipline`'s lane — zero overlap.

**Inputs extracted, not derived here.** Span, opening width, and level heights arrive from `pdf-vector-extraction-discipline` output or from the spec via the brief. This skill never re-extracts dimensions from a drawing.

**Every selection names its governing constraint.** A selected system or section without a named governing constraint (bearing, deflection, constructability) is incomplete — the constraint must appear in the rationale field.

**Trace before sizing.** Load-path tracing (Decision tree 2) runs before any sizing (Decision tree 3). Sizing without a traced path is a gap finding.

**SAGE-GENERIC.** Example values in this skill are house-reference shapes. No project-specific constants, real client names, or real site parameters appear in this file.

## Worked example — lintel (SAGE-GENERIC)

Opening: 1 800 mm clear width in an external masonry wall, one storey of masonry above, no floor landing. All norm values are placeholder — this example encodes method only.

**CoT chain:**

1. **Governing inputs:** clear width = 1 800 mm (from spec); masonry height above = 1 storey; unit weight = `pending research-fact-checker`; bearing capacity of supporting masonry = `pending research-fact-checker`.
2. **Applicable rule:** simple beam over effective span; strength (bending) and deflection checks both applicable; bearing check governs at end supports.
3. **Candidates:** 2× 100×200 mm steel lintel vs precast RC lintel; deflection governs for 1 800 mm span and masonry load (likely — confirm with limit from `research-fact-checker`).
4. **Selected:** defer section selection pending `research-fact-checker` output for load and limit values; bearing-length sizing also deferred.

```
@@LINTEL-SCHEDULE BEGIN
opening id | clear width mm | load above basis | effective span mm | selected section | bearing each side mm | end reaction | allowable bearing | IFC entity | governing state
EXT-1      | 1800          | masonry 1-storey triangle | 1800 + 2×(pending research-fact-checker) | pending research-fact-checker | pending research-fact-checker | pending research-fact-checker | pending research-fact-checker | IfcMember | pending research-fact-checker
@@LINTEL-SCHEDULE END
```

No cell is fabricated. Every norm-value slot reads `pending research-fact-checker`.

## Anti-patterns

- **Inventing a code or norm value.** Any allowable stress, load combination factor, bearing capacity, deflection limit, or unit weight produced from memory is a CLAUDE.md §4 fabrication. The cell must read `pending research-fact-checker`.
- **Sizing without a CoT chain.** Every `@@STRUCTURAL-ELEMENT` and `@@LINTEL-SCHEDULE` row requires the four-beat chain before it is written. A row without its chain is incomplete.
- **Tracing load path from memory of geometry.** All span, opening, and level inputs arrive from the spec or `pdf-vector-extraction-discipline` output. Never derive geometry from memory.
- **Collapsing multiple openings into one lintel row.** Each opening gets its own row — spans and loads differ, and collapse loses the per-opening governing state.
- **Asserting code compliance.** This skill derives dimensions and sizing; compliance verdict belongs to `research-fact-checker`.
- **Pricing quantities.** Quantities are this skill's output; pricing is `fin-*` lane.
- **Skipping bearing check on lintel ends.** Bearing stress at masonry supports often governs for short, heavily loaded spans. The check is mandatory.
- **Treating deflection as secondary.** For residential spans, deflection frequently governs over strength. Always check both and name the governing state.

## When NOT to use this skill

- Any code/norm VALUE → emit `PAUSE: need research-fact-checker for <subject>` and stop.
- IFC unit domains, placement matrices, Qto attachment → `ifc-geometry-discipline`.
- Extracting spans or openings from a source PDF → `pdf-vector-extraction-discipline`.
- ifcopenshell API signature → emit `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- Structural build-test failure → `systematic-debugging`.
- Pre-completion verification → `verification-before-completion` (load this skill alongside it for structural items).
