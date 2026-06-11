---
name: fin-categorization-audit-discipline
description: Use when auditing categorization-rule diffs, category-schema invariants, regression risk against a fixture set, or chart-of-accounts standards questions. Triggers on "review categorization rules in this diff", "audit schema invariants", "regression check this rule against fixtures", "does renaming a category break rule references". Do not use for general code review, rule authoring, statement output, M-language audits, or pre-completion.
---

# Financial Categorization Audit Discipline

This skill encodes four decision trees a reviewer-shaped agent invokes when auditing transaction-categorization-rule changes, category-schema changes, regression risk against a historical-transaction fixture set, and chart-of-accounts standards-compliance routing.

This skill co-loads with `verification-before-completion` for general pre-completion checks; it contributes categorization-specific verification items without replacing that skill's procedure. It does not overlap with `m-language-discipline` (M-language transforms) or `vba-language-discipline` (VBA macros).

Three of the four decision trees are logic-heavy: matching-logic edge-case analysis requires multi-attribute classification reasoning; schema-invariant checking requires referential-integrity traversal across code/parent/child relationships; regression-risk cross-walk requires outcome comparison per fixture row against acceptance-criterion evidence. The fourth — chart-of-accounts standards-compliance — is summarization-class at the routing layer (evaluate whether the audit surface touches a standard, then PAUSE-route); no paraphrasing of standards text is performed. The skill is classified mixed-per-tree overall and logic-heavy as a consumer-reasoning load.

## When this skill binds

Fire this skill when any of these are true:

- You are reviewing a diff that modifies transaction-categorization rules (rule files, matching logic, attribute filters, amount-sign handlers).
- You are auditing a category-schema change for invariant preservation (duplicate codes, orphan categories, parent-child consistency, broken references).
- You are checking whether a new or modified rule categorizes correctly under partial-match, ambiguous-description, negative-amount, multi-attribute, or missing-attribute conditions.
- You are cross-walking a rule change against a historical-transaction fixture set to classify each category-outcome change as intended-by-plan or unintended-regression.
- You are checking whether a renamed category breaks references in other rules.
- You are routing an audit that touches a chart-of-accounts standard.

Do NOT fire this skill for:

- General code review with no categorization-rule diff → `dev-code-reviewer`.
- Designing or authoring new categorization rules → `fin-statement-builder` [scheduled-annotation: fin-statement-builder pending future session per agent-roster.md line 786; fin-statement-output matrix row at docs/specs/audit-pairing-matrix.md line 38].
- Statement output and report generation → `fin-statement-builder` [scheduled-annotation: fin-statement-builder pending future session per agent-roster.md line 786; fin-statement-output matrix row at docs/specs/audit-pairing-matrix.md line 38].
- M-language transform audits → `m-language-discipline`.
- General pre-completion verification → `verification-before-completion` (load this skill alongside it for categorization-specific items, but `verification-before-completion` governs the overall procedure).
- Looking up a chart-of-accounts standard's current text → emit the ADR-0027 PAUSE shape (see Decision tree 4); do not WebFetch or paraphrase.

## Decision tree 1 — Matching-logic edge-case analysis

Categorization rules match transactions by one or more attributes (description text, amount sign, amount range, account code, merchant, transaction type). Edge cases arise when the matching surface is incomplete, ambiguous, or produces unexpected outcomes for boundary inputs.

**Audit procedure:**

1. Read the full rule file before applying any checks (CLAUDE.md §4; do not analyze a file you have not read).
2. For each modified or new rule, identify the match attributes and their completeness:
   - **Partial match** — the rule matches on a substring or prefix only; transactions whose description contains the pattern as a sub-phrase may match unintentionally.
   - **Ambiguous description** — two or more active rules match the same description string; identify which rule wins (priority order, first-match, most-specific) and whether the outcome is correct.
   - **Negative-amount transactions** — if the rule does not explicitly gate on amount sign, confirm whether it should apply to both positive and negative amounts or only one direction.
   - **Multi-attribute rules** — when a rule requires multiple attributes (e.g., description AND amount range), confirm all combinations of attribute presence/absence are handled (missing one attribute should not silently match or silently skip).
   - **Missing-attribute transactions** — transactions that lack an attribute the rule references; confirm the rule either skips gracefully or classifies to a defined fallback category.
