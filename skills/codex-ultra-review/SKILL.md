---
name: codex-ultra-review
description: "Use BEFORE opening/updating a PR on a non-trivial change, or on \"ultra-review this branch\" / \"catch what the auditors miss\". Runs the strongest agent-launchable review — a whole-branch Codex adversarial pass tracing beyond the diff to ground truth, then prompts the User for the billed cloud /code-review ultra. Not for routine diff review (the dual-auditor pair), the Codex gates (codex-budget, codex-routing-reflex), or PR-comment tagging (gh-pr-review-discipline)."
---

# Codex Ultra Review (pre-PR ground-truth pass)

This skill encodes the **pre-PR ground-truth review** procedure: the strongest review an agent can run *before* a pull request is opened, scoped to the whole branch and deliberately tracing **outside the diff to ground truth** — the real callees, the packaging/deployment surface, every sibling code path, and the edge-state matrix. It exists because the in-repo dual-auditor pair reviews the *diff against the tests*, and when the tests encode the same wrong assumption as the code, the auditors validate a closed loop. This procedure breaks the loop by going to ground truth, the way an external repo-wide reviewer does.

The agent-launchable engine is the **Codex adversarial review companion** (the slash command `/codex:adversarial-review` is `disable-model-invocation: true`, but its companion node script runs via Bash). The deepest pass — the billed cloud **`/code-review ultra`** (its deprecated alias is `/ultrareview`) — is **User-triggered only**; an agent can never launch it, and this skill never pretends otherwise. Its job is to run the agent pass and *prompt* the User for the cloud pass.

**The companion script is NOT the cloud pass.** Running `codex-companion.mjs adversarial-review` (steps 2–5) is the agent pass; it does **not** discharge the step-6 prompt. The two are different mechanisms with different reviewers — completing the agent pass never substitutes for recommending the cloud pass to the User.

## When this skill binds

Bind this skill when ANY of:

1. The orchestrator is about to **open or update a PR** for a change that warrants a deep pass — security-touching, a large or multi-component diff, a new public surface, or a high-risk subsystem — AND that change has not already had a whole-branch Codex pass this round. (This gate is about whether to BIND the skill initially — it never suppresses the step-5 re-run that confirms a fold closed. A small, low-risk PR does NOT need this — the dual-auditor pair suffices; this is the proportionate-to-risk extra lane, not a tax on every PR.) OR
2. The User says **"ultra-review this branch"**, "do a deep pre-PR review", "what will the reviewers catch", or "go to ground truth on this", OR
3. The User explicitly requests a whole-branch ground-truth pass outside the normal PR gate ("what did the auditors miss on this branch", "deep review before merge").

Do NOT bind for: a single trivial change (typo, copy, one-line config); the per-commit diff review the dual-auditor pair already owns; the budget or eligibility *decisions* (this skill *defers* to `codex-budget` and `codex-routing-reflex`, it does not re-implement them); classifying or tone-tagging PR review comments (`gh-pr-review-discipline`).

## Procedure

Run these steps in order. Steps 1–2 are gates; do not skip them.

### 1. Route and budget (defer — never re-implement)

- Run **`codex-routing-reflex`**. If it returns CLAUDE-ONLY (e.g. first-touch AI-dev work where Codex is excluded), **skip the Codex engine** and run the **local ground-truth checklist** (step 4) only — the angles still apply even without Codex.
- If ELIGIBLE/ORCHESTRATOR-DECIDES, run **`codex-budget`**. On REFUSE, skip the Codex engine and do step 4 locally. On ASK, surface the one-line budget state and wait. On proceed, continue.

### 2. Scope the review to the whole branch

The miss this skill prevents is *diff-scoped* review. Always scope to the full branch against its base, never just the working tree. Pass the flags as separate tokens and quote only the focus text:

```bash
# ROOT just builds the script path below; the script self-resolves its own
# plugin root internally — this env var does NOT configure it.
ROOT="$HOME/.claude/plugins/cache/openai-codex/codex/<version>"
node "$ROOT/scripts/codex-companion.mjs" adversarial-review \
  --wait --base <base-branch> --scope branch "<ground-truth focus text>"
```

