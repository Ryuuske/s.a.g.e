---
name: mid-session-checkpoint
description: Use as a drift-check when conversation context passes ~60% of the window — re-read the active plan and confirm the work in flight still matches it. Triggers when the runtime signals context ≥60%, when the User says "/checkpoint", after spawning three or more specialists since the last plan read, or after a major direction shift. Do not use for read-only Q&A, single-turn fixes, or any session with no plan file.
---

# Mid-Session Checkpoint

A long session drifts. Specialists return findings that tweak the work, the User adds a "while you're in there" ask, an audit surfaces an unrelated gap, and three hours later the diff bears only a passing resemblance to the plan that was approved at minute zero. The orchestrator does not notice this in real time because each turn is locally coherent — only the integral over the session shows the drift.

This skill is the integral check. It is cheap, fires at most twice per session, and surfaces drift while the cost of correction is still low.

## When this skill binds

Fire when any of:

- Context window has crossed 60% (the runtime exposes this; the orchestrator should check the context-meter line in the status output).
- The User typed `/checkpoint` explicitly.
- The orchestrator has dispatched three or more specialist agents since the last time it re-read `.development/plans/active.md` (or the nook's `plans` hall for the current wing).
- The conversation has had a topic shift the orchestrator did not initiate (User says "actually let's also handle X", "while we're at it Y", "before we finish, can we Z").

Do NOT fire for:

- Sessions under 30% context that have not had a topic shift.
- Read-only Q&A or exploratory sessions with no plan in flight.
- Sessions where the User has just approved a new plan (the plan is fresh; drift hasn't accumulated).

## The checkpoint protocol

Walk these in order. The output is a single short paragraph for the User — not a wall of text.

### 1. Re-read the active plan

Read `.development/plans/active.md` (or the nook's `plans` hall for the current wing). Pull the one-line summary, the acceptance criteria, and the *out-of-scope* list if one exists.

If no plan file is present, this session never had one — exit the skill silently. Drift is impossible against a plan that does not exist.

### 2. Enumerate what has happened since plan approval

From the conversation history, list:

- Specialists dispatched (with their verdicts).
- Files written or edited (paths only — no content).
- Decisions logged (ADR filenames if any).
- New asks the User added mid-session.

Keep this list internal — it is your evidence, not the output.

### 3. Classify each delta

For each item in step 2, mark it one of:

- **ON-PLAN** — directly serves an acceptance criterion.
- **EXPANSION** — a "while we're in there" addition the User explicitly approved.
- **DRIFT** — work that does not map to any acceptance criterion and was not explicitly approved as an expansion.

DRIFT is the failure mode this skill closes.

### 4. Surface the result

Produce a single paragraph for the User in NORMAL prose:

> Checkpoint at <context-%> of context. Plan: <one-line summary>. Since approval we have <done X, dispatched Y, written Z>. That's all on-plan / there's one expansion (the <thing> you asked for at <when>) / I notice <thing> drifted in — it's not in the acceptance criteria. Want me to fold it into the plan, drop it, or split it off?

The paragraph is the entire output. No headers, no bullets unless the drift list is genuinely long (three or more items).

### 5. Wait for the User's call

The User decides: fold drifted work into the plan (you append an addendum to `.development/plans/active.md`), drop it (you stop and revert if necessary), or split it (you finish the original plan and queue the drifted work as a follow-up plan).

Do not auto-decide. The point of the checkpoint is to make drift visible, not to silently re-route.

## Output (for the commit log)

This skill is User-facing; it does not produce a commit-trailer line. But if the checkpoint surfaces drift and the User chooses to drop it, the orchestrator records a one-line ADR per CLAUDE.md §8:

```
.development/decisions/NNNN-checkpoint-dropped-drift.md
- Drift: <one-line>
- Surfaced by: mid-session-checkpoint at turn <N>, context <X%>
- Decision: drop, not in scope of <plan>
```

## Failure modes this skill closes

- **Silent scope creep** — small additions accumulate; the final diff is twice the planned size.
- **Plan-criteria drift** — work that no acceptance criterion covers ships under the plan's name.
- **Context exhaustion on the wrong work** — the orchestrator burns context tokens on drift; the original plan runs out of runway.
- **Late-session-only surfacing** — without this skill, drift only becomes visible at the post-commit audit, when undoing is expensive.

Per the integration spec §7 improvement 8, the failure today is that drift accumulates silently within a session. This skill makes it turn-level instead.
