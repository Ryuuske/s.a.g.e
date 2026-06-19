<!--
scope-owned: §16 auditor pairings — single source of truth
audience: agents
source: hand
review-trigger: pairing change (ADR-grade)
-->

# Audit Pairing Matrix

> **Single source of truth for auditor pairings.** The orchestrator reads this file (via the `audit-pairing-lookup` skill) when an audit is required. Individual agents do **not** know their pairings — pairings are policy, not agent-level config. This lets you change a pairing without touching agent files.

---

## How the orchestrator uses this matrix

1. A change is committed, a state audit is requested, or a release gate fires.
2. The orchestrator classifies the change into one of the `change_type` rows below.
3. The orchestrator invokes the `audit-pairing-lookup` skill with the change_type.
4. The skill returns the pairing: `auditor_primary`, `auditor_secondary`, optional `auditor_tertiary`, and the pairing protocol.
5. The orchestrator dispatches the named auditors per the protocol (parallel where listed; sequential where specified).
6. Verdicts return to the orchestrator, which applies the resolution protocol below.

Agents themselves return verdicts in the `@@VERDICT BEGIN…END` format without knowing who their peer is. This is intentional: the matrix is the only place pairings live.

---

## Pairing rows

| change_type | Trigger (what fires this row) | auditor_primary | auditor_secondary | auditor_tertiary | Protocol |
|---|---|---|---|---|---|
| `aidev-diff` | Diff under `agents/`, `skills/`, or framework files | `aidev-code-reviewer` | `/codex:adversarial-review` *(cross-model to the implementer per ADR-0125: Codex /codex:adversarial-review when Claude implemented; the Claude aidev-adversarial-auditor when Codex implemented, unknown/mixed, or Codex unavailable/budget-refused — ADR-0123/0125)* | — | parallel |
| `aidev-state` | Live roster audit; no diff in scope | `aidev-state-reviewer` | `/codex:adversarial-review` *(cross-model to the implementer per ADR-0125: Codex /codex:adversarial-review when Claude implemented; the Claude aidev-state-adversarial-auditor when Codex implemented, unknown/mixed, or Codex unavailable/budget-refused — ADR-0123/0125)* | — | parallel |
| `dev-code-diff` | Diff under `src/`, `lib/`, or equivalent non-AI-dev source | `dev-code-reviewer` | `dev-test-engineer` | — | parallel |
| `dev-security-diff` | Code-diff change touching auth, secrets, file I/O, network, subprocess, deserialization, crypto, or dependency manifests | `dev-code-reviewer` | `sec-auditor` | — | parallel |
| `dev-ui-diff` | Code-diff change touching `src/components/`, `*.qml`, `*.tsx`, or other UI surface | `dev-code-reviewer` | `dev-ux-designer` | — | parallel |
| `dev-test-only-diff` | Diff confined to `tests/` directory only | `dev-test-engineer` | — | — | solo |
| `gh-pr-review` | External PR on a tracked GitHub project | `gh-pr-reviewer` | `dev-code-reviewer` | `sec-auditor` (if security-touching) | sequential: gh-pr-reviewer first, then dev-code-reviewer; tertiary parallel with secondary |
| `gh-workflow-diff` | Change to `.github/workflows/*.yml` | `gh-workflow-author` | `sec-auditor` | `dev-code-reviewer` | parallel |
| `gh-scaffold` | New repo init via `gh-repo-scaffolder` | `gh-repo-scaffolder` *(self-audit, then →)* | `doc-keeper` | — | sequential |
| `data-pq-diff` | Diff in Power Query M (`*.pq` files, M code embedded in workbooks) | `data-power-query-developer` | `dev-test-engineer` | — | parallel |
| `data-vba-diff` | Diff in VBA modules (`*.bas`, `*.cls`, `*.frm`) | `dev-vba-reviewer` | `dev-test-engineer` | — | parallel |
| `data-excel-diff` | Workbook structure / formatting change without VBA or M | `data-excel-architect` | — | — | solo |
| `fin-categorization-diff` | Change to transaction categorization rules or category schema | `fin-transaction-categorizer` | `dev-code-reviewer` | — | parallel |
| `fin-reconciliation-output` | Reconciliation report produced; before user-facing delivery | `fin-reconciler` *(self-pass)* | `doc-keeper` *(format/citations)* | — | sequential |
| `fin-statement-output` | Financial statement produced; before user-facing delivery | `fin-statement-builder` *(self-pass)* | `doc-keeper` | — | sequential |
| `biz-sop-output` | SOP / runbook / process document produced by `biz-process-builder`; before user-facing publication | `biz-process-reviewer` | `doc-keeper` | — | parallel |
| `docs-diff` | Pure documentation change (`*.md` outside `agents/`, `skills/`) | `doc-keeper` | — | — | solo |
| `release-gate` | Pre-merge to protected branch or pre-tag | `ops-release-readiness` | *(consults per-commit auditors via re-run)* | — | solo |
| `ai-dev-infra-diff` | Change to the catalog, the audit matrix itself, or registry protocol | `aidev-code-reviewer` | `/codex:adversarial-review` *(cross-model to the implementer per ADR-0125: Codex /codex:adversarial-review when Claude implemented; the Claude aidev-adversarial-auditor when Codex implemented, unknown/mixed, or Codex unavailable/budget-refused — ADR-0123/0125)* | `aidev-state-reviewer` | parallel |
| `propagation-batch` | `aidev-agent-creator` returns an `@@AGENT-PROPAGATE-BATCH` containing multiple embedded `@@AGENT-MODIFY` specs in response to an anti-patterns checklist update | `aidev-state-reviewer` | `/codex:adversarial-review` *(cross-model to the implementer per ADR-0125: Codex /codex:adversarial-review when Claude implemented; the Claude aidev-state-adversarial-auditor when Codex implemented, unknown/mixed, or Codex unavailable/budget-refused — ADR-0123/0125)* | `aidev-code-reviewer` | sequential: state-reviewer + the Codex adversarial pass audit the batch as a whole (parallel between them), then aidev-code-reviewer audits each embedded modify spec one-at-a-time as the orchestrator dispatches them through the normal modify-agent + audit chain |
| `freecad-bim-diff` | Diff to a parametric-IFC BIM model or its build/verify/render script (landed change) | `freecad-model-auditor` | `dev-test-engineer` | — | parallel |
| `arch-dim-extract-output` | Dimension table extracted from an architectural PDF (read-only extraction output) | `arch-pdf-extractor` | — | — | solo |
| `arch-structural-spec-output` | Structural spec / change-order from arch-structural-engineer (read-only, consumed by freecad-architect) | `arch-structural-engineer` | — | — | solo |
| `mep-spec-output` | MEP spec / change-order from arch-mep-engineer (read-only, consumed by freecad-architect) | `arch-mep-engineer` | — | — | solo |
| `arch-spec-output` | Material/finish change-order + materials/BOM schedule deliverable from arch-spec-writer | `arch-spec-writer` | `doc-keeper` | — | parallel |
| `arch-sheet-set-output` | Issued sheet-set PDF deliverable from arch-documenter | `arch-documenter` | `doc-keeper` | — | parallel |
| `arch-concept-options-output` | Concept/schematic massing-and-layout options document from arch-concept-designer (read-only, drives a client choice; chosen concept developed downstream by freecad-architect) | `arch-concept-designer` | — | — | solo (note: downstream freecad-bim-diff is the build gate for the chosen concept) |
| `arch-render-output` | 3D/photoreal render set + manifest deliverable from arch-visualizer | `arch-visualizer` | `doc-keeper` | — | parallel (note: doc-keeper scoped to the render-manifest completeness/format only; render image quality is self-passed by arch-visualizer's empty/black + completeness discipline) |
| `media-job-output` | Internal job-package artifacts produced and self-passed by the producing media agent (transcriber → job package; proofreader → proofed.md/corrections.md; indexer → index.md + manifest.chapters[]) | *(producing agent self-pass)* | — | — | solo |
| `media-manual-output` | Client-facing rendered quick-reference guide or manual from media-manual-author | `media-manual-author` *(self-pass)* | `doc-keeper` *(scoped to format/timecode-citation coverage/frame-embedding completeness only — not content judgment)* | — | parallel |

#### `freecad-bim-diff`

Primary `freecad-model-auditor` audits model-vs-drawing fidelity (round-trip losslessness, genuine-defect vs platform-limitation classification, overengineering check on the build script). Secondary `dev-test-engineer` covers gate/script test adequacy — a lane `freecad-model-auditor`'s Step 9 (REVIEWER_DISCIPLINE overengineering check) does not occupy. Mirrors the `data-pq-diff` and `data-vba-diff` rows. ADR-0098.

As of ADR-0114, the primary `freecad-model-auditor`'s expected output now INCLUDES a grounded `@@FREECAD-VISUAL-REVIEW` block (multi-angle form-correctness gate, fail-closed). APPROVE is not available on a `freecad-bim-diff` audit without it: a missing block, any unread panel, any panel that is blank/zero-dimension/wrong-count/unreadable/occluded/ambiguous, or the absence of a render path in the brief all cap the verdict at REQUEST_CHANGES regardless of round-trip and element-count results. This annotation is descriptive; enforcement lives in the agent's gating language and orchestrator verification (ADR-0011 toolkit-not-enforcer), not in a parser.

#### `arch-dim-extract-output`

Solo `arch-pdf-extractor` audits the extraction output. The mandatory independent-verification requirement (§16) is met internally: `arch-pdf-extractor`'s Tree-4 crosscheck re-derives rotation, calibration, and origin from a different anchor in a fully independent second pass, satisfying the re-derivation criterion without a separate code-reviewer pairing. ADR-0099.

#### `arch-structural-spec-output`

Solo `arch-structural-engineer` self-pass. The structural spec / change-order is a read-only artefact consumed by `freecad-architect`; the downstream `freecad-bim-diff` build audit (ADR-0098) is the independent §16 gate for the built result. Adding a second auditor at the spec stage would double-audit content that `freecad-model-auditor` will independently verify once built. ADR-0110 (Option C split).

#### `mep-spec-output`

Solo `arch-mep-engineer` self-pass. Same rationale as `arch-structural-spec-output`: the MEP spec / change-order is consumed by `freecad-architect` and independently verified by the `freecad-bim-diff` build gate (ADR-0098). ADR-0110.

#### `arch-spec-output`

`arch-spec-writer` self-pass for the spec-handoff facet (material/finish change-order routed to `freecad-architect`; covered by the `freecad-bim-diff` build gate per ADR-0098). `doc-keeper` secondary scoped **to the schedule/BOM deliverable artifact only** — format, citation completeness, and BOM coverage — not the spec-handoff facet. Shape mirrors `fin-statement-output` (producer self-pass + `doc-keeper` on a client deliverable), but the protocol is `parallel` per ADR-0110 Option C — not `sequential` like the fin rows. ADR-0110.

#### `arch-sheet-set-output`

`arch-documenter` self-pass + `doc-keeper` secondary. The issued sheet-set PDF is a client-facing deliverable that never feeds back into the model; `doc-keeper` covers deliverable format, completeness, and citation. Shape mirrors `fin-reconciliation-output` (producer self-pass + `doc-keeper` on a client deliverable), but the protocol is `parallel` per ADR-0110 Option C — not `sequential` like the fin rows. ADR-0110.

#### `arch-concept-options-output`

Solo `arch-concept-designer` self-pass. The concept-options document drives a client choice; the chosen concept is independently re-derived and developed downstream by `freecad-architect` and audited by the `freecad-bim-diff` build gate (ADR-0098), satisfying §16 via the downstream gate without a redundant second dispatch at the concept stage. Mirrors the precedent of `arch-dim-extract-output` (ADR-0099) and the P2 spec-producer rows (ADR-0110 Option C). ADR-0113.

#### `arch-render-output`

`arch-visualizer` self-pass + `doc-keeper` secondary (parallel). The render deliverable is client-facing with no downstream build gate. Render image quality (empty/black-render check, completeness discipline) is self-passed by `arch-visualizer` before delivery; `doc-keeper` is scoped strictly to the render-manifest completeness and format — NOT to image aesthetics or render parameters. Extends ADR-0110 Option C's client-deliverable pattern (established for `arch-sheet-set-output`) to the render deliverable. ADR-0113.

#### `media-job-output`

Solo producing-agent self-pass. Each producing media agent self-passes its own output artifacts: `media-transcriber` self-passes the job package (manifest.json, segments.jsonl, index.md, frames/); `media-proofreader` self-passes proofed.md/corrections.md; `media-indexer` self-passes the refined index.md and manifest.chapters[]. The `auditor_primary` column names the producing agent generically — the orchestrator substitutes the actual producing agent for the job in question. The downstream `media-manual-author` composition step is the functional gate for the indexed and proofed content — adding a second auditor at the job-artifact stage would double-audit content the composition step already validates. Follows ADR-0110 Option C's solo-self-pass pattern for read-only / internally-consumed artifacts. ADR-0117.

#### `media-manual-output`

`media-manual-author` self-pass + `doc-keeper` secondary (parallel). The rendered quick-reference guide or manual is a client-facing deliverable with no downstream build gate. `doc-keeper` is scoped strictly to deliverable format, timecode-citation presence on every step, and frame-embedding completeness — NOT to narrative accuracy, transcript correctness, or content judgment. Follows ADR-0110 Option C's client-deliverable pattern (established for `arch-sheet-set-output`) and the `arch-render-output` shape (ADR-0113). ADR-0117.

### Adding a new row

Adding a new change_type row is an ADR-grade decision. Process:

1. Write an ADR proposing the row, justifying why existing rows do not cover the change_type, and naming the auditor agents.
2. Verify each named auditor has a corresponding `agents/<name>.md` file in this repo AND exists in `~/.claude/agent-catalog.json`. Note: the catalog is generated from `agents/*.md` by `installer-assets/gen-agent-catalog.py` on every install (see `docs/specs/agent-registry-protocol.md`); an empty catalog indicates a failed or absent install, and the repo-file check remains the fallback guard.
3. Append the row to the table above; bump the `last_audited` field below.
4. The `audit-pairing-lookup` skill picks up the new row on next invocation (no code change).

---

## Protocols

### `parallel`
Both (or all three) auditors dispatched simultaneously. Each returns a verdict independently. The orchestrator does not let one auditor see the other's output before verdict. Split-verdict handling below.

### `sequential`
Auditors dispatched in order; each sees the prior's output. Used when the second auditor's lane depends on the first having confirmed something (e.g., `gh-pr-reviewer` confirms the PR is well-formed before `dev-code-reviewer` reviews the code substance).

### `solo`
Single auditor. No peer. Used for narrow change types where multi-auditor friction outweighs the catch-rate gain.

### `self-pass`
The agent that produced the output runs a structured self-check before handoff to a different auditor. Used for output-generating agents (e.g., `fin-statement-builder` checks its own output for missing cover sheet / timestamp before `doc-keeper` reviews format).

### Two-phase audit (`propagation-batch` only)

The `propagation-batch` row uses a two-phase protocol distinct from the others. This is intentional because the propagate operation produces both a roster-wide governance change (the batch as a whole, which needs state-level review) and a sequence of individual agent modifications (each of which needs per-agent diff review).

**Phase 1 (batch-level)**: state-reviewer and the §16 adversarial pass (model chosen cross-model to the batch implementer per ADR-0125 — Codex `/codex:adversarial-review`, or the Claude `aidev-state-adversarial-auditor` when Codex implemented, unknown/mixed, or unavailable) audit the `@@AGENT-PROPAGATE-BATCH` block as a unit, in parallel. They check:
- Does the shape classification chain run for every agent? (CoT injection compliance)
- Are the applicable anti-patterns correctly selected per shape?
- Are any agents misclassified (e.g., a reviewer-shaped agent treated as implementer-shaped)?
- Does the recommended dispatch order minimize lane-bleed risk?
- Is the batch size reasonable (>50 modifications in one batch is a structural finding — propose splitting)?

Phase 1 must complete with both verdicts before Phase 2 begins. If either auditor returns `REJECT`, the entire batch is held; the orchestrator returns to the user for direction (do not partial-process a failed batch).

**Phase 2 (per-modification)**: for each embedded `@@AGENT-MODIFY` spec, `aidev-code-reviewer` runs a standard diff audit as the orchestrator dispatches that modification. This follows the normal modify-agent chain — there is no special-casing at the per-modification level. Phase 2 dispatches happen one at a time per the batch's `recommended_dispatch_order`.

The two-phase structure prevents two failure modes: (a) state-level issues (e.g., wrong shape classification across the batch) being missed because each modification looks fine in isolation; (b) per-modification regressions being missed because the state auditors only saw the batch summary.

---

## Resolution protocol (split verdicts)

When auditors return different verdicts:

1. **Lane-confined disagreement** — one auditor flagged a finding in their lane that the other didn't (because it was outside the other's lane). Both findings stand; no conflict.
2. **Actually contradictory** — one says `APPROVE`, the other says `REJECT`, on the same concern. The orchestrator:
   - First pass: applies the conservative-wins rule (`REJECT` beats `APPROVE`; `REQUEST_CHANGES` beats `APPROVE`).
   - If both positions are defensible (orchestrator's judgment), dispatches a third agent — typically `aidev-state-reviewer` for AI-dev work or `dev-architect` for tech-design conflicts.
   - Every actually-contradictory disagreement produces an ADR, even a one-liner, so future audits don't re-fight the same battle.
3. **Round cap** — max 3 review rounds per change before escalation to the user. After round 3, the orchestrator surfaces the disagreement to the user with both verdicts and asks for direction.

---

## Severity thresholds (uniform across all auditors)

- **Score 0–79**: informational. The orchestrator may include in the synthesis but does not block.
- **Score 80–94**: blocking. The orchestrator returns `REQUEST_CHANGES` to the implementer (or to the user if no implementer is in flight).
- **Score 95–100**: critical. Escalates to the user immediately even mid-flow (per `~/.claude/CLAUDE.md` §7).

These thresholds are uniform so that the matrix can route purely on change_type without per-auditor calibration.

---

## What does NOT belong in this matrix

- **Project-type-specific routing** — that's the agent-manager's job (catalog activation).
- **Plan approval gates** — that's the User's job (plan-first contract).
- **Tool-grant decisions** — that's the agent file's `tools:` frontmatter.
- **Severity scoring for individual findings** — that's the auditor's own methodology.
- **Auditor descriptions or lane statements** — those live in each agent's file.

This matrix is **pairing policy only**. Everything else stays in its proper place.

---

## Maintenance

`last_audited`: 2026-06-15

When you change a row, update the date. The `audit-pairing-lookup` skill flags stale matrices (>90 days) so you know to re-verify it matches current agent capabilities.

Drift checks the skill performs on every invocation:

- Every `auditor_primary`, `auditor_secondary`, `auditor_tertiary` named is present in `~/.claude/agent-catalog.json`.
- No catalog auditor agent is unreferenced by any row (informational only — some auditors might be invoked outside the matrix).
- Every `change_type` slug is unique.

If the skill detects drift, it returns the pairing for the requested row AND a drift warning. The orchestrator surfaces the warning to the user.
