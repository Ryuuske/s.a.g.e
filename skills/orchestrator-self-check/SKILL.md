---
name: orchestrator-self-check
description: "Use as a five-question pre-flight before committing, pushing, or tagging — the orchestrator is the only unaudited actor in the loop, so it self-checks §16 (dual-auditor) and §17 (briefing) compliance before any such action or User handoff. Do not use for read-only operations, intra-turn exploration, or non-orchestrator agents."
---

# Orchestrator Self-Check

You (the orchestrator) are the only actor in the loop with no peer review. Specialists are bounded by their lane and audited by the dual-auditor pair; you are not. This skill closes that gap with a deterministic five-question check before any irreversible step.

The cost of skipping it is the failure mode that hides until the next state audit: a commit lands with no plan persisted, the wrong auditor pair ran, an override was applied silently, or the wrong-mode roster handled the work. Running this skill is cheap (under a minute) and the dispatch-time it adds is dwarfed by the cost of unwinding a bad commit.

## When this skill binds

Fire this skill **before** any of:

- `git commit` (after staging, before the commit call)
- `git push`
- `git tag`
- `/ultrareview` invocation
- Telling the User "approved, shipping" or equivalent
- Marking a multi-turn task as complete in `docs/plans/active.md`

Do NOT fire it for:

- Read-only exploration, search, recall, or wake-up
- Intra-turn artifact reads (grep, file inspection)
- Non-orchestrator agent verdicts (those have their own discipline)

## The five questions

Walk these in order. Stop and remediate at the first NO before continuing.

### 1. Did a plan exist and get approved?

Look at `docs/plans/active.md` (or the nook's `plans` hall for the current wing). It must contain a plan covering the change you are about to ship. The plan must be marked approved by the User in the conversation — implicit "go ahead" is the same as explicit.

- **YES** — quote the plan's one-line summary in your pre-commit message.
- **NO** — stop. Either (a) the change is genuinely trivial (one-line typo, copy fix); say so in the commit message and skip persistence, or (b) draft the plan now per CLAUDE.md §2.

### 2. Was the right mode-routed agent pool used?

CLAUDE.md §16 binds auditor pairs to the change type:

- Diff in `agents/`, `skills/`, framework files → `aidev-code-reviewer` + `aidev-adversarial-auditor`
- Diff in destination repo code, UI-touching → `dev-code-reviewer` + `dev-ux-designer`
- Diff in destination repo code, security-touching → `dev-code-reviewer` + `sec-auditor`
- Diff in destination repo code, neither → `dev-code-reviewer` + `dev-test-engineer`

Plus the state-audit pairings (`aidev-state-reviewer` + `aidev-state-adversarial-auditor`) when no diff is in scope.

- **YES** — list which pair you used (e.g., "dev-code-reviewer + dev-test-engineer").
- **NO** — stop. Re-dispatch the correct pair before commit.

### 3. Did the right auditor pair actually run?

Not "you intended to dispatch them" — did they produce a verdict in the conversation? Each auditor must have returned APPROVE / REQUEST CHANGES / HOLD with at least one finding-or-rationale line.

- **YES** — note both verdicts.
- **NO** — stop. Dispatch them now.

### 4. Was any blocking finding overridden, and was the override recorded?

A finding scored ≥80 (per CLAUDE.md §16) is blocking. If you proceeded past a blocking finding for any reason — User said "ship it anyway", finding was reclassified after discussion, audit was bypassed because of urgency — the override MUST exist as a new ADR in `docs/decisions/` quoting the original finding and the rationale for proceeding.

- **YES** (override exists) — quote the ADR filename.
- **NO** (no override needed; all findings ≤79 or all resolved) — say so.
- **OVERRIDE NEEDED BUT MISSING** — stop. Write the ADR now per CLAUDE.md §8.

### 5. Was the plan persisted before downstream dispatch?

CLAUDE.md §2 "Non-AI-dev plan persistence" rule: the approved plan must live at `<repo>/docs/plans/active.md` (or the nook `plans` hall) before any specialist that consumes the plan was dispatched. If you dispatched the implementer / reviewer with the plan still only in conversation, the file-reading auditor saw "no plan" and silently passed when it should have blocked.

- **YES** — confirm the file mtime predates the first specialist dispatch in this round.
- **NO** — stop. Persist the plan now; if specialists ran without it, re-dispatch them with the plan in their brief.

## Output

After all five questions clear, write a single line into the commit message body or User-facing report:

```
self-check: plan=<one-line>, pair=<lane1>+<lane2>, verdicts=<v1>/<v2>, override=<adr-or-none>, persistence=ok
```

The line is part of the commit's audit trail. State audits in `aidev-state-reviewer` will check for its presence when reviewing recent commits.

## Failure modes this skill closes

- **Plan-drift commits** — code lands that no plan covered.
- **Wrong-pair audits** — UI changes audited by dev-test-engineer; security changes audited by dev-ux-designer.
- **Silent override** — blocking finding waved through without ADR.
- **Persistence gap** — implementer dispatched before plan file existed.
- **Mode-routing miss** — aidev work audited by general specialists or vice versa.

Per the integration spec §7 improvement 5, these are the orchestrator failure modes that only surface in batch audits. The skill makes them turn-level instead.