3. Emit `@@FIN-CAT-AUDIT BEGIN` blocks (one per edge-case finding or confirmed-safe check).

## Decision tree 2 — Schema-invariant checking

A category schema is well-formed when all category codes are unique, every child category references an existing parent, and no rule references a category code that has been removed or renamed without a corresponding rule update.

**Invariant rules:**

- **No duplicate codes** — every category code in the schema must be unique. Two categories with the same code is a blocking finding.
- **No orphan categories** — every category with a `parent` field must reference a code that exists in the schema. A dangling parent reference is a blocking finding.
- **Parent-child consistency** — if the schema expresses hierarchy (parent/child nesting), children must not be assigned as parents of their own ancestors (circular reference).
- **No broken rule references** — every category code referenced in a rule file must exist in the schema at audit time. A rule referencing a removed or renamed code is a blocking finding.

**Audit procedure:**

1. Grep the schema file for all category codes. Build the set.
2. For each code, check uniqueness. Flag duplicates.
3. For each category with a `parent` field, confirm the parent code is in the set. Flag missing parents.
4. For each rule file in scope, grep for category code references. Confirm each referenced code is in the schema set. Flag missing references.
5. Emit `@@FIN-CAT-AUDIT BEGIN` blocks for each invariant violation and each confirmed-safe check.

## Decision tree 3 — Regression-risk cross-walk

When a categorization rule changes, transactions that previously matched the old rule may now route to a different category. The cross-walk procedure compares each fixture row's outcome under the new rule against its expected category from the plan's acceptance criteria to classify the change as intended-by-plan or unintended-regression.

**Cross-walk procedure:**

```
for each fixture row in the historical-transaction fixture set:
    try:
        apply the new rule set to the fixture row
        compare computed_category to fixture.expected_category
        if computed_category == fixture.expected_category:
            classification = "intended-by-plan" (no category change, or change matches plan)
        else:
            locate acceptance_criterion_ref for this fixture row's change
            if acceptance_criterion_ref resolves and matches computed_category:
                classification = "intended-by-plan"
            else:
                classification = "unintended-regression"
        emit @@FIN-CAT-AUDIT block for this fixture row
    except malformed-fixture-entry:
        log informational finding: "fixture row <id> malformed — skipped; cross-walk continues"
        continue (do NOT abort the entire cross-walk)
```

**Classification rules:**

- **Intended-by-plan** — the category change for this fixture row is traced to a named acceptance criterion in the plan. The `acceptance_criterion_ref` field must be populated with the specific criterion identifier; un-traced "intended-by-plan" claims are a blocking anti-pattern.
- **Unintended-regression** — the category change cannot be traced to any plan acceptance criterion. This is a blocking finding that must be resolved before APPROVE.
- **Malformed-fixture-entry** — a fixture row whose data cannot be parsed (missing required fields, invalid amount format, unrecognized schema). Log as informational (not a blocker); continue the cross-walk on remaining rows.

**Symbolic references:** every @@FIN-CAT-AUDIT block for a regression-risk finding must name both the rule name and its file path (e.g., `NegativeAmountAsReimbursement` at `rules/expense-reimbursements.json`), not a generic descriptor. Operation context is preserved by naming the specific artifact.

## Decision tree 4 — Chart-of-accounts standards-compliance routing

When an audit surface touches a chart-of-accounts standard (GAAP, IFRS, or any jurisdiction-specific accounting standard), this skill does not paraphrase, interpret, or reason from training-data recollections of that standard's text. Standards text must be verified against current authoritative sources; paraphrasing from training data violates CLAUDE.md §4 capability honesty.

**Routing rule:**

When the audit surface touches a chart-of-accounts standard, emit:

```
PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

Stop there. Do not attempt to classify the compliance question, paraphrase the standard's requirements, or infer the applicable rule from training data. ADR-0027 is the binding authority for this routing shape. ADR-0024 established the directional precedent for the gap-naming-with-user-action-remediation pattern; ADR-0027 is the specific authority for the PAUSE shape used here.

The `<subject>` placeholder is intentionally generic — the consuming agent fills it with the standard name and specific clause (e.g., `GAAP ASC 606 revenue recognition classification`). The placeholder must not be specialized in this skill file.

### When this skill PAUSEs

The PAUSE shape above is the ADR-0027 pattern. `research-docs-lookup` does not yet exist in the active roster. `aidev-claude-code-researcher` explicitly refuses non-Anthropic documentation queries — that is the broken route; it has been excluded from this skill and is why `research-docs-lookup` is the named receiver.

When the PAUSE fires, the orchestrator's established convention routes it to the User: the named agent's manifest absence triggers User-escalation fallback per the orchestrator's general dispatch behavior on unresolved agent names. ADR-0027 (this skill's binding source) cites ADR-0024 only as a directional precedent for the gap-naming-with-user-action-remediation pattern, not as the specific authority for PAUSE routing; the orchestrator routing itself is convention rather than ADR-codified. If a future ADR formalizes the unresolved-agent-name PAUSE routing rule, this subsection updates to cite it directly.

When `research-docs-lookup` lands per agent-roster.md step 13, the scheduled-annotation resolves and the PAUSE routes directly to that agent. If research-docs-lookup ships with the PAUSE shape this skill emits today, no skill edit is required (same-shape resolution); if its design diverges, a follow-on ADR aligns shapes and this skill amends accordingly per ADR-0021 brief-correction discipline.

## Output blocks

The consuming agent emits structured `@@FIN-CAT-AUDIT` blocks for decision trees (a) matching-logic edge-case analysis, (b) schema-invariant checking, and (c) regression-risk cross-walk. Decision tree (d) standards-compliance routing emits a one-line PAUSE only per ADR-0027 — no structured `@@FIN-CAT-AUDIT` block is produced for that tree. Regression-risk blocks carry additional fields.

**Standard finding block (Decision trees 1, 2):**
```
@@FIN-CAT-AUDIT BEGIN
tree: <matching-logic-edge-case | schema-invariant>
severity: <blocking | non-blocking | informational>
rule_or_schema_ref: <rule name> at <file path> (or <schema element> at <file path>)
evidence: <specific evidence: attribute name, code value, fixture row id, line reference>
recommendation: <specific corrective action or "confirmed safe — no finding">
@@FIN-CAT-AUDIT END
```

**Regression-risk block (Decision tree 3):**
```
@@FIN-CAT-AUDIT BEGIN
tree: regression-risk
severity: <blocking | non-blocking | informational>
rule_or_schema_ref: <rule name> at <file path>
fixture_id: <fixture row identifier>
old_category: <category before rule change>
new_category: <category after rule change>
classification: <intended-by-plan | unintended-regression>
acceptance_criterion_ref: <plan acceptance criterion ID, or "none — untraced" if missing>
evidence: <specific evidence>
recommendation: <specific corrective action or "confirmed safe — intended-by-plan">
@@FIN-CAT-AUDIT END
```

**Standards-routing block (Decision tree 4):**

Emit the ADR-0027 PAUSE text verbatim:

```
PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

No @@FIN-CAT-AUDIT block is emitted for standards-routing; the PAUSE line replaces it.

**Malformed-fixture informational finding:**
```
@@FIN-CAT-AUDIT BEGIN
tree: regression-risk
severity: informational
rule_or_schema_ref: <fixture file path>
fixture_id: <fixture row identifier or "unknown — parse failure">
evidence: <description of malformed field or missing required attribute>
recommendation: fix fixture data; re-run cross-walk after repair
@@FIN-CAT-AUDIT END
```

## Worked example — Regression-risk cross-walk (Decision tree 3)

This example demonstrates the cross-walk procedure for a rule change to `NegativeAmountAsReimbursement` at `rules/expense-reimbursements.json`. The rule previously categorized all negative-amount expense transactions as `Expense:Misc`; the updated rule categorizes them as `Income:Refund` when the description contains "reimbursement". The plan's acceptance criterion AC-7 states: "Negative-amount transactions with 'reimbursement' in description must categorize to Income:Refund."

