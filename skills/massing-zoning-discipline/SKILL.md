---
name: massing-zoning-discipline
description: "Use when generating distinct massing strategies (bar/courtyard/L/podium) from program + site envelope, zoning by function and adjacency, testing GFA vs envelope, or ranking schemes by governing constraint. Not for: site/norm VALUES (setback/FAR/GFA cap/solar angle → PAUSE research-fact-checker; never invent); IFC unit/placement (→ ifc-geometry-discipline); model mutation (→ freecad-architect); ifcopenshell API (→ PAUSE research-docs-lookup); framing (→ arch-visionary)."
---

# Massing and Zoning Discipline

This skill encodes five procedures — massing-strategy generation, functional zoning, orientation/solar and circulation reasoning, program-fit testing, and dominator comparison — that the consuming agent applies when generating and ranking distinct concept schemes from a brief and site envelope. It is the concept-phase analog to `structural-design-discipline` (which derives structural systems from sized inputs) and `ifc-geometry-discipline` (which handles unit domains and IFC entity creation). The consuming agent (`arch-concept-designer`) applies these procedures in order for each scheme set it generates.

This skill co-loads with `ifc-geometry-discipline` for the IFC mapping step (zones→IfcSpace, levels→IfcBuildingStorey) — there is zero overlap between the two skills. `ifc-geometry-discipline` carries all unit-domain and placement-mechanics logic; this skill carries zero IFC-expression logic. Site and norm values route to `research-fact-checker`; ifcopenshell API signatures route to `research-docs-lookup`. Neither routes here.

The five procedures are logic-heavy at the comparison stage: program-fit requires accumulating GFA against an envelope cap; dominator analysis requires multi-axis ranking with a named governing constraint per pair. The consuming agent applies CoT before every `@@CONCEPT-COMPARISON` row — the 4-beat chain (candidates → governing constraint → dominator analysis → ranked recommendation) is mandatory, not optional.

## When this skill binds

Fire this skill when any of these are true:

- You are generating ≥2 distinct massing strategies (bar, courtyard, L-shape, podium, or other envelope types) from a program schedule and site envelope.
- You are zoning a floor plan by function (public/private/service) and testing adjacency requirements against the layout.
- You are reasoning about orientation, solar access, or a circulation spine from brief inputs.
- You are testing GFA against an envelope cap or footprint constraint.
- You are ranking concept schemes by a governing constraint using dominator analysis.
- You are expressing a chosen concept as an IFC-massing-mapped scheme (zones → IfcSpace, levels → IfcBuildingStorey) for handoff to `freecad-architect`.

Do NOT fire this skill for:

