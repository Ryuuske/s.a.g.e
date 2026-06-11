---
name: patch-tournament
description: "Use when a task is high-value or high-risk AND the acceptance criteria are machine-testable — spawn N independent candidates in isolated git worktrees, validate each through the spine, eliminate failures, hand survivors to dev-selector. Default OFF — both conditions must hold or routes to single-implementer. Do not use for the Codex-spend budget verdict (codex-budget), winner selection (dev-selector), or debugging (systematic-debugging)."
---

# Patch Tournament

You run competitive multi-candidate implementation for one approved, high-value, machine-testable task. Candidates compete in isolation; only spine-passing patches advance. You hand survivors to `dev-selector` and guarantee every worktree is torn down on every exit path. You never ship a loser.

This skill is the operational arm of ADR-0091, which establishes the patch-tournament capability, the opt-in posture, the two-condition gate, the worktree-teardown contract, and the "never ship a loser" invariant. It is a **toolkit, not an enforcer** (ADR-0011): it describes the procedure the orchestrator runs; it ships no hook.

## When this skill binds

Both conditions must hold simultaneously before this skill is invoked:

1. **High value or high risk.** The approved task is high-value (significant feature, security-adjacent, or non-trivial surface area) or high-risk (change touches a load-bearing contract, public API, or migration path). Low-value or purely mechanical tasks stay on the single-implementer path.
2. **Machine-testable acceptance criteria.** Every acceptance criterion in the approved plan is verifiable by an automated command that exits zero on pass and non-zero on fail. If any criterion requires human judgment to evaluate, the weak-tests guard fires instead.

**Default OFF.** Single-implementer is the default per CLAUDE.md §13. Explicitly invoke this skill when both conditions hold, or auto-suggest it. If either condition fails, decline and route to the single-implementer path — say which condition was not met.

### Weak-tests guard

Before spawning any candidate, verify that every acceptance criterion in the plan is machine-testable. The guard is prose guidance per ADR-0011 — it describes the procedure, not a hard enforcement hook. Apply it before creating any worktree.

- **Refuse the tournament entirely if any criterion is non-testable.** A tournament over non-machine-testable criteria amplifies the chance a bad patch passes the spine — that failure mode is worse than single-implementer. State which criterion(ia) failed the testability check and route to single-implementer.
- **Warn (do not refuse) ONLY when criteria are PARTIALLY testable AND the caller explicitly accepts the stated risk.** If the majority of criteria are machine-testable but at least one requires manual validation, warn that the non-testable criterion will not be checked by the spine; explicitly surface the risk and wait for the orchestrator to accept it before proceeding. A warn-and-proceed that was not explicitly caller-accepted is a refuse.

The guard fires before any worktree is created. No Bash, no worktrees, no cost until the guard clears.

## Step 1 — Candidate composition

The tournament skill owns composition policy: it chooses N (the candidate count) and the per-candidate strategy prompts, and applies the Codex-default rule (Codex is the default implementer; Claude is the fallback). Before spawning, the tournament skill CONSULTS the `codex-budget` skill for a budget verdict on the Codex spend for this run. Do not re-implement budget-check logic here — delegate the verdict call to `codex-budget`.

The interaction with `codex-budget` is narrow and one-directional: the skill asks "is there budget to run N Codex candidates?" and receives Proceed, Ask, or Refuse. The `codex-budget` skill decides only the budget verdict — it does not decide N, does not assign models to candidates, and does not set strategy prompts. Those remain the tournament's responsibility.

On a Refuse from `codex-budget` (budget limited), fall back to Claude implementers for all candidates. Do not halt the tournament; adjust the candidate manifest to reflect Claude fallback and continue.

After the composition decision, record the result as the **candidate manifest**: a table mapping each candidate ID to its strategy-prompt label and assigned model.

**Candidate manifest format:**

| Candidate ID | Strategy label | Assigned model |
|---|---|---|
| candidate-A | minimal-change | Codex |
| candidate-B | typed-refactor | Claude (fallback) |
| … | … | … |

## Step 2 — Worktree isolation

