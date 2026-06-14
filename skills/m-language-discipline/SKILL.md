---
name: m-language-discipline
description: Use when writing or reviewing M-language transforms (.pq files), reasoning about Table.Buffer placement, type-declaration completeness, lazy/eager evaluation boundaries, null/error-row handling, or step naming. Triggers on "writing M code for a new transform", "should this query use Table.Buffer", "is every column explicitly typed". Do not use for test-failure investigation, test-first design, or M function-signature lookup (PAUSE-route those).
---

# M Language Discipline

This skill encodes four decision trees — type-declaration completeness, Table.Buffer placement, lazy-versus-eager evaluation boundaries, and null/error-row handling — that the consuming agent applies in both author-mode (writing M) and audit-mode (reviewing M diffs). It also encodes step-naming rules and function-reference verification routing.

This skill co-loads with `test-driven-development` (no overlap) and contributes M-specific verification items to `verification-before-completion` without duplicating its general procedure. It does not narrow `systematic-debugging` — that skill's triggers are bug, test failure, unexpected behavior, and stack trace; there is no performance entry point in that skill's trigger set.

Three of the four decision trees are logic-heavy: Table.Buffer placement requires transform-graph reference counting and materialization reasoning; lazy/eager boundary requires evaluation-model classification at preview-pull and query-folding sites; null/error-row handling requires risk-vs-construct mapping per column. The fourth — type-declaration completeness — is summarization-class: grep for `Table.TransformColumnTypes` on each step, list missing column declarations, compare against the terminal schema. The skill is classified logic-heavy overall because the three logic-heavy trees dominate the consumer's reasoning load; the type-completeness tree runs as a checklist within the same procedure.

## When this skill binds

Fire this skill when any of these are true:

- You are writing a new M transform or modifying an existing `.pq` file.
- You are reviewing an M diff in a `.pq` or `.pbix`-embedded query.
- You are reasoning about whether a query step should materialise its result (`Table.Buffer`, `List.Buffer`) versus remain lazy.
- You are checking whether every column in a transform chain carries an explicit type declaration.
- You are looking at UI-generated step names (`#"Changed Type1"`, `#"Filtered Rows2"`, `#"Merged Queries3"`, etc.) in committed M.
- You are uncertain about a specific M standard-library function signature.

Do NOT fire this skill for:

- General test-failure investigation on an M transform test → `systematic-debugging`.
- Writing a failing test for an M transform first → `test-driven-development`.
- Claiming an M transform is done and verifying it → `verification-before-completion` (this skill contributes M-specific items to that check but does not replace it).
- Designing an agent that writes M code → `agent-creation` via `aidev-agent-creator`.
- Looking up the current M language reference URL or version → emit `PAUSE: need research-docs-lookup for <subject> reference lookup` and stop; do not WebFetch or WebSearch directly. (See "When this skill PAUSEs" below for orchestrator routing.)

## Decision tree 1 — Type-declaration completeness

Every column at every terminal step in the chain must carry an explicit declared type. Auto-detected types are a silent correctness risk: schema drift at the source propagates without error.

**Audit procedure (author-mode and audit-mode):**

1. Grep the file for `Table.TransformColumnTypes`. List every call site with the columns and types declared.
2. Identify the terminal step — the last step that feeds either a `return` or a downstream query reference.
3. For each column present in the terminal step's schema, confirm a `Table.TransformColumnTypes` call names it explicitly. Auto-detected columns at the terminal step are a finding.
4. Emit `@@M-TYPE-AUDIT BEGIN` block (one row per transform step).

**Author-mode rule:** add a `Table.TransformColumnTypes` step immediately after any source-read step and again at the terminal step. Never rely on auto-detection for persistence.

## Decision tree 2 — Table.Buffer placement

A lazy M query re-evaluates its upstream steps each time it is referenced. When a derived intermediate is referenced more than once — in two joins, a self-join, a filter on one branch and a merge on another — the upstream steps execute once per reference, not once total. `Table.Buffer` materialises the result to memory, breaking the re-evaluation cycle.

