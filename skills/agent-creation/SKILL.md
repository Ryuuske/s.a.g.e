---
name: agent-creation
description: Use when creating, modifying, deleting, or propagating-updating an agent definition. Triggers on "design a new agent", "modify this agent", "propagate the new anti-patterns", "what should this agent look like", "is this agent well-structured". Do not use for skill design (`skill-creation`), for catalog/registry decisions (`aidev-agent-manager`), or to actually write the agent file (`aidev-code-implementer`).
---

# Agent Creation Skill

This skill encodes the framework's best practices for agent definitions. The primary consumer is `aidev-agent-creator`. The skill is the *playbook*; the creator is the *executor*.

For skill (SKILL.md file) design, consult the `skill-creation` skill instead.

## Canonical agent structure

Every agent file uses this exact section order. Reordering is an ADR-grade decision:

1. **Frontmatter** (YAML) — `name`, `description`, `tools`, `model`, plus the `aidev-*` manifest fields where applicable.
2. **Charter** (top-level `# Name`) — one paragraph stating the agent's lane.
3. **Operating context** — what the agent reads before working; what background it inherits.
4. **When invoked** — trigger conditions (3–5 concrete shapes).
5. **Methodology** — the steps the agent works through.
6. **Output format** — the structured output the orchestrator receives.
7. **Constraints** — what the agent will not do.
8. **Anti-patterns** — what to flag as failure modes for this lane (≥3).
9. **When NOT to use this agent** — refused adjacent lanes (≥2), each with the correct alternative agent.
10. **Output discipline** — caveman compression rules; `@@VERDICT BEGIN…END` block schema if applicable.

## The frontmatter manifest

For any `aidev-*` agent, the frontmatter MUST include the manifest block:

```yaml
required_inputs:
  - <input 1 with constraint, e.g., "path to plan, file must exist">
  - <input 2>
forbidden_inputs:
  - <thing that would corrupt the agent's lane, with rationale>
briefing_template: "<one-line template with <placeholders>>"
```

Every `forbidden_input` has a one-line rationale starting with `# why:` above the list. Every `<placeholder>` in `briefing_template` must map to a `required_inputs` entry. No orphaned placeholders.

## CoT injection rules (from GuideBench)

Decide whether the agent needs Chain-of-Thought by classifying its primary work into one of these categories:

### CoT pays (mark Yes, identify injection point)

- **Severity scoring** — assigning a 0–100 score to a finding. The chain "trigger → impact → severity" must be written before the score.
- **Dependency derivation** — sequencing tasks, ordering work items, determining parallel-safe vs sequential. The chain "what this touches → what other items touch the same → derived order".
- **Classification under conflicting rules** — categorizing transactions, mapping to schemas, applying domain rules that conflict with commonsense. The chain "attributes → applicable rules → tie-break → final category".
- **Exploit-chain inference** — security review where reachability matters. The chain "source → path → sink → impact".
- **Type-flow inference** — TypeScript type narrowing, Python typing semantics. The chain "type at N → flow → narrowing event → soundness".
- **Root-cause inference** — build error resolution, debugging. The chain "symptom → stage → likely root → fix candidate".
- **Bug-class detection** — language-specific footguns (Python late binding, VBA single-cell array). The chain "construct → semantics → failure scenario".
- **Tradeoff analysis** — architectural decisions, semver bump decisions, semantic class disambiguation. The chain "alternatives → constraint per alternative → dominator analysis → recommendation".

### CoT doesn't pay (mark No)

- **Execution** — write the file, run the command, commit the change. Adding CoT slows without benefit.
- **Mediation** — keeper-style operations that just shuttle data. CoT corrupts the "verbatim in, verbatim out" rule.
- **Drift detection** — checking that doc claims match code. Pattern matching, not chain reasoning.
- **Template assembly** — scaffolding repos, generating standard files. Mechanical work.
- **Visual/structural matching** — UI fidelity audit, design-system compliance. Pattern recognition.
- **Lookup** — fetching docs, reading rate pages. Mechanical retrieval.

The +23-point empirical lift from GuideBench (math task accuracy: 65.4% with CoT vs 42.3% without) applies to logic-heavy work. Summarization tasks showed ~0-point difference. Match the agent to the right side of this split.