Each candidate runs in its own git worktree, branched from the current HEAD of the working tree. All tournament worktrees live under the path prefix `<temp-root>/sage-tournament-<run-id>/` and all tournament branches live under the prefix `tournament/<run-id>/`. Recording these prefixes is the recovery contract: on re-entry after an interrupt or crash, run the prune sweep (described in the Interrupt-orphan recovery section below) before any new worktrees are created.

`<temp-root>` is the host-derived temp directory, resolved to a real path and with path separators normalized to forward-slash so it matches git's porcelain output on all platforms — resolved with a Python interpreter (`python3`, falling back to `python` on Windows/Git-Bash) via the canonical recipe in Step 2 below — which yields the canonical `/tmp`-style form on Unix and a forward-slash form of `%TEMP%` on Windows; the temp root is host-derived for cross-platform support, as S.A.G.E. ships `install.ps1` for Windows. Resolve `<temp-root>` once per run and reuse it consistently.

Worktrees are created before any candidate implementation begins. Teardown is guaranteed on every exit path — see the teardown contract at the end of this section.

**Create worktrees:**

`<run-id>` MUST be a filesystem- and branch-safe slug (alphanumeric + dash only, e.g. `<date>-<slug>`). The destructive branch/worktree sweeps used in teardown and interrupt-orphan recovery anchor on this constraint — an unsafe run-id produces over-matching or non-matching sweeps. Choose the run-id before creating any worktree and validate it satisfies the constraint. (The same requirement is restated in the Interrupt-orphan recovery section for re-entry context.)

Before creating any worktree, record the **tournament BASE commit SHA** — the commit that every candidate worktree will branch from (the approved-task starting point in the working tree). This SHA is used later in winner capture and all-fail capture to produce full-range diffs spanning all commits a candidate may make:

```bash
BASE_SHA=$(git rev-parse HEAD)
```

Record `BASE_SHA` alongside the run-id and candidate manifest. It does not change after this point.

Resolve the temp root once per run into `TROOT` — normalized to a real path and quoted in every subsequent command. `<temp-root>` can contain spaces or be a Windows `%TEMP%` path with backslashes; always use `"$TROOT/..."` with double quotes. Run each recipe block as its own discrete shell invocation — **do not `source` these blocks**: the guard's `exit 1` is meant to terminate a single subprocess, not the caller's shell. (The block is `set -e`-tolerant — a failing command substitution in a plain assignment does not abort the shell, so the guard is always reached — but sourcing it into an interactive session would still kill that session on the guard path.)

```bash
# Pick a Python interpreter — python3 preferred; fall back to python (Windows/Git-Bash, where python3 may be absent).
PYBIN="$(command -v python3 || command -v python)"
TROOT="$("$PYBIN" -c 'import tempfile, os; print(os.path.realpath(tempfile.gettempdir()).replace(os.sep, "/"))')"
# Guard: an empty TROOT would make every later "$TROOT/..." path resolve to "/..." — halt loudly instead of writing to the filesystem root.
[ -n "$TROOT" ] || { echo "FATAL: temp-root unresolved (no functional python3/python on PATH)" >&2; exit 1; }
```

For each candidate ID in the manifest, run:

```bash
git worktree add "$TROOT/sage-tournament-<run-id>/<candidate-id>" -b tournament/<run-id>/<candidate-id>
```

Record each worktree path in the candidate manifest.

After creation, confirm each worktree is clean:

```bash
git -C "$TROOT/sage-tournament-<run-id>/<candidate-id>" status --short
```

An unclean worktree is a setup error — halt and report.

**Teardown contract — fires on every exit path:**

Teardown wraps the entire loop. Whether the loop ends by normal completion, by validation failure eliminating all candidates, or by an unexpected interrupt, every worktree created in step 2 is removed before the skill hands off or stops:

```bash
# For each candidate worktree created:
git worktree remove --force "$TROOT/sage-tournament-<run-id>/<candidate-id>"
# After all are removed:
git worktree prune
```