**Placement rule:**

- Count distinct references to each non-trivial step.
- If a step is referenced ≥2 times in the downstream graph (directly or through intermediate named values), that step is a re-read boundary. Insert `Table.Buffer` on that step.
- If the step feeds only one downstream reference and the chain is linear, `Table.Buffer` is not needed — do not insert it "to be safe."
- `List.Buffer` applies the same rule to list-typed steps.
- Always name the boundary in a one-line rationale; a `Table.Buffer` without a stated reason is a future reviewer's confusion.

**Emit `@@M-BUFFER-DECISION BEGIN` block** for every step where you evaluated the re-read pattern, including the no-buffer decisions (they confirm the reasoning was done).

### Worked example — re-read boundary

Consider this hypothetical query:

```m
let
    RawSales = Csv.Document(File.Contents("sales.csv"), [Delimiter=",", Encoding=65001]),
    TypedSales = Table.TransformColumnTypes(RawSales, {{"OrderID", type text}, {"Amount", type number}, {"Region", type text}}),
    FilteredSales = Table.SelectRows(TypedSales, each [Amount] > 0),
    SalesByRegion = Table.Group(FilteredSales, {"Region"}, {{"TotalAmount", each List.Sum([Amount]), type number}}),
    TopRegions = Table.SelectRows(SalesByRegion, each [TotalAmount] > 10000),
    DetailJoin = Table.NestedJoin(FilteredSales, {"Region"}, TopRegions, {"Region"}, "TopDetail", JoinKind.Inner),
    Result = Table.ExpandTableColumn(DetailJoin, "TopDetail", {"TotalAmount"})
in
    Result
```

`FilteredSales` is referenced twice: once by `SalesByRegion` and once by `DetailJoin`. Without `Table.Buffer`, the M engine re-reads the CSV file and re-applies `TypedSales` and `FilteredSales` for both references — two full passes over the source file. With `Table.Buffer`, the filtered result is materialised once and both downstream steps read from memory.

Corrected form:

```m
    FilteredSales = Table.Buffer(Table.SelectRows(TypedSales, each [Amount] > 0)),
```

The `@@M-BUFFER-DECISION` block for this query:

```
@@M-BUFFER-DECISION BEGIN
step          | re-read count | evaluation pattern | decision          | rationale
TypedSales    | 1             | linear             | no-buffer         | single downstream reference
FilteredSales | 2             | re-read            | Table.Buffer      | referenced by SalesByRegion and DetailJoin — two full upstream re-evaluations without buffer
SalesByRegion | 1             | linear             | no-buffer         | single downstream reference (TopRegions)
@@M-BUFFER-DECISION END
```

**Cross-query case.** When the same intermediate is consumed by separate queries — for example, `FilteredSales` referenced from both a `SalesByRegion` query and a `DetailJoin` query defined as sibling queries in the same workbook, not as downstream steps within one query — `Table.Buffer` is placed at the terminal step of the producing query, not at the join sites inside the consuming queries. Buffering at the consumer would materialise the intermediate once per consuming query; buffering at the producer materialises it once for all consumers. The upstream protection mechanism is the producing query's explicit `Table.Buffer` materialization — without it, each sibling query re-evaluates the full upstream chain independently. The `@@M-BUFFER-DECISION` block is emitted in the producing query's audit, not the consuming queries'.

## Decision tree 3 — Lazy-versus-eager evaluation boundaries