- Any site or norm VALUE (setback distance, height limit, FAR cap, GFA cap, solar angle, soil bearing, access road width, parking-ratio requirement) → emit `PAUSE: need research-fact-checker for <subject>` and stop; the cell reads `pending research-fact-checker`. Never invent or recall a site/norm value (CLAUDE.md §4).
- IFC entity creation, unit domains, placement translations, or Qto attachment → `ifc-geometry-discipline` (co-load; does not overlap with this skill).
- ifcopenshell API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup` and stop.
- Model geometry mutation or IFC regeneration → `freecad-architect`.
- Design framing or brief narrative → `arch-visionary`.

## Hard boundary — no site or norm value from memory

**NEVER encode, recall, or invent a site/norm VALUE.** This boundary has no exceptions and no threshold below which a "well-known" value is safe to use. Values affected include (non-exhaustive): setback distances (front, rear, side); building height limits; floor-area ratio (FAR) caps; gross floor area (GFA) caps; solar azimuth and altitude angles; site access constraints; soil bearing capacity; parking ratios; minimum room dimensions mandated by regulations.

Every cell in every output block that would hold a site or norm value instead reads:

```
pending research-fact-checker
```

The PAUSE fires before the row is written:

```
PAUSE: need research-fact-checker for <site/norm subject>
```

Encoding METHOD here while routing VALUES to `research-fact-checker` is the contract. Violating it is a CLAUDE.md §4 fabrication.

## CoT contract

Every `@@CONCEPT-COMPARISON` row requires its 4-beat chain-of-thought before the row is written:

1. **Candidates enumerated** — list the schemes under comparison by label (scheme A, scheme B, …).
2. **Governing constraint per scheme** — for each candidate, name the single constraint that most limits or enables it (program-fit | adjacency | orientation/solar | envelope/GFA cap | site access).
3. **Dominator analysis** — scheme A dominates scheme B if and only if scheme A is no worse than scheme B on every comparison axis AND strictly better on the governing constraint. Apply this per pair. Name the pair and the verdict.
4. **Ranked recommendation** — the top-ranked scheme with its governing constraint as the rationale field.

A `@@CONCEPT-COMPARISON` row without its preceding 4-beat chain is incomplete. Beat 4's governing constraint becomes the `rationale` field in the output block.

## Procedure 1 — Massing-strategy generation

Generate ≥2 distinct massing strategies from the program schedule and site envelope supplied in the brief. Envelope dimensions arrive from the brief; never invented. At least two strategies must be structurally distinct (bar vs courtyard; L-shape vs compact; podium vs dispersed) — two variants of the same type do not satisfy this requirement.

**Procedure:**

1. Confirm the program schedule and envelope footprint are present in the brief. If any dimension is absent, surface `PAUSE: orchestrator must clarify <specific dimension>`.
2. For each massing type considered, name the structural rationale (e.g., bar for linear site and single aspect, courtyard for internal amenity and perimeter efficiency).
3. Derive the approximate GFA for each massing from footprint × storeys using brief values. Any site/norm cap that bounds this → `pending research-fact-checker`.
4. Assign a short label to each scheme (Scheme A, Scheme B, …) and carry it consistently through all subsequent procedures.
5. Emit one `@@CONCEPT-SCHEME` block per scheme.

## Procedure 2 — Functional zoning

Zone the floor plan for each scheme by function: public (entrance, living, reception), private (sleeping, utility, secure storage), and service (kitchen, bathroom, plant, circulation vertical). Test whether required adjacencies named in the program schedule are satisfied.

**Procedure:**

1. From the program schedule in the brief, enumerate required adjacencies (e.g., kitchen adjacent to dining; bathroom accessible from every bedroom zone).
2. For each scheme, map functions to zones and identify which zone type each room belongs to (public | private | service).
3. Test each required adjacency: is the required pair in adjacent or connected zones? Flag violated required adjacency as a finding.
4. Confirm the public/private separation is maintained (service and private functions are not exposed through a public path without a transition zone).
5. Record the adjacency-pass/fail result per scheme. A violated required adjacency is a failing mark for that scheme on the adjacency axis in Procedure 5.

## Procedure 3 — Orientation, solar access, and circulation spine

For each scheme, reason about the orientation relative to the cardinal directions stated in the brief, assess solar access to the main habitable spaces, and trace the single primary circulation spine.

**Procedure:**

1. Source the site orientation from the brief (cardinal direction, north arrow, street frontage). If absent, surface `PAUSE: orchestrator must clarify site orientation`.
2. Reason about which facades in each scheme face south/north/east/west relative to the brief's orientation. Solar angle values → `pending research-fact-checker`.
3. For each scheme, identify which habitable spaces (living, sleeping) face the preferred solar orientation (south-facing in northern hemisphere; brief may override). Flag schemes where main habitable spaces face the unfavorable direction.
4. Trace the single primary circulation spine: the main horizontal movement path from entrance to every habitable zone. Schemes with multiple competing spines or dead-end zones are noted.
5. Record the orientation/solar verdict (favourable | unfavourable | partial) and the spine verdict (single | fragmented) per scheme.

## Procedure 4 — Program-fit test

Test each scheme's GFA against the brief's program schedule total and any envelope or site cap (cap value → `pending research-fact-checker`).

**Procedure:**

1. Sum the program schedule areas from the brief: room-by-room area total = program minimum GFA.
2. Derive each scheme's envelope GFA: footprint × storeys, using brief values. Any FAR or GFA cap → `pending research-fact-checker`.
3. Test: does scheme GFA ≥ program minimum GFA? If not, the scheme cannot accommodate the program at the stated footprint — record as a program-fit FAIL.
4. Test: does scheme GFA ≤ any cap (if cap value is known from brief or research-fact-checker output)? A scheme that exceeds the cap has a planning-constraint conflict — record as a cap-exceed FAIL.
5. Record the program-fit verdict (PASS | FAIL | PENDING-cap-value) per scheme.

## Procedure 5 — Dominator comparison

Rank the schemes by applying the dominator analysis. Scheme A dominates scheme B if and only if scheme A is no worse than scheme B on every axis AND strictly better on the governing axis.

**Comparison axes (SAGE-GENERIC — map brief constraints to these axes):**

- **Program fit** — GFA ≥ program minimum and ≤ cap (Procedure 4 result).
- **Adjacency** — required adjacencies satisfied (Procedure 2 result).
- **Orientation/solar** — main habitable spaces face preferred orientation (Procedure 3 result).
- **Envelope/GFA cap** — scheme within any hard cap (Procedure 4 result).
- **Site access** — scheme satisfies site-access and setback requirements (value → `pending research-fact-checker`).

**Procedure:**

1. For each scheme pair (A vs B), apply the 4-beat CoT chain (see CoT contract above).
2. Identify the governing constraint for each scheme — the single axis on which the schemes differ most decisively.
3. Apply the dominator test per pair.
4. Rank all schemes from best to worst. Where no dominator exists across all pairs, rank by the governing constraint of the brief (stated explicitly or inferred from the program — note `INFERRED` if inferred).
5. Emit one `@@CONCEPT-COMPARISON` block per scheme, with the top-ranked scheme first.

## Output blocks

The consuming agent emits structured blocks for each procedure applied.

**Concept scheme (one per scheme):**

```
@@CONCEPT-SCHEME BEGIN
scheme | massing type | footprint mm (from brief) | storeys | approx GFA m² | orientation/solar verdict | adjacency verdict | spine verdict | program-fit verdict
@@CONCEPT-SCHEME END
```

**Concept comparison (one per scheme, ranked best first):**

```
@@CONCEPT-COMPARISON BEGIN
rank | scheme | governing constraint | program-fit | adjacency | orientation/solar | envelope/GFA cap | site access | dominator over | rationale (governing constraint)
@@CONCEPT-COMPARISON END
```

Precede every `@@CONCEPT-COMPARISON` block with the 4-beat CoT chain (see CoT contract above). No block without its chain.

**Concept change-order (one, if a chosen scheme is supplied in the brief):**

```
@@CONCEPT-CHANGE-ORDER BEGIN
chosen scheme | IfcSpace zones (label, function, approx area, level) | IfcBuildingStorey levels (label, elevation-from-spec) | circulation spine description | spec-domain units | handoff note for freecad-architect
@@CONCEPT-CHANGE-ORDER END
```

The `@@CONCEPT-CHANGE-ORDER` block maps the chosen concept to IFC-domain language for `freecad-architect`. IFC placement mechanics (coordinates, translations) defer to `ifc-geometry-discipline` (co-load). Only zone labels, function names, approximate areas, and level labels are stated here — no raw coordinate values.

Site/norm value cells read `pending research-fact-checker`, never blank or fabricated.

## PAUSE routing

Three distinct PAUSE destinations — do not conflate:

- **Site or norm VALUE** (setback, height limit, FAR, GFA cap, solar angle, soil bearing, access width) → `PAUSE: need research-fact-checker for <subject>`.
- **ifcopenshell API signature** → `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- **Ambiguous brief** (missing envelope dims, unclear program, undefined orientation) → `PAUSE: orchestrator must clarify <specific question>`.

