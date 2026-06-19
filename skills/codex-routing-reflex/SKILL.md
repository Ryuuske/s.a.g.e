---
name: codex-routing-reflex
description: Use when about to dispatch on a Codex-eligible surface — before composing any /codex:* command — to get a routing verdict (Codex-eligible / Claude-only / orchestrator-decides) based on AI-dev exclusion, one-touch rule, brief-shape classification, and Codex presence detection. Do not use to check budget headroom (that is codex-budget for runtime, codex-budget-plan-time for planning), to decide auditor pairings (that is audit-pairing-lookup), or to route non-Codex third opinions.
---

# Codex Routing Reflex

This skill is the dispatch-time gate the orchestrator consults when a Codex touch might be appropriate. It runs one stage before `/codex:*` is composed — earlier than `codex-budget` (which reads live rate-limit data at the moment of invocation) and earlier than `codex-budget-plan-time` (which flags predicted budget pressure during plan drafting). The question it answers is "should this go to Codex at all"; the budget skills answer "is there room in the budget if it does."

This skill operationalizes ADR-0028 (`.development/decisions/0028-codex-dispatch-time-reflex-check.md`) and is a sub-pattern of ADR-0011 (`.development/decisions/0011-claude-md-section-12-no-shipped-hooks.md`) toolkit-not-enforcer principle: the skill recommends and informs; it does not enforce via hooks or hard blocks.

## When this skill binds

Consult this skill at the moment the orchestrator is deciding whether to dispatch to Codex on any of:

- A §6 third-opinion split-verdict (per CLAUDE.md §6 step 2) — but ONLY when Codex was not already an auditor on this change; if Codex was the §16 adversarial secondary, the third opinion must be a NON-Codex agent (ADR-0125)
- A §13 large-diff review of non-framework code (per CLAUDE.md §13: "prefer Codex for 'review the whole repo' or large-codebase tasks")
- A goal-shaped bulk-implementation brief in a non-AI-dev destination

Do NOT consult this skill for:

- "What is my Codex budget right now?" → consult `skills/codex-budget/SKILL.md`
- "Will this plan's Codex calls exhaust my weekly window?" → consult `skills/codex-budget-plan-time/SKILL.md`
- "Which auditor pair handles this diff?" → consult `skills/audit-pairing-lookup/SKILL.md`

## Decision tree

Walk the elements in order. Return the verdict at the first terminal condition encountered.

### Element 1 — Presence detection

Read `~/.cache/sage/codex-presence.json`. If the file is missing, skip to re-detection below. If the file exists, read `{detected_at, present, signal}`.

**Cache validity checks (in order):**

1. **Age:** if `detected_at` is more than 30 days before now, treat the cache as missing and re-detect.
2. **Signal path:** stat the path recorded in the `signal` field. Use a bounded operation (cap stat at 100ms per finding D5 in `.development/audits/2026-05-27-adr-0036-aidev-adversarial-auditor-post-2.md`). If any stat failure occurs — `ENOENT`, `EACCES`, `ELOOP`, permission denied, or any other error — treat the path as no-longer-exists and re-detect immediately (per finding D4: all stat failures collapse to treat-as-missing; do not escalate as error).
3. **Present flag:** if both checks pass and `present: false`, Codex is absent — go to the absent-degradation exit below.
4. If both checks pass and `present: true`, proceed to Element 2.

**Re-detection procedure:**

Check whether the directory `~/.claude/plugins/marketplaces/openai-codex/` exists via Bash stat. This directory is the chosen presence signal — it is shell-accessible, survives plugin updates within the marketplace tree, and is stable enough that a 30-day TTL is appropriate. Alternative signal (deferred): `codex:codex-rescue` agent availability via the Agent tool surface — suitable if the directory layout drifts; maintainers may switch signals by updating the `signal` field in the schema and deleting the cache.

Write the result:

```bash
# fresh detection — presence found
echo '{"detected_at":"<ISO8601-now>","present":true,"signal":"~/.claude/plugins/marketplaces/openai-codex/"}' \
  > ~/.cache/sage/codex-presence.json

# fresh detection — presence absent
echo '{"detected_at":"<ISO8601-now>","present":false,"signal":"~/.claude/plugins/marketplaces/openai-codex/"}' \
  > ~/.cache/sage/codex-presence.json
```