`Table.Buffer` and `List.Buffer` are explicit force-evaluation sites. The M engine also forces evaluation at preview-pull boundaries (when a step's result is displayed in the editor UI) and at query-folding cut-off points (where operations can no longer be folded back to the data source). These implicit boundaries are invisible in the code.

**Audit procedure:**

1. Grep the file for `Table.Buffer` and `List.Buffer` — these are intentional force-evaluation sites.
2. For each: confirm the intent is documented (one-line rationale inline or in the buffer-decision block).
3. Flag any `Table.Buffer` that was inserted without a named re-read boundary in the `@@M-BUFFER-DECISION` block — this is the "default to buffer to be safe" anti-pattern.
4. Identify steps where folding cannot continue (e.g., after custom column additions or local function applications) and confirm the author was aware the engine will materialise there regardless.

Emit `@@M-EVAL-BOUNDARY BEGIN` block for every identified force-evaluation site.

## Decision tree 4 — Null and error-row handling

Nulls and errors in M propagate silently by default. A null in a column used downstream in a merge, group, or arithmetic step produces wrong results without surfacing an error. An error row in a source read silently drops or corrupts the affected record depending on how the consumer handles it.

**Audit procedure:**

1. Identify every column where null or error is a realistic input — foreign-key columns in joins, numeric columns used in aggregations, date columns parsed from strings.
2. For each at-risk column, check whether a handler is present:
   - `Table.SelectRows` filter that excludes nulls before the at-risk step, or
   - `try ... otherwise ...` wrapping an error-prone expression, or
   - explicit `Table.ReplaceValue` or `Table.FillDown` treatment.
3. Columns with none of the above and a named risk are a finding: "silent propagation — add explicit handler."

Emit `@@M-NULL-HANDLING BEGIN` block for every at-risk column.

## Step naming

UI-generated step names are a correctness risk in two ways: they convey no meaning to the next reader, and `#"Changed Type1"` vs `#"Changed Type2"` naming conflicts arise when queries are edited. In committed M, every step name must be meaningful.

**Author-mode rule:** name every step for what it produces, not the operation applied. `TypedSales` not `#"Changed Type1"`. `FilteredPositiveAmounts` not `#"Filtered Rows"`.

**Audit-mode rule:** for every UI-generated step name matching the pattern `#"(Changed Type|Filtered Rows|Merged Queries|Expanded [A-Z]|Added Custom|Removed Columns|Reordered Columns|Renamed Columns)\d*"`, emit an inline finding:

```
step `#"Changed Type1"` at step N → rename to `#"<MeaningfulName>"`
```

Do not propose the meaningful name if you cannot infer intent from the step's body — surface the finding and let the author name it.

If a step-naming finding is logged (informational) on a diff, and the next diff against the same file commits with the UI-generated name unchanged, the consuming agent escalates the finding on that second diff to sev 60 (informational → near-blocking). Trigger is git-auditable: the finding was raised; the next commit on the same path shipped without remediation.

## Function-reference verification

Never guess an M standard-library function signature. If you are uncertain whether `Table.NestedJoin` takes the join-kind as the fifth or sixth argument, or whether `List.Accumulate` expects the seed before or after the accumulator function, emit:

```
PAUSE: need research-docs-lookup for <subject> reference lookup
```

Stop there. Do not attempt the call with a guessed signature. The cost of a wrong signature is silent data corruption — a join on the wrong columns, an accumulate that ignores initial state — which is worse than a visible error.

### When this skill PAUSEs

The PAUSE shape above is the ADR-0027 pattern. `research-docs-lookup` is in the active roster. When the PAUSE fires, the orchestrator dispatches `research-docs-lookup` to resolve the M function signature. ADR-0027 cites ADR-0024 as directional precedent for the gap-naming-with-user-action-remediation pattern (per ADR-0104 landing reconciliation).

## Output blocks

The consuming agent emits structured blocks for each decision tree applied. All blocks use the delimiter pattern established across the agent roster.

**Type audit:**
```
@@M-TYPE-AUDIT BEGIN
step | columns | declared-type status (all-typed | partial | auto-detected) | finding (none | declare-explicit-types)
@@M-TYPE-AUDIT END
```

**Buffer decision:**
```
@@M-BUFFER-DECISION BEGIN
step | re-read count | evaluation pattern (linear | re-read | cross-reference) | decision (no-buffer | Table.Buffer | List.Buffer) | rationale (≤1 line)
@@M-BUFFER-DECISION END
```

**Evaluation boundary:**
```
@@M-EVAL-BOUNDARY BEGIN
site | force trigger (Table.Buffer | List.Buffer | preview pull | other) | intentional (yes | no)
@@M-EVAL-BOUNDARY END
```

**Null/error handling:**
```
@@M-NULL-HANDLING BEGIN
column | brief-named risk (yes | no) | handler (Table.SelectRows filter | try/otherwise | none — silent propagation flagged)
@@M-NULL-HANDLING END
```

Step-naming findings are inline (not in a block), one line per found UI-generated name.

Function-reference uncertainty surfaces as a standalone `PAUSE:` line before any code is emitted.

## Anti-patterns

- **Defaulting to `Table.Buffer` without naming the re-read boundary.** Every buffer insertion must name the downstream references that cause the re-read. "Safety" is not a rationale.
- **Trusting auto-type-detection at the chain's terminal step.** Auto-detection is silent schema drift waiting to happen. Explicit `Table.TransformColumnTypes` at the terminal step is mandatory.
- **Silently propagating nulls or errors.** A column with a named null risk and no handler is a finding, not a style preference.
- **Leaving UI-generated step names (`#"Changed Type1"`, `#"Filtered Rows2"`) in committed M.** These are a regression in readability and a conflict risk on the next edit.
- **Guessing an M standard-library function signature.** Emit the ADR-0027 PAUSE shape instead; wrong signatures produce silent data corruption.
- **Mixing forced-evaluation operations into a linear single-reference chain with no re-read boundary.** This is the inverse of the first anti-pattern — inserting `Table.Buffer` where there is no re-read does not help performance and signals the author did not reason about the graph.

## Output guidance

### Semantic guidance

- Never claim a transform is "correct" without naming the type-completeness state of every column at the chain's terminal step.
- Never insert `Table.Buffer` without naming the boundary in a one-line rationale (inside the `@@M-BUFFER-DECISION` block).
- Never silently let nulls or errors propagate — every at-risk column gets a handler entry in `@@M-NULL-HANDLING`.
- No product names ("Power Query", "Power BI", "Excel", "Microsoft") in output unless naming a file extension (`.pq`, `.pbix`, `.xlsx`). The technical surface is "M language". Per ADR-0023 case-b.
- In author-mode, every step gets a meaningful name before the work is considered done. In audit-mode, UI-generated step names are flagged for renaming.

### Tool guidance

- **Read** — view the `.pq` file in full before applying any decision tree (CLAUDE.md §4; do not edit a file you have not read).
- **Grep** — scan for `Table.TransformColumnTypes`, `Table.Buffer`, `List.Buffer`, `Table.NestedJoin`, `Table.SelectRows`, `try`, and the UI-generated step-name pattern (`#"Changed Type`, `#"Filtered Rows`, `#"Merged Queries`, `#"Expanded `, `#"Added Custom`, `#"Removed Columns`, `#"Reordered Columns`, `#"Renamed Columns`).
- **Glob** — locate `.pq` files when the brief names a transform area without an exact path.
- **No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.
- **No WebFetch or WebSearch** — function-reference uncertainty emits a `PAUSE:` line only (ADR-0027 shape); the orchestrator dispatches `research-docs-lookup`.

## When NOT to use this skill

- General test-failure investigation on any transform → `systematic-debugging`.
- Test-first design for any transform → `test-driven-development`.
- General pre-completion verification → `verification-before-completion` (load this skill alongside it for the M-specific items, but `verification-before-completion` governs the overall procedure).
- Looking up an M function signature → emit `PAUSE: need research-docs-lookup for <subject> reference lookup`; the orchestrator dispatches `research-docs-lookup` (ADR-0027).
- Any language other than M.