## Inline invariants

These hold unconditionally before any procedure is entered.

**No site or norm value from memory.** See Hard boundary above. No exception.

**Method here; expression elsewhere.** This skill encodes massing and zoning reasoning method. IFC entity expression (unit domains, placement translations, entity creation, Qto attachment) is `ifc-geometry-discipline`'s lane — co-load; zero overlap.

**Envelope dims from brief, not memory.** Footprint, storey height, and site dimensions arrive from the brief or from spec values via Bash derivation in `arch-concept-designer`. Never derived from memory.

**≥2 distinct massing strategies.** Two variants of the same type (bar-variant-A vs bar-variant-B) do not satisfy the minimum — they must be structurally distinct strategies.

**Every scheme carries a label through all procedures.** Scheme A / Scheme B / … assigned in Procedure 1 and held consistently through output blocks. Relabelling mid-procedure is a finding.

**CoT chain before every @@CONCEPT-COMPARISON row.** No comparison row without the 4-beat chain. The chain is evidence that the ranking is derived, not asserted.

**SAGE-GENERIC.** Example values in this skill are house-reference shapes. No project-specific constants, real client names, or real site parameters appear in this file.

## Worked example — bar vs courtyard (SAGE-GENERIC)

Program: living 30 m², kitchen/dining 25 m², 3 bedrooms (3×12 m²), 2 bathrooms (2×6 m²), utility 8 m². Total program minimum GFA ≈ 105 m². Notional plot: 12 m × 20 m. Orientation: north at top. All site/norm values are placeholder — this example encodes method only.

**Scheme A — bar (single-aspect linear):**

- Footprint: 12 m × 8 m = 96 m² per floor. Two storeys → ≈192 m² GFA. Program fit: PASS (192 ≥ 105). FAR cap: `pending research-fact-checker`.
- Orientation: long axis east–west; south facade serves living/kitchen. Solar verdict: FAVOURABLE.
- Adjacency: kitchen adjacent to dining (PASS); bathrooms accessible from bedroom zone (PASS).
- Spine: single corridor east–west. Verdict: SINGLE.

