---
name: sheet-set-assembly-discipline
description: "Use when exporting plan/section sheets via IfcConvert, checking the issued set for completeness (no numbering gap/duplicate, titleblocks filled, PDF in order), or diagnosing a blank sheet. Not for: model mutation (→ freecad-architect); 3D render (→ arch-visualizer); PDF dim extraction (→ arch-pdf-extractor); round-trip audit (→ freecad-headless-round-trip); IfcConvert flag lookup (→ PAUSE research-docs-lookup); tool crash (→ systematic-debugging)."
---

# Sheet-Set Assembly Discipline

This skill encodes five verifiable procedures — IfcConvert plan/section/SVG export, sheet-numbering integrity, titleblock-field completeness, schedule-table column-set matching, and deliverable-PDF concatenation — that the consuming agent applies when assembling the issued sheet set from an IFC model. It is the output-assembly analog to `ifc-geometry-discipline` (build) and `pdf-vector-extraction-discipline` (extract). The consuming agent (`arch-documenter`) applies these procedures in order; CoT injection is not applicable (this is assembly/template work, not derivation — summarization-class per `ai-dev-conventions.md` CoT classification).

This skill co-loads with `ifc-geometry-discipline` for the IfcConvert `--section-height` unit invariant (always SI metres), which is re-stated here because a millimetre value is the signature blank-sheet failure. This skill does not duplicate `ifc-geometry-discipline`'s broader unit-domain logic — the only unit question in the assembly lane is the `--section-height` domain.

## When this skill binds

Fire this skill when any of these are true:

- You are invoking IfcConvert to export a plan/section/elevation SVG for a sheet.
- You are diagnosing a blank or empty export result.
- You are checking the issued set for numbering gaps, duplicates, or missing sheets.
- You are verifying titleblock fields are filled from the brief.
- You are checking that a rendered schedule table matches the column set in the brief.
- You are assembling the deliverable PDF in sheet-number order.

Do NOT fire this skill for:

- Model geometry mutation or IFC regeneration → `freecad-architect`.
- 3D or photoreal rendering → `arch-visualizer`.
- Extracting dimensions from a source PDF → `arch-pdf-extractor`.
- Assessing round-trip fidelity of the IFC → `freecad-headless-round-trip`.
- IfcConvert flag or API lookup (flag uncertainty) → emit `PAUSE: need research-docs-lookup for <subject>` and stop.
- A tool crash (IfcConvert exits non-zero with a stack trace) → `systematic-debugging`.

## Procedure 1 — IfcConvert plan/section/SVG export

**SI-metres section-height invariant:** `--section-height` is always SI metres. This is the single most common blank-sheet failure.

```
--section-height 1.5   # cuts at 1.5 m above storey origin — correct for a floor plan
--section-height 1500  # cuts at 1500 m above origin — above any building, blank result
```

A millimetre value produces a section height above the roof of any ordinary building, returning an empty SVG with exit code 0. An empty SVG with exit code 0 is NOT a successful export.

**Include filter requirement:** IfcConvert SVG output includes only `IfcSpace` entities by default. To include walls, slabs, doors, windows, or structural elements in a plan SVG, pass an explicit `--include` filter for each element class required by the brief. Omitting it and expecting walls to appear is a silent omission, not a tool failure.

**Export procedure:**

1. Source the `--section-height` value from the brief in SI metres. If the brief supplies a millimetre value, convert before passing to IfcConvert (`mm / 1000`). Never pass the raw mm value.
2. Build the `--include` filter from the brief's named element classes.
3. Execute the IfcConvert command. Capture both stdout and stderr verbatim.
4. Check the output: stat the file for byte count; inspect the SVG element count. A zero-byte file or an SVG with no drawable elements is `EMPTY`.
5. **Success criterion is the non-empty (byte/element) check — not exit code.** A zero exit with a blank SVG is the signature failure mode for a wrong `--section-height` unit. Do not declare success on exit code alone.
6. If `EMPTY`: diagnose from the captured evidence before escalating.

