<!--
scope-owned: aidev manifest fields + §17 checks
audience: agents + devs
source: hand
review-trigger: manifest change
-->

# aidev context-manifest schema

Three-field manifest block within the agent's YAML front matter: what to include in a brief (`required_inputs`), what to exclude (`forbidden_inputs`), and a paste-ready template line (`briefing_template`). These three fields sit alongside the standard Claude Code agent fields (`name`, `description`, `tools`, `model`); the manifest schema below covers only the three manifest-specific fields.

Version: 1.1 — 2026-05-26

**YAML-quoting note:** any `required_inputs` or `forbidden_inputs` item whose body contains a colon-space (`": "`), an embedded single quote, or a flow-mapping brace (`{`) MUST be wrapped in YAML quotes (single or double). An unquoted item with embedded `: ` is silently parsed as a mapping, not a string, and the §17 validator's input-string check then operates on a dict — masking real briefing gaps. Live agents that include such characters in body text use the quoted form; mirror this in any new manifest you author.

---

## Fields

### `required_inputs`

**Type:** YAML list of strings.

What the orchestrator must supply in the brief. Each string names one input the agent cannot function without.

**Directory-shortcut ban (path-list items):** When a `required_inputs` item names a list of file paths (e.g., "list of ADR file paths", "list of agent files"), the orchestrator must supply ≥1 explicit file path per element — supplying the directory shortcut (e.g., `docs/decisions/`) does NOT satisfy the item. A directory path is non-empty and passes the §17 stat-check trivially, bypassing the intent that the caller enumerate specific files. Orchestrators that pass a directory shortcut instead of explicit paths are in violation of §17 step 2 even if the directory exists and is non-empty.

### `forbidden_inputs`

**Type:** YAML list of strings.

What the orchestrator must NOT pass. Each string names an input category that actively degrades output — wastes context, introduces framing bias, or narrows the adversarial angle prematurely.

### `briefing_template`

**Type:** Scalar string (single line).

A one-line template the orchestrator pastes verbatim into the brief, with `<placeholder>` tokens the orchestrator fills before sending.

---

## What "populated" means

Before invoking any aidev agent, the orchestrator verifies all of:

1. Every `required_inputs` item has a non-placeholder value in the brief — no `<...>` token remains, no item is absent. A non-placeholder value means a substantive payload: a path that exists and is non-empty at brief construction (orchestrator must stat the path before dispatch), a diff hunk pasted inline, or a quoted excerpt — an excerpt must be a complete logical unit (full hunk, full function, full file section) — partial excerpts of larger artifacts do not satisfy. References to prior conversation ("see above", "as discussed") do not satisfy; paste or path-reference the artifact.

   **Literal-string alternatives (accepted):** Some `required_inputs` items explicitly offer a literal-string alternative alongside a path (e.g., `path to aidev-state-reviewer's report OR literal "solo contrarian pass — no peer report"`). When an item declares such an alternative, the orchestrator satisfies the check by supplying the exact documented literal string — no stat-check is applied to a literal string. Validation is by **exact-match** against the agent's documented expected-literal: a near-literal or paraphrase does NOT satisfy the check and the brief is incomplete. The exact-match requirement prevents a malformed brief from passing manifest validation on a close-but-wrong string.

2. No `forbidden_inputs` item appears in the brief, even paraphrased.
3. The `briefing_template` line appears with all `<placeholder>` tokens filled.

Any failing check makes the brief incomplete; orchestrator holds rather than dispatches.

---

## Rejected fields

Considered and rejected.

| Field | Rationale for rejection |
|---|---|
| `output_path` | Already in agent body; duplicating in front matter creates drift risk. |
| `model_override` | Already in front matter `model:` field; a second field invites contradiction. |
| `max_brief_tokens` | Runtime concern, not schema; no enforcement tooling exists. |
| `prior_agent` | Sequencing belongs in the plan's work-items table; couples schema to plan structure. |
| `confidence_threshold` | Verdict thresholds are methodology; externalizing causes agent body / front matter divergence. |

---

## Subdir-loading verdict

**Flat form wins.** Claude Code's subagent loader discovers agents at `~/.claude/agents/<name>.md` only — no subdir-form support in this installation. Subdir form would produce agents the loader cannot find.

**Consequence for CHANGELOG placement:** per-agent changelogs live in the S.A.G.E. repo under `docs/agents/<name>.CHANGELOG.md` — outside `~/.claude/agents/`, avoiding loader confusion. (Per-agent BACKLOG files were retired 2026-06-10; work items live only in `internal/BACKLOG.md`.)

