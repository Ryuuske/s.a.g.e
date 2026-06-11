<!--
scope-owned: universal agent doctrine — constraint blocks, CoT classification, shareability
audience: agents
source: hand
review-trigger: constraint change
-->

# Universal agent constraints and doctrine

Extracted 2026-06-10 from the hand-maintained roster (Master Run Stage 3d;
original archived at internal/archive/docs/agent-roster-handwritten.md).
The roster itself is now GENERATED at docs/reference/agent-roster.md.

## Design principle: agent definitions are shareable, runtime context is not

Every agent definition in this roster is **generic and shareable**. No agent file names a specific employer, client, project, software product, colleague, internal convention, theme, or proprietary workflow. This is deliberate:

- **What goes in the agent file**: lane statement, methodology, formatting/semantic/tool constraints, refused lanes — the *role*, not the *workplace*.
- **What goes in runtime memory** (nook drawers, conversation context, project-level config): employer name, client names, software products in use, internal style conventions, color schemes, specific deliverable names, colleague names, NDAs, anything that identifies who you are or who you work with.

When an agent is dispatched, the orchestrator passes runtime context from memory into the brief (e.g., "the project uses palette X", "the company calls this concept Y", "the report goes to person Z"). The agent applies that context but does not encode it.

This rule has three benefits:

1. **Shareable**: the roster can be published, forked, or handed to another user without leaking confidential details.
2. **Portable**: the same agent works across different employers/projects without rewrite — the only thing that changes is the runtime context.
3. **Auditable**: confidentiality boundaries are checkable. If a future agent's definition starts naming specific clients or products, that's a regression visible to `aidev-state-reviewer`.

When adding a new agent, ask: *would this description make sense to a stranger who has never seen my work?* If not, the specifics belong in memory, not the file.

---

## Foundations: where CoT and the constraint types come from

The `CoT`, `Formatting constraints`, `Semantic constraints`, and `Tool constraints` columns in each agent entry aren't conventions — they encode specific research findings. A new AI reading this roster should understand the reasoning so it can apply the rules correctly when designing new agents or auditing existing ones.

### Chain-of-Thought (CoT)

Chain-of-Thought is the technique of forcing the model to write its reasoning step-by-step *before* producing an answer. Introduced by Wei et al. (2022), CoT has been measured across many task types since.

The decisive finding for this roster comes from **GuideBench** (Diao et al., ACL 2025, "Benchmarking Domain-Oriented Guideline Following for LLM Agents"). They tested 11 LLMs across 7 agent task categories with and without CoT prompting on the same model. The result:

| Task category | With CoT | Without CoT | Delta |
|---|---|---|---|
| Math / logical reasoning | 65.4% | 42.3% | **+23.1 pts** |
| Summarization | 89.7% | 89.7% | 0.0 pts |

**Rule applied in this roster**: CoT is marked `Yes` only when the agent's primary work is logic-heavy — severity scoring, dependency derivation, classification under conflicting rules, exploit-chain inference, type-flow inference, root-cause inference, bug-class detection. CoT is marked `No` for summarization-class work — execution, mediation, drift detection, template assembly, visual matching, lookup. The +23-point gain on logic-heavy work justifies the latency cost; the ~0-point gain on summarization-class work doesn't.

For agents marked `CoT: Yes`, the `Where to inject` column names the specific methodology step where the chain must appear. "Use CoT throughout" is unenforceable; the injection point is what the auditor checks. Examples: "before any score ≥80, require a 2-line chain trigger → impact → severity rationale", or "per transaction, chain attributes → applicable rule → final category".

### The three constraint types (formatting, semantic, tool)

The three-way classification comes from **AGENTIF** (Tsinghua University, "Benchmarking Instruction Following of Large Language Models in Real-World Agentic Scenarios"). They analyzed 50 production agent prompts collected from industrial applications and open-source agentic systems, manually annotated the constraints, and classified them into three types:

1. **Formatting constraints** — the structure or presentation of the output. Strict verdict blocks, required fields, table shapes, canonical section order, parser-targeted schemas. Machine-parseable contracts.
2. **Semantic constraints** — language style, register, content rules. "No hedge language", "verbatim drawer content", "always cite the source URL with fetch timestamp", "≤15-word quotes per source". Human-checkable, not machine-parseable.
3. **Tool constraints** — schemas for tool invocations. What tools the agent uses, with what parameter format, against what targets. "Bash schema bounded to `git`, `gh`, `pytest`", "WebFetch domain-bounded to `docs.claude.com`", "one fetch per invocation".

AGENTIF found that real-world agent prompts cluster around these three dimensions; they are independent (an agent can be strong on formatting but weak on tool constraints) and the weakest dimension across most rosters is tool constraints — most agents describe tool use in prose rather than schema-constraining it.