The teardown is not conditional on success. It fires whether zero, one, or all candidates survive. If a remove fails (worktree path already gone), log the condition and continue pruning the rest — a missing path is not a blocking error in teardown. After teardown, verify:

```bash
git worktree list
```

No `tournament/<run-id>/*` entry should remain. If any entry remains, attempt `git worktree prune` once more and report the residual as a finding to the orchestrator — do not suppress it.

### Interrupt-orphan recovery

Tournament worktrees live under `<temp-root>/sage-tournament-<run-id>/` and tournament branches live under `tournament/<run-id>/`. A mid-loop interrupt or crash can leave worktrees and branches matching these prefixes behind. On re-entry after an interrupt or crash, run the following prune sweep before creating any new worktrees.

The run-id and the temp-root recipe are the recovery contract: both must be known or re-resolvable on re-entry. The run-id comes from the recorded manifest; the temp root is re-resolved deterministically using the same recipe as Step 2 (the original shell is gone; the recipe is host-stable and yields the same path). As in Step 2, run this block as its own discrete shell invocation — do not `source` it:

```bash
# Re-resolve TROOT on re-entry — the shell that created the worktrees is gone; this recipe is deterministic.
PYBIN="$(command -v python3 || command -v python)"
TROOT="$("$PYBIN" -c 'import tempfile, os; print(os.path.realpath(tempfile.gettempdir()).replace(os.sep, "/"))')"
# Guard: an empty TROOT here would make the prune sweep target "/sage-tournament-<run-id>/" — halt loudly instead.
[ -n "$TROOT" ] || { echo "FATAL: temp-root unresolved on re-entry (no functional python3/python on PATH)" >&2; exit 1; }
# Prune disconnected worktree records:
git worktree prune
# Remove any remaining worktrees for THIS run only (anchored to run-id prefix, metachar-safe, space-safe):
git worktree list --porcelain | grep -F "worktree $TROOT/sage-tournament-<run-id>/" | sed 's/^worktree //' | while IFS= read -r wt; do git worktree remove --force "$wt"; done
# Delete any leftover tournament branches for THIS run only (anchored to refs/heads/tournament/<run-id>/):
git for-each-ref --format='%(refname:short)' "refs/heads/tournament/<run-id>/" | xargs -r -n1 git branch -D
```

`<run-id>` MUST be a filesystem- and branch-safe slug (alphanumeric + dash only, e.g. `<date>-<slug>`) — see Step 2 where this constraint is required at creation; both the worktree sweep and the branch sweep rely on it for safe, non-over-matching anchoring.

The branch sweep is anchored at `refs/heads/tournament/<run-id>/` and is scoped to the active run-id only. It cannot match `feat/patch-tournament`, `release/tournament/*`, or any branch outside this run's namespace. It must never touch the active feature branch. The worktree sweep is equally anchored: only paths under `<temp-root>/sage-tournament-<run-id>/` are removed; worktrees from other runs are untouched.

Per ADR-0011 this skill is prose, not an automatic trap — the prune-sweep-on-reentry is the recovery mechanism. The orchestrator is responsible for running this sweep when resuming after an interrupted tournament.

## Step 3 — Implementation

Dispatch one implementer agent per candidate into its isolated worktree. Each implementer receives:

- The worktree path for its candidate.
- The approved plan and acceptance criteria.
- The strategy-prompt label from the manifest.
- An explicit instruction to commit only within its worktree branch and to write no candidate code outside that branch.

Implementers run sequentially (CLAUDE.md §5: no two filesystem writers concurrently). Each implementer's work is committed to its worktree branch before the next implementer is dispatched.

No candidate code is committed to the main working tree during this step. No `git push` of any candidate branch is performed in this loop.

## Step 4 — Validation spine

After all implementers complete, run the validation spine against each candidate's worktree. Run per candidate, in the worktree:

```bash
# Lint check
cd "$TROOT/sage-tournament-<run-id>/<candidate-id>" && uv run ruff check .

# Test suite
cd "$TROOT/sage-tournament-<run-id>/<candidate-id>" && uv run pytest -q

# Coverage — read the destination project's configured threshold; do NOT hard-code a percentage.
# In sage the threshold is 85% (pyproject.toml [tool.coverage.report] fail_under); confirm for
# the destination project before comparing. Then:
cd "$TROOT/sage-tournament-<run-id>/<candidate-id>" && uv run pytest --cov=<cov-target> --cov-report=term-missing -q
# <cov-target>: read from the destination project's coverage config (pyproject.toml
# [tool.coverage.run] source, setup.cfg [coverage:run] source, or .coveragerc [run]
# source). Do NOT hard-code a package name. In sage itself <cov-target> is "sage".
```

**Coverage threshold:** read the destination project's configured `fail_under` (or equivalent) from its project manifest (`pyproject.toml`, `setup.cfg`, `.coveragerc`, etc.) before running coverage. Do not assume a fixed percentage. Compare the measured coverage against the destination-configured threshold.

Also run any destination-project gates named in the brief (additional lint rules, type-checking commands, integration smoke tests, etc.).

Record the result for each candidate as a **validation result row**:

| Candidate ID | ruff check | pytest | Coverage vs threshold | Destination gates | Outcome |
|---|---|---|---|---|---|
| candidate-A | PASS | PASS | ≥ threshold | PASS | SURVIVED |
| candidate-B | FAIL (3 errors) | — | — | — | ELIMINATED |

A candidate is **ELIMINATED** if it fails any single spine element. Partial passes do not advance. The outcome is binary: SURVIVED or ELIMINATED.

## Step 5 — All-candidates-fail fallback

If every candidate is ELIMINATED:

1. Identify the **best partial** — the candidate with the fewest validation failures (e.g., passed ruff but failed pytest; failed one test vs. five tests). If two or more candidates tie, prefer the one with the smallest diff against base.
2. **Capture the best partial's diff as a durable artifact before any teardown.** A candidate may have made multiple commits; capture the FULL range from the tournament base to the candidate's HEAD so no commits are lost. Export it to a file:

   ```bash
   git -C "$TROOT/sage-tournament-<run-id>/<best-partial-id>" diff <BASE_SHA>..HEAD > "$TROOT/sage-tournament-<run-id>/best-partial-<run-id>.patch"
   ```

   Where `<BASE_SHA>` is the tournament base commit recorded at worktree creation (Step 2). This captures every commit the candidate made — not just the last one. This file survives worktree removal. The handoff to `systematic-debugging` uses this captured diff/patch file — never a worktree path that teardown will delete.

3. Hand the captured artifact to `systematic-debugging`. The handoff payload includes: the captured diff/patch file path, the full validation spine output (exact stdout/stderr) for the best-partial candidate, and the approved plan's acceptance criteria.
4. Emit a failure report naming: total candidates spawned, each candidate's ELIMINATED reason (exact failure output), and the best-partial candidate handed to `systematic-debugging`.
5. **Run teardown** (step 2 teardown contract) — even on all-fail, all worktrees are removed before halting.
6. **Halt. Do not advance any candidate. Do not ship a failing patch.** The best partial is NOT shipped. Wait for `systematic-debugging` to surface findings before re-running.

## Step 6 — Survivor-set handling

After the validation spine, count the candidates that SURVIVED:

**0 survivors** → proceed to step 5 (all-candidates-fail fallback).

**Exactly 1 survivor** → that candidate advances directly as the winner WITHOUT invoking `dev-selector`. It already cleared the full validation spine; there is nothing to select between. Proceed to winner capture (below) immediately.

**2 or more survivors** → assemble the survivor-set handoff payload for `dev-selector`:

