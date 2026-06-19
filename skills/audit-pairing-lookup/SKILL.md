---
name: audit-pairing-lookup
description: "Use to determine which auditor agents pair on a given change type before dispatching an audit (diff, state-audit, or release-gate). Reads `docs/specs/audit-pairing-matrix.md` (the single source of truth) and returns a structured pairing block. Do not use to design new auditor pairings (an ADR-grade User decision) or to dispatch the agents (the orchestrator does that after)."
---

# Audit Pairing Lookup

This skill is invoked by the orchestrator whenever an audit needs to be dispatched. It encapsulates the pairing-lookup logic so that the orchestrator does not have to re-implement the lookup on every dispatch, and so that agents do not need to know their own pairings.

The skill reads `docs/specs/audit-pairing-matrix.md`, finds the row matching the given `change_type`, validates the named auditors against `~/.claude/agent-catalog.json`, and returns the pairing as a structured block.

## When the orchestrator invokes this skill

Invoke whenever the orchestrator needs auditor pairings for any of the following events:

- A diff has been committed and needs review (any `*-diff` row in the matrix).
- A state audit is requested without a diff (`aidev-state`, etc.).
- A release gate fires (`release-gate`).
- An external PR needs review (`gh-pr-review`).
- A generated output is ready for handoff (`fin-reconciliation-output`, `fin-statement-output`, etc.).

If the orchestrator is uncertain which `change_type` applies, the skill accepts a `change_description` and infers the type from file paths + change content. If inference fails, the skill returns `UNKNOWN_CHANGE_TYPE` so the orchestrator can ask the user.

## When NOT to invoke this skill

- For dispatching the auditors themselves — that's the orchestrator's job after this skill returns the pairing.
- For agent-definition lookups (lane statements, tool grants) — read the agent file directly or the catalog.
- For designing a new pairing row — that's an ADR-grade decision the User approves, then `aidev-code-implementer` writes to the matrix.
- For severity scoring — auditors do that in their own methodology.
- For project-type detection — that's `aidev-agent-manager`.

## Inputs

```
change_type: <slug from the matrix>           (preferred)
change_description: <free-text description>   (fallback if change_type unknown)
diff_paths: [<list of file paths in diff>]    (optional, used for inference)
```

At least one of `change_type` or `change_description` (plus `diff_paths` if available) must be provided.

## Procedure

1. **Resolve change_type.**
   - If `change_type` is provided, validate it matches a row in the matrix. If not, return `INVALID_CHANGE_TYPE`.
   - If only `change_description` + `diff_paths` are provided, infer the type by:
     - Matching `diff_paths` against the trigger column patterns (e.g., `agents/*.md` → `aidev-diff`; `*.pq` → `data-pq-diff`).
     - Falling back to `change_description` keyword matching (e.g., "security", "auth" → `dev-security-diff`).
     - If no clear match, return `UNKNOWN_CHANGE_TYPE`.

2. **Read the matrix.** Open `docs/specs/audit-pairing-matrix.md` and extract the row matching `change_type`.

3. **Validate auditors against the catalog.** For each of `auditor_primary`, `auditor_secondary`, `auditor_tertiary`:
   - Check the name exists in `~/.claude/agent-catalog.json`.
   - If a named auditor is missing from the catalog, set `drift_warning` and continue (return the pairing with the warning rather than failing — the orchestrator decides whether to proceed).
   - **Codex-pass carve-out:** a `/codex:*` slash-command secondary (the §16 adversarial-review lane, ADR-0123/0125) is NOT a catalog agent. Recognize a `/codex:adversarial-review` entry as a valid Codex pass and SKIP the `~/.claude/agent-catalog.json` check for it; do NOT set `drift_warning` for a `/codex:*` entry. Codex-pass readiness is validated by `codex-budget` / `codex:setup`, not by the agent catalog. **Cross-model selection (ADR-0125):** the adversarial secondary's model is chosen relative to the change's implementer — emit the /codex:adversarial-review token when Claude implemented the change; emit the Claude aidev-adversarial-auditor / aidev-state-adversarial-auditor (a catalog agent, validated normally) when Codex implemented it, or when the implementer is unknown/mixed (fail-safe to Claude).

