---
name: media-to-manual
description: "Use when ingesting a media source into a job package or navigating one for manuals — the read-index-first + timecode-join discipline the media-* agents share (pipeline order, naming, manual generation). Triggers: 'ingest a recording', 'transcribe/proofread/index a media package', 'create a quick-ref or manual from a video', 'find where the recording explains X'. Not skill/agent design (→ skill-creation); not reimplementing scripts/media/."
---

# Media-to-Manual Discipline

This skill encodes the pipeline order, naming conventions, read-index-first discipline,
timecode-join rule, and manual-generation procedure for the four media-* consumer agents:
`media-transcriber`, `media-proofreader`, `media-indexer`, and `media-manual-author`.
Every agent in the family loads this skill via description-match auto-load.

## When this skill binds

Fire this skill when any of these are true:

- You are running or verifying the scripts/media/ ingestion pipeline for a source file.
- You are proofreading segments.jsonl — fixing mishears, flagging uncertain domain terms, writing proofed.md + corrections.md.
- You are refining chapter boundaries, titles, summaries, or keywords in index.md.
- You are authoring a quick-reference guide or full manual from an existing job package for a named topic.
- You are navigating a package to answer "where in the recording does it explain X."
- You are deciding whether to load the full transcript or only a chapter range.

Do NOT fire this skill for:

- Skill or agent design — use `skill-creation` / `agent-creation` via the aidev pipeline.
- Reimplementing or debugging scripts/media/ Python or shell code — use the general code lane + `systematic-debugging`.
- PDF dimension extraction — use `pdf-vector-extraction-discipline`.
- SOP body writing or auditing — use `biz-sop-discipline`.

## Pipeline order (canonical)

```
doctor → probe → extract_audio → transcribe → extract_frames
  → build_manifest → build_index
  → [proofread → refine index]
  → PACKAGE READY
```

On demand, without reprocessing:

```
topic → read index.md ONLY → match chapter(s) → load only those segments + frames
  → author quick-ref OR full manual → render via pandoc/docgen → cite timecodes
```

**Resumability:** each stage writes its artifact and sets a `stages` marker in manifest.json.
A rerun skips any stage whose marker is already `done`. Stages are idempotent.

## Naming conventions (PRD §9)

| Item | Convention |
|---|---|
| Job slug | `kebab-case` + short hash (e.g., `onboarding-demo-a1b2`) |
| Segment IDs | `s` + 4 digits (e.g., `s0001`) |
| Frame IDs | `f` + 6 digits (`f000123`); filename `f_<6digit>_<HH-MM-SS>.jpg` |
| Chapter IDs | `c` + 2 digits (e.g., `c01`) |
| Quick-ref output | `quick-ref-<topic-slug>.<ext>` |
| Full-manual output | `manual-<topic-slug>.<ext>` |

Job packages live at `~/dev/30-operations/jobs/media/<slug>/`. They are local-only and must never be committed or pushed.

## Read-index-first discipline (load-bearing)

**Read `index.md` first. Always.**

`index.md` is small enough to always read in full; it points to everything else. Load the full transcript only if the topic genuinely spans most of the content. Loading the whole transcript when a single chapter suffices wastes context and violates the core design rule.

Decision rule: if the matched chapter(s) cover the topic, load only their segment ranges + frame_ids from manifest.json. If the topic spans ≥ 80% of the chapters, loading the full transcript is justified — state the reason explicitly.

## Timecode-join discipline

**Timecode is the single join key** between transcript segments, frames, chapters, and output documents.

`manifest.json` is the machine join: it maps `segment_id → t_start/t_end → frame_ids → chapter_id`. Never derive joins by filename parsing or line proximity. Every citation in output documents names the timecode.

**Type-folders, not time-folders.** Splitting artifacts by time fragments transcript and frames into parallel trees that must be cross-walked. Type-folders (`transcript/`, `frames/`, `output/`) + timecode-in-filename let any agent jump from a topic to the matching frame via one shared key (PRD §3b rationale).

## Manual-generation procedure (PRD §16, 7 steps)

1. **Read `index.md` only** — identify chapters, timecode ranges, keywords.
2. **Match topic** to chapter(s) via title/summary/keywords. Use the topic-match CoT chain (media-manual-author injection point): `topic → matched chapter id(s) → segment-range + frame_id set to load`.
3. **Load only matched chapters'** segment ranges from `segments.jsonl` + their `frame_ids` from `manifest.json`. Full transcript only if topic spans most content.
4. **Read actual frame images** for those IDs. Prefer `scene-change` / `ui-cue` frames over `interval` frames. Use the frame-selection CoT chain: `action step → candidate frame_ids → chosen frame (reason)`.
5. **Compose** — quick-ref: condensed steps + key frames; full manual: numbered steps, one screenshot per meaningful action, narrative from proofed.md (timecoded headers, segment IDs).
6. **Render** to requested format via pandoc / `~/.venvs/docgen`.
7. **Cite timecodes** throughout. Every output references the timecode it draws from.

## Artifact contracts (authoritative sources — no duplication)