### The injection point

Where in the methodology should the CoT chain be required? Be specific. Not "use CoT throughout" — that's unenforceable. Examples:

- "Before any score ≥80, require a 2-line chain: trigger → impact → severity rationale."
- "Before the work-items table, require a shared-resource pass listing which files each item touches."
- "Per transaction, chain attributes → applicable rule → final category before stating the category."

The injection point is what the auditor checks when reviewing the agent's output.

## AGENTIF constraint types

Every agent has all three constraint types filled in. Match the agent to canonical patterns:

### Formatting constraints

The machine-parseable contract of the agent's output. Examples:

- `@@VERDICT BEGIN…END` strict block with required fields (severity, file, line, category, summary).
- Per-finding table shape (ID, Issue, Angle, Score, Blocking).
- Canonical section order in the agent's output.
- Required fields in a structured response block.

Rule: if the orchestrator needs to parse it programmatically, the format is a constraint. Spell it out.

### Semantic constraints

Style, register, language rules that humans can check but machines can't easily parse. Examples:

- "No hedge language (`might`, `could potentially`, `seems like`) in audit output."
- "Refuse analysis requests; return only structured payloads."
- "Always cite the source URL with fetch timestamp."
- "Hard ban on hedge language for adversarial roles."
- "≤15-word quotes per source; paraphrase the rest."
- "Match the active project's voice — read recent examples for calibration."

Rule: semantic constraints are about *what kind of language* the agent uses, not what data it returns.

### Tool constraints

Schemas for tool calls — which tools the agent uses, with what parameter format, against what targets. This is the weakest dimension in most rosters and the highest-leverage to formalize. Examples:

- "Bash schema bounded to `git`, `gh`, `pytest`, `node scripts/*`."
- "WebFetch domain-bounded to `docs.claude.com`, `support.claude.com`."
- "Write surface bounded to `<repo>/.development/audits/<YYYY-MM-DD>-<scope>-<agent>-<round>.md`."
- "Per-operation parameter schema: `nook_search(query, wing, limit≤20, agents?)`."
- "One fetch per invocation; chain via orchestrator re-invocation, not internal loops."

Rule: every granted tool needs either a named methodology step that uses it OR an explicit schema constraint on its invocation. No "Bash" without justification.

## Lane discipline rules

Every agent file declares:

- **Lane statement** (one sentence in the charter). If you can't say it in one sentence, the lane is too broad — split into two agents.
- **Refused adjacent lanes (≥2)** in the "When NOT to use" section. Each refused lane names the correct alternative agent. Missing or empty refused-lanes is a blocking finding.
- **Tool grants minimum-viable.** Every granted tool must be used in the methodology. Unused grants are tool-grant creep.
- **Model justified.** `opus` for reasoning-heavy advisory work; `sonnet` for execution-heavy work. Mismatch needs justification.

## Anti-patterns (flag during design)

The list below is partitioned by which agent shape they apply to. `aidev-agent-creator.propagate-anti-patterns` uses these same partitions to audit the existing roster when this checklist changes.

### Universal (every agent)

- **Lane bleed** — lane statement contains "and also". Split or drop the "and also".
- **Refused lanes as afterthought** — if you can't name two adjacent lanes this agent rejects, you haven't understood the lane.
- **Tool-grant creep** — granting Bash because "it might be useful". Justify or refuse.
- **Frontmatter divergence** — inventing new manifest fields. Match house style or propose an ADR.
- **Skipping output discipline** — the caveman compression section is mandatory on every agent.
- **CoT applied to execution agents** — slows the agent for ~0 benefit.
- **Vague injection points** — "use CoT throughout" instead of a specific methodology step.
- **Tool constraints in prose only** — describing the tool's use in English instead of as a schema. AGENTIF research shows this is the weakest dimension across rosters; formalize it.
- **Identifying info in agent descriptions** — employer, client, project, software, or internal convention names belong in runtime memory, not the agent file. See the design principle in `agent-roster.md`.

### Implementer-shaped (write / produce / build / generate / execute / scaffold)