**Rule applied in this roster**: every agent has all three constraint types filled in. Tool constraints are formalized as schemas where the methodology permits. Semantic constraints encode lane-specific language rules. Formatting constraints reference the strict block (`@@VERDICT BEGIN…END`, `@@PAIRING BEGIN…END`, etc.) that the orchestrator parses. None of the three columns is allowed to be empty — empty columns are a blocking finding for the state auditors.

### Why this matters for a new AI reading this roster

A new AI handed these documents without prior context should be able to:

- See an agent marked `CoT: Yes` and understand it means the work is logic-heavy per the GuideBench classification, with the chain required at a specific methodology step.
- See `Tool constraints: WebFetch domain-bounded to docs.claude.com` and understand this is an AGENTIF tool-constraint schema, not a stylistic preference.
- Design a new agent and know to: classify the work against the CoT split, fill in all three AGENTIF constraint types, derive ≥2 refused lanes from existing agents, justify every tool grant against a methodology step.

The grounding is empirical, not stylistic. CoT and the constraint types are enforced because the research shows measurable performance differences, not because the framework prefers rigorous-looking prompts.

---

## Universal Agent Constraints

These constraints apply across all agents of a given work-shape (implementer-shaped, reviewer-shaped). They are defined here centrally so that future principle updates touch one section rather than 30+ agent files. Each implementer-shaped agent's `semantic_constraints` inherits `IMPLEMENTER_DISCIPLINE`; each reviewer-shaped agent inherits `REVIEWER_DISCIPLINE` as an additional check angle. Compliance is audited by `aidev-agent-creator`'s `propagate-anti-patterns` operation.

### Why these constraints exist

Beyond the GuideBench (CoT classification) and AGENTIF (constraint types) research, this framework adopts a third class of constraints derived from observations on LLM coding pitfalls — LLMs systematically tend to (a) make silent assumptions when briefs are ambiguous, (b) overcomplicate code with speculative abstractions, (c) drift into adjacent code unrelated to the request, and (d) accumulate orphaned imports and helpers without cleanup. These failure modes compound: a wrong assumption produces wrong code; overcomplication makes the wrong code harder to fix; adjacent drift means the fix touches code that should have stayed stable; orphan accumulation makes the next change harder still. The cost grows non-linearly.

The framework bias is **accuracy over speed**. One additional round-trip to clarify a brief is cheaper than implementing the wrong feature and debugging the result. The constraints below codify that bias as enforceable rules.

### IMPLEMENTER_DISCIPLINE

Every implementer-shaped agent inherits these four rules in its `semantic_constraints`. Implementer-shaped means the agent's primary work is producing or writing artifacts — code, files, configs, reports, sheets, deployment commands.

1. **Pause when ambiguous.** If the brief is ambiguous or requires assumptions not stated in the plan or vision, do not silently pick an interpretation. Surface `PAUSE: orchestrator must clarify <specific question>` instead. Silent assumption-making is the most expensive failure mode: it produces work that has to be redone after the wrong assumption surfaces later.
2. **Minimum code only.** Write the minimum code that satisfies the acceptance criteria. No speculative abstractions, no configurability that was not requested, no error handling for scenarios not named in the plan or vision. Each abstraction, config option, or error handler must trace to an acceptance criterion or named risk. If 200 lines could be 50, write 50.
3. **Match existing style.** Match the existing codebase's conventions even if the implementer's preference differs. Style critique is the dev-architect's lane and the reviewer's lane, not the implementer's. Introducing inconsistent style is a finding.
4. **Clean only your own orphans.** When your changes orphan imports, variables, or functions, remove them. Pre-existing dead code is `dev-refactor-cleaner`'s lane unless that agent has explicitly flagged it. Do not "improve" adjacent code, comments, or formatting.

### REVIEWER_DISCIPLINE

Every reviewer-shaped agent adds the following check angle to its review methodology. Reviewer-shaped means the agent's primary work is auditing diffs, code, output, or designs produced by other agents or by humans.

**Overengineering check angle**: for every new abstraction, configuration option, or error handler in the diff, the reviewer asks "does this trace to an acceptance criterion or named risk in the plan?". If no traceable justification exists, flag as a finding. Severity calibrated to magnitude:

- Single-use abstraction with no listed reuse path → 60–70 (informational)
- Configuration option for a single-caller path → 65–75 (informational, escalates to blocking if combined with other overengineering)
- Error handler for a scenario not in the plan's risks list → 70–80 (informational unless the handler silently swallows errors, then 85–95 blocking)
- Fully configurable plugin system / abstraction tower for a one-off task → 85–95 (blocking)

The reviewer adds this angle as part of their existing review methodology, not as a separate pass. The chain "find new abstraction → trace to plan or risks → if untraced, severity 60–95 based on magnitude" is the injection point.