Resolve `<version>` by listing `~/.claude/plugins/cache/openai-codex/codex/` and picking the **highest** version (more than one dir can coexist briefly during a plugin bump). Use `--base main` (or the PR's actual base). A whole-branch pass takes minutes, so run it one of two ways: **`--wait`** (blocking) inside a `run_in_background: true` Bash task and poll the task output; OR **`--background`** (the script submits async and returns a job id) then poll via the companion's `status <job-id>` subcommand. Do not pair `--wait` and `--background`.

### 3. Inject the ground-truth angles into the focus text

The focus text is where this skill earns its keep. Tell Codex to do what diff-scoped auditors don't — for EACH angle below, name it explicitly in the focus string so the review is steered, not generic:

1. **Mock fidelity** — "For every mock in the tests, open the REAL callee and confirm the mocked return shape matches what the callee actually produces." *(This single angle catches the most: green tests that encode the code's own wrong assumption.)*
2. **Contract tracing** — "For every 'mirrors X' / 'same as Y' / 'calls Z' claim, open X/Y/Z and diff the shapes; flag any divergence."
3. **Deployment context** — "Does this work when installed (wheel/package), not just from a source checkout? Trace every repo-relative path, packaged-data load, and entry point."
4. **Path / mode parity** — "List every behavior; confirm EVERY path implements each (e.g. an in-process path and a CLI/subprocess path, sync and async). Find the path that got the fix and the sibling that didn't."
5. **Edge-state matrix** — "Walk empty / absent / null-or-empty-field / divergent-store / idle-repoll / concurrent / malformed-input for each entry point; flag any unhandled state."

### 4. Local ground-truth checklist (always, even when Codex is skipped)

Independently of Codex, apply the five angles yourself. This step has **teeth** — it is not a vibe-check. Two requirements make it falsifiable:

- **Mock-fidelity is CITE-or-it-didn't-happen.** For each external dependency the tests mock, you must NAME the real callee you opened and the shape you confirmed (e.g. "opened `nook_graph.list_tunnels` → returns `list[{source:{wing}}]`; mock matched"). The output card (below) reports those citations. "mock-fidelity: nothing surfaced" with no named callee is an INCOMPLETE check, not a pass — an agent may not report the angle clear without at least one citation per mocked boundary.
- **At least one mock-free verification layer must exist in the pipeline.** An integration test against a real (tiny) artifact, a build-and-import smoke, or a typed boundary on the dependency. If none exists, that is itself a finding to fold (add one) — because the whole point is to break the closed loop where the tests and the code share one wrong assumption.

### 5. Fold, re-run, confirm

- Fold every `needs-attention` finding (≥ the project's blocking bar), then **re-run the Codex pass to confirm closure** (pass-N until APPROVE or an `aidev-arbiter` ruling resolves a genuine split) — the same fold-and-confirm loop the autonomy-loop uses. **Each re-run is a new `/codex:*` call → re-enter step 1 (budget) before it**; `codex-budget` is consulted before every invocation, not just the first.

**The release gate — defined for BOTH paths:**

- **Codex ran:** do not open/refresh the PR until the pass is `APPROVE` (all findings folded), or the User waives.
- **Codex was skipped** (step 1 returned CLAUDE-ONLY, or `codex-budget` REFUSEd, or Codex is unavailable): there is no Codex APPROVE to wait for — the gate is **the step-4 local checklist completed (with its mock-fidelity citations) plus a one-line note to the User that the Codex engine was skipped and why.** Do not dead-end waiting on a verdict that cannot arrive.

### 6. Prompt the User for the cloud pass (never launch it)

After the agent pass clears, surface a one-line prompt: the billed cloud **`/code-review ultra`** (deprecated alias `/ultrareview`) is the deepest multi-agent pass and is **User-triggered only** — recommend the User run it on the branch before merge for the highest-confidence review. State plainly that the agent cannot launch it, and that the step 2–5 companion pass does not substitute for it.

## Anti-patterns

- **Claiming the agent ran the cloud `/code-review ultra` / `/ultrareview`.** It is User-triggered and billed; the agent cannot launch it via Bash or otherwise. Prompt, never pretend.
- **Treating the companion pass as the cloud pass.** Running `codex-companion.mjs` (steps 2–5) does NOT discharge the step-6 cloud prompt — different mechanisms; the agent pass never substitutes for recommending the cloud pass.
- **Reporting mock-fidelity clear without a citation.** "nothing surfaced" with no named callee is an unrun check, not a pass.
- **Diff-scoped review.** Reviewing only the changed lines is the exact failure this skill prevents — always `--scope branch` and trace to ground truth beyond the diff.
- **Trusting green tests.** A passing suite whose mocks share the code's wrong assumption proves nothing. The mock-fidelity angle is non-negotiable.
- **Skipping the gates.** Calling the Codex engine without `codex-routing-reflex` then `codex-budget` first.
- **Re-implementing the gates.** This skill defers to `codex-routing-reflex` and `codex-budget`; it does not duplicate their decision rules.
- **Opening the PR before the pass clears.** Folding `needs-attention` findings is a precondition, not a follow-up.
- **One-and-done.** A single Codex pass without re-running to confirm the fold actually closed the finding.

## Output guidance

Report the result to the User as a short STATUS card, not a transcript:

- `gates:` routing verdict + budget state (one line; defer to those skills' own output).
- `codex:` scope (`--base … --scope branch`), verdict (`APPROVE` / `needs-attention`), and finding count; `folded N, re-confirmed` if a fold round ran.
- `local angles:` which of the five angles surfaced anything, one line each. For mock-fidelity, CITE the real callee(s) opened and the shape confirmed — an uncited "nothing surfaced" is not acceptable.
- `cloud:` a single line prompting the User to run `/code-review ultra` (User-only), or noting they declined. Never report it as something the agent ran.
- `gate:` `PR-ready` only after the Codex pass is APPROVE / findings folded, OR (Codex skipped) the local checklist is complete with citations and the skip is disclosed — or the User waives.

## When NOT to use this skill

- **Routine per-commit diff review** → `audit-pairing-lookup` selects the dual-auditor pair; that is the in-repo lane.
- **The budget or eligibility decision itself** → `codex-budget` (headroom) and `codex-routing-reflex` (eligibility); this skill consumes their verdicts.
- **Classifying / tone-tagging PR review comments** → `gh-pr-review-discipline`.
- **Verifying a "done/fixed/passing" claim** → `verification-before-completion`.
- **A trivial change** with no reviewer-facing surface → just open the PR.
