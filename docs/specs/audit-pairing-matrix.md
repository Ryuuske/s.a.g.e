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
| `aidev-diff` | Diff under `agents/`, `skills/`, or framework files | `aidev-code-reviewer` | `aidev-adversarial-auditor` | — | parallel |
| `aidev-state` | Live roster audit; no diff in scope | `aidev-state-reviewer` | `aidev-state-adversarial-auditor` | — | parallel |
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
| `ai-dev-infra-diff` | Change to the catalog, the audit matrix itself, or registry protocol | `aidev-code-reviewer` | `aidev-adversarial-auditor` | `aidev-state-reviewer` | parallel |
| `propagation-batch` | `aidev-agent-creator` returns an `@@AGENT-PROPAGATE-BATCH` containing multiple embedded `@@AGENT-MODIFY` specs in response to an anti-patterns checklist update | `aidev-state-reviewer` | `aidev-state-adversarial-auditor` | `aidev-code-reviewer` | sequential: state-reviewer + state-adversarial-auditor audit the batch as a whole (parallel between them), then aidev-code-reviewer audits each embedded modify spec one-at-a-time as the orchestrator dispatches them through the normal modify-agent + audit chain |

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

**Phase 1 (batch-level)**: state-reviewer and state-adversarial-auditor audit the `@@AGENT-PROPAGATE-BATCH` block as a unit, in parallel. They check:
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

`last_audited`: 2026-06-07

When you change a row, update the date. The `audit-pairing-lookup` skill flags stale matrices (>90 days) so you know to re-verify it matches current agent capabilities.

Drift checks the skill performs on every invocation:

- Every `auditor_primary`, `auditor_secondary`, `auditor_tertiary` named is present in `~/.claude/agent-catalog.json`.
- No catalog auditor agent is unreferenced by any row (informational only — some auditors might be invoked outside the matrix).
- Every `change_type` slug is unique.

If the skill detects drift, it returns the pairing for the requested row AND a drift warning. The orchestrator surfaces the warning to the user.
