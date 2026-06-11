---
name: skill-creation
description: Use when creating, modifying, or deleting a skill (SKILL.md file). Triggers on "design a new skill", "modify this skill", "delete this skill", "is this skill well-scoped". Do not use for agent design (`agent-creation`), or to actually write the SKILL.md file (`aidev-code-implementer` after the design pass).
---

# Skill Creation Skill

This skill is the canonical playbook for skill design. The primary consumer is `aidev-skill-creator`. The skill is the *playbook*; the creator is the *executor*.

## Canonical SKILL.md structure

Every SKILL.md file uses this structure:

1. **Frontmatter** (YAML) — `name` and `description` (the description is what the loader matches against; it must concisely state the trigger and refusal scope).
2. **Top-level `# Name`** heading.
3. **Scope statement** — one paragraph stating what the skill encodes.
4. **Triggers and refusals** — when the skill should fire and when it should not.
5. **Procedure / content** — the actual guidance the consuming agent follows.
6. **Anti-patterns** — failure modes for the consumer agent to flag.
7. **Output guidance** (formatting / semantic / tool — see below).
8. **When NOT to use this skill** — adjacent skills with explicit alternatives.

## Frontmatter `description` rules

The `description` field is load-bearing — it's what the model matches against user phrasings to decide whether to load the skill. Rules:

- **≤500 characters.** Anything longer dilutes the trigger signal.
- **Lead with the trigger condition.** "Use when X" or "Use to do Y". Not "This skill helps with…".
- **Include refusal scope.** "Do not use for Z" — at least one explicit refusal.
- **No marketing language.** "Best-in-class", "comprehensive", "advanced" — drop them.
- **Triggers as concrete phrasings.** "When the user says A, B, or C" is stronger than "for analysis tasks".

## Trigger derivation

Triggers are the phrasings the consuming agent (or orchestrator-controlled router) will see. They must be concrete enough to match real phrasings without colliding with adjacent skills.

**Strong triggers**:
- "I just modified the X module — review it" (specific phrasing)
- "Find the failure mode an optimist missed" (specific intent)
- "Look up the current model string for Claude Sonnet" (specific lookup)

**Weak triggers**:
- "Review tasks" (too broad)
- "Analysis work" (no specific phrasing)
- "When something needs checking" (vague)

For each new skill, identify ≥3 concrete trigger phrasings AND ≥2 refused triggers (phrasings that look similar but should route elsewhere).

## CoT classification (from GuideBench)

A skill supports either logic-heavy work or summarization-class work. The classification determines what kind of guidance the skill provides:

### Logic-heavy support

The skill encodes a procedure for chain-reasoning work — debugging chains, severity grounding, dependency derivation, exploit-chain inference. Examples:

- `systematic-debugging` — supports root-cause inference (logic chain symptom → stage → root cause → fix).
- `verification-before-completion` — supports a verification chain before any "done" claim.
- `test-driven-development` — supports the red-green-refactor logical sequence.

For logic-heavy skills, the procedure should require the consuming agent to write out reasoning, not just produce an answer.

### Summarization-class support

The skill encodes a template-application procedure — internal-comms templates, scaffolding manifests, content extraction. Examples:

- `internal-comms` — match the comm type's template, populate.
- `pdf-reading` — choose the right reading strategy per document type.
- `audit-pairing-lookup` — read the matrix, find the row, return the pairing.

For summarization-class skills, the procedure should be deterministic enough that a different consuming agent would produce a similar output.

## AGENTIF dimensions adapted for skills

The skill provides guidance to its consuming agent on:

### Formatting guidance

What the consuming agent's output should look like under this skill. Examples:

- `agent-creation` provides formatting guidance: the `@@AGENT-DESIGN BEGIN…END` block schema.
- `audit-pairing-lookup` provides formatting guidance: the `@@PAIRING BEGIN…END` block.
- `internal-comms` provides formatting guidance: match the comm type's template (status report shape, FAQ shape, memo shape).

### Semantic guidance

What kind of language the consuming agent should use under this skill. Examples:

- `systematic-debugging` — never propose a fix without verification.
- `verification-before-completion` — verify before claiming, not after.
- `agent-creation` — no hedge language in design specs; identifying info banned.

### Tool guidance

What tools the consuming agent uses under this skill, and how to constrain them. Examples:

- `agent-creation` — Read/Grep/Glob for reading existing agents; no Write.
- `pdf-reading` — choose extraction strategy per document type (text-heavy, scanned, slide-deck).
- `audit-pairing-lookup` — Read the matrix file; validate against catalog.

## Scope discipline

A skill encodes one coherent procedure. Multi-procedure skills should be split:

- **Wrong**: a skill called "code-quality" that covers reviewing, testing, AND refactoring.
- **Right**: three skills — `code-review`, `test-driven-development`, `refactoring`.

If the scope statement requires "and" or "also", the skill is too broad. Split.

## Modify-skill additional rules

When modifying an existing skill:

1. Read all consumer agents (Glob across agents for the skill name).
2. Identify the impact: does the modification change what consumer agents should do?
3. If yes: the modification needs an accompanying update to each consumer agent's methodology. Surface this as a knock-on effect.
4. Backwards-compatibility check: if the modification removes a trigger that consumer agents currently rely on, that's a breaking change requiring ADR coverage.

## Delete-skill additional rules

When deleting a skill:

1. List every consumer agent.
2. For each consumer agent, propose either:
   - Inline the procedure (if the skill was only consumed by one agent).
   - Route to an alternative skill (if multiple agents consume it).
3. Removal is only safe after every consumer agent has been updated. Order of operations matters.

## Anti-patterns for skill design

- **Trigger lists as keywords.** Triggers are full phrasings, not lists of words.
- **Description longer than 500 characters.** Dilutes the loader's match signal.
- **Skills without consumer agents.** A skill exists because some agent uses it. If you can't name the consumer, the skill shouldn't exist.
- **Multi-procedure skills.** One skill = one coherent procedure. Split anything else.
- **Skills that duplicate agent methodology.** If the procedure lives in only one agent, it should stay inline in that agent. Skills are for procedures that span ≥2 agents.
- **Identifying info in the description.** Per the design principle in `agent-roster.md`, no employer, client, project, or convention names.

## Current Claude Code conventions

This skill encodes practices that match current Claude Code documentation. When unsure whether SKILL.md frontmatter requirements have changed (allowed fields, max description length, loading behavior), coordinate via the orchestrator to dispatch `aidev-claude-code-researcher` for verification.

## Output

This skill does not produce output directly. It informs the design pass that `aidev-skill-creator` runs. The creator's structured output is the artifact that downstream agents consume.