- **Missing "pause when ambiguous" rule.** Silent assumption-making is the most expensive failure mode. The agent must include a semantic constraint requiring a `PAUSE: orchestrator must clarify <question>` when the brief is ambiguous, rather than picking an interpretation.
- **Missing "minimum code only" rule.** Every abstraction, configuration option, and error handler must trace to an acceptance criterion or named risk. Speculative additions are bugs that pretend to be features.
- **Missing "match existing style" rule.** Style critique is the dev-architect's and reviewer's lane, not the implementer's. The agent must include a semantic constraint requiring style consistency with the existing codebase.
- **Missing "clean only your own orphans" rule.** Cleanup scope is strictly the imports / variables / functions that this agent's changes orphaned. Pre-existing dead code is `dev-refactor-cleaner`'s lane.

### Reviewer-shaped (review / audit / check / validate / assess)

- **Missing overengineering check angle.** Every new abstraction, configuration option, or error handler in the diff must be checked for a traceable justification (acceptance criterion or named risk). Untraced ones become findings; severity is calibrated to magnitude per the `REVIEWER_DISCIPLINE` block in `agent-roster.md`.

### Framer-shaped (frame / plan / design / sharpen)

- **Skipping the acceptance criteria pass.** Plans and visions must produce 3–5 testable acceptance criteria. Vague success conditions ("make it work") force the downstream chain to guess.

These shape-specific anti-patterns are the operational text of the IMPLEMENTER_DISCIPLINE / REVIEWER_DISCIPLINE blocks in `agent-roster.md`. When this list changes, `aidev-agent-creator.propagate-anti-patterns` is the mechanism that brings existing agents into compliance.

## Skill-assignment pass (mandatory for create-agent)

Every new agent's design must include an explicit skill-assignment pass. The pass connects the agent's methodology to the existing skill set, identifying both reuse opportunities and gaps.

Procedure:

1. **Read all existing skill frontmatter.** Glob `<repo>/skills/*/SKILL.md` (or `~/.claude/skills/*/SKILL.md` fallback). Read each file's frontmatter `name` and `description`.
2. **Walk the new agent's methodology.** For each methodology step, ask: does this step encode a procedure that would benefit from being in a skill — either because it's reusable across multiple agents or because the step requires specific discipline (TDD, systematic debugging, verification before completion, etc.)?
3. **For each beneficial step**, compare against existing skills:
   - **Match exists** → assign the skill in `existing_skills_assigned` with the methodology step it supports. The new agent's file will reference the skill the same way other agents do.
   - **No match exists, but a skill would help** → add an entry to `missing_skills_needed` with:
     - `proposed_name`: a slug for the new skill.
     - `purpose`: what the skill would encode.
     - `triggers`: 3+ concrete phrasings the consuming agent should see.
     - `consumed_at`: which methodology step in the new agent uses it.
     - `rationale`: why no existing skill covers this need.
   - **Step is agent-specific** (no reuse value across agents, no special discipline needed) → leave the procedure inline in the agent's methodology, no skill assignment.
4. **Do not invent skills.** Flag the need; skill creation is `aidev-skill-creator`'s job.
5. **The pass must conclude explicitly.** Even if no skills apply, the pass must run and the spec must show `existing_skills_assigned: []` and `missing_skills_needed: []` — the empty arrays are the signal that the pass ran and found nothing.

When the agent-creator returns with non-empty `missing_skills_needed`, the orchestrator must dispatch `aidev-skill-creator` for each missing skill (parallel-safe) before dispatching `aidev-code-implementer` for the agent. The `implementation_blocked_until` field in the spec tells the orchestrator which skill slugs to wait on.

## Cross-creator handoff protocol

`aidev-agent-creator` and `aidev-skill-creator` never call each other directly. The orchestrator mediates:

1. User → orchestrator: "create an agent for X"
2. Orchestrator → `aidev-agent-creator` (operation: create-agent)
3. Agent-creator: designs the agent, runs skill-assignment pass, returns spec with `missing_skills_needed`
4. Orchestrator: for each entry in `missing_skills_needed`, dispatch `aidev-skill-creator` (operation: create-skill) in parallel
5. Skill-creator: designs each missing skill, returns specs
6. Orchestrator: dispatches `aidev-code-implementer` to write the skills first (parallel where independent), then the agent
7. Audit chain per the audit-pairing matrix

