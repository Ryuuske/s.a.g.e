---
name: pdf-vector-extraction-discipline
description: "Use when extracting dimensions from an architectural PDF, reasoning about page rotation or coordinate transposition, calibrating scale from a known grid or face-pair, or classifying strokes as wall-cut vs dimension/axis lines. Do not use for: extraction stack traces (→ systematic-debugging); claiming extraction done (→ verification-before-completion); API signature lookup (→ PAUSE for research-docs-lookup)."
---

# PDF Vector Extraction Discipline

This skill encodes four decision trees — rotation audit, calibration, dimension reading, and independent crosscheck — that the consuming agent applies when extracting scaled dimensions from architectural PDF pages. Each read carries a calibrated confidence score and a stated basis. The discipline applies to both author-mode (writing an extraction script) and audit-mode (reviewing extracted dimension tables).

This skill co-loads with `test-driven-development` (no overlap) and contributes PDF-specific verification items to `verification-before-completion` without duplicating its general procedure. It does not narrow `systematic-debugging` — that skill's triggers are bug, test failure, unexpected behavior, and stack trace; there is no extraction-logic entry point in that skill's trigger set.

All four decision trees are logic-heavy: rotation audit requires classifying the page's coordinate space and applying a matrix transformation before any measurement; calibration requires deriving a scale factor from independent anchor geometry and self-checking it; dimension reading requires stroke-width classification to distinguish wall-cut from dimension/axis lines and confidence-scoring each read; crosscheck requires an independently derived second pass that catches systematic offset. The consuming agent (arch-pdf-extractor) should apply CoT throughout all four trees.

## When this skill binds

Fire this skill when any of these are true:

- You are extracting dimensions from an architectural PDF using PyMuPDF or equivalent.
- You are reasoning about whether page coordinates are transposed due to a 90° page rotation.
- You are calibrating scale from a known reference grid or a nominal face-pair.
- You are deciding whether a stroke is a wall cut, a dimension line, or an axis line.
- You are seeing absolute position offsets of ~60–70 mm while individual gaps match the spec.

Do NOT fire this skill for:

- Extraction script returned a stack trace, investigate → `systematic-debugging`.
- Summarizing the text content of a PDF (room labels, schedules, notes) → general read path; do not load this skill.
- Claiming extraction is done and verifying it → `verification-before-completion`.
- PyMuPDF or other library API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup` and stop; do not WebFetch or WebSearch directly.
- Designing an agent that runs PDF extraction → `agent-creation` via `aidev-agent-creator`.

## Decision tree 1 — Rotation audit

**Rotation-first is the non-negotiable opening clause.** No coordinate from `page.get_text("words")` or `page.get_drawings()` is trusted until rotation has been assessed and, if non-zero, the rotation matrix applied.

**Audit procedure:**

1. Read `page.rotation`. Record the value.
2. If `page.rotation == 0`, the coordinate space is nominal; no transformation is required. Proceed.
3. If `page.rotation != 0`, multiply every point returned by `get_text("words")` and `get_drawings()` by `page.rotation_matrix` BEFORE any measurement or calibration step. Applying calibration to unrotated points and then correcting for rotation after is incorrect — calibration is derived from the rotated coordinate space.
4. Emit `@@PDF-ROTATION-AUDIT BEGIN` block.

```
@@PDF-ROTATION-AUDIT BEGIN
page.rotation | rotation_matrix applied (yes | no | n/a) | coord space after (nominal | rotated) | finding
@@PDF-ROTATION-AUDIT END
```

A non-zero rotation with the matrix unapplied is a blocking finding — no downstream reads are valid until resolved.

## Decision tree 2 — Calibration

Calibration derives a scale factor K (mm per PDF point) from reference geometry visible on the page. A single anchor is not sufficient; an independent self-check anchor is required.

**Calibration sources in order of preference:**

1. Named grid chain — two grid-line intersections whose nominal spacing is known (e.g., a grid module stated on the drawing or in the spec). Use the measured point-space distance between the two intersections and the nominal spacing to derive K.
2. Nominal face-pair — two wall faces whose nominal clear distance is known (e.g., a corridor width from the spec). Use the measured point gap and the nominal dimension to derive K.

**Self-check requirement:** after deriving K, apply it to at least one independent anchor (a different grid chain, a different face-pair, or a known overall dimension not used in the primary calibration). Agreement within ±2 % is acceptable. A discrepancy > 2 % is a finding: "calibration self-check failed — K may not be representative for this drawing zone."

**Emit `@@PDF-CALIBRATION BEGIN` block:**

```
@@PDF-CALIBRATION BEGIN
calibration source | nominal dimension mm | measured pts | derived K (mm/pt) | self-check anchor | self-check K | agreement (yes | no | discrepancy %)
@@PDF-CALIBRATION END
```

## Decision tree 3 — Dimension reading

Each dimension read is classified by type, source, and confidence.

**Read types:**

- **Gap** — the clear distance between two opposing faces or features. Gaps are exact to the stroke's rendered edge. Confidence is higher.
- **Absolute position** — the coordinate of a single feature relative to a drawing origin. Absolute positions carry ±60–70 mm typical drift due to drawing insertion-point offset, axis-line placement convention, and page-trim margin variation. Always assign lower confidence to absolute reads.

**Stroke classification — wall cut vs dimension/axis line:**

Distinguish wall-cut strokes from dimension lines and axis lines by stroke-width class:
- Wall-cut strokes are typically the heaviest stroke class on the page (e.g., ≥ 0.5 pt in the drawings dict width field, but verify the class boundary from the actual drawing).
- Dimension lines are thinner (typically 0.1–0.25 pt) and appear flanked by arrowheads or tick marks.
- Axis lines are thin and often dashed with a centre-line dash pattern.
- **Width-class collision warning:** if the wall-cut and dimension-line classes have overlapping width ranges on a specific page, note it in the `@@PDF-DIMENSION-READ` block and assign lower confidence to any read from that zone.

**Flags:**

- Single-read from a cluttered zone (swing-arc overlap, furniture symbol crossing the measurement path): mark UNVERIFIED, not guessed.
- Raster-only features (scanned lines not returned by `get_drawings()`): flag lower-confidence, source = raster, and note that the read depends on pixel-space inference rather than vector geometry.

**Emit `@@PDF-DIMENSION-READ BEGIN` block (one row per read):**

```
@@PDF-DIMENSION-READ BEGIN
measurement | value mm | read type (gap | absolute) | source (vector | raster) | confidence 0-100 | basis | status (verified | UNVERIFIED)
@@PDF-DIMENSION-READ END
```

### Worked example — gap vs absolute confidence

A corridor width measured as the gap between two wall-face strokes of known stroke class:

```
@@PDF-DIMENSION-READ BEGIN
measurement              | value mm | read type | source | confidence | basis                            | status
corridor width grid A–B  | 1 850    | gap       | vector | 85         | wall-face strokes, K=0.412 mm/pt | verified
room origin x from grid 0| 4 620    | absolute  | vector | 55         | axis-line position, K=0.412      | UNVERIFIED
@@PDF-DIMENSION-READ END
```

The gap read is higher confidence (85) because gaps are exact between vector edges. The absolute read is lower confidence (55) because absolute positions carry ±60–70 mm typical drift from drawing insertion-point and axis-line placement conventions.

## Decision tree 4 — Independent crosscheck

**A second, fully independent pass is required before any dimension table is considered verified.** The crosscheck pass must re-derive rotation, calibration, and origin from scratch — it does not reuse the K or rotation-matrix from the primary pass. A systematic offset only visible as a constant shift across all absolute positions (e.g., ~22 mm uniform bias) is a real phenomenon detected in practice and is only caught via a from-scratch second pass.

**Crosscheck procedure:**

1. Reset all intermediate values. Do not carry forward K, rotation-matrix, or origin from the primary pass.
2. Select a different calibration anchor than the one used in the primary pass.
3. Re-derive K independently.
4. Re-measure a subset of the primary reads using the independently derived K.
5. Compare. If all values agree within ±2 %, the table is verified. If a systematic offset is found, record it in the crosscheck block and re-examine whether the primary origin assumed a nominal axis rather than a measured wall face.

**Emit `@@PDF-CROSSCHECK BEGIN` block:**

```
@@PDF-CROSSCHECK BEGIN
rotation re-derived (yes | no) | calibration re-derived from (different anchor) | independent K (mm/pt) | systematic offset detected (none | <magnitude> mm uniform) | verdict (verified | UNVERIFIED — re-examine origin)
@@PDF-CROSSCHECK END
```

Absent a completed independent crosscheck, the dimension table status remains UNVERIFIED regardless of primary-pass confidence scores.

## Function-reference verification

Never guess a PyMuPDF API call shape. If uncertain whether `page.get_drawings()` returns a list of dicts or objects, or what keys the `rect` field in a drawing element carries, emit:

```
PAUSE: need research-docs-lookup for <subject> reference lookup
```

Stop there. Do not attempt the call with a guessed signature.

### When this skill PAUSEs

The PAUSE shape above is the ADR-0027 pattern. `research-docs-lookup` is in the active roster. When the PAUSE fires, the orchestrator dispatches `research-docs-lookup` to resolve the API signature.

## Output blocks

The consuming agent emits structured blocks for each decision tree applied. All blocks use the delimiter pattern established across the agent roster.

**Rotation audit:**
```
@@PDF-ROTATION-AUDIT BEGIN
page.rotation | rotation_matrix applied (yes | no | n/a) | coord space after (nominal | rotated) | finding
@@PDF-ROTATION-AUDIT END
```

**Calibration:**
```
@@PDF-CALIBRATION BEGIN
calibration source | nominal dimension mm | measured pts | derived K (mm/pt) | self-check anchor | self-check K | agreement (yes | no | discrepancy %)
@@PDF-CALIBRATION END
```

**Dimension read:**
```
@@PDF-DIMENSION-READ BEGIN
measurement | value mm | read type (gap | absolute) | source (vector | raster) | confidence 0-100 | basis | status (verified | UNVERIFIED)
@@PDF-DIMENSION-READ END
```

**Crosscheck:**
```
@@PDF-CROSSCHECK BEGIN
rotation re-derived (yes | no) | calibration re-derived from | independent K (mm/pt) | systematic offset detected (none | <magnitude> mm uniform) | verdict (verified | UNVERIFIED — re-examine origin)
@@PDF-CROSSCHECK END
```

API-signature uncertainty surfaces as a standalone `PAUSE:` line before any read is emitted.

## Anti-patterns

- **Applying calibration before applying the rotation matrix.** Calibration is derived from the rotated coordinate space. The rotation matrix must be applied first; calibration on unrotated points produces a wrong K and silently wrong dimensions.
- **Trusting absolute positions at the same confidence as gaps.** Absolute positions carry ±60–70 mm typical drift. They always receive lower confidence and the basis must name the drift risk explicitly.
- **Assigning VERIFIED status without an independent crosscheck.** A crosscheck is required before verification. Primary-pass confidence scores do not substitute for an independent second pass.
- **Guessing a measurement from a cluttered zone.** Swing-arc overlaps, furniture symbols, and double-line hatching all obscure wall-face positions. Flag these UNVERIFIED; do not interpolate.
- **Using the same calibration anchor in both primary pass and crosscheck.** The crosscheck must use a different anchor. Re-using the same anchor is not an independent verification.
- **Ignoring width-class collision.** If wall-cut and dimension-line strokes overlap in width, reads from that zone get lower confidence and the collision is noted. Treating all strokes in a collision zone as wall cuts is a finding.
- **Treating raster-only reads as vector-precision.** Raster reads carry lower confidence and must be flagged with source = raster.
- **Guessing a PyMuPDF API call shape.** Emit the PAUSE shape instead.

## Output guidance

### Semantic guidance

- Never claim a dimension is verified without completing the independent crosscheck (@@PDF-CROSSCHECK).
- Never emit an absolute-position read at confidence > 70 — systematic drift is real and confidence must reflect it.
- Every read carries a stated basis naming the strokes, grid lines, or face pairs used.
- Absent crosscheck, the full table status is UNVERIFIED, not "mostly verified."

### Tool guidance

- **Read** — view the extraction script in full before applying any decision tree (CLAUDE.md §4).
- **Grep** — scan for `page.rotation`, `rotation_matrix`, `get_drawings`, `get_text`, `get_text("words")`, and numeric literals used as calibration references.
- **Glob** — locate extraction scripts when the brief names a drawing area without an exact path.
- **No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.
- **No WebFetch or WebSearch** — API-signature uncertainty emits a `PAUSE:` line only (ADR-0027 shape); the orchestrator dispatches `research-docs-lookup`.

## When NOT to use this skill

- Extraction script returned a stack trace → `systematic-debugging`.
- Summarizing text content of a PDF (room names, schedules) → general read path; this skill does not apply.
- Pre-completion verification → `verification-before-completion` (load this skill alongside it for the PDF-specific items, but `verification-before-completion` governs the overall procedure).
- PyMuPDF API signature lookup → emit `PAUSE: need research-docs-lookup for <subject> reference lookup`; the orchestrator dispatches `research-docs-lookup` (ADR-0027).
- Any non-PDF vector extraction pipeline.