**Note on `general-purpose` built-in:** refused-lane pointer items in `forbidden_inputs` or "When NOT to use" sections MAY name `general-purpose` as a routing target. `general-purpose` is a Claude Code built-in subagent — no file under `<repo>/agents/` is required; the runtime resolves it by name. The §17 stat-check applies only to paths that resolve to files; built-in names are exempt from the existence check.

---

## Worked examples — one per aidev agent

Illustrative only. Do not install into live agent files.

### aidev-adversarial-auditor

```yaml
required_inputs:
  - git diff or file-by-file read of the change under review
  - path to docs/plans/active.md (plan ref)
  - path to aidev-code-reviewer's report for this round (when invoked as paired dual-auditor), OR the literal string "solo contrarian pass — no peer report" (when invoked alone)
# why: pre-framing biases verdict before seeing the diff; summarizing peer report collapses the independent angle dual-auditor pairing requires
forbidden_inputs:
  - optimistic framing of the change (e.g., "this improves X by doing Y") alongside the diff
  - aidev-code-reviewer's verdict pre-framed in the brief body (full report in required_inputs; do not summarize or characterize it before the audit)
briefing_template: "Audit <scope> diff at <diff-path>. Plan: <plan-path>. Peer report: <reviewer-report-path-or-'solo contrarian pass — no peer report'>. Round: <N>."
```

### aidev-agent-designer

```yaml
required_inputs:
  - plan item that triggered this design (verbatim from docs/plans/active.md)
  - names of all existing agents in ~/.claude/agents/ (for lane-conflict check)
  - list of ADR file paths constraining agent shape (≥1 explicit element, not the directory shortcut docs/decisions/)
# why: a pre-written draft anchors the designer before refused-lane and lane-conflict passes run; architect-lane rationale pulls scope outside this agent's charter
forbidden_inputs:
  - a pre-written draft of the agent file (biases toward the draft, skips refused-lane pass)
  - tech-selection rationale (belongs in architect's lane)
briefing_template: "Design agent for plan item <item-N>: <item-description>. Existing agents: <agent-list>. Relevant ADRs: <adr-list-or-none>."
```

### aidev-code-implementer

```yaml
required_inputs:
  - path to docs/plans/active.md (approved plan)
  - design spec from aidev-agent-designer (if the plan item is a new or reworked agent)
  - list of WHERE targets for this work item
# why: whole-repo dump bloats context beyond WHERE targets; pre-loading verdicts makes the implementer litigate findings instead of executing the plan
forbidden_inputs:
  - whole-repo content dump (targeted file reads are sufficient)
  - review verdicts or audit findings (implementer executes the plan, not the audit)
briefing_template: "Implement plan item <item-N>: <item-description>. WHERE: <target-path>. Plan: <plan-path>. Design spec: <spec-path-or-none>."
```

### aidev-code-reviewer

```yaml
required_inputs:
  - git diff or file paths of the change (verified, not claimed)
  - path to docs/plans/active.md
  - path to docs/forbidden-patterns.md if present
  - round number (pre or post, N)
# why: self-assessment primes the reviewer toward approval; auditor verdict before review completes collapses the independent angle dual-auditor pairing requires
forbidden_inputs:
  - implementer's self-assessment (e.g., "I think this is correct because...")
  - aidev-adversarial-auditor's verdict before the code-review round completes
briefing_template: "Review <scope> change. Diff: <diff-path>. Plan: <plan-path>. Forbidden-patterns: <fp-path-or-none>. Round: <pre|post>-<N>."
```

### aidev-planner

```yaml
required_inputs:
  - vision artifact from aidev-visionary (or a concrete User request if framing was skipped)
  - list of ADR file paths that constrain this scope (≥1 explicit element, not the directory shortcut docs/decisions/)
  - current docs/plans/active.md if one exists (conflict check)
# why: pre-loaded approach narrows the plan before trade-off analysis runs; unvetted specialist verdicts pre-empt the User's approval role on the plan artifact
forbidden_inputs:
  - a proposed implementation approach (planner derives approach from the vision; pre-loading narrows the plan)
  - specialist verdicts the User has not seen (plan is the approval artifact; pre-loading pre-empts User judgment)
briefing_template: "Plan scope: <scope-description>. Vision: <vision-path-or-inline>. ADRs: <adr-list-or-none>. Active plan: <plan-path-or-none>."
```

### aidev-visionary