Cache invalidation (explicit refresh): `rm ~/.cache/sage/codex-presence.json`. The next consult re-detects and rewrites.

**Absent-degradation exit:** if `present: false` after detection, emit and stop:

```
Codex routing: CLAUDE-ONLY — Codex absent.
```

Append a one-line install pointer: "To enable Codex routing, install the Codex plugin via the Claude Code marketplace (`claude plugin install openai-codex`) and delete `~/.cache/sage/codex-presence.json` to force re-detection." This is not an error; it is graceful degradation (ADR-0011 toolkit-not-enforcer: no per-session pestering, feature-off degrades silently to Claude-only routing).

### Element 2 — AI-dev exclusion

Classify the brief surface:

- **AI-dev surface:** the brief is for an implementer or reviewer-primary lane on changes inside `agents/`, `skills/`, framework files, hooks, or any other S.A.G.E. AI-dev artifact. This is a hard refusal for first-touch and per-phase dispatches — emit:

  ```
  Codex routing: CLAUDE-ONLY — AI-dev exclusion (implementer/reviewer-primary lane).
  ```

  **Exception — §6 third-opinion:** if the brief is for a §6 third-opinion split-verdict resolution (per CLAUDE.md §6 step 2), the AI-dev exclusion does NOT apply — PROVIDED Codex was not already an auditor on this change. If Codex was the §16 adversarial secondary on this change, reusing Codex is not an independent third opinion (ADR-0125): route the split to a NON-Codex third agent and emit CLAUDE-ONLY here. Otherwise proceed to Element 3 with the brief classified as a third-opinion surface.

  **Exception — §16 adversarial-audit lane (ADR-0123, amended by ADR-0125 — cross-model guard):** the adversarial-auditor slot of every AI-dev dual-auditor pairing (matrix rows `aidev-diff`, `aidev-state`, `ai-dev-infra-diff`, `propagation-batch`) MUST run a contrarian read from a **different model family than the implementer of the change under audit** — the AI-dev exclusion does NOT apply to the adversarial lane, because a different model giving the contrarian read is the entire point. Default = Codex `/codex:adversarial-review`, because Claude is the usual implementer (Claude implements → Codex adversarial). **When Codex implemented the change under audit, the adversarial pass uses the Claude fallback auditor (`aidev-adversarial-auditor` / `aidev-state-adversarial-auditor`)** — this is the correct cross-model outcome, not a fallback degradation. If Codex is unavailable OR budget-refused for this lane, fall back to the Claude auditor; NEVER skip the adversarial lane. The reviewer-primary slot (`aidev-code-reviewer` / `aidev-state-reviewer`) stays CLAUDE-ONLY. **Fail-safe on unknown/mixed implementer:** if the implementer model is unknown or mixed across the change, default the adversarial pass to the Claude auditor — never assume Codex implemented.

  **Exception — scoped terminal pass on high-risk surfaces (ADR-0086):** if the brief is the single whole-change terminal Codex pass (run once at the end of a multi-phase AI-dev build, on the full `main...HEAD` diff, before the PR opens) AND the build touches at least one high-risk surface (install scripts — `install.sh`, `install.ps1`, `installer-assets/`; hooks — `hooks/scripts/`; or security-sensitive framework code), the AI-dev hard-refusal does NOT apply for this terminal pass. This is a §6-style third-opinion lane (not a per-phase reviewer-primary dispatch); the one-touch rule applies to the terminal pass as a whole. Pure agent/skill-prose builds with no high-risk surface contact do not qualify and remain CLAUDE-ONLY. Emit:

  ```
  Codex routing: ELIGIBLE — terminal-carve-out (high-risk AI-dev surface, whole-change pass), no prior touch; consult codex-budget next.
  ```

  Then proceed through Elements 3–5 normally (one-touch check, budget pointer). The terminal-carve-out is a recommendation; `codex-budget` refusal supersedes; no hard hook enforces it.

- **Non-AI-dev surface:** proceed to Element 3.

