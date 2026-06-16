---
name: media-to-manual
description: "Use when navigating a media job package (index.md, manifest.json, segments.jsonl, frames/), answer topic queries by timecode, select frames, author a quick-reference guide or step-by-step manual, or run pipeline in stage order. Not for: done-gate (→ verification-before-completion); IFC sheet/render (→ sheet-set-assembly-discipline / ifc-render-pipeline-discipline); PDF dim extraction (→ pdf-vector-extraction-discipline); SOP authoring, no media package (→ biz-sop-discipline)."
---

# Media-to-Manual Discipline

This skill encodes the shared navigation and authoring discipline that the four media agents apply when working with a media job package. The consuming agent reads the index first, uses timecode as the single join key, loads only the chapters relevant to the task, and renders output that cites timecodes. CoT injection is not applicable here (this is navigation and template application — summarization-class per ai-dev-conventions.md CoT classification); the consuming agents carry CoT where their own classification warrants it.

## When this skill binds

Fire this skill when any of these are true:

- You are answering a query that requires navigating index.md or manifest.json to locate a topic, chapter, or timecode range.
- You are composing a quick-reference guide or full manual from a media job package.
- You are selecting frame_ids for inclusion in an output document.
- You are running pipeline scripts in stage order, honoring resume markers.
- You are joining segments, frames, and chapters by timecode.

Do NOT fire this skill for:

- Generic completion verification → `verification-before-completion`.
- IFC sheet assembly, IfcConvert export, or render pipeline → `sheet-set-assembly-discipline` or `ifc-render-pipeline-discipline`.
- Extracting dimensions from a source PDF → `pdf-vector-extraction-discipline`.
- SOP authoring driven by business-process interviews with no source-media package → `biz-sop-discipline`.

## File contracts

A media job package at `~/dev/media-jobs/<slug>/` consists of:

**`transcript/segments.jsonl`** — one JSON object per line; each segment carries `segment_id` (s + 4 digits, e.g. `s0001`), `t_start`, `t_end` (seconds, float), `text`, `words[]`. Immutable after transcription; the proofreader writes `transcript/proofed.md` and `transcript/corrections.md` as separate outputs, never mutating this file.

**`manifest.json`** — top-level keys: `job`, `segments[]`, `frames[]`, `chapters[]`, `stages`. The `stages` object is the resume-marker store; a stage name with value `"done"` means that stage completed successfully. The implementer writes the full manifest at build time; the indexer writes `chapters[]` only during refinement. No other agent edits `segments[]`, `frames[]`, `job`, or `stages`.

**`frames/<f_id>_<HH-MM-SS>.jpg`** — extracted frames. Each frame entry in `manifest.json frames[]` carries `id` (f + 6 digits, e.g. `f000042`), `t`, `path`, `reason` (`"scene-change"` | `"topic-midpoint"` | `"ui-cue"` | `"interval"`), `phash`. A `frame_id` is only valid if its `path` exists on disk.

**`index.md`** — YAML front matter followed by one section per chapter; each section names the chapter id (`c` + 2 digits, e.g. `c01`), title, `t_start`–`t_end`, summary, keywords, and a `segment_ids[]` list with representative `frame_ids[]`. This is the navigation entry point — all agents read it first.

**`transcript/proofed.md`** — corrected transcript prose keyed to timecodes; produced by the proofreader after the base transcript exists.

**`transcript/corrections.md`** — append-only log; one row per correction: `timecode | original | corrected | reason | confidence`. Never rewritten, only appended.

**`output/`** — rendered documents: `quick-ref-<topic-slug>.<ext>` (condensed steps + key frames) and `manual-<topic-slug>.<ext>` (numbered steps, one screenshot per meaningful action, narrative from proofed transcript). Every document cites timecodes for every step.

**ID conventions:** slug = kebab-case + short hash; segment_id = `s` + 4 digits; frame_id = `f` + 6 digits; frame filename = `f_<6digit>_<HH-MM-SS>.jpg`; chapter_id = `c` + 2 digits.

## Procedure 1 — Read-index-first navigation

The consuming agent always reads `index.md` before loading any other file.

