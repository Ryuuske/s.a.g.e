---
name: ral-surface-style-discipline
description: "Use when assigning a material (IfcMaterial vs IfcMaterialLayerSet) to an IFC element class, mapping a RAL code to IfcColourRgb (RAL → normalised 0–1 sRGB), or checking a materials/BOM schedule for completeness. Do not use for: geometry/placement/Qto (→ ifc-geometry-discipline); ifcopenshell API (→ PAUSE research-docs-lookup); material properties like U-value/fire-rating (→ PAUSE research-fact-checker); pricing (→ fin-* lane)."
---

# RAL Surface Style Discipline

This skill encodes three decision trees — IfcMaterial vs IfcMaterialLayerSet selection, RAL-code-to-IfcColourRgb mapping, and schedule/BOM completeness checking — that the consuming agent applies when deriving a material/finish/colour change-order for a parametric IFC BIM model. It is the IfcSurfaceStyle and material counterpart to `ifc-geometry-discipline`, which explicitly scopes material assignment and colour out of its domain. This skill has zero IFC geometry/placement logic; `ifc-geometry-discipline` handles geometry, unit domains, and placement.

All three decision trees encode verifiable method. The consuming agent (`arch-spec-writer`) applies CoT throughout: entity-decision, colour-normalisation, and completeness steps each carry a chain before any row is emitted. The headline invariant — the colour-domain trap — is as critical as the mm-vs-SI-metres trap in `ifc-geometry-discipline`: `IfcColourRgb` components are 0–1; passing 0–255 (or hex-only) values is ~255× out of range.

## When this skill binds

Fire this skill when any of these are true:

- You are assigning a material or finish to an IFC element class (selecting single `IfcMaterial` vs ordered `IfcMaterialLayerSet`).
- You are mapping a RAL code to an `IfcSurfaceStyle` colour — deriving the normalised sRGB `IfcColourRgb` triple.
- You are checking a materials/finishes schedule or BOM for completeness (every element class assigned).
- You are reconciling layer-set total thickness against element thickness.
- You are binding an `IfcStyledItem` to an element class via an `IfcSurfaceStyle`.

Do NOT fire this skill for:

- Geometry creation, placement matrices, unit domains, or Qto attachment → `ifc-geometry-discipline`.
- Applying the material/style to the model (IFC write) → `freecad-architect`.
- ifcopenshell API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup` and stop.
- Material-property facts (U-value, fire-rating, thermal conductivity, density) → `PAUSE: need research-fact-checker for <subject>`; never invent or recall a material property value.
- Pricing or unit-rate costing → `fin-*` lane. This skill derives quantities; pricing is out of scope.

## Headline invariant — colour-domain trap

**`IfcColourRgb` components are 0–1.** A 0–255 value is ~255× out of range. This is the colour analog of passing project-mm to `geometry.edit_object_placement` — a silent scale error that corrupts every colour in the model.

The correct normalisation is:

```
R = RAL_R / 255.0
G = RAL_G / 255.0
B = RAL_B / 255.0
```

where `RAL_R`, `RAL_G`, `RAL_B` are the 0–255 sRGB components from the published RAL reference for that code.

An un-sourced triple (not traceable to a published RAL reference) is a finding. Hex-only notation (`#RRGGBB` without explicit ÷255 normalisation) is not acceptable as an `IfcColourRgb` value — the normalisation step must be shown.

## Decision tree (a) — IfcMaterial vs IfcMaterialLayerSet

Decide whether a single `IfcMaterial` or an ordered `IfcMaterialLayerSet` applies to each element class.

**Decision rule:**

- **Single layer, no build-up** (e.g. steel beam, glass pane, timber rafter) → `IfcMaterial` bound via `IfcRelAssociatesMaterial`.
- **Multi-layer build-up** (e.g. insulated wall, floor slab with screed/insulation/substrate, roof build-up) → `IfcMaterialLayerSet`: ordered list of `IfcMaterialLayer` entries, each carrying a material and a thickness.

