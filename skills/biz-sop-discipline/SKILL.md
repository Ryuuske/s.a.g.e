---
name: biz-sop-discipline
description: "Use when writing or auditing SOP body content (docs/sops/<slug>.md): verifiable output, role ownership, decision-branch completeness, exception tracing, handler naming, escalation/recovery, audit-log compliance. Triggers on \"writing an SOP process step\", \"is this escalation concrete\". Do not use for categorization-rule auditing, M-language transforms, VBA macros, or general pre-completion verification."
---

# SOP Discipline

This skill encodes seven decision trees — process-step verifiable output, process-step role ownership, decision-branch completeness, exception-class tracing, exception handler naming, escalation path concreteness, and audit-log template compliance — plus banned-vague-fill enforcement and banned-software-register substitution that consuming agents apply at write time and review time to SOP body content at `docs/sops/<slug>.md`.

This skill is consumed by two agents in different modes:

- **biz-process-builder** (Phase D agent #4, methodology step 5, author-mode) — applies the decision trees to drive table-cell content when writing SOP sections; a BLOCKING verdict in any tree stops writing and surfaces the gap before continuing.
- **biz-process-reviewer** (Phase D agent #5, forward reference, audit-mode) — applies the same decision trees to flag violations in an existing SOP diff; a BLOCKING verdict is a blocking finding requiring remediation before APPROVE.

This dual-consumption pattern mirrors `m-language-discipline`, which is consumed by data-power-query-developer in both author-mode and audit-mode.

This skill co-loads with `verification-before-completion` for general pre-completion checks; it contributes SOP-specific verification items without replacing that skill's procedure. It does not overlap with `m-language-discipline` (M transforms), `vba-language-discipline` (VBA macros), or `fin-categorization-audit-discipline` (categorization-rule audits).

The seven decision trees are summarization-class per GuideBench classification: each tree is mechanical per-step pattern matching against a fixed checklist, or a banned-token grep, or graph traversal producing a deterministic verdict per diamond. This parallels `m-language-discipline`'s type-declaration-completeness tree.

**Cross-agent coupling note:** this skill cites `agents/biz-planner.md` at multiple sites — specifically the operating-context substitution table (lifecycle-verb pairs) for Tree 6 recovery-path vocab and the lifecycle-verb half of banned-software-register substitution, and the plan output template (Work-items table Role column; Escalation matrix Handler role column) for Tree 2 role-register and Tree 5 exception-handler role. These citations are version-coupled: if biz-planner.md is amended in any future session (substitution table moved or reorganised, plan output template columns renamed or reordered), this skill may silently carry stale references. Re-verify the citations in this skill body on any biz-planner.md amendment before dispatching biz-process-builder or biz-process-reviewer.

## When this skill binds

Fire this skill when any of these are true:

- You are writing a process step for a SOP body and must confirm it has a verifiable output.
- You are checking whether an exception handler names a specific role or uses a generic label.
- You are checking whether an escalation path is concrete (names next role + trigger condition) or vague ("escalate as appropriate", "see manager").
- A SOP step uses "use judgment" at the decision diamond — flag it.
- You are checking whether an audit-log entry shape is compliant with the standard template (timestamp + role + step number + decision + control reference).
- You are writing or reviewing the `docs/sops/<slug>.md` body per the approved plan.
- A SOP body uses banned-vague-fill tokens ("see manager", "the team", "as needed") — flag them.
- You are checking whether every decision diamond in the SOP leads to a named next step or terminal state.
- You are checking whether a recovery path reverses the exception using process-register vocabulary inherited from biz-planner.md's operating-context substitution table.
- You are checking whether an exception class traces to a plan-named exception class.

Do NOT fire this skill for:

- Writing a new categorization rule → `fin-statement-builder`.
- Reviewing categorization rules in a diff → `fin-categorization-audit-discipline`.
- Writing an M transform for the SOP's data step → `m-language-discipline`.
- Writing the VBA macro the SOP references → `vba-language-discipline`.
- Designing the agent that will write SOPs → `agent-creation` via `aidev-agent-creator`.
- Checking whether a SOP plan is well-scoped → `biz-planner` [scheduled-annotation: biz-planner defined at docs/reference/agent-roster.md line 812; no matrix row required — biz-planner output is the plan artifact at .development/plans/active.md, plan files are not auditor-paired].
- General pre-completion verification on the SOP → `verification-before-completion` (load `biz-sop-discipline` alongside; `verification-before-completion` governs the overall procedure).
- Looking up the current ISO 9001 / ITIL / COBIT or any external audit-framework reference clause text → emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]` and stop. (See "When this skill PAUSEs" below.)

## Decision tree 1 — Process-step verifiable output

Every process step must name a concrete, verifiable output artifact. A step that "reviews" or "checks" without naming what artifact the check produces is incomplete.

**Audit procedure (author-mode and audit-mode):**

1. Read the full `docs/sops/<slug>.md` and the approved plan before applying any check.
2. For each process step, identify whether a verifiable output artifact is named (a document, a record, a system entry, a logged decision).
3. A step with no named output artifact is a finding: "verifiable output absent — name the artifact."
4. Emit `@@SOP-STEP-AUDIT BEGIN` block (one row per step).

**Author-mode rule:** every step names its output artifact before the step cell is considered complete.

## Decision tree 2 — Process-step role ownership

Every process step must assign ownership to a specific named role. Generic labels ("the team", "staff", "someone") obscure accountability and make the SOP unauditable.

**Audit procedure:**

1. For each process step, identify the role-owner field.
2. A role-owner that is generic (not a named role in the process register) is a finding: "generic role label — name the specific role".
3. Emit row in `@@SOP-STEP-AUDIT BEGIN` block.

**Author-mode rule:** role owners are drawn from the plan's Work-items table Role column (per biz-planner.md output template — the "Role" column in the work-items table names the specific business role responsible for each process step) and from the plan's Escalation matrix Handler role column (per biz-planner.md output template — the "Handler role" column in the escalation matrix names the role responsible for each exception class). These are the on-disk locations where biz-planner writes role assignments for the SOP's process steps and exception handlers. If a required role is not present in either the Work-items table Role column or the Escalation matrix Handler role column of the approved plan, PAUSE and surface the gap to the orchestrator before writing. Do not reference the biz-planner.md operating-context substitution table for role-register lookup — that table contains process-lifecycle verb pairs only (no role names).

## Decision tree 3 — Decision-branch completeness

Every decision diamond in the SOP must lead to a named next step or a named terminal state. A diamond leading to "use judgment", an unnamed state, or a missing branch is a BLOCKING finding in both modes.

**Audit procedure:**

1. Grep the SOP body for decision-diamond markers: "Decision:", "If/Then" or "if X then Y" patterns, "When X then Y", "Yes:/No:" labels, "Condition:", "Branch:", "Approve/Reject" terminal labels, numbered-condition rows (e.g., "1. If approved...", "2. If rejected..."), mermaid flowchart syntax (` ```mermaid `), natural-language if-else sentences, and table-formatted decision columns.
2. For each diamond, enumerate all named outcome branches.
3. Confirm each branch terminates at either a named next step (by step number or label) or an explicit terminal state ("process ends", "escalate to <named-role>", "reject with notification to <named-role>").
4. A branch leading to "use judgment", an unnamed state, or a missing next-step reference is a BLOCKING finding: "decision diamond incomplete — branch <X> has no named next step or terminal state."
5. Emit `@@SOP-STEP-AUDIT BEGIN` block row.