1. Read `index.md` in full. Map the topic query to one or more chapter entries by matching against title, summary, and keywords.
2. If the match is confident: record the matched chapter ids and their `t_start`–`t_end` ranges and `segment_ids[]`.
3. If the match is ambiguous (two chapters equally likely, or no chapter covers the topic): surface `PAUSE: orchestrator must clarify — topic maps to chapters <list> or none; confirm which chapter range to load`.
4. Load only the matched chapters' segment ranges from `transcript/segments.jsonl` (seek by `segment_id` range — do not read the full file unless the topic spans most content).
5. Collect the `frame_ids[]` for the matched chapters from the index and/or manifest.

**Timecode is the single join key.** Never re-derive a segment-to-frame or chapter-to-segment join by text matching. Every join uses the timecode fields (`t_start`, `t_end`) present in segments, frames, and chapters.

## Procedure 2 — Frame selection

When composing an output document, select frame_ids from the matched chapter entries.

**Priority order (by `reason` field value):**

1. `"scene-change"` — visual transition; always prefer.
2. `"topic-midpoint"` — marks a semantic shift; prefer over `"ui-cue"` and `"interval"`.
3. `"ui-cue"` — a recognizable UI event; prefer over `"interval"`.
4. `"interval"` — fallback only when no higher-priority frame covers the step.

Read the actual frame `.jpg` file (Read tool on the frame path) before writing a caption or embedding a reference. Do not caption from the filename alone.

A `frame_id` referenced in output must exist in `manifest.json frames[]` AND its file must be present on disk. An absent frame_id is a blocking self-finding.

## Procedure 3 — Stage-order pipeline execution

The transcriber agent runs pipeline scripts in stage order, honoring the `stages` resume markers in `manifest.json`.

**Preflight:** doctor runs before any stage (via `~/.venvs/media/bin/python scripts/media/run.py <source_file> <job_dir> --slug <slug>`). Use `--no-doctor` to skip if doctor was already confirmed clean.

**Stage order (six stages):** probe → audio → transcribe → frames → manifest → index.

The real CLI is positional: `~/.venvs/media/bin/python scripts/media/run.py <source_file> <job_dir> --slug <slug> [--force-stage <stage>] [--no-doctor]`. One invocation runs all six stages in order, honoring resume markers. `--force-stage <stage>` re-runs a single stage. There is no `--stage <name> <slug>` flag.

For each stage:

1. Check `manifest.json stages.<stage_name>`. If `"done"`, skip — do not re-run.
2. If not done: invoke via the positional CLI above. Capture stdout and stderr verbatim.
3. After the stage completes, verify the output is non-empty (stat byte count; inspect structure). A zero-exit empty output is NOT a successful stage — it is the signature failure mode.
4. Emit `@@STAGE-CHECK` row with the stage name, byte count (or element count), schema-valid status, and captured stdout/stderr verbatim beneath.
5. If a stage fails: surface the failure with the quoted output. Do not advance to the next stage.

**Idempotency:** a second run on a fully completed package skips all done stages and exits cleanly. This is the correct behavior — do not treat skip-all as an error.

**Missing dependency:** if the doctor stage reports a missing tool, surface the finding as `run setup.sh to install missing dependencies` and stop. Do not attempt to install dependencies directly.

## Procedure 4 — Output document composition

When composing a quick-reference guide or full manual:

1. Determine the document type (`quick-ref` or `manual`) and output format(s) (`md`, `pdf`, `docx`) from the brief.
2. Match the topic to chapter(s) via Procedure 1.
3. Load only the matched segment ranges + frame_ids.
4. Read the matched frame `.jpg` files.
5. Compose the document:
   - **Quick-reference:** condensed steps; key frames only (one per major transition); timecode citation per step.
   - **Manual:** numbered steps in chronological order; one screenshot per meaningful action; narrative text drawn from `transcript/proofed.md` where available, otherwise from `transcript/segments.jsonl` for the matched range; timecode citation per step.
6. Every step cites its timecode in the form `(t_start–t_end)` or `[HH:MM:SS]`.
7. Never assert a step that the proofed transcript does not support. Absent content → omit, not invent.
8. Render via pandoc or the docgen toolkit at `~/.venvs/docgen`. Capture the render command and output verbatim.
9. Verify the rendered output is non-empty (stat byte count). A zero-exit empty file is not a successful render.
10. Write to `output/quick-ref-<topic-slug>.<ext>` or `output/manual-<topic-slug>.<ext>`.