**Thickness reconciliation:**

For every `IfcMaterialLayerSet`, sum the layer thicknesses and compare to the element thickness from the spec:

```
Σ(layer thickness) == element thickness from spec
delta = element thickness − Σ(layer thickness)
```

A non-zero delta is a reconciliation finding. The `@@LAYER-SET` block carries `summed thickness | element thickness from spec | delta`.

**Procedure:**

1. For each element class, determine whether a single material or a multi-layer build-up applies.
2. For multi-layer: enumerate layers in structural order (outermost to innermost, or as stated in the brief). Each layer carries: `IfcMaterial` name, thickness in project units (from spec, not invented).
3. Compute the layer-thickness sum and compare to spec element thickness.
4. Apply CoT chain before writing the row: element class + convention → entity decision → layer thicknesses reconcile to element thickness.
5. Emit `@@MATERIAL-ASSIGNMENT` and (if applicable) `@@LAYER-SET` rows.

## Decision tree (b) — RAL code to IfcColourRgb

Map a RAL code to a normalised `IfcColourRgb` triple for use in `IfcSurfaceStyle`.

**Procedure:**

1. Identify the RAL code from the brief.
2. Look up the published RAL Classic reference sRGB values for that code. An un-sourced triple is a finding. State the reference explicitly.
3. Normalise: divide each 0–255 channel by 255.0. Show the arithmetic.
4. Bind the `IfcColourRgb` in an `IfcSurfaceStyleRendering` within an `IfcSurfaceStyle`, bound to the element class via `IfcStyledItem`.
5. Apply CoT chain before writing the row: RAL code → published reference → normalised sRGB triple → binding target.
6. Emit `@@SURFACE-STYLE` row showing: RAL code | published R,G,B (0–255) | normalised R (÷255) | normalised G (÷255) | normalised B (÷255) | binding target element class.

**Binding target verification:** confirm that the `IfcStyledItem` is bound to the correct element class representation — an `IfcSurfaceStyle` with no `IfcStyledItem` binding is an orphan style, a finding.

## Decision tree (c) — Schedule/BOM completeness

Check that every element class in the project inventory has a material and finish assignment.

**Procedure:**

1. Enumerate every element class in the project inventory (from the spec or brief).
2. For each class, check whether a `@@MATERIAL-ASSIGNMENT` row and (where applicable) a `@@SURFACE-STYLE` row exist from Decision trees (a) and (b).
3. Record the status: `assigned` or `UNASSIGNED`.
4. An `UNASSIGNED` element class is a completeness gap — a finding in the `@@SCHEDULE-COMPLETENESS` block.
5. Quantities in the schedule are derived from the spec geometry (no pricing — quantities only, per `fin-*` lane boundary).
6. Apply CoT chain: element inventory class → assigned (y/n) → gap.
7. Emit one `@@SCHEDULE-COMPLETENESS` row per element class.

**No-fabricated-property invariant:** any material property (fire rating, thermal resistance, U-value, density) required for a schedule cell that the brief does not supply is `pending research-fact-checker`. Do not invent or recall.

**Quantities-not-prices invariant.** The BOM carries element counts and quantities (area m², volume m³, length m). No unit rates, no totals in currency. Pricing belongs to `fin-*` lane.

## Output blocks

The consuming agent emits structured blocks for each decision tree applied.

**Material assignment (one per element class):**

```
@@MATERIAL-ASSIGNMENT BEGIN
element class | entity type (IfcMaterial | IfcMaterialLayerSet) | material name(s) | layer count | IFC binding (IfcRelAssociatesMaterial) | completeness (complete | UNASSIGNED)
@@MATERIAL-ASSIGNMENT END
```

**Surface style — RAL colour (one per RAL code / element class binding):**

```
@@SURFACE-STYLE BEGIN
element class | RAL code | published R,G,B (0-255) | reference cited | normalised R (0-1) | normalised G (0-1) | normalised B (0-1) | IfcSurfaceStyle binding target | orphan check (bound | ORPHAN)
@@SURFACE-STYLE END
```