The carve-outs are bounded: AI-dev split-verdict resolution via §6 is permitted; the single scoped terminal pass on high-risk surfaces is permitted; first-touch implementer or reviewer-primary dispatches on AI-dev surfaces remain refused regardless of brief shape. Per-phase and prose-only builds stay under the hard refusal. References: ADR-0086 (scoped terminal carve-out), ADR-0028 (AI-dev exclusion, retained in full outside the two exceptions).

### Element 3 — One-touch rule

Check whether this change has already received a Codex touch in the current session or the prior commit chain. Look for `/codex:` references in the current session's orchestrator turns and for `Codex-touched-by:` or equivalent notation in recent commit messages.

Per ADR-0028 clause (2) and ADR-0011 toolkit-not-enforcer: this rule is a recommendation, not an enforcement gate. The recording-medium convention (commit-trailer vs session-state vs nook drawer) is deferred to Session F/G. Until then, the orchestrator self-checks against session memory.

- **Prior Codex touch detected on this change:** emit:

  ```
  Codex routing: CLAUDE-ONLY — one-touch consumed (recommend refusing second touch per ADR-0028 clause 2).
  ```

  **§16 adversarial-lane carve-out (ADR-0125):** the one-touch rule suppresses a *second* Codex touch on the same change — it NEVER suppresses the adversarial lane itself (§16: the lane always runs). When the prior Codex touch is the *implementer* of the change under audit, routing the adversarial pass to the Claude auditor is the CORRECT cross-model outcome (ADR-0125), not a one-touch collision. The carve-out also permits a §16 **fold-confirmation rerun**: when the prior Codex touch was the §16 adversarial pass itself (Claude implemented the change, Codex is the selected adversarial lane), re-running that same already-selected Codex adversarial lane to confirm a fold is allowed — the one-touch rule does NOT emit CLAUDE-ONLY for a fold-confirmation rerun of the already-selected §16 adversarial lane.

- **No prior touch detected:** proceed to Element 4.

### Element 4 — Brief-shape classifier

Classify the brief's primary character:

- **Contract-shaped:** the brief contains verbatim requirements, `<placeholder>` tokens to fill, explicit anti-pattern lists, named ADRs to cite or not cite, specific file paths and function signatures, or a WHERE target with acceptance criteria. The orchestrator works from a specification, not a goal. → Route to Claude.

- **Goal-shaped:** the brief describes an outcome ("build feature X", "review this entire diff", "scaffold this repo") without prescribing the implementation steps, or is a bulk multi-file implementation task. The executor needs judgment at scale. → Codex-eligible.

- **Mixed:** the brief combines contract elements (some prescribed steps) with goal elements (some open-ended implementation scope). → Orchestrator decides based on primary character.

### Element 5 — Budget pointer (non-blocking)

Before emitting an ELIGIBLE verdict, note: budget check required. `skills/codex-budget/SKILL.md` is the runtime gate; budget refusal supersedes routing eligibility.

## Verdict emission

One line, no preamble, no narration. Three shapes:

```
Codex routing: ELIGIBLE — <brief-shape>, no prior touch, presence detected; consult codex-budget next.
Codex routing: CLAUDE-ONLY — <reason: AI-dev exclusion | one-touch consumed | contract-shaped brief | Codex absent>.
Codex routing: ORCHESTRATOR-DECIDES — mixed-shape brief on Codex-eligible surface; pick based on primary character.
```

No structured block. No emojis. No explanation of the decision tree to the User.

## Worked examples

### Example 1 — Contract-shaped brief (real)

**Brief description (this exact skill implementation brief):** verbatim design spec with `@@SKILL-DESIGN BEGIN/END` block, named ADRs to cite (`.development/decisions/0036-...`, `.development/decisions/0014-...`), scheduled annotation list with stat requirements, `<placeholder>` tokens throughout, explicit WHERE target, acceptance criteria R-2 + R-3 + R-4, named `forbidden_inputs`, explicit section order, 9-step implementation checklist.

**Decision path:**
1. Presence: assume detected (present: true, cache valid).
2. AI-dev exclusion: this is a brief for `aidev-code-implementer` on a `skills/` artifact — hard refusal applies, no §6 carve-out.