## Output blocks

The consuming agent emits structured blocks when applying this discipline.

**Stage check (one row per pipeline stage):**

```
@@STAGE-CHECK BEGIN
stage | non-empty (y N-bytes | EMPTY) | schema-valid (y/n/na) | note
<captured stdout verbatim>
<captured stderr verbatim>
@@STAGE-CHECK END
```

**Manual plan (before rendering):**

```
@@MANUAL-PLAN BEGIN
topic | matched chapter id(s) | doc type | segment ranges loaded | frame_ids selected + reason
@@MANUAL-PLAN END
```

**Render evidence (one per render command):**

```
@@RENDER BEGIN
exact command (verbatim) | exit code | output path | non-empty (y N-bytes | EMPTY)
<captured stdout verbatim>
<captured stderr verbatim>
@@RENDER END
```

Never paraphrase a command or its output. Quote verbatim (CLAUDE.md no-fabrication rule).

## Inline invariants

These hold unconditionally before any procedure is entered.

**Read index.md first.** No other file is loaded before index.md is read. Jumping directly to segments.jsonl is a lane violation.

**Timecode is the single join key.** Never re-derive a join by text matching. If a segment_id or frame_id is absent from the index or manifest, surface a PAUSE rather than guessing the association.

**Non-empty check, not exit code.** A zero-exit empty output is the signature failure mode for pipeline stages and renders. Byte count or element count determines success.

**Never embed an absent frame_id.** A frame_id referenced in output must exist in manifest.json and its file must be on disk. Absent → blocking self-finding, not a warning.

**Prefer `scene-change` and `topic-midpoint` frames.** `interval` frames are a last resort when no higher-priority frame covers the step. Priority: `scene-change` → `topic-midpoint` → `ui-cue` → `interval`.

**Cite timecodes on every step.** An output document step without a timecode citation is incomplete output.

**Never write into the framework repo.** All job artifacts (job package, output documents) live at `~/dev/media-jobs/<slug>/`. Nothing from a media job enters `agents/`, `skills/`, or any repo path.

**SAGE-GENERIC.** No product names, vendor names, employer names, client names, or hardcoded topic strings in this file. Runtime context arrives via brief.

## Anti-patterns

- **Bulk-reading segments.jsonl when the topic maps to a chapter subset.** Load only the matched chapter's segment ranges. Full-file reads are reserved for topics that span most of the content.
- **Joining by text match instead of timecode.** Text-based joins drift when proofreading changes wording. Timecode is the contract.
- **Citing a frame_id absent from manifest or missing on disk.** Any frame referenced in output must be verified present before the document is written.
- **Preferring an `interval` frame when a `scene-change` or `ui-cue` frame exists in the chapter range.** Always prefer the highest-priority `reason` value: `scene-change` → `topic-midpoint` → `ui-cue` → `interval`.
- **Skipping a done stage to force a re-run.** Done stages are idempotent by design; re-running them is wasteful and can produce inconsistent outputs.
- **Writing a job artifact into the repo.** Job packages and output documents are local-only at `~/dev/media-jobs/<slug>/`.
- **Asserting a step absent from the transcript.** If the transcript does not support a step, omit it — never invent.
- **Omitting a timecode citation on a step.** Every step in a quick-ref or manual carries a timecode.
- **Hardcoding a product, vendor, client, or topic name in skill or agent content.** All runtime names arrive via brief.
- **Declaring a stage or render done on exit code alone.** Stat the output; a zero-exit empty file is not done.

## When NOT to use this skill

- Generic completion verification → `verification-before-completion`.
- IFC sheet assembly or IfcConvert export → `sheet-set-assembly-discipline`.
- IfcConvert render pipeline → `ifc-render-pipeline-discipline`.
- Extracting dimensions from a source PDF → `pdf-vector-extraction-discipline`.
- SOP authoring driven by business-process interviews with no source-media package → `biz-sop-discipline`.
- Systematic debugging of a tool crash (non-zero exit with stack trace) → `systematic-debugging`.