```yaml
required_inputs:
  - User's raw request or session transcript excerpt describing the pain/intent
  - list of ADR file paths (≥1 explicit element, not the directory shortcut docs/decisions/) (to check vision against prior decisions)
# why: pre-loading a plan skips the framing pass visionary is designed to perform; inherited acceptance criteria substitute the User's voice with the orchestrator's assumptions
forbidden_inputs:
  - a proposed plan or implementation steps (visionary works before the plan; passing one skips the framing pass)
  - feature lists or acceptance criteria the User has not stated (visionary surfaces these; does not inherit them)
briefing_template: "Frame request: \"<user-raw-request>\". ADRs: <adr-list-or-none>. No plan exists yet."
```

### aidev-state-reviewer

```yaml
required_inputs:
  - "audit scope statement (literal text, ≥3 lines; must name (a) the artifact set in scope as a path or glob list, (b) the specific governance axis under verification — lane discipline, §16 compliance, §17 manifest integrity, refused-lane pointer integrity, or ADR supersession chain — and (c) the precipitating reason the audit was triggered. One-word or single-glob briefs do not satisfy this field.)"
  - "path to docs/plans/active.md"
  - "path list of state artifacts in scope (agents/*.md, skills/*.md, framework files) — verified non-empty"
  - "round number (pre or post, N)"
# why: a diff poisons the manifest input check — state-reviewer operates on live roster state, not a change; peer verdict before review completes collapses the independent angle the dual-auditor pairing requires
forbidden_inputs:
  - any git diff (use aidev-code-reviewer instead)
  - peer auditor verdict before review completes
briefing_template: "State review <scope>. Artifacts: <path-list>. Plan: <plan-path>. Round: <pre|post>-<N>."
```

### aidev-keeper

```yaml
required_inputs:
  - "operation — one of {wake-up, search, file-handoff, diary-read, diary-write, register-wing}"
  - "wing — the wing slug the operation targets (or current_wing on wake-up)"
  - "context payload for the operation (query / drawer content / agent name + entry, etc., depending on operation)"
# why: the Keeper writes to the nook on dispatch; an unconfirmed sentinel can produce phantom drawers
forbidden_inputs:
  - 'operation issued without a concrete payload (e.g. "search the nook" with no query)'
  - "direct invocation of nook_add_drawer / nook_diary_write from any other agent — those calls route through the Keeper"
briefing_template: "Keeper: <operation>. Wing: <wing>. Payload: <payload>."
```

### aidev-state-adversarial-auditor

```yaml
required_inputs:
  - "audit scope statement (literal text, ≥3 lines; must name (a) the artifact set in scope as a path or glob list, (b) the failure-mode class under pressure-test — lane bleed, manifest defect, dispatch ambiguity, or §16 coverage gap — and (c) the precipitating reason the contrarian pass is being run. One-word or single-glob briefs do not satisfy this field.)"
  - "path to docs/plans/active.md"
  - "path list of state artifacts in scope (agents/*.md, skills/*.md, framework files) — verified non-empty"
  - 'path to aidev-state-reviewer''s report OR literal "solo contrarian pass — no peer report"'
  - "round number (pre or post, N)"
# why: optimistic framing primes failure-mode scan toward approval before adversarial pass runs; pre-framing peer verdict collapses the independent angle the dual-auditor pairing requires
forbidden_inputs:
  - optimistic framing of the state alongside artifact paths
  - peer reviewer's verdict pre-framed in brief body
  - any git diff (use aidev-adversarial-auditor instead)
briefing_template: "Adversarial state audit <scope>. Artifacts: <path-list>. Plan: <plan-path>. Peer report: <reviewer-report-path-or-'solo contrarian pass — no peer report'>. Round: <pre|post>-<N>."
```

---

## Roster-level test patterns

When an ADR enumerates a file-set contract — a named set of agents that must carry a specific structural guarantee — pair a **parametrized presence test** with a **disk-discovery counterscan**. The presence test asserts, for each explicitly named agent, that the required clause appears in its Constraints section. The counterscan walks every agent file on disk for the structural signals that would identify a copy-paste-skeleton sibling that matches the contract's profile but was never added to the named set. Both tests must exist together: the presence test alone misses new additions; the counterscan alone cannot assert per-agent wording precision.

ADR-0017 (auditor bounded-write grant) is the shipped example: it names a five-agent set (`aidev-code-reviewer`, `aidev-adversarial-auditor`, `aidev-state-reviewer`, `aidev-state-adversarial-auditor`, `dev-code-reviewer`) that must each carry the uniform "Write surface bounded to `<repo>/docs/audits/`" clause. The clause is currently present in all five agent files. (Note: the fifth agent was renamed from `code-reviewer` to `dev-code-reviewer`; ADR-0017 retains the historical name as append-only record; this live doc uses the current name.) The paired presence-test + counterscan that would mechanically guard the contract has not yet been written in v0.1.0; the contract is enforced by hand-review until that test lands.