- Machine-readable schemas: `scripts/media/schema/manifest.schema.json` and `scripts/media/schema/segments.schema.json`. These are the authoritative contracts; do not reproduce schema shapes in prose.
- `corrections.md` is **append-only**. Each entry: `original → corrected | timecode | reason-code` (reason codes: `mishear`, `product-name`, `acronym`). Uncertain terms use `FLAG` entries with the same format. Never truncate, overwrite, or reorder existing entries.
- `manifest.json` `stages` markers are the resume contract. A stage marked `done` must not be re-run unless explicitly requested.

## Job-home reminder

All job packages are at `~/dev/30-operations/jobs/media/<slug>/`, outside the framework repo. The framework repo never contains media files, audio, frames, or job-package artifacts. Nothing under `~/dev/30-operations/jobs/media/` is ever pushed (this path is outside the GitHub publication gate at `~/dev/github/Ryuuske/`).

## Anti-patterns

- **Whole-transcript default load.** When the topic maps to 1–2 chapters, loading the full transcript wastes context and violates the read-index-first design rule. Always check index.md first.
- **Fabricating a timecode or frame_id.** Citing a frame_id or timecode not present in manifest.json, or referencing a frame file absent on disk, violates the no-fabrication rule (CLAUDE.md §4). Every citation is verified against the manifest.
- **Time-folders instead of type-folders.** Organizing frames or transcript by time rather than by type makes the timecode join ambiguous. Use the canonical folder structure.
- **Interval frames over scene/ui when alternatives exist.** Interval frames are fallback only. Always prefer `scene-change` or `ui-cue` frames when they are available in the manifest.
- **Silent proofread edits.** Every correction to proofed.md must be logged in corrections.md with timecode + reason-code. Editing without logging breaks the audit trail.
- **Reimplementing pipeline logic in an agent.** Deterministic work (audio extraction, transcription, frame extraction, manifest/index generation) belongs in scripts/media/. Agents invoke those scripts; they do not reimplement them.
- **Committing ~/dev/30-operations/jobs/media/ content.** Job packages are local-only. A commit that includes media artifacts or job-package output is a publication-gate violation.
- **Duplicating schema shapes in prose.** The authoritative contracts are the schema JSON files. Prose paraphrases of the segment or manifest schema drift over time.

## Output guidance

### Formatting guidance

- `@@MEDIA-PIPELINE BEGIN…END` (media-transcriber): one row per stage (stage | exit | artifact | non-empty | sanity verdict) + verbatim stdout/stderr.
- `@@PROOF-SUMMARY BEGIN…END` (media-proofreader): corrections N | flags N | high-uncertainty segments.
- `@@INDEX-REFINE BEGIN…END` (media-indexer): chapters N | boundaries changed | coverage verdict (full | gap:<list> | overlap:<list>).
- `@@MANUAL-BUILD BEGIN…END` (media-manual-author): topic | type | chapters used | frames embedded | render format | render exit.
- All structured blocks: technical terms exact; never compressed inside blocks; verbatim command + stdout/stderr where evidence is required.
- Every inline reply ≤200 words after the structured block; WHERE on package root and key artifacts.

### Semantic guidance

- Never claim a stage passed without captured stdout/stderr that proves it. Absence of evidence is UNVERIFIED, not PASS.
- Never cite a timecode or frame_id not confirmed in manifest.json.
- Flag uncertain domain terms — never silently correct a term you are not confident about.
- Coverage verdict (media-indexer) is binary: `full` or `gap:<list>` / `overlap:<list>`. No hedge.
- Identify-info ban applies: no employer, client, product, or domain-specific terms hard-coded in agent or skill files. Domain/glossary data arrives via the brief as runtime context.

### Tool guidance

- **Read** — `index.md` before any other package file; all referenced segments, manifest, frame images bounded to selected frame_ids only; scripts/media/ source files when investigating pipeline behavior.
- **Bash** — bounded to `python3 scripts/media/{doctor,probe,run}.py` (media-transcriber) and `pandoc` + `~/.venvs/docgen` render (media-manual-author). No network, no install, no sudo, no writes to package source artifacts.
- **Write / Edit** — bounded per agent: media-proofreader writes only to `<package>/transcript/{proofed.md,corrections.md}`; media-indexer writes only to `<package>/index.md` (and manifest chapters block if brief-assigned); media-manual-author writes only to `<package>/output/`.
- **Grep / Glob** — bounded to the package tree and scripts/media/.
- **No WebFetch or WebSearch** — pipeline questions route to the scripts/media/ source files or surface as a PAUSE.

## When NOT to use this skill

- Designing a new agent → `agent-creation` via `aidev-agent-creator`.
- Writing or auditing skills → `skill-creation` via `aidev-skill-creator`.
- Debugging or modifying scripts/media/ Python/shell code → general code lane + `systematic-debugging`.
- PDF dimension or vector extraction → `pdf-vector-extraction-discipline`.
- SOP body writing or auditing → `biz-sop-discipline`.
- Any work that does not involve a `~/dev/30-operations/jobs/media/<slug>/` package or the scripts/media/ pipeline.