This keeps each creator's lane clean and the orchestrator owns sequencing.

When the operation is MODIFY (not CREATE):

1. Read the existing agent file in full before proposing changes.
2. Identify what's actually changing — lane statement, refused lanes, methodology, tool grants, output format, constraints. Be specific.
3. **Re-run the skill-assignment pass** for any methodology steps that are added, removed, or substantially changed. Removed methodology steps may free up existing skill references (record in `skill_changes.removed_assignments`); new methodology steps may need existing skill assignments (`skill_changes.added_assignments`) or new skills (`skill_changes.new_skills_needed`).
4. Check for knock-on effects: does this modification require a manifest field change (frontmatter), an audit-pairing matrix row update, or an ADR?
5. Produce a diff-style proposal: "current → proposed" per section.

## Delete-agent additional rules

When the operation is DELETE:

1. Scan all references to the agent across the framework: other agents (refused-lane pointers, methodology references), skills, ADRs, the catalog, the active rosters in any project, the audit-pairing matrix.
2. Produce a dependency report: every place that needs to be updated alongside the deletion.
3. Propose a removal plan: order of operations (update referring files first, then remove the agent file, then remove from catalog).
4. Flag whether the deletion is reversible (file revert is cheap; removing an agent the orchestrator already routes to is not).

## Propagate-anti-patterns operation

This operation is the framework's mechanism for keeping the existing roster consistent with the current anti-patterns checklist. Use when the Anti-patterns section above (or in `aidev-agent-creator.md`) has been changed and existing agents need to be brought into compliance.

Procedure:

1. Read the anti-patterns checklist from `aidev-agent-creator.md` (the running source of truth).
2. Glob the existing agent set: `<repo>/agents/*.md` or `~/.claude/agents/*.md`.
3. **CoT-required classification pass** for each agent. The chain "agent's primary verb in lane statement → work-shape (implementer / reviewer / framer / mediator / detector) → applicable anti-patterns from the checklist → compliance check per applicable anti-pattern → missing rules" must be written out before any compliance decision. Wrong shape classification means the wrong rules get applied (or missing rules go undetected).
4. For each non-compliant agent, queue an embedded `@@AGENT-MODIFY` spec inside the `@@AGENT-PROPAGATE-BATCH` block. The modify spec describes the specific missing rules and where they belong (which section of the target agent).
5. Surface the batch to the orchestrator. **The operation does not modify agents directly** — it produces specs. The orchestrator processes the batch through the normal modify-agent + audit chain per audit-pairing-matrix row `propagation-batch`.

Compliance is binary per anti-pattern: either the agent has the required rule in the correct section, or it doesn't. Partial matches ("the agent mentions style somewhere but not as a hard rule") are non-compliant — the rule must be enforceable.

When to run:

- After the anti-patterns checklist is updated (new principle, sharpened rule).
- As the first real exercise of the propagation flow during initial framework rollout (step 6 of the implementation order in `agent-roster.md`).
- Not on every catalog update; not on every code change. The orchestrator tracks the anti-patterns section hash to refuse duplicate runs at the same version.

## Current Claude Code conventions

This skill encodes practices that match current Claude Code documentation. When unsure whether a convention has changed (model names, agent SDK shape, MCP integration patterns), invoke `aidev-claude-code-researcher` to verify against `docs.claude.com` before finalizing a design.

Conventions known to change frequently:

- Model identifiers (e.g., `claude-opus-4-7`, `claude-sonnet-4-6`) — verify against the current models page.
- Tool naming conventions for MCP servers.
- Agent SDK behavior (system prompt structure, tool grant format).
- Hook event names and trigger conditions.

When the skill's encoded rule conflicts with current Claude Code documentation, current docs win. Surface the conflict to `aidev-agent-creator` so the skill can be updated via the normal change process (planner → implementer → audit).

## Output

This skill does not produce output directly. It informs the design pass that `aidev-agent-creator` runs. The creator's structured output is the artifact that downstream agents (`aidev-code-implementer`, the audit pair) consume.
