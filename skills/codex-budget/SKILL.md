---
name: codex-budget
description: Use when about to invoke /codex:* — check Codex rate-limit budget before calling /codex:review, /codex:adversarial-review, /codex:rescue, or any task that fans out to Codex. Also use when the User asks "how much Codex do I have left" or "what's my Codex budget". Do not use for routine work that does not invoke Codex.
---

# Codex Budget Check

You do not invoke `/codex:*` blind. Before any call that spends Codex tokens, you consult the budget and decide whether to proceed, ask, or refuse. The data is local — there is no excuse for guessing.

This skill is the operational arm of CLAUDE.md §4 (no fabrication, capabilities clause) and §13 (cost and context discipline): you cannot honestly tell the User a Codex review is "free to run" without knowing whether the window is near reset.

## When this skill binds

Run this skill in the moment **before** any of:

- `/codex:review`
- `/codex:adversarial-review`
- `/codex:rescue` (including `--background`)
- Any agent or sub-task whose plan says "fan out to Codex"
- The User asking "how much do I have left" / "is it worth running Codex now"

You do NOT need this skill for:
- Reading code, answering questions, drafting plans — none of those touch Codex.
- Quick `/codex:*` retries inside the same orchestration step if you already checked within the last 30 seconds.

## How to run it

One command, JSON out:

```
sage-codex-budget.py --pretty
```

Add `--refresh` if the cache is older than the rate of change you care about (default cache TTL is 30s). Output structure:

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

`primary` is the rolling 5h window. `secondary` is the rolling weekly window. `used_percent` is what fraction of that window you have already consumed — higher means less room.

## The decision rule

After reading the numbers, apply this rule mechanically. Do not negotiate with yourself.

Let `P = primary.used_percent`, `W = secondary.used_percent`, `R = rate_limit_reached_type`.

| Condition                                | Decision                                                                                  |
| ---                                       | ---                                                                                       |
| `stale: true` or `R != null` or `P > 95` | **Refuse this invocation.** Escalate to the User. Quote the number. Do not run `/codex:*`.            |
| `W > 90`                                  | **Refuse this invocation.** Weekly is the harder cap; the 5h reset will not help. Re-check next time. |
| `P > 80` or `W > 75`                      | **Ask the User.** "Codex is at P% / W%; reset in X. Proceed anyway?" Wait for a yes.                  |
| otherwise                                 | **Proceed silently.** No need to narrate.                                                              |

Each row decides **this invocation only.** The skill is consulted again
before the next `/codex:*` — refusing now is not a state lock, and the
budget may have changed when you next consult it.

If `credits.hasCredits == false` and `credits.unlimited == false` and `credits.balance == "0"`, and the plan type is one of `free`, `go`, `plus`, `prolite`: the rate-limit reset is the only thing standing between you and an immediate `rate_limit_reached`. Treat the **Ask** threshold as **Refuse** in that case — the User should know before another token leaves.

## Offload thresholds (proactive offload, telemetry-calibrated)

The decision rule above gates a `/codex:*` you already decided to run. This section gates the
*prior* question for **proactive** offload — spending Codex budget to take large-context or
second-opinion work off Claude's context window (CLAUDE.md §13: "prefer Codex for review-the-whole-repo
or large-codebase tasks"). Whether the work *should* route to Codex at all is `codex-routing-reflex`'s
call (ADR-0028); this section only sets the budget-headroom bar for *initiating* an offload that
`codex-routing-reflex` already marked Codex-eligible. The two compose: routing-reflex says "eligible,"
this threshold says "and there is room to spend on it proactively."

Let `P = primary.used_percent`, `W = secondary.used_percent`.

| Headroom band | Proactive-offload decision |
| --- | --- |
| `P ≤ 50` and `W ≤ 50` | **Offload freely.** Ample headroom; route Codex-eligible large-context / second-opinion work to Codex without narrating. |
| `50 < P ≤ 80` and `W ≤ 75` | **Offload only load-bearing work.** Reserve Codex for the high-value cases (a §6 split-verdict third opinion, a genuinely large diff). Skip nice-to-have second opinions. |
| `P > 80` or `W > 75` | **Do not initiate proactive offload.** A required `/codex:*` still falls under the decision rule above (Ask/Refuse); but do not *start* optional offload work in this band. |

Proactive offload is always optional — never initiate it in a band that would push a *required*
`/codex:*` (an audit-chain adversarial review, a stuck-task rescue) into the Ask/Refuse band. The
required call has priority on the budget; optional offload yields to it.

### Telemetry calibration (verdict-log binding)

The offload bands are the floor, not the whole rule — the verdict-log telemetry
(`~/.sage/telemetry/turns.jsonl`, schema `docs/specs/telemetry.md`) tunes *which lanes* are worth
spending an offload on. After the Phase-4 re-tier (ADR-0065) moved several agents to sonnet, a
re-tiered lane that the telemetry shows trending toward higher disagreement or recurring blocking
findings is the lane where a Codex second opinion pays; a lane whose verdicts stay clean does not need
one. Query the signal before deciding to offload a *review* (a value question, not a budget question):

```bash
# disagreement / blocking-finding rate by lane (post-mining: sage recall "audit verdict" --wing telemetry --agent <name>)
tail -n 200 ~/.sage/telemetry/turns.jsonl | jq -r 'select(.phase=="audit") | [.agent,.verdict,.severity_top] | @tsv'
```

If a re-tiered (sonnet) lane shows a rising `REQUEST_CHANGES`/`REJECT` rate or repeated `severity_top ≥ 80`,
that is the empirical signal ADR-0065 names for reverting a re-tier if the cut regressed quality. The signal
is a TRIGGER TO RE-EVALUATE THE TIER, not a licence to spend Codex on AI-dev review: **any Codex escalation
must first re-run `codex-routing-reflex` and obey its verdict.** Most re-tiered lanes are framework
agents/skills (AI-dev), and `codex-routing-reflex` hard-refuses first-touch AI-dev Codex routing — for an
AI-dev lane, a Codex second opinion is permitted ONLY as a CLAUDE.md §6 split-verdict third opinion, never
as a telemetry-triggered first-touch review. So the telemetry signal's primary action is to flag the lane
for a tier-revert decision (a one-line `model:` flip, no Codex) and, where the lane is a routing-eligible
surface (a §6 split-verdict, or a non-AI-dev large review), to additionally consider a Codex second opinion
subject to the budget bands above. If the telemetry is too thin to read (as at Phase 4, ~55 rows), default
to the headroom bands alone and let the Phase-5 / Phase-8 runs accumulate the signal.

## What to output to the User after a Refuse or Ask

One line, no preamble:

```
Codex P% / W% used (resets in <Xh Ym> / <Nd>). Plan: <plan_type>. <Refuse|Asking before> running /codex:<command>.
```

No emojis. No commentary about how careful you are being. The number speaks.

## When the data is missing

If `sage-codex-budget.py` is not installed, the orchestrator has no Codex telemetry. Default to **Ask** for any `/codex:*` invocation until the User confirms they are fine running blind. Suggest installing S.A.G.E. at `~/.sage/install.sh`.
