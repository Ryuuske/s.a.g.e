<!--
scope-owned: per-agent CHANGELOG format (BACKLOG half retired 2026-06-10)
audience: devs
source: hand
review-trigger: convention change
-->

# aidev per-agent BACKLOG / CHANGELOG schema

Format spec for per `aidev-*` agent `BACKLOG.md` and `CHANGELOG.md` files. Follows `Orchestrator/BACKLOG.md` conventions; deviates only where per-agent scope demands it.

Version: 1.1 — 2026-05-24

> **Partial supersession (2026-06-10, Master Run Stage 1):** the BACKLOG.md
> half of this schema is retired — the 9 per-agent BACKLOG files were deleted
> and work items live ONLY in `.development/BACKLOG.md` (sequential `B-###` IDs).
> The CHANGELOG.md half (append-only, ≤15-word entries, reverse-chronological)
> remains live and binding. BACKLOG format sections below are preserved as the
> historical record of the retired convention.

---

## Scope

This schema applies to the `aidev-*` agent family only: `aidev-visionary`, `aidev-planner`, `aidev-agent-designer`, `aidev-code-implementer`, `aidev-code-reviewer`, `aidev-adversarial-auditor`, `aidev-keeper`, `aidev-state-reviewer`, `aidev-state-adversarial-auditor`.

The 8 generic agents — `dev-architect`, `dev-code-implementer`, `dev-code-reviewer`, `doc-keeper`, `ops-release-readiness`, `sec-auditor`, `dev-test-engineer`, `dev-ux-designer` — are deliberately out of scope. Per-agent lifecycle for those agents lives in the root `BACKLOG.md` / `CHANGELOG.md`. The roster-shape rationale for this distinction is in ADR-0006.

---

## File locations

Per the subdir-loading verdict in `docs/specs/manifest-schema.md`: agent files are flat at `~/.claude/agents/<name>.md`, so per-agent backlogs/changelogs live in the S.A.G.E. repo, not in `~/.claude/agents/`.

```
sage/.development/agents/<aidev-agent-name>.BACKLOG.md
sage/.development/agents/<aidev-agent-name>.CHANGELOG.md
```

---

## BACKLOG.md format

```markdown
# Backlog — <agent-name>

Loose priority order: top = next, bottom = someday.
Delete items when shipped; add one-line entry to CHANGELOG.md.

- [ ] <item description> `[YYYY-MM-DD, <surfacer>]`
- [ ] <item description>
- [ ] <item description> `[YYYY-MM-DD, <surfacer>]`
```

Rules:
- Header: `# Backlog — <agent-name>`.
- Items: checkbox bullets (`- [ ]`).
- Optional tag `[YYYY-MM-DD, surfacer]` — who or what surfaced the item (agent, User, test run). Omit if unknown.
- Order = priority. No labels unless list exceeds ~15 items; then use Orchestrator BACKLOG tiers: Critical / High / Medium / Low / Ideas.

---

## CHANGELOG.md format

```markdown
# Changelog — <agent-name>

Reverse-chronological. One line per shipped item.

- YYYY-MM-DD — <what shipped, ≤15 words>
- YYYY-MM-DD — <what shipped>
```

Rules:
- Header: `# Changelog — <agent-name>`.
- Reverse-chronological (newest first).
- Each entry: `- YYYY-MM-DD — <description>` (≤15 words; plan or ADR ref in parens if helpful).
- No grouping by version or milestone.

A header-only CHANGELOG means no shipped change yet. Pre-graduation drafts do not log here — only post-graduation edits to live agent files produce CHANGELOG entries. This distinguishes "dormant" from "broken" without tooling.

**Graduation:** first commit of a manifest-carrying agent file to `sage/agents/` or a downstream user's canonical roster location. Pre-graduation drafts do not log to CHANGELOG; post-graduation edits do.

---

## Lifecycle rule

When an item ships:

1. Delete the checkbox line from `BACKLOG.md`.
2. Append a new entry at the top of `CHANGELOG.md` (below the header).

Do not strike through or comment out backlog items. Delete them. Changelog is the record; backlog is the live queue.

**Partial-ship:** if an item ships for a subset of targets, rewrite the backlog line to scope-down (e.g., "add `briefing_template` to remaining agents: visionary, code-reviewer") and append a changelog entry naming the shipped subset. Do not delete until all targets are shipped.

**Strictness note:** parent Orchestrator BACKLOG permits strikethrough or delete; this schema mandates delete-only — CHANGELOG carries the history.

---

## Worked ship-lifecycle example

**Before ship — `aidev-planner.BACKLOG.md`:**

```markdown
# Backlog — aidev-planner

- [ ] Add `briefing_template` to front matter per manifest schema `[2026-05-23, aidev-code-implementer]`
- [ ] Cross-link plan output format to .development/plans/README.md when that file exists
```

**After ship — `aidev-planner.BACKLOG.md`:**

```markdown
# Backlog — aidev-planner

- [ ] Cross-link plan output format to .development/plans/README.md when that file exists
```

**After ship — `aidev-planner.CHANGELOG.md`:**

```markdown
# Changelog — aidev-planner

- 2026-05-23 — added briefing_template to front matter (manifest schema item 3)
```

The backlog item is gone. The changelog entry is the sole record of what shipped and when.

---

## Worked partial-ship example (multi-target item)

Scenario: item targets three agents; only one ships this cycle.

**Before partial ship — `aidev-planner.BACKLOG.md`:**

```markdown
# Backlog — aidev-planner

- [ ] Add briefing_template to: aidev-planner, aidev-visionary, aidev-code-reviewer `[2026-05-23, aidev-adversarial-auditor]`
```

**After shipping aidev-planner only — rewritten BACKLOG line (scope-down, not deleted):**

```markdown
# Backlog — aidev-planner

- [ ] Add briefing_template to remaining agents: aidev-visionary, aidev-code-reviewer `[2026-05-23, aidev-adversarial-auditor]`
```

**CHANGELOG entry for what shipped:**

```markdown
- 2026-05-23 — added briefing_template to aidev-planner (partial ship; visionary, code-reviewer remain)
```

The backlog item is retained and narrowed until all three targets ship; only then is it deleted.

---

## Backlog item vs. in-flight task

Two other workshop conventions this schema must not collide with:

- `.development/in-flight/` — active work assigned and running, not yet committed. Item goes here when started; leaves when the commit lands.
- `BACKLOG.md` (per-agent) — queued, not yet assigned. Item lives here until the orchestrator picks it up.

A backlog item is not in-flight until explicitly started; once running, it moves to `.development/in-flight/`.
