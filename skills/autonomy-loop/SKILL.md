---
name: autonomy-loop
description: "Use to drive a User-approved multi-phase autonomous build with no per-phase pauses — the branch→audit-pair→fold→codex→arbiter→self-merge→private-tag loop, its self-halts (unplanned-destructive-op, archive-before-squash, retain-private, never-flip-visibility), the REQUIRED reporting card, and cross-context self-continuation. Triggers on User-approved 'run to the terminal'. Not for a single approved change or the wake-up picker (session-lifecycle), nor to make anything public."
---

# Autonomy Loop

The procedure the orchestrator runs when the User has approved a **multi-phase autonomous build** to execute without per-phase hand-backs — the engine the S.A.G.E./S.A.G.E. master-plan run drove on (a 2026-05/06 multi-phase private build). It formalizes the loop the orchestrator already runs per phase under CLAUDE.md §2/§5/§6/§7/§16, makes the machine-floor self-halts explicit, and binds the reporting-contract card format as a required output property.

This skill is a **toolkit, not an enforcer** (ADR-0011): it describes the loop the orchestrator follows; it does not ship a hard hook that blocks on deviation. The orchestrator self-checks against it.

## When this skill binds

Bind this skill when ALL of:

1. The User has approved a plan that spans multiple phases (`.development/plans/*.md`), AND
2. The User has explicitly said to run autonomously without pausing at phase boundaries (e.g. "no per-phase pause", "run to the terminal", "self-continue across context boundaries"), AND
3. A durable run-log exists or is created at `.development/handoff/<run>-run-log.md` (the re-anchor spine + the User's end-of-run audit trail).

Do NOT bind for: a single approved change (use the normal session-lifecycle + the `docs/specs/audit-pairing-matrix.md` pairing); the session-start destination picker (that is `session-lifecycle`); any operation that would make a private repo public (out of scope — see Floor 3).

## The loop (per phase)

For each phase of the approved plan, run this loop. Never two filesystem writers concurrently (CLAUDE.md §5); audits and arbiter are read-only/advisory and may run in parallel.

1. **Branch.** `git checkout -b <phase-slug>` off the current private `main`. Never work on `main` directly.
2. **Open the phase.** Emit the START card (see Reporting contract). Recon the surface; record it in the run-log.
3. **Implement.** Produce the phase's work-items as atomic commits (CLAUDE.md §5: one logical change per commit). The orchestrator may implement directly or dispatch the per-phase executor; persisted upstream artifacts (visionary framing, planner decomposition) are not re-derived when already on disk (§13 efficiency) — but every verification gate below is preserved.
4. **Audit (parallel).** **Select the adversarial model cross-model to this phase's implementer (ADR-0125):** Codex `/codex:adversarial-review` when Claude implemented this phase; the matrix row's Claude fallback auditor (`aidev-adversarial-auditor` for diff rows, `aidev-state-adversarial-auditor` for state/propagation-batch rows) when Codex implemented it (or the implementer is unknown/mixed — fail-safe to Claude). **If the selected model is Codex, budget-gate it first:** run `codex-budget` before dispatching; on refusal, route the adversarial slot to the matrix row's Claude fallback auditor (`aidev-adversarial-auditor` for diff rows; `aidev-state-adversarial-auditor` for `state` / `propagation-batch` rows) (NEVER skip the lane). Dispatch the auditor pair the `docs/specs/audit-pairing-matrix.md` row selects for the diff (aidev-diff → aidev-code-reviewer + the §16 adversarial pass — cross-model to the implementer per ADR-0125 (Codex `/codex:adversarial-review` when Claude implemented this phase; the Claude `aidev-adversarial-auditor` when Codex implemented it, or when Codex is unavailable/budget-refused — ADR-0123/0125); state → the state pair; propagation-batch → state pair + code-reviewer two-phase; docs → doc-keeper; etc.). Auditors run in parallel; each emits a `@@VERDICT` block. Auditors with Bash inspect committed state with `git show <sha>:file` — NEVER `git checkout`/`switch`/`reset` on the shared tree (the 0b detached-HEAD lesson). **Capture governance telemetry (§16):** pipe each auditor's `@@VERDICT` reply into `sage verdict log --phase audit --mode <aidev|normal> --wing <wing>` (pin `--turn-id <hex>` to bind paired-auditor rows). The CLI parses the block and exits nonzero on a parse error or a HOLD/ABORT verdict. A VALID verdict (including HOLD/ABORT) appends one row to `~/.sage/telemetry/turns.jsonl` — for HOLD/ABORT the row is logged AND the nonzero exit is a signal to surface, not a logging failure. A PARSE failure (no `@@VERDICT` block, unknown fields) logs NOTHING (no governance signal to record) and the gap must be surfaced — re-dispatch the auditor for a clean block. Without this pipe the loop still runs, but governance stays unmeasured; with it, disagreement rate, recurring blockers, and per-lane verdict quality become queryable (`docs/specs/telemetry.md`).
5. **Fold or escalate (≤6 rounds).** Findings ≥80 are blocking — fold them and re-audit. Non-blocking findings: fold the actionable ones inline (the User's standing fix-low-severity instruction), accept the rest with a logged reason. Cap at 6 fold-rounds.
6. **§16 adversarial secondary.** The per-phase Codex `/codex:adversarial-review` dispatched in step 4 (budget-gated there) is the §16 adversarial-auditor secondary, not a second distinct pass. Fold any needs-attention findings; re-run the selected adversarial model only to confirm folded findings (the same model the §16 lane selected in step 4 — the Claude `aidev-adversarial-auditor` when Codex implemented this phase, Codex `/codex:adversarial-review` when Claude implemented, per ADR-0125) (pass-N until APPROVE or the arbiter resolves a genuine split).
7. **Arbiter.** Every decision/fork/ambiguity that survives the audit pair + codex → `aidev-arbiter` (framework-internal, §7 step-2), logged as a DECISION card + an ADR. Never to the User except a Floor breach (below).
8. **Self-merge.** Two gates BEFORE `gh pr merge`, both mandatory: (a) CI green (`gh pr checks <N>` shows no `pending`/`failure`); (b) **review threads addressed** — fetch ALL inline review comments (`gh api --paginate repos/<owner>/<repo>/pulls/<N>/comments` — without `--paginate` only the first page returns and later-page findings are silently missed) and reviews (`gh pr view <N> --json reviews`); external reviewers (e.g. the Codex GitHub app) post asynchronously, so re-check after CI completes, not only at PR-open. Every finding is fixed or accepted-with-logged-reason and the thread replied to before merge. Then push the branch, open a PR (use the PR template), wait for both gates, self-merge `--merge --delete-branch`, fast-forward local `main`. (Gate (b) added after 2026-06-10: PRs #42/#43/#45 merged green with seven unread Codex findings, one P1.)
9. **Close the phase.** Emit the PHASE COMPLETE card. Update the run-log with the COMPLETE card + any backlog. Proceed to the next phase WITHOUT pausing (the autonomy contract).

### Terminal (last phase only)

After the last phase's step-9 close, run T1–T3 before the private-tag action:

**T1 — Whole-branch review.** Dispatch the aidev auditor pair (`aidev-code-reviewer` + the §16 adversarial pass — cross-model to the implementer per ADR-0125 (Codex `/codex:adversarial-review` when Claude implemented the reviewed changes; the Claude `aidev-adversarial-auditor` when Codex implemented them, or unknown/mixed/unavailable — ADR-0123/0125)) on the full `git diff main...HEAD` output — a cross-phase integration check at the contract level, not a per-phase re-audit. Apply contract-tracing and mirror/symmetry check angles (see `~/.claude/docs/specs/universal-agent-constraints.md` Universal Agent Constraints REVIEWER_DISCIPLINE — Contract-tracing across paths and Mirror/symmetry check). Findings ≥80 are blocking; fold and re-audit (≤6 fold-rounds cap applies). Emit a T1 STATUS card when T1 clears.

**T2 — Scoped terminal Codex pass.** When the build touches high-risk surfaces (install scripts — `install.sh`, `install.ps1`, `installer-assets/`; hooks — `hooks/scripts/`; security-sensitive framework code), run `/codex:adversarial-review` on `main...HEAD` as a §6-style third-opinion lane, not a blocking auditor. Subject to `codex-budget` gating (budget refusal supersedes; if budget refuses, skip T2 and log the skip). Pure agent/skill-prose builds with no high-risk surface contact skip T2 entirely. Fold any T2 findings scored ≥80; re-run Codex to confirm (one additional touch permitted to confirm a fold; the combined terminal pass counts as the one-touch for this build). Emit a T2 STATUS card when T2 clears or is skipped.

**T3 — Watch-CI before handoff.** After the PR is open, run `gh pr checks <N>` in a loop until every check resolves to passing — no `pending` or `failure` states, including any jobs that were added mid-build. A PR with pending CI is not done. Do not hand back to the User until CI is fully green. Emit a T3 STATUS card when CI is green.

**Private-tag action (after T1–T3 clear):** `release gate → archive tag → tag <version> (PRIVATE, via gh-release-manager EXECUTE with required_visibility: private) → build + run the export → VERIFY the clean export artifact`. The run makes NOTHING public. The public publish is the User's manual hand, outside the loop (Floor 3).

## Machine floors (self-halt conditions)

These are absolute. The loop runs autonomously EXCEPT when a floor trips — then it self-halts and surfaces to the User.

- **Floor 1 — Unplanned destructive/irreversible op.** If a step would require a genuinely-UNPLANNED destructive or irreversible operation (one not sanctioned by the approved plan and its ADRs) — STOP, do not perform it, surface to the User with the exact op and why it is unplanned. Planned irreversible ops (an approved rename, a tagged release, an archive-then-rebalance) are NOT floor breaches; they proceed behind their own checkpoints.
- **Floor 2 — Archive-tag-before-squash / before any hardest-to-reverse step.** Cut and push an `archive/<marker>` tag BEFORE any squash, history rewrite, or runtime data migration. The tag + any pre-op backup are the recovery floor. A count/fingerprint mismatch on a data migration self-halts.
- **Floor 3 — NEVER flip repo visibility.** The loop never runs `gh repo edit --visibility`, never publishes a private repo, never makes anything public. This is asserted, not assumed: grep the loop's own actions for any visibility-flip path → must be zero. The single highest-irreversibility action (going public) is explicitly OUTSIDE the loop's reach — the User's manual hand. When the loop drives `gh-release-manager` EXECUTE, it pins `required_visibility: private` (ADR-0064) on every dispatch, so an accidentally-public repo ABORTs the release rather than publishing.
- **Floor 4 — Retain the private working repo + its full trail.** Never delete the private repo, its `.development/decisions/`/`.development/audits/`/`.development/plans/` trail, or the archive tags. The clean export is produced by an allowlist (public-by-construction), not by destroying the private trail.
- **Floor 5 — `~/.claude` self-mods are arbiter-gated.** Any change to the active `~/.claude/` spine/skills/agents goes through the audit pair → `aidev-arbiter` ruling → then `/reload-plugins` (or a deferred end-of-run install-sync if the plan defers it). The loop never grants itself a `~/.claude` write the §12 classifier blocks.

A floor breach is the ONLY thing that stops an approved autonomous run to reach the User. Everything else — every decision, fork, split, codex edge — routes to `aidev-arbiter` and is logged.

## Reporting contract (REQUIRED output property)

The orchestrator's User-facing output for an autonomous run uses the card format as the permanent default (the User's standing instruction). This is a REQUIRED property of the loop — the orchestrator self-checks every phase's output against it:

- **START card** opening each phase: phase id + one-line goal, the full phase-order line with the current phase marked, a `NOW:` line.
- **Progress lines** between milestones: short present-tense, ~1 line each, no long summaries.
- **STATUS card** at each milestone: the completed item with ✓ + key facts (commit hash, ADR #, counts, PR #); an optional `note:`; a `NOW:`/`NEXT:` line.
- **DECISION card** for every arbiter ruling: `<step> DECISION ✓ ADR-NNNN (<choice>) — arbiter conf NN`.
- **PHASE COMPLETE card** closing each phase: what landed (bulleted), the PR/merge, "Decisions logged → arbiter (N): nothing surfaced to you," and any backlog logged.

This is a User OUTPUT PREFERENCE, adopted directly — not a framework fork, and (per ADR-0011) not enforced by a hard blocking hook. Mechanical support is a non-blocking evaluator, never a gate (see Hooks below).

## Self-continuation across context boundaries

An autonomous run outlives a single context window. When the context auto-compacts or a fresh session starts mid-run, the orchestrator RE-ANCHORS and CONTINUES without waiting for the User:

1. Read `.development/handoff/<run>-run-log.md` (the durable spine) + `git log`/`git status`. Git history is truth on any conflict.
2. Identify the current phase from the run-log's last COMPLETE card / in-progress section and the branch state.
3. Resume the loop at the exact step the run-log records. Keep the run-log current at every milestone — it is both the re-anchor spine and the User's end-of-run audit trail.

The run-log is the continuation contract. If the installed plugin/skills are stale (e.g. mid-run, before an end-of-run reinstall), IGNORE the installed wake-up — re-anchor from the run-log + git directly, not from a stale Nook wake-up.

**Marker contract (orchestrator-owned).** So the SessionStart continuation injector and the reporting evaluator (below) have an input, the orchestrator writes one marker when an autonomous run is in-flight and clears it at the terminal:

```
~/.sage/autonomy-run.json
  { "run_log": "<abs path to .development/handoff/<run>-run-log.md>",
    "phase": "<current phase id>",
    "status": "in-flight" | "terminal",
    "skills_changed": true|false }   # set true after a run edits skills, until /reload-plugins
```

Absent / malformed / `status != "in-flight"` ⇒ the hooks no-op (silent). The hooks NEVER infer a run from cwd — same discipline as `~/.sage/current_wing` + `last_keeper_dispatch`. Delete the marker (or set `status: "terminal"`) at the run's terminal.

## GitHub Issues integration

Track defects and decisions as GitHub Issues for the run (the Issue/PR templates from the GitHub-workflow phase). A decision that produces an ADR may also open a `decision`-labelled Issue; a defect found mid-loop opens a `bug` Issue. Issues are the durable cross-phase tracker that complements the run-log.

## Hooks (continuation + reporting support — context-injectors / evaluators, never enforcers)

Per ADR-0011 the framework ships NO enforcement hooks — only fail-open context-injectors and non-blocking evaluators. The autonomy loop's hook support follows that rule (the exact shapes are ruled by `aidev-arbiter` and recorded in the Phase-5 ADR):

- **SessionStart continuation injector** — a fail-open SessionStart hook that, when a run is in-flight (a run-log with an unfinished terminal), injects a one-line re-anchor pointer ("autonomous run in-flight → read `.development/handoff/<run>-run-log.md` and continue") so a fresh session self-continues. It injects context; it does not block. If skills changed mid-run, it surfaces the `/reload-plugins` reminder rather than forcing a reload (no native hook-level skill-reload exists; the injector informs).
- **Reporting-contract evaluator** — a non-blocking evaluator (NOT a per-message gate; no `MessageDisplay` hook event exists in Claude Code, and ADR-0011 forbids a blocking hook) that records whether the run's output followed the card format, for post-hoc review. It never alters or blocks output.
- **Stop / PreCompact** — the existing keeper emergency-drawer fallback (`hooks/scripts/stop.py` / `precompact.py`) already captures session state; the loop relies on the orchestrator-owned run-log as the primary continuation spine and those hooks as the fallback.

## Anti-patterns

- **Pausing at a phase boundary** during an approved no-pause autonomous run (other than a Floor breach). The contract is to continue.
- **Flipping repo visibility** anywhere in the loop (Floor 3). The public publish is the User's manual hand.
- **Skipping the archive tag** before a hardest-to-reverse step (Floor 2).
- **Merging before CI is green** (step 8). Always `gh pr checks` first.
- **Merging with unread review threads** (step 8 gate b). Green CI is not the only merge gate: async reviewers (the Codex GitHub app) post inline findings after PR-open; merging on CI alone silently discards that review lane. Fetch `pulls/<N>/comments` between CI-green and merge — a merge-on-green watcher must include the comment check, not bypass it.
- **An auditor running `git checkout`/HEAD-moving cmds** on the shared tree (use `git show <sha>:file`).
- **Escalating a framework-internal decision to the User** instead of `aidev-arbiter` (only a Floor breach reaches the User).
- **Building a hard enforcement hook** to mechanically gate output/visibility (ADR-0011: toolkit-not-enforcer; the floors are behavior constraints, the hooks are injectors/evaluators).
- **Relying on a stale installed wake-up** for state mid-run instead of the run-log + git.
- **Re-deriving persisted upstream artifacts** (visionary/planner outputs already on disk) — that is §13 waste; but never skip a verification gate to save tokens.
- **Opening the PR without T1 (whole-branch review).** Per-phase audits review phases in isolation; cross-phase contract and symmetry failures are invisible to them. T1 on `main...HEAD` is the only pass that can catch them. Opening a PR before T1 clears skips the one check that closes the cross-phase integration gap.
- **Declaring done while CI is pending (T3).** A build that opens the PR and immediately hands back to the User without waiting for `gh pr checks` to resolve leaves the User holding a potentially broken merge. CI jobs added mid-build (e.g., a new platform job) may not appear until after the PR opens — watch-CI must run to completion, not just until the jobs the orchestrator expects are green.
- **Running the terminal Codex pass on a prose-only build (T2).** The terminal Codex pass is scoped to high-risk surfaces (install scripts, `hooks/scripts/`, security-sensitive framework code). Running it on a build that touches only agent/skill-prose files wastes Codex budget and violates the §13 cost discipline that the scoped carve-out exists to honor.
