---
paths:
  - "**/docs/finance/**"
  - "**/finance/**"
  - "**/budgets/**"
  - "**/reconciliation/**"
  - "**/journal/**"
  - "**/*.xlsx"
  - "**/*.xlsm"
  - "**/categorization-rules.{json,yaml,md}"
---

# Finance work conventions

These conventions apply when working on budgets, cash flow, reporting, categorization, or reconciliation — for either business or personal finance. For lifecycle entry points use `fin-visionary` and `fin-planner` per CLAUDE.md §9 (Session lifecycle — mode-classification and intake dispatch).

## Period as a first-class dimension

Every finance work item carries a period (date range, fiscal period, reporting cadence) in its `WHERE` field — not just a file/sheet/range. `fin-planner` runs a period-dependency pass before sequencing: which item needs which prior period closed first. Period dependencies are tighter than software dependencies — wrong order produces silently-wrong numbers, not loud errors.

## Reconciliation baseline mandatory

Every reconciliation work item specifies the baseline (which source is the truth, which is being reconciled to it) and the tie-out tolerance (exact match, ≤X% variance, materiality threshold). Plans without explicit baselines are blocked at User approval.

## Categorization rules cite their source

`fin-transaction-categorizer` never proposes a category without citing the rule from the schema that justified it. Low-confidence categorizations (<70) are marked `needs_review` rather than auto-applied. Schema lives at `<repo>/docs/finance/categorization-rules.{md,yaml,json}` per the destination repo's convention.

## Reconciliation handles five break classes

Breaks classify as: **timing** (will resolve in next period), **amount** (true variance), **classification** (categorized differently across sources), **missing** (in one source only), **duplicate** (counted twice). `fin-reconciler` shows both sides of every break; never auto-resolves; only classifies and recommends. Audit pair: `fin-reconciler` self-pass + `doc-keeper` (format), sequential.

## Statement deliverables include cover sheet

Every financial statement (P&L, balance sheet, cash flow) includes a cover sheet with: date generated, scope (entity + period), source-of-truth references, "data as of" timestamp. `fin-statement-builder` self-passes before `doc-keeper` reviews format. Statement-specific conventions: P&L grouped by category; balance sheet balanced; cash flow statement reconciled to opening/closing cash.

## No tax or investment advice

`fin-visionary` frames the work; it does not advise on the substance. Tax positions, investment decisions, and similar judgment calls escalate to the User per §7 — they are not the visionary's lane and they are not the planner's lane. The framework documents the process; the User makes the substantive call.

## Confidentiality boundary

Finance work routinely touches sensitive figures, salaries, account numbers. None of this appears in agent files (per the identifying-info ban). Sensitive data lives in runtime context passed via brief, in memory, or in gitignored project files — never in agent definitions, ADRs, or committed plan files.

## Multi-currency hygiene

For multi-currency work, every amount carries its currency code (ISO 4217). FX rates cite source and date. `fin-fx-revaluator` outputs include the rate source, fetch timestamp, and the standard applied (spot vs period-end vs weighted-average). Different standards produce different numbers — silently picking one creates silently-wrong results.