**Verdict:**
```
Codex routing: CLAUDE-ONLY — AI-dev exclusion (implementer/reviewer-primary lane).
```

### Example 2 — Goal-shaped brief (hypothetical)

> (hypothetical — recent session work is overwhelmingly AI-dev-classified per R-2.1 visionary refutation; goal-shaped examples are constructed to illustrate the classifier)

**Brief description:** "Review the full diff for the new fin-transaction-categorizer feature landing in the customer's billing service. ~800 lines across 12 files, no framework artifacts touched, no security surface."

**Decision path:**
1. Presence: detected (present: true).
2. AI-dev exclusion: not an AI-dev surface (destination repo code, non-framework). Proceed.
3. One-touch: no prior Codex touch on this change.
4. Brief shape: "review the full diff … 800 lines … 12 files" — bulk review task, open-ended outcome. Goal-shaped.
5. Budget pointer: consult `codex-budget` next.

**Verdict:**
```
Codex routing: ELIGIBLE — goal-shaped (large diff review), no prior touch, presence detected; consult codex-budget next.
```

### Example 3 — Mixed brief (hypothetical)

> (hypothetical — same sourcing note as Example 2)

**Brief description:** "Scaffold the gh-repo-scaffolder agent with the design spec below, then review the result for lane boundary compliance."

**Decision path:**
1. Presence: detected (present: true).
2. AI-dev exclusion: this is an agent file implementation brief — hard refusal for the first part (scaffold). The review-for-compliance part could be §6-adjacent but this is not a split-verdict resolution.
3. The brief mixes a contract-shaped implementation task (spec provided) with a goal-shaped review task (outcome defined, method open). Primary character is AI-dev implementation (hard refusal applies to the scaffold step).

**Verdict:**
```
Codex routing: CLAUDE-ONLY — AI-dev exclusion (implementer/reviewer-primary lane).
```

Note: if the scaffold step is separated from the review step, the review step alone (non-AI-dev compliance check on a completed artifact) could produce an ORCHESTRATOR-DECIDES verdict.

## Scope boundaries

- Do NOT use to read or predict Codex rate-limit consumption — that is `skills/codex-budget/SKILL.md` (runtime) and `skills/codex-budget-plan-time/SKILL.md` (planning).
- Do NOT use to decide auditor pairings — that is `skills/audit-pairing-lookup/SKILL.md`.
- Do NOT use as a §6 third-opinion router for non-Codex third opinions (e.g., dispatching a third roster agent).
- Do NOT use to enforce the one-touch rule against prior-session state beyond what session memory holds until Session F/G formalizes the recording convention.
- Do NOT use to modify the Codex plugin presence-detection contract itself — schema changes are ADR-grade.

## Anti-patterns

- Routing an AI-dev implementer or reviewer-primary brief to Codex on the grounds that "the brief is goal-shaped." The AI-dev exclusion is a hard refusal regardless of brief shape; only §6 third-opinion and the §16 adversarial-audit lane (ADR-0123, amended by ADR-0125) are exempt (the adversarial slot follows the cross-model guard — Codex when Claude implemented, Claude when Codex implemented; the reviewer-primary slot stays CLAUDE-ONLY).
- Emitting ELIGIBLE without checking for a prior Codex touch on the same change. The one-touch rule applies even when the budget would allow more touches.
- Treating the cache file as authoritative when its recorded signal path no longer exists. Stale presence cache produces phantom-Codex verdicts that fail at dispatch.
- Narrating the decision tree to the User. Emit the verdict line only; the reasoning chain is internal.
- Treating any stat failure as an error to escalate. All stat failures (EACCES, ENOENT, ELOOP, permission denied) collapse to treat-as-missing and trigger re-detection.
- Running the SHALL-verify stat without a time bound. The 100ms cap (finding D5, post-2 adversarial audit) is load-bearing on slow filesystems.
- Conflating "Codex absent" with "Codex refused." Absent is graceful degradation to Claude-only; refused is a deliberate routing verdict on a Codex-eligible surface.
- Calling this skill a budget gate. It is a routing gate. The budget gate is `skills/codex-budget/SKILL.md`.
