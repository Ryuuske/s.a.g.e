---
name: bim-visual-verification
description: "Use at a BIM audit gate when counts are green and round-trip is lossless but the SHAPE must be confirmed by inspecting rendered panels for form-correctness. Not for: round-trip/count/vertex (→ freecad-headless-round-trip); render production/empty-frame (→ ifc-render-pipeline-discipline); crash (→ systematic-debugging); pre-completion (→ verification-before-completion); sheet-set (→ sheet-set-assembly-discipline); structural sizing (→ structural-design-discipline)."
---

# BIM Visual Verification Discipline

This skill encodes the multi-angle visual-verification procedure that a consuming agent applies when performing a form-correctness audit of a BIM/IFC model at an audit gate. It establishes the required view set, the single-angle occlusion rule, the fail-closed render semantics, the image-grounding requirement, and the honest scope boundary. It does not duplicate `freecad-headless-round-trip` (which handles round-trip fidelity, element counts, platform-limitation classification, and vertex fidelity) and does not enter the render-production lane owned by `ifc-render-pipeline-discipline`.

The consuming agent co-loads `freecad-headless-round-trip` alongside this skill when a full audit is in scope — the two skills are complementary, not overlapping. This skill's lane is form-correctness visual review only: exterior massing, shell/roof form, and below-grade structural form.

The motivation for this skill comes from a real defect: a terrace roof modeled as a flat box (geometrically wrong) passed element-count checks, passed the NativeIFC round-trip (Δ=0, bit-exact vertices), and passed a single-angle render (the defect was physically occluded from that camera angle) — and shipped. Counts-green + round-trip-lossless ≠ form-correct, and a single render angle can hide a defect. This skill exists to prevent that failure mode permanently.

CoT:No — grounded-observation discipline. The consuming agent describes what is visible in each rendered panel image; it does not infer from filenames, counts, or mechanical-check results. Visual matching is a summarization-class task, not a logic-heavy severity-inference chain (per `ai-dev-conventions.md`).

## When this skill binds

Fire this skill when any of these are true:

- You are at a BIM model audit gate and the question is whether the geometric SHAPE is correct — after counts-green and round-trip-lossless have already been established (or alongside them).
- You are asked to inspect rendered panels and tell, from the images themselves, whether roof form, terrace form, or below-grade structural form is correct.
- The brief supplies a render path or render-script invocation and asks for a visual form-correctness review.
- You are emitting a `@@FREECAD-VISUAL-REVIEW` block.

Do NOT fire this skill for:

- Round-trip losslessness, element counts, vertex-coordinate fidelity, Qto presence, or FreeCAD platform-limitation classification → `freecad-headless-round-trip` (co-load; do not duplicate this skill's content with those decisions).
- Render production, empty/black-frame diagnosis, scene export verification, camera/light-rig binding, or material binding → `ifc-render-pipeline-discipline` (arch-visualizer's lane).
- A genuine render crash or stack trace → `systematic-debugging`.
- General pre-completion gate → `verification-before-completion` (load this skill alongside it for the visual-verification items, but `verification-before-completion` governs the overall procedure).
- 2D sheet-set completeness → `sheet-set-assembly-discipline`.
- Structural sizing, load-path analysis, or structural capacity checking → `structural-design-discipline`.

## Required view set

The following panels are the minimum required view set for any visual-verification pass. All spatial values (z-clips, vertex coordinates, bounding boxes) are SI metres — ifcopenshell.geom with `use_world_coords=True` outputs SI metres even when the IFC file is authored in project-millimetres. The per-project brief supplies the actual render-script path, the expected panel count, and any expected morphology.

A single render angle can hide a defect. The flat-box terrace roof that shipped looked correct from the front elevation and from above — the defect (a collapsed roof geometry) was physically occluded from every single-camera perspective tried. Only a second isometric view from the opposing corner, combined with a roof-only isolation, revealed the form error. **This is why the view set requires at least two isometric corners plus roof-only isolation.** Single-angle sign-off is never sufficient.

**Minimum required panels:**

1. **Isometric A** — isometric view from one corner of the model (brief specifies which).
2. **Isometric B** — isometric view from the OPPOSING corner. This is the single-angle-occlusion countermeasure. Do not omit.
3. **Low side elevation** — a low, near-horizontal side elevation of the defect-prone face (the face most likely to hide a terrace-roof or shell-collapse defect). Brief specifies the face orientation.
4. **Roof-only isometric** — isometric view of the roof/terrace layer in isolation, with all below-roof elements suppressed or hidden.
5. **Roof-only top-down** — plan/orthographic top-down view of the roof/terrace layer in isolation.
6. **Below-grade** — isometric or side view with the z-clip dropped to the DEEPEST element tip in the model (SI metres). Never set the z-clip to z=0 — a z=0 clip misses all below-grade elements (footings, piles, basement walls, raft slabs). The authoritative deepest-element z is read from the IFC geometry, not assumed.

**Optional panels (per brief):**

- **Per-layer isolation** — one panel per `IfcPresentationLayerAssignment` layer named in the brief. If the model has no `IfcPresentationLayerAssignment` entities, per-layer panels are marked N/A with that reason in the block — they are never silently skipped, and the absence of assignments is not a pass condition.

The render-script / contact-sheet generation path and the expected panel count are supplied by the per-project brief. A proven reference shape for a contact-sheet generator is a ≥6-panel contact sheet combining the views above — this is a reference shape only; no specific path or project identifier is encoded here.

> **REFERENCE:** Render invocation hygiene — temp-cwd isolation, WSL-only %TEMP% path translation, and the fail-closed no-new-untracked-files check — is governed by `freecad-wsl-invocation-hygiene` (single source of truth). Do NOT duplicate those rules here.

## Discipline

### Rule A — Required view set

Emit all panels in the required view set listed above. A missing panel is a fail-closed finding. The view set is not negotiable; it exists because any subset of it has a documented failure mode (the single-angle occlusion case above). The brief may specify additional panels beyond the minimum; the minimum set is always required regardless of what the brief names.

### Rule B — Actually inspect (anti-theater)

The consuming agent MUST use the Read tool on each rendered panel PNG. Read renders PNGs visually — the consuming agent sees the image content and grounds every observation in what is visible in the image.

Per-panel observations MUST describe what is actually visible in the rendered image. Never infer a panel's content from:

- Its filename or path.
- Its panel name or view label in the contact sheet.
- Object counts or element-class counts from the mechanical audit.
- The result of the prior round-trip pass (Δ=0, bit-exact vertices, bbox-match).

A `@@FREECAD-VISUAL-REVIEW` block whose per-panel observations are not grounded in the rendered image is non-conformant and cannot support APPROVE, regardless of what the round-trip pass shows.

### Rule C — Fail-closed render semantics

If the brief supplies no render path or no render-script invocation for the required view set, this gate cannot APPROVE. The verdict is at most REQUEST_CHANGES. A missing render path is not a platform limitation and must not be routed to `freecad-headless-round-trip`'s platform-limitation classification tree.

For individual panels, apply these fail-closed conditions:

- **Blank / zero-byte file** → fail-closed for that panel.
- **Zero-dimension image** (zero-width or zero-height) → fail-closed.
- **Wrong panel count** (fewer panels than the brief specifies, or fewer than the required 6-panel minimum) → fail-closed.
- **Unreadable** (corrupt file, Read cannot parse the image) → fail-closed.
- **Occluded** (the relevant geometry is hidden behind other elements from this angle, so the view cannot confirm form-correctness) → fail-closed; request the opposing-corner panel or the roof-only isolation view.
- **Ambiguous** (image is present but quality is too low or scale is too small to confirm form) → fail-closed.

A failed, errored, or occluded render is NEVER reclassified as a FreeCAD platform limitation. The platform-limitation path in `freecad-headless-round-trip` handles round-trip gaps caused by documented NativeIFC behaviors — it does not absorb render failures or occluded views.

Image-validity checks before any observation: confirm nonzero dimensions and that the expected panel count is present. A panel that fails these checks fails closed before any form-correctness observation is attempted.

### Rule D — Adversarial posture

This skill is written to hunt for form defects, not to confirm everything looks fine. The default posture is REQUEST_CHANGES on any panel that is blank, occluded, ambiguous, or missing. The question is not "does this look acceptable?" but "can I prove the form is correct from this image?"

Counts-green + round-trip-lossless does not equal form-correct. That combination is the specific failure mode that motivated this skill, and it must be stated explicitly in the block.

### Rule E — Honest scope

This gate covers and claims only:

- Exterior massing (overall building footprint, envelope shape, storey heights visible from exterior).
- Shell and roof form (roof plane geometry, terrace levels, parapet profiles, overhangs visible from exterior and from the roof-only panels).
- Below-grade structural form (foundation type and depth, basement walls, raft slab, pile caps — visible in the below-grade panel with the z-clip at the deepest element tip).

This gate does NOT cover and must NOT claim:

- Interior spatial arrangements, room sizes, partition layouts.
- MEP routing, clash detection, or service clearances.
- Absolute dimensional accuracy without a scale reference (no ruler or dimension reference in the brief → no scale claim).
- Handedness or mirror errors unless the brief supplies a reference showing expected orientation.
- Full architectural correctness — only the exterior massing, shell/roof, and below-grade structural form visible in the required panels.

The consuming agent must make and record an affirmative form judgment (`form-determination`) on every panel, regardless of whether the brief supplies expected morphology. "Expected morphology: none supplied" does NOT downgrade the gate to a render-quality-only check. Even without a reference, the agent must affirmatively confirm the observed form is a coherent, non-degenerate building form consistent with the model's stated intent in the brief (`form-confirmed`). If the agent cannot affirmatively confirm this, the panel's `form-determination` is `form-unconfirmable` and the panel fails closed.

If the brief supplies expected-morphology references for any panel (e.g., a reference rendering or a described expected shape), the block states observed-vs-expected for that panel and the `form-determination` must align: a MISMATCH against expected morphology sets `form-determination` to MISMATCH. If no expected morphology is supplied, the `expected-vs-observed` column states "n/a no-expected," but the `form-determination` column must still be filled with the agent's image-grounded affirmative judgment (`form-confirmed` or `form-unconfirmable`).

Per-layer panels with no `IfcPresentationLayerAssignment` in the model are marked N/A with the reason — they are never silently passed.

**Symmetric-massing residual (honest scope).** A symmetric-but-displaced massing error — for example, a wing or mass that is correctly formed but on the wrong side of the building, yet reads plausibly from both opposing isometric corners because the error is mirror-symmetric relative to that axis — may evade the two-opposing-isometric coverage when no expected morphology or plan/reference view is supplied. The brief SHOULD supply expected morphology (and/or a plan view) for any asymmetry-sensitive form, for the same reason it should supply expected morphology for any complex roof form. This disclaimer is additive to the existing handedness/mirror-error exclusion (which covers chirality differences requiring a known-good reference): it names the specific case where both isometrics agree on a wrong-but-symmetric massing, and no reference exists to detect the displacement.

### Rule F — Expected-vs-observed and affirmative form-confirmation

Where the brief supplies expected morphology (a reference rendering, a textual description of the expected roof form, a diagram), the `@@FREECAD-VISUAL-REVIEW` block asserts observed-vs-expected per panel. This is not a free-form description of what looks nice — it is a structured assertion of match or mismatch against the stated expectation. A MISMATCH against expected morphology sets `form-determination` to MISMATCH and caps the verdict at REQUEST_CHANGES.

If no expected morphology is supplied for a panel, the "expected morphology" column states "none supplied" and the "expected-vs-observed" column states "n/a no-expected." However, the `form-determination` column MUST still be filled: the agent must make an image-grounded affirmative judgment that the observed form is a coherent, non-degenerate building form consistent with the model's stated intent in the brief. "None supplied" does not license a free pass — it shifts the burden from reference-matching to independent form-confirmation. If the agent cannot make that affirmative confirmation, `form-determination` is `form-unconfirmable` and the panel fails closed. A panel passes ONLY when render-quality = pass AND form-determination = form-confirmed.

## Unit-domain note

ifcopenshell.geom with `use_world_coords=True` outputs SI metres unconditionally, even when the IFC project is authored in project-millimetres (e.g., the `IfcProject.UnitsInContext` declares millimetres). Every z-clip value, vertex coordinate, and bounding-box extent used in the render-script invocation is therefore in SI metres. Setting `z_clip = 0.0` to expose below-grade elements misses all elements whose deepest tip is below z=0 in SI metres. The correct z-clip is the minimum z value of the deepest element's bounding box in the world-coordinate SI-metre frame — always read from the model geometry, never assumed to be 0.

## Output block

The consuming agent emits this block. The render path and panel list are supplied by the brief; the observations are grounded in the Read PNG images.

```
@@FREECAD-VISUAL-REVIEW BEGIN
view set: <view-set-id from brief> | required panels: <N> | panels Read: <N> | render path supplied (yes | NO → fail-closed)
panel | image path Read (actual PNG path) | visually observed (image-grounded, what is seen) | expected morphology (from brief, or "none supplied") | expected-vs-observed (match | MISMATCH | n/a no-expected) | render-quality (pass | FAIL-CLOSED:<blank|zero-dim|wrong-count|unreadable|occluded|ambiguous|missing>) | form-determination (form-confirmed | MISMATCH | form-unconfirmable)
scope line: covers exterior massing + shell/roof + below-grade structural form ONLY; NOT interiors/MEP/clearances/absolute-scale/handedness unless brief supplies references
gate line: all required panels Read and image-grounded (yes/no) | any panel fail-closed (yes/no) | any panel MISMATCH or form-unconfirmable (yes/no) | visual gate result (PASS | FAIL-CLOSED)
@@FREECAD-VISUAL-REVIEW END
```

The gate line is the controlling summary. A panel passes ONLY when render-quality = pass AND form-determination = form-confirmed. If any panel is fail-closed on render quality, or if any panel's form-determination is MISMATCH or form-unconfirmable, the gate line reads `visual gate result (FAIL-CLOSED)` and the overall visual gate does not APPROVE.

## Anti-patterns

- **Single-angle false-negative.** Reviewing only one isometric and declaring the form correct. The documented failure mode is a defect occluded from a single angle that passes Δ=0, bit-exact round-trip, and a single render — and ships. Two opposing isometrics plus roof-only isolation are the minimum.
- **Rubber-stamp block with no image-grounded observation.** Writing "panel observed: correct" without having Read the PNG and described what is visible in the image. Any per-panel observation that does not describe visible image content is non-conformant.
- **Treating a broken or blank render as a platform limitation.** A failed, errored, blank, or occluded render is fail-closed and stays in this skill's lane. The `freecad-headless-round-trip` platform-limitation path handles NativeIFC round-trip gaps — it does not absorb render failures.
- **Counts-green + round-trip-lossless treated as form-correct.** These are necessary but not sufficient. The combination Δ=0 + bit-exact vertices + bbox-match proves element counts survived the round-trip; it says nothing about whether the roof is flat when it should be pitched. Visual inspection of rendered panels is the only check that catches form defects.
- **Below-grade z-clip set to z=0.** A z=0 clip misses all below-grade elements whose deepest geometry is below the project's z=0 plane in world coordinates. The z-clip must be set to the deepest element tip in SI metres, read from the model geometry.
- **Silent pass on an unproducible panel.** If a panel cannot be rendered (missing layer assignment, script error, path error), it is marked FAIL-CLOSED with the reason. It is never silently omitted or counted as passing.
- **Free-form "looks nice" where expected morphology was supplied.** Where the brief supplies expected morphology, the block asserts observed-vs-expected — not a subjective quality judgment.
- **Scope inflation.** Claiming this gate verified interior layouts, MEP routing, dimensional accuracy, or handedness/mirror orientation when the brief did not supply the required references and panels to support those claims.
- **Treating "no expected morphology supplied" as license to pass on render-quality alone.** When the brief supplies no expected morphology, the `expected-vs-observed` column correctly reads "n/a no-expected" — but the `form-determination` column must still carry an affirmative, image-grounded form-confirmation (`form-confirmed`) or record the inability to confirm (`form-unconfirmable`). Filling `form-determination` with a blank or omitting the column entirely, and then granting PASS because no render-quality token was tripped, is a form-correctness gate failure. Every panel needs an affirmative, image-grounded form judgment; an unconfirmable form fails closed.

## When NOT to use this skill

- Round-trip losslessness, element counts, vertex fidelity, Qto presence, or FreeCAD platform-limitation classification → `freecad-headless-round-trip` (co-load alongside this skill for a full audit, but the mechanical fidelity checks are that skill's lane).
- Render production, empty/black-frame diagnosis, scene export verification, camera or light-rig binding, material binding → `ifc-render-pipeline-discipline`.
- Genuine render crash (stack trace, non-zero exit with error output) → `systematic-debugging`.
- Generic pre-completion gate → `verification-before-completion`.
- 2D plan/section sheet-set completeness → `sheet-set-assembly-discipline`.
- Structural sizing, load-path, or capacity analysis → `structural-design-discipline`.
- Any non-IFC / non-BIM visual review task not at a model audit gate.