- Candidate IDs of all surviving candidates.
- **Worktree path** for each surviving candidate (e.g., `<temp-root>/sage-tournament-<run-id>/<candidate-id>`). `dev-selector` requires these paths to run `git diff` for criterion-2 (diff size) and to scope `Grep` for criterion-4 (public-API delta) — the payload is unusable without them.
- Validation spine output artifact paths for each surviving candidate (the exact files or output captures from step 4).
- Coverage output artifact paths for each surviving candidate.
- The **tournament BASE commit SHA** (`BASE_SHA` recorded at Step 2), so `dev-selector` can compute `git diff <BASE_SHA>..HEAD` per candidate.
- The list of **destination-project gates that were run** (names and commands, not just artifact paths), so `dev-selector` can re-confirm the same gates in its criterion-1 re-check.
- The approved plan's acceptance criteria.
- Candidate IDs and strategy-prompt labels from the manifest, for provenance. The assigned-model (producer) column is **omitted** from this payload — `dev-selector` is producer-blind by contract and must not know or weight by which model produced a candidate. (The tournament retains the full candidate manifest including the producer column in its own composition records, but that column does not appear in the dev-selector handoff.)

Hand this payload to `dev-selector`. The tournament skill does **not** select the winner — that is `dev-selector`'s lane (ADR-0091). The skill's work ends at handing off a clean survivor set with complete evidence.

### Winner capture and unconditional teardown

Once a winner is identified — either as the sole survivor or as named by `dev-selector` — capture the winning patch as a durable artifact before any teardown. A candidate may have made multiple commits; capture the FULL range from the tournament base to the winner's HEAD so no commits are lost:

```bash
# Export all commits from tournament base to winner HEAD as a patch series:
git -C "$TROOT/sage-tournament-<run-id>/<winner-id>" format-patch <BASE_SHA>..HEAD --stdout > "$TROOT/sage-tournament-<run-id>/winner-<run-id>.patch"
# Or export the combined diff directly:
git -C "$TROOT/sage-tournament-<run-id>/<winner-id>" diff <BASE_SHA>..HEAD > "$TROOT/sage-tournament-<run-id>/winner-<run-id>.diff"
```

Where `<BASE_SHA>` is the tournament base commit recorded at worktree creation (Step 2). This captures every commit the winner made — not just the last one. Using `HEAD~1` is incorrect and silently loses all but the final commit when a candidate makes 2+ commits; after teardown destroys the worktree, that loss is unrecoverable.

After the winning patch is captured as a durable file, apply it to the orchestrator's working branch so the winner's commits land on the working tree before the worktrees are destroyed:

```bash
# Apply the captured winner patch series onto the working branch:
git am "$TROOT/sage-tournament-<run-id>/winner-<run-id>.patch"
# If the winner's branch ref is still accessible, cherry-pick is an alternative:
# git cherry-pick <BASE_SHA>..<winner-branch-HEAD>
```

The winner re-enters via its captured patch file (or branch ref, if the ref is still reachable) — this is the only mechanism that carries the winner's commits out of the worktree before teardown removes it. Confirm the apply succeeds (exit 0) before proceeding.

Run teardown unconditionally after the winner is applied to the working branch: `git worktree remove --force` for ALL candidate worktrees, including the winner's, then `git worktree prune`. Teardown depends only on winner-capture AND winner-application completing — it never waits on a partner-agent signal.

The winner, now present on the working branch, proceeds into the normal §16 dual-auditor pipeline. The tournament loop does not audit, push, or merge — those are §16's lane.

## Output per run

Produce three artifacts in sequence:

**1. Candidate manifest** (step 1): table of candidate ID → strategy-prompt label → model source.

**2. Per-candidate validation result** (step 4): table of candidate ID → ruff result → pytest result → coverage vs destination threshold → destination gates result → SURVIVED/ELIMINATED.

**3. Survivor-set handoff payload or failure report** (step 5 or 6): either the structured survivor set (IDs + artifact paths) with the winner identifier, or the all-fail failure report (captured patch file path + handoff to `systematic-debugging`).

No inline prose summaries without this three-part structure. If a step is not reached (e.g., all candidates eliminated before step 6), record the step as "not reached — see failure report."

## Anti-patterns