**Historical-transaction fixture set (3 rows):**

| fixture_id | description                    | amount   | expected_category (pre-change) |
|------------|--------------------------------|----------|-------------------------------|
| F-001      | "airline reimbursement Q1"     | -142.50  | Expense:Misc                  |
| F-002      | "hotel stay — business trip"   | -310.00  | Expense:Travel                |
| F-003      | [malformed — missing amount]   | (absent) | Expense:Misc                  |

**Cross-walk execution:**

```
for each fixture row in [F-001, F-002, F-003]:

    fixture F-001:
        apply NegativeAmountAsReimbursement (updated):
            amount < 0 AND description contains "reimbursement" → Income:Refund
        computed_category = Income:Refund
        fixture.expected_category = Expense:Misc  (pre-change baseline)
        category changed: Expense:Misc → Income:Refund
        locate acceptance_criterion_ref: AC-7 matches (negative-amount + "reimbursement" → Income:Refund)
        classification = intended-by-plan
        emit @@FIN-CAT-AUDIT block

    fixture F-002:
        apply NegativeAmountAsReimbursement (updated):
            amount < 0 AND description does NOT contain "reimbursement" → rule does not apply
            fallthrough to next rule → Expense:Travel (unchanged)
        computed_category = Expense:Travel
        fixture.expected_category = Expense:Travel
        no category change
        classification = intended-by-plan (no regression)
        emit @@FIN-CAT-AUDIT block

    fixture F-003:
        try: parse amount field → parse failure (field absent)
        except malformed-fixture-entry:
            log informational: "fixture row F-003 malformed — missing amount field; skipped; cross-walk continues"
            continue
```

**Emitted blocks:**

```
@@FIN-CAT-AUDIT BEGIN
tree: regression-risk
severity: non-blocking
rule_or_schema_ref: NegativeAmountAsReimbursement at rules/expense-reimbursements.json
fixture_id: F-001
old_category: Expense:Misc
new_category: Income:Refund
classification: intended-by-plan
acceptance_criterion_ref: AC-7 (negative-amount + "reimbursement" description → Income:Refund)
evidence: description "airline reimbursement Q1", amount -142.50; rule condition (amount < 0 AND description contains "reimbursement") satisfied; AC-7 traces this change
recommendation: confirmed safe — intended-by-plan per AC-7
@@FIN-CAT-AUDIT END

@@FIN-CAT-AUDIT BEGIN
tree: regression-risk
severity: informational
rule_or_schema_ref: NegativeAmountAsReimbursement at rules/expense-reimbursements.json
fixture_id: F-002
old_category: Expense:Travel
new_category: Expense:Travel
classification: intended-by-plan
acceptance_criterion_ref: n/a — no category change
evidence: description "hotel stay — business trip", amount -310.00; rule condition not satisfied (no "reimbursement" substring); category unchanged
recommendation: confirmed safe — no regression
@@FIN-CAT-AUDIT END

@@FIN-CAT-AUDIT BEGIN
tree: regression-risk
severity: informational
rule_or_schema_ref: rules/expense-reimbursements.json (fixture file)
fixture_id: F-003
evidence: amount field absent — parse failure; fixture row skipped; cross-walk continued on F-002 and remaining rows
recommendation: fix fixture data (add amount field); re-run cross-walk after repair
@@FIN-CAT-AUDIT END
```

The malformed-fixture entry (F-003) is recorded as informational and does not abort the cross-walk. The intended-by-plan change (F-001) carries a populated `acceptance_criterion_ref` (AC-7). If a fourth fixture row produced `classification: unintended-regression` with `acceptance_criterion_ref: none — untraced`, that block would be a blocking finding.

## Anti-patterns