**Blank-sheet diagnosis order** (apply in sequence from the captured evidence):

1. **Wrong-unit section-height** — the `--section-height` value in the command is in mm (value >> 3.0). Correct: pass SI metres.
2. **Missing --include filter** — the SVG contains only `IfcSpace` elements (walls/doors absent). Correct: add `--include`.
3. **Section above roof** — the section height in SI metres is correct in unit but the value exceeds the model height at that storey. Check storey heights from brief vs the value passed. Correct: reduce `--section-height`.
4. **Other / unknown** — capture evidence and surface `PAUSE: need research-docs-lookup for IfcConvert <flag> reference lookup` or `systematic-debugging` for a crash.

Emit `@@SHEET-EXPORT` row with the exact command verbatim, captured stdout/stderr verbatim beneath the block, exit code, byte count, element count, and diagnosis if EMPTY.

## Procedure 2 — Sheet-numbering integrity

The issued set must satisfy four conditions simultaneously before it can be declared issue-ready:

1. **No numbering gap** — the sheet numbers form a complete sequence with no missing numbers (e.g. A1–A7 present with no A4 missing).
2. **No duplicate** — no two sheets carry the same number.
3. **Every named sheet present** — every sheet named in the brief sheet-list has a corresponding output file.
4. **Revision-token consistent** — every sheet carries the same revision token (or the tokens match the per-sheet revision table in the brief — no accidental carry-forward from a prior issue).

**Procedure:**

1. Enumerate the sheet numbers from the brief sheet-list.
2. Enumerate the output files and extract their sheet numbers (from file names or titleblock fields per the brief convention).
3. Compare sets: identify gaps (in brief but not output), duplicates (same number on two outputs), and extras (in output but not brief).
4. Check revision tokens across all sheets.
5. Emit the `@@SHEET-INDEX` integrity verdict line.

**Issue-ready criterion:** all four conditions clear simultaneously. A set with one gap is not issue-ready, even if all other sheets are titleblocked and non-empty.

## Procedure 3 — Titleblock-field completeness

Every sheet carries a titleblock. All fields named in the brief titleblock template must be filled.

**Typical titleblock fields (SAGE-GENERIC — exact fields from brief):** project title, drawing title, sheet number, revision, date, author/drafter designation, scale. Every field value is sourced from the brief — never invented.

**Procedure:**

1. From the brief, enumerate the required titleblock fields and their values.
2. For each sheet, verify each field is filled.
3. An empty or `n/a` field for a required titleblock entry is a completeness gap — a finding.
4. Emit the `titleblock applied (y/n)` column in `@@SHEET-INDEX`.

## Procedure 4 — Schedule-table column-set matching

Rendered schedule tables (material schedule, door/window schedule, lintel schedule) must match the column set in the brief.

**Procedure:**

1. From the brief, enumerate the required columns for each schedule table.
2. For each rendered schedule, verify each required column is present.
3. An absent column is a gap finding. An extra column not in the brief is noted but not a blocking finding unless the brief explicitly prohibits it.
4. Emit a finding note in `@@SHEET-INDEX` for any schedule sheet with a missing column.

## Procedure 5 — Deliverable-PDF concatenation order

The deliverable PDF assembles sheets in strict sheet-number order.

**Procedure:**

1. Sort the sheet output files by sheet number (ascending).
2. Concatenate into the deliverable PDF using the PDF-assembly tooling named in the brief.
3. Capture the command and its output verbatim.
4. Verify the page count of the deliverable PDF equals the sheet count.
5. Write the sheet-index manifest (one row per sheet: number, title, source file, revision, page in deliverable).

## Output blocks

The consuming agent emits structured blocks for each procedure applied.

**Sheet index (one row per sheet):**

```
@@SHEET-INDEX BEGIN
number | title | source view/schedule | titleblock applied (y/n) | revision | present (y/MISSING)
<integrity verdict line: numbering gap (none|<list>) | duplicate (none|<list>) | every named sheet present (y/n) | revision-token consistent (yes|<discrepancy>)>
@@SHEET-INDEX END
```