### REVIEWER_DISCIPLINE — False-positive catalog

LLM reviewers systematically raise findings that a senior engineer on the team would not act on. Before raising any finding that matches a pattern below, the reviewer must clear the pattern's disqualifying condition; if it cannot, the finding is dropped.

- **"Missing input validation"** — disqualified when a caller already validates. Trace at least one real caller before raising; if the value is validated upstream, it is not missing.
- **"Possible null/None dereference"** — disqualified when the preceding lines narrow the type or guard the value. Trace the type-flow (assignment → narrowing event → use); do not pattern-match on the presence/absence of `?.` or a None-check token.
- **"Unhandled exception / no try-except"** — disqualified when the scenario is not in the plan's named risks and the caller or framework already handles it. An error handler for an unnamed scenario is itself an overengineering finding, not a missing-handler finding.
- **"Magic number / hardcoded value"** — disqualified when the value is used once and reads clearly in context. A single-use literal is not a configuration gap.
- **"This should be refactored / extracted"** — disqualified when the duplication is two occurrences or the extraction adds an abstraction with no second caller. Premature extraction is overengineering, not a quality win.
- **"Inconsistent naming / style"** — disqualified unless it diverges from the file's own established convention. Matching the existing local style is correct, even if the reviewer prefers another. Pure style is the formatter's lane.
- **"Race condition / concurrency bug"** — disqualified when the code path is single-threaded or serialized by the framework. Trace the actual concurrency model before raising; do not assume parallelism.

Closing heuristic (apply to every finding, catalog-matched or not): **"Would a senior engineer on this team actually change this in code review? If no, skip it."** This heuristic is an enumerated invariant of REVIEWER_DISCIPLINE; reviewer-family agents restate it inline as a one-line reference, per ADR-0036's allowance for restating invariant-lists.

### REVIEWER_DISCIPLINE — Contract-tracing across paths

Diff-scoped review has a structural blind spot: it sees the changed lines but not the unchanged code those lines must interact with. When a diff adds or changes a feature that carries a stated contract — a kill-switch, an environment dial, a feature flag, a guard clause, or an invariant — the reviewer must trace that contract to EVERY entry point and code path that should honor it, explicitly including unchanged sibling files NOT in the diff, and confirm each one honors it. A kill-switch added to one module but not reaching the installed hook path that actually runs is the canonical failure: the diff looks complete, the contract is silently partial.

The chain is: identify the contract introduced/changed by the diff → enumerate every entry point or code path that should observe it (grep the unchanged tree, do not assume the diff is the whole surface) → confirm each honors the contract → for any path that does not, flag a finding. Severity calibrated to reach: a contract that fails to reach a path users actually exercise (the installed hook, the production entry point) → 85–95 (blocking, because the feature is silently inert where it matters); a contract that misses a rarely-hit or already-dead path → 60–75 (informational). Run this angle as part of the existing review pass, not as a separate step.

### REVIEWER_DISCIPLINE — Mirror/symmetry check

When a change hardens, validates, fixes, or adds a property to ONE side of a symmetric pair, the reviewer must verify the mirror side has the same property. Canonical pairs: destination↔source, read↔write, encode↔decode, serialize↔deserialize, encrypt↔decrypt, install↔uninstall, register↔deregister, request↔response, migration-up↔migration-down. Hardening destination-side fence validation while leaving the source side unvalidated is the canonical failure — the fix is half-applied and the unprotected mirror is the exploitable or breakable side.

The chain is: identify any side of a symmetric pair touched by the diff → name its mirror → check whether the mirror has the same property (validation, guard, error handling, cleanup) → if the mirror lacks it, flag a finding. Severity calibrated to consequence: an unguarded mirror that is reachable with untrusted or unvalidated input → 85–95 (blocking); an unguarded mirror behind a trusted boundary or not yet wired → 60–75 (informational). Run this angle as part of the existing review pass, not as a separate step.

### Maintenance protocol

When new behavioral principles emerge (research findings, framework drift observations, post-mortem lessons), the workflow is:

1. Update the `Anti-patterns` section of `aidev-agent-creator` with the new rule, classified by work-shape (implementer / reviewer / framer / mediator / detector).
2. Update this Universal Agent Constraints section with the operational text the new rule enforces.
3. Dispatch `aidev-agent-creator` with `operation: propagate-anti-patterns` against the existing roster.
4. The creator returns an `@@AGENT-PROPAGATE-BATCH` block listing non-compliant agents and embedded `@@AGENT-MODIFY` specs.
5. Orchestrator processes the batch through the normal modify-agent + audit chain (audit pairing `change_type: propagation-batch`).

This keeps the framework's behavior consistent without requiring per-agent manual review on every principle update. The constraint definitions live in one place; the propagation flow distributes them.

---