4. **Check matrix freshness.** If the matrix's `last_audited` field is more than 90 days old, set `stale_warning`.

5. **Return the pairing block.**

## Output format

```
@@PAIRING BEGIN
change_type: <slug>
auditor_primary: <agent-name>
auditor_secondary: <agent-name, /codex:* Codex-pass token (with Claude fallback in parens), or null>
auditor_tertiary: <agent-name or null>
protocol: parallel | sequential | solo | self-pass
matrix_source: docs/specs/audit-pairing-matrix.md
matrix_last_audited: <YYYY-MM-DD>
drift_warning: <agent-name not in catalog | null>
stale_warning: matrix older than 90 days | null
@@PAIRING END
```

If the change_type cannot be resolved:

```
@@PAIRING BEGIN
result: UNKNOWN_CHANGE_TYPE | INVALID_CHANGE_TYPE
matrix_source: docs/specs/audit-pairing-matrix.md
recommend: ask user for explicit change_type, or propose new row via ADR
@@PAIRING END
```

## Examples

### Example 1 — diff under `agents/`

Input:
```
change_type: aidev-diff
```

Output:
```
@@PAIRING BEGIN
change_type: aidev-diff
auditor_primary: aidev-code-reviewer
auditor_secondary: /codex:adversarial-review (cross-model default — Claude implemented; if Codex implemented or unknown/mixed, the Claude aidev-adversarial-auditor — ADR-0123/0125)
auditor_tertiary: null
protocol: parallel
matrix_source: docs/specs/audit-pairing-matrix.md
matrix_last_audited: 2026-05-26
drift_warning: null
stale_warning: null
@@PAIRING END
```

### Example 2 — inference from diff paths

Input:
```
change_description: "added auth middleware"
diff_paths: ["src/auth/middleware.ts", "tests/auth.test.ts"]
```

Output:
```
@@PAIRING BEGIN
change_type: dev-security-diff
auditor_primary: dev-code-reviewer
auditor_secondary: sec-auditor
auditor_tertiary: null
protocol: parallel
matrix_source: docs/specs/audit-pairing-matrix.md
matrix_last_audited: 2026-05-26
drift_warning: null
stale_warning: null
@@PAIRING END
```

### Example 3 — drift detected

Input:
```
change_type: data-pq-diff
```

If `data-power-query-developer` is not yet activated in the catalog:

```
@@PAIRING BEGIN
change_type: data-pq-diff
auditor_primary: data-power-query-developer
auditor_secondary: dev-test-engineer
auditor_tertiary: null
protocol: parallel
matrix_source: docs/specs/audit-pairing-matrix.md
matrix_last_audited: 2026-05-26
drift_warning: data-power-query-developer not in catalog — recommend aidev-agent-manager.add-agent
stale_warning: null
@@PAIRING END
```

The orchestrator sees the warning and routes to `aidev-agent-manager` to activate the missing auditor before proceeding.

## Anti-patterns

- **Hardcoded pairings in agent files** — every pairing lives in the matrix. If you find yourself wanting to add a pairing to an agent file, you are bypassing this skill. Update the matrix instead.
- **Skipping validation against the catalog** — a matrix row that names a non-existent agent fails silently at dispatch time. Always validate. Exception: `/codex:*` secondaries are validated by Codex-readiness (`codex-budget` / `codex:setup`), not the agent catalog — do not raise `drift_warning` for a `/codex:*` entry.
- **Inferring change_type from natural language alone** — file paths are stronger evidence. Prefer the path-pattern match over keyword match.
- **Adding pairing-resolution logic to this skill** — when auditors return split verdicts, that's resolution policy, not lookup. The matrix's "Resolution protocol" section covers it; the orchestrator applies it after both verdicts return. This skill ends at "here are the auditors to dispatch."
