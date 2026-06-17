<!--
scope-owned: @@VERDICT block contract, paired with src/sage_mcp/verdict_parser.py
audience: agents + devs
source: hand
review-trigger: parser change
-->

# Auditor Verdict Schema

This document defines the canonical structured verdict format every auditor in the roster emits as part of its inline reply to the orchestrator. The schema replaces the previous prose-only verdict line, which forced the orchestrator to fuzzy-match wording to detect agreement, disagreement, and blocking-finding presence.

Per `~/.claude/CLAUDE.md` §16, the structured block is mandatory for:

- `dev-code-reviewer`
- `dev-test-engineer`
- `sec-auditor`
- `dev-ux-designer`
- `doc-keeper` (when serving as an auditor)
- `aidev-code-reviewer`
- `aidev-adversarial-auditor`
- `aidev-state-reviewer`
- `aidev-state-adversarial-auditor`

The block is parsed by `sage.verdict_parser.parse_verdict`. Lines outside the block (the compressed prose summary, the report path) are not parsed — they are for the orchestrator's prose synthesis to the User.

## Format

Each auditor's inline reply begins with the structured block, followed by the compressed prose summary, followed by an optional report-path line.

```
@@VERDICT BEGIN
verdict: APPROVE | REQUEST_CHANGES | REJECT | HOLD | ABORT
lane: <auditor-name>
report: <relative-path-to-report-md | none>
findings: <integer>
@@FINDING 1
severity: <0-100>
file: <relative-path | n/a>
line: <integer | 0>
category: <governance|security|test|ux|lane|manifest|drift|docs|other>
summary: <one-line, ≤200 chars, no newlines>
@@FINDING 2
severity: ...
file: ...
...
@@VERDICT END
```

### Field semantics

| Field | Type | Meaning |
| --- | --- | --- |
| `verdict` | enum | `APPROVE` = no blocking findings; `REQUEST_CHANGES` = blocking findings present but the work can be fixed in-place; `REJECT` = the change should not land in this form even with patches; `HOLD` = the auditor cannot decide without more information (always paired with one finding describing the gap); `ABORT` = the audit was malformed (missing required input, bad brief). |
| `lane` | string | The auditor's own agent name. Used to detect duplicate dispatch and confirm §16 pair correctness. |
| `report` | path \| `none` | Relative path from repo root to the full structured report. `none` if the auditor's lane does not require a separate report file. |
| `findings` | integer | Count of `@@FINDING N` blocks that follow. Must match the actual count. |
| `severity` | integer 0–100 | Per `~/.claude/CLAUDE.md` §16, findings ≥80 are blocking. Auditors must not soften scores to avoid a blocking verdict. |
| `file` | path \| `n/a` | The file the finding lives in. `n/a` for findings that span the change as a whole (e.g., "no tests added"). |
| `line` | integer | Line number of the finding. `0` if not applicable or unknown. |
| `category` | enum | The lane-discipline category. Helps the orchestrator route disagreements (e.g., two `security` findings from different auditors signal lane overlap). |
| `summary` | string | One line, max 200 chars, no newlines. Long context goes in the report. |

### Verdict-to-findings consistency rules

The parser enforces:

- `APPROVE` allows zero blocking findings (severity ≥80). Non-blocking findings may still be listed.
- `REQUEST_CHANGES` requires at least one blocking finding (severity ≥80).
- `REJECT` requires at least one finding with `severity = 100`.
- `HOLD` requires exactly one finding describing the missing input or gap.
- `ABORT` requires exactly one finding describing the malformed brief; `severity = 100`.

A verdict-finding mismatch (e.g., `APPROVE` with a finding scored 85) is itself a parser error and the orchestrator must surface it.

### Compressed prose summary

Immediately after `@@VERDICT END`, the auditor writes a ≤200-word compressed-agent-comm summary per its existing Output discipline section. This is for the orchestrator to relay to the User and is NOT parsed. Example:

```
@@VERDICT END

Plan covers all 7 acceptance criteria. Risks file complete. Lane discipline clean across new agents. Test-engineer report agrees. Single blocking finding: aidev-planner.md missing required `forbidden_inputs` per manifest schema. Fix: insert empty list in frontmatter, no schema-impact. Report path above.
```

### Report-path line (optional)

If `report:` in the structured block is `none`, no report-path line follows. Otherwise the auditor closes with the report path on its own line, NORMAL prose, for the User's reference:

```
Report: .development/audits/2026-05-25-skills-batch-aidev-code-reviewer-post.md
```

## Parser contract

`sage.verdict_parser.parse_verdict(text: str) -> Verdict` returns a `Verdict` dataclass with the typed fields above plus a `valid: bool` and a `parser_errors: list[str]`. Callers (the orchestrator) check `valid` and surface `parser_errors` if any.

The parser is strict about the `@@VERDICT BEGIN` / `@@VERDICT END` delimiters and about the field-per-line `key: value` shape. Auditors should not paraphrase the field names. Unknown fields are recorded in `parser_warnings` and do **not** invalidate the block (`valid` reflects `parser_errors` only; an unknown field is non-fatal, so a forward-compatible auditor that adds a field is not rejected — see `verdict_parser.py`). Auditors should still include only the documented fields above.

## Discipline

Every auditor in the §16 roster emits the structured block as the first thing in its inline reply. The repository's `agents/*.md` files declare the block in their `## Output discipline` section, and `install.sh` / `install.ps1` propagate those agents to `~/.claude/agents/`. The parser is strict — no block, no valid verdict — and the orchestrator surfaces parser errors immediately so a non-conforming auditor is caught at dispatch time, not at audit time.

Any new auditor added to the roster must include the block format in its own `## Output discipline` section. The validator in `sage.verdict_parser` is the source of truth for what counts as well-formed.

## Why this schema exists

Without a shared schema, each auditor's verdict format differs by agent and the orchestrator must fuzzy-match to detect agreement vs disagreement. A shared schema makes split detection mechanical and makes the disagreement-to-ADR pipeline reliable.

The schema is also the substrate for improvement 7 (loop telemetry): each parsed verdict is logged to `~/.sage/telemetry/turns.jsonl` and later mined into wing `telemetry`, hall `turns`.