**Blocking rule:** "use judgment" at any decision diamond is always BLOCKING, in both author-mode and audit-mode.

## Decision tree 4 — Exception-class tracing

Every exception class named in the SOP body must trace to a plan-named exception class. Exceptions introduced in the SOP body that were not anticipated in the plan represent scope drift.

**Audit procedure:**

1. Read the approved plan's escalation matrix (the "Escalation matrix" section — per biz-planner.md output template). The first column ("Exception class") of that table is the authoritative exception-class list.
2. For each exception class in the SOP body, confirm it appears in the plan's escalation matrix first column by name or by direct derivation from a named row.
3. An exception class that cannot be traced to the plan is a finding: "untraced exception class — not in plan-named exception classes."
4. Emit `@@SOP-EXCEPTION-AUDIT BEGIN` block (one row per exception class).

## Decision tree 5 — Exception handler naming

Every exception handler must name a specific role. Generic escalation targets ("see manager", "escalate as appropriate") are BLOCKING in both modes.

**Audit procedure:**

1. For each exception class, identify the handler role in the exception section of the SOP.
2. A handler role that is generic, absent, or uses a banned-vague-fill token is a BLOCKING finding: "generic exception handler — name the specific role."
3. Emit `@@SOP-EXCEPTION-AUDIT BEGIN` block row.