**Sheet export (one per IfcConvert command):**

```
@@SHEET-EXPORT BEGIN
sheet number | exact command (verbatim) | exit code | output path | non-empty (y N-bytes/N-elements | EMPTY) | empty diagnosis (n/a | wrong-unit section-height | missing --include | section above roof | other:<detail>)
<captured stdout verbatim>
<captured stderr verbatim>
@@SHEET-EXPORT END
```

Never paraphrase a command or its output. Quote verbatim (CLAUDE.md §4).

## PAUSE routing

Two PAUSE destinations for this skill:

- **IfcConvert flag or API uncertainty** → `PAUSE: need research-docs-lookup for IfcConvert <flag> reference lookup`.
- **Tool crash** (non-zero exit with stack trace) → `systematic-debugging`.

## Inline invariants

These hold unconditionally before any procedure is entered.

**--section-height is always SI metres.** No exception. The brief's mm value is converted before passing to IfcConvert. A mm value passed as-is is a guaranteed blank sheet.

**Non-empty check, not exit code.** A zero-exit empty SVG is the signature blank-sheet failure. Byte count and element count are the success criteria.

**Never self-certify model fidelity.** This skill checks sheet completeness and numbering correctness. Whether the sheet view correctly represents the model geometry is `freecad-model-auditor`'s verdict. The two lanes are distinct — do not conflate.

**All sheet field values sourced from brief.** Sheet number format, titleblock strings, revision token, and PDF assembly order all arrive from the brief. None are invented or carried forward from a prior issue without explicit brief confirmation.

**Exact command + captured stdout/stderr verbatim.** Every `@@SHEET-EXPORT` block quotes the exact command and its captured output. A paraphrase of the output is not evidence (CLAUDE.md §4).

**Issue-ready requires all four integrity conditions.** No softening — a single numbering gap or missing sheet blocks the issue-ready claim.

**Read view before composing.** Every source view or schedule file is Read before it is composed onto a sheet (CLAUDE.md §4 "view first" rule).

**SAGE-GENERIC.** No hardcoded sheet numbers, titleblock strings, tool paths, or project names appear in this file. All values arrive from the brief.

## Anti-patterns

- **--section-height in millimetres.** Cuts above the roof → blank sheet with exit code 0. The silence makes it the most dangerous failure mode in this lane.
- **Declaring success on exit code alone.** A zero exit with a blank SVG is the signature failure. Byte count and element count determine success.
- **Numbering gap or duplicate.** The issue-ready criterion requires no gap and no duplicate. A gap on one sheet cannot be excused by the completeness of others.
- **Claiming "issued" without the exact command + captured stdout/stderr + per-sheet present-check.** Evidence is the contract (CLAUDE.md §4).
- **Self-certifying that a sheet view matches the drawing.** Model-view fidelity is `freecad-model-auditor`'s verdict.
- **Inventing a titleblock value.** Every titleblock field value is sourced from the brief.
- **Omitting the --include filter when non-IfcSpace elements are needed.** IfcConvert includes only IfcSpace by default; walls, doors, and windows require explicit `--include`.
- **"While I'm assembling" extras.** Any sheet or output not named in the brief is out of scope.
- **Paraphrasing a command or output.** Quote verbatim — a paraphrase is not evidence.

## When NOT to use this skill

- Model mutation or IFC regeneration → `freecad-architect`.
- 3D or photoreal rendering → `arch-visualizer`.
- Reading dimensions out of a source PDF → `arch-pdf-extractor`.
- Round-trip fidelity audit → `freecad-headless-round-trip`.
- IfcConvert flag/API lookup → emit `PAUSE: need research-docs-lookup for IfcConvert <flag> reference lookup`.
- Tool crash → `systematic-debugging`.
- Pre-completion verification → `verification-before-completion` (load this skill alongside it for sheet-assembly items).