- **Re-implementing budget-check logic.** The tournament owns composition policy (N, strategy prompts, Codex-default rule). The `codex-budget` skill provides only the budget verdict. Do not re-implement Refuse/Ask/Proceed logic here; delegate that call.
- **Claiming codex-budget decides N, model assignment, or strategy.** It decides only the budget verdict on the Codex spend. N and per-candidate strategy are the tournament's responsibility.
- **Running a tournament over non-machine-testable criteria without firing the weak-tests guard.** A tournament amplifies the chance a bad patch passes the spine when the spine cannot test what matters. The guard fires first; do not skip it.
- **Warn-and-proceed without explicit caller acceptance.** A warn-and-proceed that was not explicitly accepted by the caller is a refuse. The distinction is load-bearing: a tournament over non-testable criteria that proceeds silently is worse than a refused tournament.
- **Shipping the largest or "most complete" surviving candidate.** Winner selection is `dev-selector`'s lane when 2+ survivors exist. The tournament hands a survivor set; it does not pick.
- **Invoking dev-selector for a single survivor.** `dev-selector` refuses a one-candidate set. A sole survivor advances directly — it already passed the spine; there is nothing to select between.
- **Shipping any candidate after all-fail.** The all-fail fallback captures the best partial's diff and routes to `systematic-debugging`, then halts. "Ship the least bad one" is not a valid tournament outcome — it violates the "never ship a loser" invariant.
- **Handing systematic-debugging a worktree path that teardown will delete.** Capture the best-partial diff to a durable file BEFORE teardown; hand that file, not the worktree path.
- **Tearing down before winner-capture completes.** Capture the winning patch as a durable artifact first; then tear down unconditionally. Teardown waits only on winner-capture — never on any partner-agent signal.
- **Leaving orphan worktrees.** Teardown fires on every exit path: normal completion, all-fail halt, interrupt. A worktree that survives the tournament is a bug in this skill. On re-entry after a crash, run the prune sweep before creating new worktrees.
- **Hard-coding a coverage percentage.** Read the destination project's configured threshold from its project manifest. Hard-coding 85% (or any number) produces false passes on projects with a higher threshold and false eliminations on projects with a lower one.
- **Pushing candidate branches to remote.** Candidate code stays local until the winner is captured and the orchestrator enters the normal §16 audit pipeline.
- **Invoking the tournament as the default for ordinary tasks.** Default OFF. Single-implementer is the default per CLAUDE.md §13. Two conditions must hold; if either fails, decline.

## When NOT to use this skill

- **Trivial or low-value single changes.** Route to single-implementer (`dev-code-implementer`). The tournament's cost (N implementer invocations, N spine runs, worktree overhead) is not justified for a one-line config change, a rename, or a minor copy fix.
- **Tasks without machine-testable acceptance criteria.** The weak-tests guard refuses when criteria are not machine-testable. Do not invoke the tournament hoping the spine will cover it — the spine can only verify what the tests test.
- **Codex-spend budget verdict.** Route the Proceed/Ask/Refuse verdict to the `codex-budget` skill. (This skill owns composition itself — N, strategy, model mix — and delegates only the budget verdict.)
- **Wiring the tournament as a substitution inside the autonomy-loop implement step.** That is a phase-2 deferred item (ADR-0091). Out of scope for this skill. Route that discussion to the orchestrator.
- **Picking the winner from a validated survivor set of 2+.** Route to the `dev-selector` agent. The tournament skill hands survivors; it does not select.
- **Debugging validation failures in a candidate.** Route to `systematic-debugging`. The tournament's all-fail fallback captures the best partial and hands that captured artifact to `systematic-debugging`; do not embed debugging logic in the tournament loop.

## Handoff

When the run completes, the work product is:

- The candidate manifest (provenance record).
- The per-candidate validation result table (evidence of elimination decisions).
- Either the winner identifier with the captured patch artifact, or the all-fail failure report with the captured best-partial patch file handed to `systematic-debugging`.
- Worktrees fully torn down and confirmed gone (`git worktree list` shows no `tournament/` entries).

The winner then enters the normal §16 dual-auditor pipeline unchanged. The tournament loop does not audit, does not push, and does not merge.

Any residual worktree that could not be removed is surfaced as a named finding to the orchestrator — never silently dropped.
