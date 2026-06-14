---
name: ifc-render-pipeline-discipline
description: "Use when exporting an IFC to a renderable scene, verifying scene non-empty (triangle count > 0) before rendering, checking cameras/lights bind, binding authored materials to a throwaway scene, or diagnosing empty/black frames. Not for: model mutation (→ freecad-architect); IfcMaterial authoring (→ arch-spec-writer); sheet assembly (→ arch-documenter); round-trip audit (→ freecad-headless-round-trip); engine flags (→ PAUSE research-docs-lookup); crash (→ systematic-debugging)."
---

# IFC Render Pipeline Discipline

This skill encodes four verifiable procedures — scene/mesh export and empty-scene verification, camera-resolution and light-rig binding checks, material-binding for render, and empty/black render diagnosis — that the consuming agent applies when driving the render pipeline from an IFC model to client-facing image artifacts. It is the output-assembly analog to `sheet-set-assembly-discipline` (which assembles 2D documentation) applied to the 3D/photoreal lane. The consuming agent (`arch-visualizer`) applies these procedures in order; CoT injection is not applicable (render execution and scene assembly are summarization-class tasks per the CoT classification in `ai-dev-conventions.md`).

**Headline invariant: a zero-exit black or empty frame is a signature failure — not a successful render.** This is the render analog of the `sheet-set-assembly-discipline` mm-vs-SI blank-SVG trap. Success is `non-empty AND non-black`, measured by byte count, pixel dimensions, and mean luma (or non-background-pixel ratio) — never by exit code alone. Materials are bound onto a THROWAWAY render scene; the authoritative `IfcMaterial` / `IfcSurfaceStyle` spec is never mutated (that is `arch-spec-writer` / `ral-surface-style-discipline`'s lane).

This skill does not overlap with `ifc-geometry-discipline` (which handles authoritative IFC entity creation and placement) or with `freecad-headless-round-trip` (which audits round-trip fidelity). It complements `verification-before-completion` without duplicating its general procedure.

## When this skill binds

Fire this skill when any of these are true:

- You are exporting an IFC model to a renderable scene (mesh export, scene file generation) and need to verify the export is non-empty before proceeding.
- You are checking that every named camera resolves within the scene bounds and every named light rig binds before rendering.
- You are binding already-authored materials onto a throwaway render scene.
- You are diagnosing an empty or black render frame.
- You are emitting `@@RENDER-EXEC` or `@@RENDER-MANIFEST` blocks.

Do NOT fire this skill for:

- Model geometry mutation or IFC regeneration → `freecad-architect`.
- Authoring the authoritative `IfcMaterial` / `IfcSurfaceStyle` spec → `arch-spec-writer` (consult `ral-surface-style-discipline`).
- 2D plan/section sheet assembly → `arch-documenter` (consult `sheet-set-assembly-discipline`).
- Round-trip fidelity audit of the IFC → `freecad-headless-round-trip`.
- Render-engine flag or API lookup (flag uncertainty) → emit `PAUSE: need research-docs-lookup for <subject>` and stop.
- A tool crash (render engine exits non-zero with a stack trace) → `systematic-debugging`.

## Procedure 1 — Scene/mesh export and empty-scene verification

**Non-empty verification before rendering is mandatory.** Rendering against an empty scene produces a black frame with exit code 0 — the signature failure. Verify the exported scene is non-empty (triangle/element count > 0) before issuing any render command.

**Export procedure:**

1. Source the export command from the brief (IFC path, export format, output path). All scene constants, paths, and flags arrive from the brief; none are invented.
2. Execute the export command via Bash. Capture stdout and stderr verbatim.
3. After export, stat the scene file: byte count AND element/triangle count. An empty file (0 bytes) OR a file with zero triangles/elements is `EMPTY`.
4. **If EMPTY: do not proceed to rendering.** Diagnose the cause (see Procedure 4 — empty-scene diagnosis comes first in the order). Flag as a finding and surface `PAUSE: orchestrator must clarify <cause>` before continuing.
5. **If non-empty (triangle/element count > 0):** record the count and proceed to Procedure 2.

**Success criterion for scene export:** byte count > 0 AND triangle/element count > 0. Exit code alone is not sufficient.

## Procedure 2 — Camera-resolution and light-rig binding checks

Before rendering, verify that every named camera resolves within the scene bounds and every named light rig binds with non-zero intensity.

**Camera-resolution procedure:**

1. From the brief, enumerate every named camera (id, view description, approximate position).
2. For each camera, verify that the camera definition resolves: camera position is within or proximate to the scene bounding box; camera target is within the scene. An out-of-bounds camera produces a render looking into empty space.
3. An unresolved camera is a finding (unbound camera → finding; do not render that camera without resolution).
4. Record the camera-resolution verdict (resolved | UNBOUND) per camera id.

**Light-rig binding procedure:**

1. From the brief, enumerate every named light rig (id, type, intensity).
2. For each light rig, verify it binds in the scene with non-zero intensity. A rig with zero intensity renders the scene in darkness.
3. An unbound rig or zero-intensity rig is a finding.
4. Record the light-rig verdict (bound | UNBOUND | zero-intensity) per rig id.

## Procedure 3 — Material-binding for render

Bind already-authored materials onto the throwaway render scene. This procedure operates on a throwaway export only — never on the authoritative `IfcMaterial` / `IfcSurfaceStyle` spec in the source IFC or spec JSON.

**Procedure:**

1. From the brief, enumerate the material-binding spec: surface-type → material-id → render-engine material slot.
2. For each surface, bind the material to the render-engine slot. All material values (colour, roughness, reflectivity) arrive from the brief (authored by `arch-spec-writer`); none are invented or corrected here.
3. A surface with no binding entry in the brief is a `MATERIAL-BIND-MISS`. Record it as a finding note. Do not invent a binding — surface the miss and continue with other surfaces.
4. A `MATERIAL-BIND-MISS` does not block rendering of other cameras but is reported in the verdict.
5. **Never mutate the authoritative IfcMaterial / IfcSurfaceStyle spec.** Any "fix" that writes to the source IFC or spec JSON is a lane violation. Material misses route back to `arch-spec-writer`.

## Procedure 4 — Empty/black render diagnosis

Apply this diagnosis ORDER sequentially from the captured evidence when a render frame is EMPTY (zero bytes) or BLACK (non-zero bytes but mean luma below threshold or non-background-pixel ratio near zero).

**Diagnosis order (apply in sequence; do not skip):**

1. **Empty-scene export** — was the scene file itself empty (zero triangles/elements per Procedure 1)? If yes: this is the root cause. The render engine had nothing to draw. Fix: re-run Procedure 1, diagnose export failure, surface to `freecad-architect` if model export is broken.
2. **Unbound camera** — was any camera flagged UNBOUND in Procedure 2? If yes: the camera points into empty space. Fix: correct the camera definition (source from brief, not invented) and re-render.
3. **No/zero lighting** — was any light rig flagged UNBOUND or zero-intensity in Procedure 2? If yes: the scene rendered in darkness. Fix: correct the light-rig binding from the brief and re-render.
4. **Clipping plane / extent** — does the camera near-clip or far-clip exclude the model? Check camera clip distances against the scene bounding box. If the model is outside the clip range, the frame is black even with a valid scene and camera.
5. **Material-bind miss** — was a significant surface area a `MATERIAL-BIND-MISS`? A miss on an opaque enclosing surface (floor, wall, ceiling) can produce a frame that appears black or empty even with geometry present.
6. **Other** — if none of the above explains the empty/black result, capture all available evidence and surface `PAUSE: need research-docs-lookup for <render-engine flag or issue>` or `systematic-debugging` for a crash.

**Never short-circuit.** Apply each step in order from the captured evidence; do not jump to step 6 without exhausting steps 1–5.

## Output blocks

The consuming agent emits structured blocks for each render id and the manifest.

**Render execution (one per render id):**

```
@@RENDER-EXEC BEGIN
render id | exact command (verbatim) | exit code | output path | non-empty (y N-bytes WxH | EMPTY) | non-black (y mean-luma | BLACK) | diagnosis (n/a | step:<1-6> <detail>)
<captured stdout verbatim>
<captured stderr verbatim>
@@RENDER-EXEC END
```

**Render manifest (one per render + completeness verdict):**

```
@@RENDER-MANIFEST BEGIN
render id | output path | non-empty | non-black | present (y | MISSING)
<completeness verdict line: named renders: N | present: N | missing: <list or none> | every named render present (y/n)>
@@RENDER-MANIFEST END
```

Never paraphrase a command or its output. Quote verbatim (CLAUDE.md §4). The exact command and captured stdout/stderr are the evidence that a render was actually attempted.

## PAUSE routing

Two PAUSE destinations for this skill:

- **Render-engine flag or API uncertainty** → `PAUSE: need research-docs-lookup for <subject>`.
- **Tool crash** (non-zero exit with stack trace) → `systematic-debugging`.

## Inline invariants

These hold unconditionally before any procedure is entered.

**Non-empty AND non-black — not exit code.** A zero-exit black or empty frame is the signature failure. Byte count, pixel dimensions, and mean luma (or non-background-pixel ratio) are the success criteria. Exit code is recorded but never the sole success criterion.

**Verify scene before rendering.** Procedure 1 (export + non-empty check) completes before any render command is issued. Rendering against an empty scene wastes time and produces a black frame that mimics a render-engine failure.

**Never mutate the authoritative material spec.** Materials are bound onto a throwaway render scene. The source IFC and spec JSON are read-only from this skill's perspective. Any material miss routes to `arch-spec-writer`, not to a write in this pipeline.

**Diagnosis order is sequential.** Empty/black diagnosis must follow the six-step order. Jumping directly to "other" without checking steps 1–5 is an anti-pattern.

**Exact command + captured stdout/stderr verbatim.** Every `@@RENDER-EXEC` block quotes the exact command and its captured output. A paraphrase is not evidence (CLAUDE.md §4).

**All render values from brief (SAGE-GENERIC).** Camera coordinates, sun angles, material bindings, resolution, and output paths all arrive from the brief. None are invented or hard-coded in this file.

**Read scene before binding.** The scene file is Read (stat + element count) before any material-binding or render command is issued (CLAUDE.md §4 "view first" rule).

## Anti-patterns

- **Success on exit code alone.** A zero-exit black or empty frame is the signature failure. Byte count, pixel dimensions, and mean luma determine success.
- **Rendering on an empty scene export.** If Procedure 1 returns EMPTY, stop and diagnose before issuing any render command. Proceeding produces a guaranteed black frame.
- **Mutating the model or authoritative material spec while "fixing" a bind.** Material misses route to `arch-spec-writer`. This skill binds materials onto a throwaway export only.
- **Self-certifying geometry matches the drawing.** This skill checks render completeness and non-emptiness. Whether the rendered geometry correctly represents the design is `freecad-model-auditor`'s verdict.
- **Inventing camera angle, exposure, sun angle, or material value.** All render values arrive from the brief. Invented values are CLAUDE.md §4 fabrications.
- **Short-circuiting the diagnosis order.** Jumping to "other" (step 6) without exhausting steps 1–5 from the captured evidence is a gap finding.
- **Claiming "rendered" without exact command + captured stdout/stderr + per-render non-empty/non-black check.** Evidence is the contract (CLAUDE.md §4).
- **"While I'm rendering" extras.** Any render not named in the brief is out of scope.
- **Paraphrasing a command or its output.** Quote verbatim — a paraphrase is not evidence.

## When NOT to use this skill

- Model geometry mutation or IFC regeneration → `freecad-architect`.
- Authoring the authoritative `IfcMaterial` / `IfcSurfaceStyle` spec → `arch-spec-writer` (consult `ral-surface-style-discipline`).
- 2D plan/section sheet assembly → `arch-documenter` (consult `sheet-set-assembly-discipline`).
- Round-trip fidelity audit → `freecad-headless-round-trip`.
- Render-engine flag/API lookup → emit `PAUSE: need research-docs-lookup for <subject>`.
- Tool crash → `systematic-debugging`.
- Pre-completion verification → `verification-before-completion` (load this skill alongside it for render items).