## Decision tree 6 — Escalation path and recovery path concreteness

Every escalation path must name: (a) the specific next role to escalate to, and (b) the trigger condition that initiates escalation. Every recovery path must use process-register vocabulary inherited from biz-planner.md's operating-context substitution table (the forbidden→required register pairs) to describe how the exception is reversed.

**Audit procedure:**

1. For each exception class, identify the escalation path (next role + trigger condition).
2. An escalation path that uses banned-vague-fill tokens ("as appropriate", "as needed", "if necessary") or omits the trigger condition is a BLOCKING finding.
3. For each exception class, identify the recovery path. Confirm it references process-register vocabulary inherited from biz-planner.md's operating-context substitution table (the required-column terms: "publish", "roll out", "reverse the rollout", "rescind").
4. A recovery path that uses generic language not drawn from biz-planner.md's process-register substitution table is a finding: "recovery path lacks process-register vocabulary."
5. Emit `@@SOP-EXCEPTION-AUDIT BEGIN` block rows.

**Blocking rule:** "escalate as appropriate" is always BLOCKING. "see manager" is always BLOCKING.

## Decision tree 7 — Audit-log template compliance

For every compliance-required process step, the audit-log entry shape must carry all five template fields: timestamp, role, step number, decision, and control reference. A missing field is a finding.

**Audit procedure:**