**Layer set (one per multi-layer element):**

```
@@LAYER-SET BEGIN
element class | layer order | layer material | layer thickness (project mm) | summed thickness | element thickness from spec | delta | reconciliation (ok | delta:<N>mm)
@@LAYER-SET END
```

**Schedule completeness (one per element class):**

```
@@SCHEDULE-COMPLETENESS BEGIN
element class | material assigned (yes | UNASSIGNED) | surface style assigned (yes | UNASSIGNED | n/a) | quantity (from spec) | unit | notes
@@SCHEDULE-COMPLETENESS END
```

Never collapse multiple element classes into a single row.

## PAUSE routing

Three distinct PAUSE destinations — do not conflate:

- **Material-property fact** (U-value, fire rating, thermal conductivity, density, acoustic rating) → `PAUSE: need research-fact-checker for <subject>`.
- **ifcopenshell API signature** → `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- **Ambiguous brief** (RAL code not specified, element class inventory not defined, open material choice) → `PAUSE: orchestrator must clarify <specific question>`.

## Inline invariants

These hold unconditionally before any decision tree is entered.

**Colour domain — 0–1 always.** Every `IfcColourRgb` component is 0–1. The normalisation step (÷255) is mandatory and shown. An un-sourced or un-normalised triple is a finding.

**No fabricated material property.** Any material property not supplied via brief reads `pending research-fact-checker`. No exception for "well-known" values.

**Layer-set Σthickness = element thickness.** A delta is a finding. The reconciliation must be shown.

**Quantities not prices.** The BOM carries counts/areas/volumes. No unit rates, no currency totals. Pricing is `fin-*` lane.

**No silent open choice.** When the brief presents a material choice (e.g. two facing brick options) that has not been resolved, surface `PAUSE: orchestrator must clarify <specific question>`. Do not silently pick one.

**No orphan style.** Every `IfcSurfaceStyle` must be bound via an `IfcStyledItem` to a representation. An unbound style is a gap finding.

**SAGE-GENERIC.** No real RAL codes, no real material product names, no real spec filenames or sheet numbers appear in this file. Example values are house-reference shapes.

## Anti-patterns

- **IfcColourRgb in 0–255 or hex-only.** This is ~255× out of range — the colour analog of the mm-vs-SI-metres trap. Every channel must be ÷255, shown explicitly.
- **Orphan IfcSurfaceStyle with no IfcStyledItem binding.** A style that is not bound to any representation is a gap finding.
- **Layer-set Σthickness ≠ element thickness.** The reconciliation check is mandatory. A delta is a finding.
- **Wrong entity class for single-material elements.** Applying `IfcMaterialLayerSet` to a homogeneous element (steel beam, glass pane) that has no layered build-up adds schema noise. Use `IfcMaterial` for single-material elements.
- **Fabricating a material property.** Fire rating, U-value, density, or any other property not supplied in the brief reads `pending research-fact-checker`.
- **Price in a BOM block.** The BOM carries quantities only. A currency total in a BOM block is a lane violation.
- **Softening a completeness gap.** An `UNASSIGNED` element class is a finding. It cannot be softened to "informational" or "low priority" — every class needs an assignment before the change-order is complete.
- **Guessing an ifcopenshell API signature.** Emit the PAUSE shape instead.
- **Silently picking an open material choice.** An unresolved brief choice surfaces as a `PAUSE: orchestrator must clarify` — not a silent decision.

## When NOT to use this skill

- Geometry creation, placement, Qto → `ifc-geometry-discipline`.
- Applying material/style to the model (IFC writes) → `freecad-architect`.
- ifcopenshell API signatures → emit `PAUSE: need research-docs-lookup for <subject> reference lookup`.
- Material-property facts → emit `PAUSE: need research-fact-checker for <subject>`.
- Pricing/cost → `fin-*` lane.
- Pre-completion verification → `verification-before-completion` (load this skill alongside it for material items).
