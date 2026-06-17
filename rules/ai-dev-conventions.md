---
paths:
  - "**/agents/**"
  - "**/skills/**"
  - "**/hooks/**"
  - "**/statusline/**"
  - "**/claude-md/**"
  - "**/docs/specs/**"
  - "**/.development/agents/**"
  - "**/.development/decisions/**"
  - "**/installer*.sh"
  - "**/installer-assets/**"
---

# AI-dev work conventions

These conventions apply when working on agents, skills, framework files, hooks, or plugin definitions for any AI coding tool. For lifecycle entry points use `aidev-visionary` and `aidev-planner` per CLAUDE.md §9 (Session lifecycle — mode-classification and intake dispatch).

## Agent file structure

Every agent file follows the canonical structure encoded in the `agent-creation` skill: frontmatter (with manifest if `aidev-*`), Charter, Operating context, When invoked, Methodology, Output format, Constraints, Anti-patterns, When NOT to use this agent, Output discipline. Reordering is an ADR-grade decision.

## CoT injection classification

CoT marked `Yes` only when the agent's primary work is logic-heavy per GuideBench classification — severity scoring, dependency derivation, classification under conflicting rules, exploit-chain inference, type-flow inference, root-cause inference, bug-class detection. CoT marked `No` for execution, mediation, drift detection, template assembly, visual matching, lookup. For `CoT: Yes` agents, the methodology specifies the injection point — "use CoT throughout" is unenforceable.

## AGENTIF constraint types

Every agent has all three constraint types filled: formatting (machine-parseable output contract), semantic (language style and content rules), tool (schemas for tool invocations). Empty columns are blocking findings for state auditors.

## Universal Agent Constraints

Implementer-shaped agents inherit `IMPLEMENTER_DISCIPLINE` (four rules: pause when ambiguous, minimum code only, match existing style, clean only your own orphans). Reviewer-shaped agents add the overengineering check angle (`REVIEWER_DISCIPLINE`). Both live in `docs/specs/universal-agent-constraints.md`; propagation across the roster is `aidev-agent-creator.propagate-anti-patterns`.

## Identifying info ban

Agent files are generic and shareable. No agent file names a specific employer, client, project, software product, colleague, or internal convention. Runtime context (employer name, software in use, color schemes, etc.) is passed via brief from memory, never encoded in the file. If a future agent's description starts naming specific clients or products, that's a regression visible to `aidev-state-reviewer`.

**Case-a (identity-intrinsic) exemption.** An agent whose lane *is* a named integration — its tool grants are bound to the named product's MCP server or equivalent integration surface — may carry that product name in its file. The exemption is narrow and per-agent: each case-a claim requires its own ADR. The canonical precedent is ADR-0023 (`aidev-keeper` exempted on S.A.G.E. integration grounds). Incidental product references in non-integration agents (case-b) remain banned.

## Audit pairings

The audit-pairing matrix at `docs/specs/audit-pairing-matrix.md` is the single source of truth for which auditors pair on what work. AI-dev diff changes route to `aidev-code-reviewer` + a Codex adversarial pass (`/codex:adversarial-review`; fallback `aidev-adversarial-auditor` when Codex is unavailable — ADR-0123) (parallel). AI-dev state audits (no diff) route to `aidev-state-reviewer` + a Codex adversarial pass (`/codex:adversarial-review`; fallback `aidev-state-adversarial-auditor` — ADR-0123) (parallel). Roster-wide governance updates use the `propagation-batch` row with the two-phase audit protocol — state reviewer + the Codex adversarial pass audit the batch as a whole first, then `aidev-code-reviewer` audits each embedded modification.

## CRUD flow

All agent and skill CRUD operations go through `aidev-agent-creator` or `aidev-skill-creator`. The orchestrator mediates cross-creator handoff when an agent design surfaces `missing_skills_needed`. Implementation flows through `aidev-code-implementer` per the spec the creator produces — creators never write files themselves.

## ADR discipline

Reordering sections, adding new manifest fields, adding new auditor pairings, or any one-way governance change requires an ADR at `.development/decisions/NNNN-slug.md`. ADRs are append-only — supersede, never edit. ADR-grade decisions for AI-dev work include: section reordering in agent files; new manifest field additions; new audit-pairing matrix rows; family additions; lane statement changes on existing agents.