**Scheme B — courtyard (perimeter):**

- Footprint: 12 m × 20 m perimeter, 6 m × 14 m void = perimeter area ≈ 156 m². One storey → ≈156 m² GFA. Program fit: PASS (156 ≥ 105). FAR cap: `pending research-fact-checker`.
- Orientation: internal courtyard receives light from all aspects; main rooms wrap perimeter. Solar verdict: PARTIAL (varies by wing).
- Adjacency: kitchen adjacent to dining (PASS); bathrooms — depends on arrangement (PASS if planned correctly; flag for verification).
- Spine: perimeter corridor around courtyard. Verdict: SINGLE.

**4-beat CoT chain:**

1. Candidates: Scheme A (bar), Scheme B (courtyard).
2. Governing constraint per scheme: Scheme A — orientation/solar (strong south facade); Scheme B — internal amenity / perimeter-use ratio.
3. Dominator analysis: Scheme A is no worse on program fit (PASS), adjacency (PASS), envelope cap (pending); strictly better on orientation/solar (FAVOURABLE vs PARTIAL). Scheme A dominates Scheme B on the orientation/solar axis.
4. Ranked recommendation: Scheme A first, governing constraint: orientation/solar.

```
@@CONCEPT-SCHEME BEGIN
scheme | massing type | footprint mm (from brief) | storeys | approx GFA m² | orientation/solar verdict | adjacency verdict | spine verdict | program-fit verdict
A | bar | 12 000 × 8 000 | 2 | 192 | FAVOURABLE | PASS | SINGLE | PASS
B | courtyard | 12 000 × 20 000 perimeter | 1 | 156 | PARTIAL | PASS | SINGLE | PASS
@@CONCEPT-SCHEME END
```

```
@@CONCEPT-COMPARISON BEGIN
rank | scheme | governing constraint | program-fit | adjacency | orientation/solar | envelope/GFA cap | site access | dominator over | rationale
1 | A | orientation/solar | PASS | PASS | FAVOURABLE | pending research-fact-checker | pending research-fact-checker | B | South facade serves main habitable rooms; dominator on orientation/solar axis
2 | B | internal amenity | PASS | PASS | PARTIAL | pending research-fact-checker | pending research-fact-checker | — | Lower GFA headroom; solar access partial
@@CONCEPT-COMPARISON END
```

No cell is fabricated. Every site/norm-value slot reads `pending research-fact-checker`.

## Anti-patterns

- **Inventing a site or norm value.** Any setback, height limit, FAR cap, GFA cap, solar angle, or soil parameter produced from memory is a CLAUDE.md §4 fabrication. The cell reads `pending research-fact-checker`.
- **Collapsing to one scheme without comparison.** The minimum is ≥2 structurally distinct strategies. A single scheme with variants is not a comparison.
- **@@CONCEPT-COMPARISON row without its CoT chain.** The 4-beat chain (candidates → governing constraint → dominator analysis → ranked recommendation) is mandatory before each comparison block. A row without its chain is incomplete.
- **Two variants of the same massing type as "distinct" schemes.** Bar-A and bar-B are variants, not distinct strategies. Distinct means structurally different envelope type (bar vs courtyard, L-shape vs podium).
- **Deriving envelope dims from memory.** All footprint, storey-height, and site-dimension values arrive from the brief. Never assumed or recalled.
- **Pronouncing code compliance.** This skill derives massing and program fit; compliance against planning regulations belongs to `research-fact-checker`.
- **Pricing scheme quantities.** Quantities are outputs; pricing is `fin-*` lane.
- **Relabelling schemes mid-procedure.** Scheme labels assigned in Procedure 1 are held through all output blocks.
- **Speculative scheme inflation.** Generate the number of schemes named in the brief (default ≥2). Adding extra schemes not requested violates the atomic-work rule.
- **Asserting IFC placement values.** IFC coordinate values, placement translations, and Qto fields are `ifc-geometry-discipline`'s lane — co-load, do not re-derive.

## When NOT to use this skill

- Any site or norm VALUE → emit `PAUSE: need research-fact-checker for <subject>` and stop.
- IFC unit domains, placement matrices, entity creation, Qto attachment → `ifc-geometry-discipline` (co-load; zero overlap).
- ifcopenshell API signature → emit `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- Model geometry mutation or IFC regeneration → `freecad-architect`.
- Design framing or brief narrative → `arch-visionary`.
- Structural sizing or lintel scheduling → `structural-design-discipline`.
- Pre-completion verification → `verification-before-completion` (load alongside this skill for concept-design items).
