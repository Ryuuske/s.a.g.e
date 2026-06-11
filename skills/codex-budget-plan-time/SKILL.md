---
name: codex-budget-plan-time
description: Use during plan drafting to flag predicted external-review-budget concerns before the User approves the plan. Triggers when a drafted plan references `/codex:review`, `/codex:adversarial-review`, `/codex:rescue`, `/ultrareview`, or any other token-billed external call as a planned step. Do not use for plans with zero external-review touches, for already-approved plans (it is a plan-time check, not a runtime gate), or as a substitute for the runtime `codex-budget` skill.
---

# Codex Budget at Plan Time

The runtime `codex-budget` skill (`skills/codex-budget/SKILL.md`) is the source of truth for live rate-limit thresholds and the refuse-or-ask decision at the moment of dispatch. This skill is its plan-time twin: it surfaces *predicted* budget pressure during planning so the User can decide before approving whether to commit a Codex slot to this plan.

The failure today is cosmetic but real: the User approves a plan, the orchestrator runs through it, and at the last step — the Codex review — the runtime skill reports "weekly budget exhausted, refuse." The plan was approved against a phantom budget. Surfacing it during planning makes the trade-off explicit while there is still room to reshape the plan.

## When this skill binds

Fire during plan drafting (after `aidev-planner` returns a draft or while the orchestrator is composing a plan inline) when the plan references any of:

- `/codex:review`
- `/codex:adversarial-review`
- `/codex:rescue` (including its `--background` flag)
- `/ultrareview`
- Any other token-billed external review path the framework adds in the future

Do NOT fire for:

- Plans with zero external-review touches.
- Plans the User has already approved — runtime gating belongs to `codex-budget`, not this skill.
- Brief Q&A in NORMAL prose with no plan being drafted.

## The check

Walk these in order. Output is a single line appended to the plan's "Risks and edge cases" section, not a separate user-facing message.

### 1. Count external-review touches in the draft plan

Scan the draft for invocations of the trigger commands. Each unique invocation counts once. If the plan loops over a list of items and calls Codex per item, count the loop's expected cardinality.

### 2. Read the budget snapshot

Prefer the sibling skill's CLI for consistency: `sage-codex-budget.py --pretty` (same JSON the runtime `codex-budget` skill reads). If the CLI is unavailable, fall back to reading `~/.cache/sage/codex.json` directly. If neither is present, the skill exits with a single line: `Codex budget: snapshot unavailable; assume normal headroom.`

The shape of the snapshot (matches `skills/codex-budget/SKILL.md:36-44`):

```json
{
  "plan_type": "prolite",
  "primary":   {"used_percent": 0,  "window_minutes": 300,   "resets_at": <epoch>},
  "secondary": {"used_percent": 2,  "window_minutes": 10080, "resets_at": <epoch>},
  "credits":   {"hasCredits": false, "unlimited": false, "balance": "0"},
  "rate_limit_reached_type": null,
  "stale": false
}
```

Let `P = primary.used_percent` (the rolling 5h window) and `W = secondary.used_percent` (the rolling weekly window).

### 3. Apply the plan-time thresholds

Conservative compared to the runtime skill — at plan time, slack is cheaper than at call time:

| `P` (primary)  | `W` (secondary)   | Verdict |
| --- | --- | --- |
| < 50           | < 70              | OK      |
| 50-79          | 70-89             | TIGHT   |
| ≥ 80           | ≥ 90              | RISKY   |

If `stale: true` or `rate_limit_reached_type != null`, treat as RISKY regardless of percentages.

If the plan has multiple external-review touches, escalate one tier (OK→TIGHT, TIGHT→RISKY) per additional touch beyond the first.

### 4. Append to the plan's risk section

Add a single line, NORMAL prose, to the plan's "Risks and edge cases" section before the "Approve this plan to begin production?" prompt:

- **OK:** *(no line added — silent pass.)*
- **TIGHT:** `Codex budget: tight — primary <P%>, secondary <W%>, and this plan calls Codex <N> time(s). Likely OK but expect a wait if another plan is in flight.`
- **RISKY:** `Codex budget: risky — primary <P%>, secondary <W%>, and this plan calls Codex <N> time(s). The final review step may refuse at runtime. Options: defer the review, switch to inline review by an aidev-* auditor, or proceed and accept the risk.`

The line does not block plan approval. It is information the User reads before saying "approved."

## Output

This skill has no commit-trailer output. Its effect is the one line appended to the plan's risk section during plan drafting. If the User approves the plan despite a RISKY verdict, the runtime `codex-budget` skill is still authoritative at dispatch time — this skill does not pre-clear the runtime gate.

## Failure modes this skill closes

- **Approve-then-refuse cycle** — User approves a plan that hits a hard refuse at the last step, forcing replan after work is already done.
- **Multi-review pile-up** — a plan with three Codex calls eats two days of weekly budget; surfaced only at the second call when the orchestrator hits the runtime gate.
- **Silent slow-down** — TIGHT cases slip through; the User waits for Codex without context.

Per the integration spec §7 improvement 9, the failure today is cosmetic: the User finds out at call time. This skill makes it plan-time instead.
