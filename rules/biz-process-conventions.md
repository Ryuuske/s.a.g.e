---
paths:
  - "**/docs/sops/**"
  - "**/docs/runbooks/**"
  - "**/docs/processes/**"
  - "**/sops/**"
  - "**/runbooks/**"
---

# Business-ops work conventions

These conventions apply when working on SOPs, runbooks, process designs, workflow documents, or rollout planning. For lifecycle entry points use `biz-visionary` and `biz-planner` per CLAUDE.md §9 (Session lifecycle — mode-classification and intake dispatch).

This family is **distinct from the `ops-` family**, which handles deployment operations. `ops-` is software release and deployment; `biz-` is business process and SOP design. Both can coexist on a project — a software release SOP would frame/plan via `biz-*` and execute via `ops-*`.

## Role as a first-class dimension

Every process step names an executing role. Every decision point names who decides. Every exception names who handles it. Every escalation names a specific role, not "your manager." Plans without per-step role assignments are blocked at User approval.

## Step output verifiable

Every step has a verifiable output — a specific deliverable, signal, or state change that proves the step completed. "Review the invoice" is not a step; "Compare invoice line items against PO and mark each as match / mismatch / clarify" is. `biz-process-reviewer` flags steps without verifiable output as severity 80+ findings.

## Every decision has a named path

No SOP step says "use judgment" at a decision point. Every branch leads to a specific next step. If judgment is genuinely required, the SOP names the criteria for that judgment and the escalation path if the criteria don't resolve.

## Every exception has a handler

Exception classes are enumerated per step. Each exception names: the handler role, the recovery path, and the escalation if the handler is unavailable. `biz-process-reviewer` flags exceptions without handlers as severity 90+ findings; missing escalation paths as severity 85+.

## SOP file structure

Canonical section order: Purpose, Scope, Roles, Process steps (numbered hierarchically: 1.1, 1.2, 1.2.1; with step/role/decision/control/output columns), Exception handlers, Audit log template, Revision history. Live at `<repo>/docs/sops/<slug>.md`. Paired training material at `<slug>-training.md` and checklist at `<slug>-checklist.md` when specified in the plan.

## Audit pairings

`biz-process-builder` produces SOP → `biz-process-reviewer` + `doc-keeper` (parallel per matrix row `biz-sop-output`). The two-auditor pair catches different failure classes: `biz-process-reviewer` audits completeness / measurability / operability; `doc-keeper` audits format / drift / citations.

## Rollout is part of the plan

`biz-planner` plans include rollout: who is trained before whom, what controls must be in place before rollout, comms plan via `doc-internal-comms`, audit log template ready. Plans that don't include rollout are blocked at User approval — process design without rollout is design that never executes.

## Compliance and audit-point visibility

Every SOP names which compliance requirements it satisfies (if any) and where the audit log lives. Processes that touch regulated domains (financial reporting, data handling, vendor contracts) name the specific regulation and the audit-point per step. `biz-visionary` surfaces compliance requirements in the Think phase; missing compliance signals are a finding at framing time, not at review time.

## Operability check

A new person who didn't write the SOP must be able to follow it without implicit knowledge. `biz-process-reviewer` runs this check: read each step, ask "could someone with the named role complete this step from just the text?". Implicit knowledge ("you'll know which form to use") is a finding.

## Automation as a separate Build artifact

If a process step is going to be automated (a script, a workflow tool, a scheduled job), the automation lives as a separate Build artifact — usually in the `software-dev` lifecycle. Don't conflate "process design" with "process automation." The SOP describes what the process is regardless of who or what executes each step.