- **Paraphrasing standards text instead of emitting the PAUSE.** When the audit surface touches a chart-of-accounts standard, emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]` and stop. Do not interpret, summarize, or infer from training-data recollections of the standard's text. This violates CLAUDE.md §4 capability honesty.
- **Specializing the `<subject>` placeholder in this skill file.** The placeholder is intentionally generic. It is filled by the consuming agent at emit time, not hardcoded here.
- **Citing ADR-0024 as the PAUSE-routing authority.** ADR-0024 established the directional precedent for gap-naming with user-action remediation. ADR-0027 is the specific binding authority for the PAUSE shape. Citing ADR-0024 as the authority for PAUSE routing is incorrect.
- **Skipping the historical-fixture cross-walk on rule changes.** A rule change that is not cross-walked against the fixture set has unchecked regression risk. The cross-walk is mandatory for any rule modification, not optional.
- **Un-traced "intended-by-plan" classifications.** Every intended-by-plan classification requires a populated `acceptance_criterion_ref` tracing to a specific plan criterion. An empty or "n/a" ref on an intended-by-plan classification with a category change is a silent-regression risk.
- **Generic descriptor without rule name and file path.** Every `@@FIN-CAT-AUDIT` block for a rule-level finding must name the rule (e.g., `NegativeAmountAsReimbursement`) and its file path (e.g., `rules/expense-reimbursements.json`). A generic descriptor like "the updated rule" loses operation context and makes findings non-actionable.
- **Worked example that violates the safety mechanism it demonstrates.** The worked example in Decision tree 3 demonstrates the malformed-fixture try/catch pattern, the continue-not-abort behavior, and the PAUSE shape. If modifying this example, confirm it still demonstrates the safety mechanism (malformed-fixture skip and PAUSE emit), not a violation of it.
- **Loading for an implementer-shaped lane.** This skill encodes a reviewer-shaped procedure. Loading it in an agent that authors new categorization rules (implementer lane) is a scope mismatch — use `fin-statement-builder` for authoring.

## Output guidance

### Semantic guidance

- Never claim a rule change is regression-safe without completing the cross-walk against the historical-transaction fixture set.
- Never emit `classification: intended-by-plan` on a changed-category fixture row without a populated `acceptance_criterion_ref` tracing to a specific plan criterion.
- Never paraphrase or interpret a chart-of-accounts standard's text. Emit the ADR-0027 PAUSE shape and stop.
- Every `@@FIN-CAT-AUDIT` block for a rule-level finding must name the rule and its file path — not a generic descriptor. This preserves operation context for the remediation step.
- No accounting-software product names (no QuickBooks, Xero, Sage, NetSuite, or similar) in output. The technical surface is the rule files and schema under audit. Per ADR-0023 case-b.
- GAAP and IFRS may be named minimally and only when unavoidable (e.g., naming the standard being routed to research-docs-lookup). Do not reproduce standards text; emit the PAUSE shape instead.

### Tool guidance

- **Read** — view every rule file and schema file in scope before applying any decision tree (CLAUDE.md §4; do not analyze a file you have not read).
- **Grep** — scan for: category codes in schema files, parent field references, rule files' category code references, amount-sign conditions, description-match patterns.
- **No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.
- **No WebFetch or WebSearch** — standards-compliance questions emit the ADR-0027 PAUSE shape only; the orchestrator routes to `research-docs-lookup` or to the User until that agent ships (ADR-0027).

## When NOT to use this skill

- General code review with no categorization-rule diff → `dev-code-reviewer`.
- Designing or authoring new categorization rules → `fin-statement-builder` [scheduled-annotation: fin-statement-builder pending future session per agent-roster.md line 786; fin-statement-output matrix row at docs/specs/audit-pairing-matrix.md line 38].
- Statement output and report generation → `fin-statement-builder` [scheduled-annotation: fin-statement-builder pending future session per agent-roster.md line 786; fin-statement-output matrix row at docs/specs/audit-pairing-matrix.md line 38].
- Auditing M-language transforms → `m-language-discipline`.
- General pre-completion verification → `verification-before-completion` (load this skill alongside it for categorization-specific items, but `verification-before-completion` governs the overall procedure).
- Looking up a chart-of-accounts standard's authoritative text → emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]`; the orchestrator routes this to the User until `research-docs-lookup` ships (ADR-0027).