1. Grep the SOP for compliance-required steps (steps flagged as audit-log-required in the plan or in the SOP's compliance-controls section).
2. For each such step, confirm the audit-log entry template includes: timestamp, role, step number, decision, and control reference.
3. A missing field is a finding: "audit-log entry missing field <field> at step <N>."
4. Emit `@@SOP-AUDIT-LOG-AUDIT BEGIN` block (one row per compliance-required step).

## Banned-vague-fill enforcement

The following tokens are banned from all SOP body content. Detection is grep-shaped: scan the full SOP body for each token listed, case-insensitive.

Banned tokens: "the team", "see manager", "as needed", "use judgment", "as appropriate", "escalate as appropriate", "if necessary", "where applicable", "etc.", "TBD", "various", "multiple", "stakeholders", "appropriate party", "relevant party", "designated person", "responsible party", "qualified personnel".

Each hit is a finding surfaced inline (one line per hit): `banned-vague-fill: "<token>" at step <N> — replace with specific role, artifact, or condition.`

"use judgment" and "escalate as appropriate" and "see manager" at decision points are BLOCKING (per decision trees 3 and 5/6). Other banned-vague-fill hits are findings but not automatically BLOCKING unless they appear at a decision diamond or exception handler.

## Banned-software-register substitution

The SOP body must not name specific software products, employer names, client names, or internal tool names. Per ADR-0023 case-b, incidental product references in non-integration artifacts are banned.

This register covers two distinct ban scopes with two distinct authorities:

**Product-name ban** (primary authority: `rules/ai-dev-conventions.md` identifying-info ban, case-b per ADR-0023): the ban states "no agent file names a specific employer, client, project, software product, colleague, or internal convention" — the product-name aspect applies equally to SOP body content per ADR-0023 case-b. Software product names (Jira, Salesforce, SharePoint, Notion, or any named product), employer names, client names, and internal tool names are banned. When a product name appears in the SOP body, surface inline: `banned-software-register: "<token>" at step <N> — substitute with a generic process-register term that describes the function (e.g., "the issue-tracking system", "the CRM", "the collaboration platform").`

**Lifecycle-verb substitution** (authority: biz-planner.md operating-context substitution table, lines 40–49): the forbidden-column terms (release the SOP, deploy the process, ship the runbook, rollback the process, rollback the SOP) must be replaced with the required-column terms (publish, roll out, reverse the rollout, rescind). When a lifecycle-verb violation appears in the SOP body, surface inline: `banned-software-register: "<token>" at step <N> — substitute with the required process-register term per biz-planner.md operating-context substitution table.`

If neither authority names a substitute for the banned token, PAUSE and surface the gap to the orchestrator before writing.

### When this skill PAUSEs

When the SOP body references a framework standard clause (ISO 9001, ITIL, COBIT, or any external audit-framework reference), emit:

```
PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

Stop there. Do not paraphrase, interpret, or reason from training-data recollections of the standard's text. This violates CLAUDE.md §4 capability honesty. The `<subject>` placeholder is filled by the consuming agent with the standard name and specific clause; it must not be specialized in this skill file.

The PAUSE shape above is the ADR-0027 pattern. `research-docs-lookup` does not yet exist in the active roster. When the PAUSE fires, the orchestrator's established convention routes it to the User: the named agent's manifest absence triggers User-escalation fallback per the orchestrator's general dispatch behavior on unresolved agent names. ADR-0027 is the binding authority for this routing shape. ADR-0024 established the directional precedent for the gap-naming-with-user-action-remediation pattern; ADR-0027 is the specific authority. When `research-docs-lookup` lands per agent-roster.md step 13, the scheduled-annotation resolves and the PAUSE routes directly to that agent.

## Output blocks

The consuming agent emits three structured block types. All blocks use the delimiter pattern established across the agent roster.

**Process-step audit (Decision trees 1, 2, 3):**
```
@@SOP-STEP-AUDIT BEGIN
step_number | role_owner (specific role | generic — flag) | verifiable_output (named artifact | absent — flag) | next_step_or_terminal (named | "use judgment" — BLOCKING) | banned_vague_fill_hits (count + tokens) | finding
@@SOP-STEP-AUDIT END
```

**Exception audit (Decision trees 4, 5, 6):**
```
@@SOP-EXCEPTION-AUDIT BEGIN
exception_class (plan-traced ref | untraced — flag) | handler_role (specific | generic — flag) | escalation_path (specific next-role + trigger condition | "escalate as appropriate" — BLOCKING) | recovery_path (concrete reversal steps + process-register vocab | absent — flag) | finding
@@SOP-EXCEPTION-AUDIT END
```

**Audit-log compliance audit (Decision tree 7):**
```
@@SOP-AUDIT-LOG-AUDIT BEGIN
step_number | template_fields_present (timestamp | role | step_number | decision | control_reference) | missing_fields | finding
@@SOP-AUDIT-LOG-AUDIT END
```

Banned-vague-fill findings surface inline (one line per finding). Banned-software-register substitution findings surface inline (one line per finding). Decision-branch-completeness findings on diamonds leading to "use judgment" or unnamed states are BLOCKING and must appear as their own row in `@@SOP-STEP-AUDIT`. Framework-standard-clause lookup requests surface as the ADR-0027 PAUSE line — no structured block is produced for that routing.

## Anti-patterns

- **Permitting "use judgment" at a decision diamond.** This is always BLOCKING. The consuming agent must not write or accept a decision diamond that routes to "use judgment."
- **Permitting "see manager" or "escalate as appropriate" as exception handler or escalation path.** Always BLOCKING. Name the specific role and the trigger condition.
- **Generic role labels in process-step ownership.** "The team", "staff", "someone" obscure accountability. Name the specific role from the plan's process register.
- **Process steps without a verifiable output artifact.** A step that "reviews" or "confirms" without naming the artifact produced is incomplete.
- **Exception classes that don't trace to plan-named exception classes.** Untraced exceptions represent scope drift. Trace every exception class to the plan's escalation matrix first column or PAUSE.
- **Audit-log entries missing template fields.** All five fields (timestamp, role, step number, decision, control reference) are mandatory for compliance-required steps.
- **Banned-software product names in the SOP body.** Per ADR-0023 case-b, incidental product references in non-integration SOP artifacts are banned. Product-name ban authority: `rules/ai-dev-conventions.md` identifying-info ban. Lifecycle-verb substitution authority: biz-planner.md operating-context substitution table (lines 40–49). Two distinct scopes; do not conflate.
- **Recovery paths without process-register vocabulary.** A recovery path that does not use process-register vocabulary from biz-planner.md's operating-context substitution table cannot be verified as reversing the exception within the process.
- **Loading this skill for non-SOP work.** The skill's surface is `docs/sops/<slug>.md` body content. Loading it for general document review, code review, or data-pipeline work is a scope mismatch.
- **Paraphrasing ISO 9001 / ITIL / COBIT or any external audit-framework reference clause text from training data.** Violates CLAUDE.md §4 capability honesty. Emit the ADR-0027 PAUSE shape instead.

## Output guidance

### Semantic guidance

- Never claim a SOP step is complete without: (1) a named verifiable output artifact, (2) a specific role owner, and (3) a named next step or terminal state.
- Never let an exception handler or escalation path carry a banned-vague-fill token. BLOCKING findings must surface before any further writing in author-mode, and before APPROVE in audit-mode.
- Every exception class must trace to a plan-named exception class. Untraced classes surface as findings before the SOP section is considered complete.
- Every escalation path names concrete next-role plus trigger condition. "Escalate as appropriate" is never acceptable.
- Every recovery path uses process-register vocabulary from biz-planner.md's operating-context substitution table.
- Every audit-log entry on a compliance-required step carries all five template fields.
- Decision tree verdict is identical in author-mode and audit-mode; only the consuming agent's action differs (author-mode stops and corrects; audit-mode emits a finding and flags for remediation).
- No employer, client, software product, or internal convention names in SOP body output. Per ADR-0023 case-b.

### Tool guidance

- **Read** — view `docs/sops/<slug>.md` in full; the approved plan (for the Work-items table Role column = role-register authority for tree 2; the Escalation matrix Handler role column = exception-handler role authority for trees 2 and 5; the Escalation matrix first column = exception-class authority for tree 4); and `agents/biz-planner.md` operating-context substitution table (lifecycle-verb pairs = authority for tree 6 recovery-path vocab and the lifecycle-verb half of banned-software-register substitution). Product-name ban authority for banned-software-register is `rules/ai-dev-conventions.md` identifying-info ban — read that rule when a suspected product name appears. Per CLAUDE.md §4; do not analyze a file you have not read.
- **Grep** — scan for banned-vague-fill tokens ("the team", "see manager", "as needed", "use judgment", "as appropriate", "escalate as appropriate", "if necessary", "where applicable", "etc.", "TBD", "various", "multiple", "stakeholders", "appropriate party", "relevant party", "designated person", "responsible party", "qualified personnel"); banned-software-register tokens: lifecycle-verb violations (from biz-planner.md operating-context substitution table "Forbidden" column: "release", "deploy", "ship", "rollback" when applied to process artifacts) and product-name violations (named software products, employer names, client names per `rules/ai-dev-conventions.md` identifying-info ban); and decision-diamond markers.
- **Glob** — locate `docs/sops/<slug>.md` when the brief names an area without an exact path.
- **No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.
- **No WebFetch or WebSearch** — framework-standard clause lookups emit the ADR-0027 PAUSE shape only; the orchestrator routes to `research-docs-lookup` or to the User until that agent ships.

## When NOT to use this skill

- Writing a new categorization rule → `fin-statement-builder`.
- Reviewing categorization rules in a diff → `fin-categorization-audit-discipline`.
- Writing an M transform for the SOP's data step → `m-language-discipline`.
- Writing the VBA macro the SOP references → `vba-language-discipline`.
- Designing the agent that writes SOPs → `agent-creation` via `aidev-agent-creator`.
- General pre-completion verification → `verification-before-completion` (load `biz-sop-discipline` alongside it for the SOP-specific items, but `verification-before-completion` governs the overall procedure).
- Looking up a framework standard's authoritative clause text → emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]`; the orchestrator routes this to the User until `research-docs-lookup` ships (ADR-0027).
- Any document that is not a SOP body at `docs/sops/<slug>.md`.
