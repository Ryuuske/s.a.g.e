---
name: gh-pr-review-discipline
description: "Use when reviewing GitHub PRs as gh-pr-reviewer — seven runtime trees: nit/constructive/blocker classification, CI verification, test-coverage proportionality, hedge-language re-grep against canonical banned-vague-fill list, sequential handoff to dev-code-reviewer, tone calibration, 4-step CoT chain. Triggers on 'classify finding as nit/blocker', 'verify CI before approve', 're-grep for hedge-language'. Do not use for non-PR review or pre-completion verification."
---

# GitHub PR Review Discipline

This skill encodes seven runtime decision trees that `gh-pr-reviewer` consults at methodology steps 4–6 to classify findings, verify CI state, assess test coverage, enforce the canonical hedge-language ban, calibrate tone, prepare the sequential handoff to `dev-code-reviewer`, and emit the mandatory 4-step CoT chain per finding.

This skill is consumed exclusively by `gh-pr-reviewer` (reviewer-shaped, Phase D agent #6). It does not overlap with `dev-code-reviewer` (code substance review, downstream in the sequential pair per `docs/specs/audit-pairing-matrix.md` line 30) or with `verification-before-completion` (pre-completion claim/output check — that skill governs `gh-pr-reviewer`'s methodology step 8 self-check; this skill contributes the nit/constructive/blocker classification and the 4-step CoT chain as inputs to that check, without replacing it).

The seven trees are logic-heavy per `rules/ai-dev-conventions.md` CoT injection classification: severity scoring (0–100 per finding) plus nit/constructive/blocker classification under conflicting rules (a stylistic preference that touches a security boundary escalates to blocker, not nit). The higher-severity-wins rule in Element A is binding across all trees — this skill never softens a borderline call to avoid a `REQUEST_CHANGES` verdict.

ADR-0029 (`docs/decisions/0029-gh-pr-reviewer-identifying-info-exemption.md`) grants `gh-pr-reviewer` a case-a exemption for `gh` CLI and GitHub references; this skill inherits the exemption through its consuming agent.

## When this skill binds

Fire this skill when any of these are true:

- You are classifying a PR finding as nit, constructive, or blocker.
- You are verifying CI state before emitting an APPROVE verdict.
- You are assessing whether test coverage is proportional to the PR's change shape.
- You are re-grepping your own output against the canonical banned-vague-fill list (methodology step 4).
- You are formatting the handoff payload for `dev-code-reviewer`'s sequential audit.
- You are calibrating tone register (contributor-perspective vs maintainer-perspective) for the PR audit.
- You are emitting the mandatory 4-step CoT chain for a finding.

Do NOT fire this skill for:

- Non-PR code review — that is `dev-code-reviewer`'s lane downstream in the sequential pair per `docs/specs/audit-pairing-matrix.md` line 30.
- Pre-completion claim verification — `verification-before-completion` handles claim → falsifying-command discipline.
- SOP body audit — `biz-sop-discipline` handles SOP decision trees.
- Language-specific code review (M, VBA) — `m-language-discipline` / `vba-language-discipline`.
- Audit-pairing resolution — `audit-pairing-lookup` reads the matrix.
- `/codex:*` dispatch reflex decisions — `codex-routing-reflex` is orchestrator-consumed per ADR-0028 and is explicitly out of lane here.
- "Tests are passing, mark this done" → `verification-before-completion` (claim-before-completion check, not PR finding classification).
- "Is this SOP escalation concrete?" → `biz-sop-discipline` (SOP body audit, distinct lane).
- "Which auditors should pair on this diff?" → `audit-pairing-lookup` (pairing matrix resolution, not finding classification).

## Element A — Nit / constructive / blocker classification

Every finding emitted in a `@@PR-COMMENT` block carries a `tone-tag` (one of: `constructive`, `blocker`, `nit` — literals only, no synonyms) and a `severity` (integer 0–100 — no symbolic levels).

**Decision tree (walk in order; first match wins):**

1. Does the finding name a **security boundary** (auth, secrets, file I/O, network, subprocess, deserialization, crypto, dependency manifest)? → **blocker**
2. Does the finding name a **correctness defect provable by the diff alone** (off-by-one, null-deref, type mismatch, missed branch)? → **blocker**
3. Does the finding name a **missing test for new code** in a security boundary? → **blocker**
4. Does the finding name a **missing test for new non-security-boundary code**? → **constructive** (severity 60–79)
5. Does the finding name a **style/naming/comment preference unbacked by a project convention**? → **nit** (severity 1–29)
6. Does the finding name a **refactoring opportunity unrelated to the PR's stated intent**? → **nit** OR omit entirely per CLAUDE.md §4 no-speculative-problems rule.
7. Otherwise → **constructive** (severity 30–79); cite the convention or rule that grounds the score.

**Severity anchor table:**

| Range | Classification | Examples |
|-------|---------------|---------|
| 0–29 | nit | Style, naming, comment density, doc phrasing |
| 30–59 | constructive non-blocking | Test coverage gap on non-critical path, refactor opportunity on touched code, missing doc on public API |
| 60–79 | constructive borderline | Missing test on touched-but-not-security-boundary code, error-handling gap on non-fatal path |
| 80–100 | blocker | Security boundary defect, correctness defect provable from diff, ADR-violation, missing test on security boundary code |

**Higher-severity-wins rule:** when a finding could be classified two ways under different rules, the higher severity wins. This skill never softens a borderline call to avoid a `REQUEST_CHANGES` verdict.

## Element B — CI verification protocol

Run `gh pr checks <num> --json name,status,conclusion` before emitting any APPROVE verdict. Parse and coerce each check's status to the canonical CI status enum.

**Canonical CI status enum:** `success` | `pending` | `failure` | `cancelled` | `skipped` — these five values only. Do not emit gh CLI synonyms.

**Coercion table:**

| gh CLI value (status / conclusion) | canonical |
|---|---|
| `completed` + conclusion `success` | `success` |
| `completed` + conclusion `failure` | `failure` |
| `completed` + conclusion `cancelled` | `cancelled` |
| `completed` + conclusion `skipped` | `skipped` |
| `completed` + conclusion `neutral` | `pending` |
| `completed` + conclusion `action_required` | `failure` |
| `completed` + conclusion `timed_out` | `failure` |
| `completed` + conclusion `stale` | `pending` |
| `completed` + conclusion `startup_failure` | `failure` |
| `in_progress` | `pending` |
| `queued` | `pending` |
| `waiting` | `pending` |
| `requested` | `pending` |
| `pending` | `pending` |
| `cancelled` (status) | `cancelled` |
| `skipped` (status) | `skipped` |

**Unknown-value fallthrough rule:** any gh CLI `status` or `conclusion` value not covered by the table above coerces to `pending` AND contributes to a SINGLE aggregate finding (severity 60, category `other`, summary `unmapped gh CLI check values: <comma-separated raw-status/raw-conclusion pairs across all unmapped checks>`). The unmapped condition triggers `HOLD` per the verdict mapping below. Never silently default an unmapped value to `success`.

**HOLD-verdict single-finding discipline (per `docs/specs/verdict-schema.md` line 63):** the `@@VERDICT` schema requires `HOLD` carries exactly one finding describing the missing input or gap. When `HOLD` fires from the fallthrough rule above OR the HOLD criteria below, the consuming agent emits ONE finding (the HOLD cause); any other findings discovered during the audit (linked-issue 404 per tiebreak (c), test-coverage gaps per Element C, per-comment classifications per Element A) defer to the next dispatch round when the HOLD cause has cleared. If multiple HOLD causes apply simultaneously (e.g., draft PR + unmapped CI value), aggregate them into one finding summary: `HOLD causes: <comma-separated cause list>`. The aggregation preserves schema compliance while documenting all reasons for the HOLD.

**Verdict mapping:**

- All checks `success` → APPROVE-eligible (continue to per-comment classification before final verdict).
- Any check `failure` → `REQUEST_CHANGES` at minimum; if the failure indicates a defect the PR introduces (not a pre-existing flake) → REJECT-eligible per Element A severity classification.
- Any check `pending` → `HOLD` (do not approve; re-dispatch later).
- All checks `skipped` / `cancelled` with zero `success` — CI signal absent → `HOLD` with one finding describing the gap.

**HOLD criteria (transient — re-dispatch when the cause condition clears):**

- Any required check at `pending`.
- PR is in draft state (`isDraft: true` from `gh pr view ... --json isDraft`).
- Unmapped gh CLI value encountered (per fallthrough rule above).

**ABORT criteria (structural — re-dispatch will not succeed without changing the brief):**

- `gh` CLI returns 404 (PR not found at the supplied number).
- PR state in `{closed, merged}` (review of a closed/merged PR is structurally void).
- Repository access denied (`gh` CLI returns 403 or auth error).

HOLD = transient; ABORT = structural. Emit verdict accordingly per `docs/specs/verdict-schema.md` verdict-to-findings consistency rules.

**Tiebreak rules (edge cases where multiple criteria overlap):**

1. **CI re-running after a push** — any check `status` in `{queued, in_progress, waiting, requested, pending}` overrides any check `conclusion` `failure` from the prior run. The pending re-run wins → `HOLD`. Reason: a transient failure that the new commits supersede must not be re-flagged as `REQUEST_CHANGES` until CI settles.
2. **Draft PR with passing CI** — `isDraft: true` always trumps an all-success CI conclusion → `HOLD`. The author has signaled the PR is not yet ready for review; honor the author's signal regardless of CI state.
3. **Linked-issue 404 only** — if a URL in the PR body or review thread cannot be fetched but every other audit dimension is satisfied (CI all-success, PR state `open`, diff fetchable), apply the **PR-body-intent-completeness test**: count independent intent statements in the PR description (acceptance criteria, scope bullets, completion checklists, statement-of-change sentences that are not pure pointers to the broken link). If the count is ≥1, the PR body is self-contained — emit one finding (severity 30–50, category `other`, summary `linked-issue URL unreachable: <url>`) on the APPROVE/REQUEST_CHANGES path and continue with the per-comment classification pass. If the count is 0 (PR body is exclusively pointer-only constructs like `See #123`, `Per linked spec`, `Fix from linked issue`), the PR's intent is unverifiable → `HOLD` per the single-finding discipline above. Do NOT `HOLD` on a broken link when the PR body carries independent intent.

## Element C — Test coverage assessment

Proportionality heuristics for test coverage assessment:

- PR adds a new function in a non-test file → expect at least one new test asserting the function's contract; gap is a **constructive** finding (severity 60–79).
- PR modifies an existing function's behavior (not pure refactor) → expect tests updated or added covering the changed branch; gap is **constructive** (severity 60–79).
- PR modifies a security boundary (per Element A security list) → expect tests for the security-relevant input space; gap is a **blocker** (severity 80–95).
- PR is pure refactor (no behavior change) with existing tests still green → no new tests required; flag only if the refactor measurably reduces coverage.
- PR is docs/config/comment only → no test expectation.

**Test-naming signal:** if the PR adds a function `foo` but no test file or test function references `foo`, the gap is concrete and citable. If test coverage tooling output is present in the PR's CI checks, prefer that signal over heuristic inference.

## Element D — Canonical banned-vague-fill list

The consuming agent re-greps its own emitted output verbatim against this list at methodology step 4. The list is canonical — do not paraphrase — so the re-grep is mechanical. Re-grep every `@@PR-COMMENT` block's step-4 suggested-fix text, every `tone-tag` prose field, and the full `@@VERDICT` summary against these tokens before emitting.

**Banned hedge tokens (verbatim):**

- `might`
- `may`
- `maybe`
- `perhaps`
- `possibly`
- `could potentially`
- `seems like`
- `seems to`
- `appears to`
- `looks like`
- `I think`
- `I believe`
- `IMO`
- `in my opinion`
- `kind of`
- `sort of`
- `somewhat`
- `a bit`
- `rather`
- `probably`
- `likely`
- `try to`
- `attempt to`
- `arguably`
- `in theory`
- `tends to`
- `would suggest`
- `in some cases`
- `ostensibly`
- `presumably`
- `feasibly`
- `could be argued`

**Canonical tone-tag enum:** `constructive` | `blocker` | `nit` — these three literals only.

Forbidden synonyms: `polish`, `improvement`, `issue`, `concern`. If classification is ambiguous, escalate severity per Element A higher-severity-wins rule.

**Canonical severity type:** integer 0–100.

Forbidden symbolic levels: `low`, `medium`, `high`, `minor`, `major`, `trivial`, `significant`.

**Canonical CI status enum:** `success` | `pending` | `failure` | `cancelled` | `skipped`.

Forbidden CI synonyms: `passing`, `failed`, `in-progress`. Coerce gh CLI output to canonical at parse time, not at emit time.

**Security-finding categorization convention (post-sweep 4-value subset limitation):** the canonical `@@VERDICT category` enum is `<test | other | governance | manifest>` per the Phase D sweep (commit c2a3d7c). The wider 9-value `docs/specs/verdict-schema.md` enum carries `security`, but the canonical subset does not. When a finding under Element A clause 1 (security boundary defect) requires emission, set `category: other` AND prefix the summary with the literal token `[security]` so the orchestrator's lane-overlap routing can detect security findings without an enum slot. Example summary: `[security] shell injection at <file>:<line> — shell=True with user input`. The convention is mechanical (verbatim prefix, no synonyms); the framework-level question of expanding the canonical subset or formalizing the prefix in `docs/specs/verdict-schema.md` is a §7 framework-internal structured decision deferred to `aidev-arbiter` per ADR-0022.

## Element E — Sequential handoff to dev-code-reviewer

Per `docs/specs/audit-pairing-matrix.md` line 30 (`gh-pr-review` change_type): `gh-pr-reviewer` runs first to confirm the PR is well-formed (CI signal interpretable, PR state `open`, diff fetchable); `dev-code-reviewer` reviews code substance with `gh-pr-reviewer`'s verdict and findings in hand. Lanes do not overlap: `dev-code-reviewer` does not re-litigate `gh-pr-reviewer`'s verdict, and `gh-pr-reviewer` does not anticipate `dev-code-reviewer`'s substance review.

**Required handoff payload fields:**

1. PR number, head ref, base ref.
2. PR title and stated intent (verbatim from PR body).
3. Per-file diff summary (one line per file touched, with line counts).
4. CI status (all canonical-enum values across all checks).
5. `gh-pr-reviewer`'s full `@@VERDICT` block (so `dev-code-reviewer` sees the verdict and findings before starting).
6. `gh-pr-reviewer`'s findings list with severities, tone-tags, and 4-step CoT chains.

All six fields are required. A handoff missing any field is malformed — do not dispatch `dev-code-reviewer` until the payload is complete.

If the PR is security-touching (any changed file in the Element A security boundary list), `sec-auditor` runs parallel with `dev-code-reviewer` per the `gh-pr-review` matrix row tertiary lane.

## Element F — Tone calibration matrix

Tone register is per-audit, not per-finding. Select the register once per PR based on the PR author's relationship to the project. Tone does NOT modulate severity.

**Contributor-perspective (default for external PRs — PR author is a third-party contributor):**

Register is constructive-leading: surface the rule or convention first, then the gap.

- Blocker example: "This change introduces an unbounded subprocess call (`file.py:42`). Project security boundary per ADR-NNNN requires `shell=False` or explicit arg-list construction. Gap: `shell=True` with user-input interpolation. Suggested fix: pass args as list to `subprocess.run` with `shell=False`."
- Constructive example: "This change adds `parse_input()` but no test covers it. Project convention: new public functions land with at least one contract-asserting test. Gap: `tests/test_parser.py` has no `parse_input` reference. Suggested fix: add `tests/test_parser.py::test_parse_input_basic` asserting the documented contract."
- Nit example: "Variable name `tmp_x` in `file.py:88` is less informative than the surrounding code's naming density suggests. Project convention: descriptive names on function-scope variables. Gap: `tmp_x` is opaque. Suggested fix: rename to `tokenized_input` or similar."

**Maintainer-perspective (default for first-party PRs — PR author is on the project team):**

Register is decision-leading: surface the verdict implication first, then the rule.

- Blocker example: "Blocking finding: unbounded subprocess call at `file.py:42` introduces shell injection. Required to land: `shell=False` with arg-list construction. Cite: ADR-NNNN security boundary rule."
- Constructive example: "Coverage gap: `parse_input()` lands without a test (`file.py:30`, `tests/test_parser.py` has no reference). Required to land: at least one contract-asserting test per project convention on public functions."
- Nit example: "Style nit: rename `tmp_x` (`file.py:88`) for naming density consistency. Non-blocking."

Pick one register per PR. Do not mix registers within a single audit.

**Mixed-authorship tiebreak (edge case where the PR contains commits from both project members and external contributors, or where the PR author is a former maintainer now contributing externally):** select the register based on the PR author's CURRENT relationship to the project at the time of review, not their historical relationship and not the relationship of the commit authors within the diff. If the PR author at review time is on the project team → maintainer-perspective. Otherwise → contributor-perspective. The diff is reviewed as a single unit; mixed authorship within the diff does NOT modulate register. Tiebreak is per-PR-author, not per-commit-author.

## Element G — 4-step CoT chain template

Every finding emitted in a `@@PR-COMMENT` block carries the mandatory 4-step CoT chain inline. Steps are non-reorderable.

**Template (emit verbatim per finding, filling all placeholders):**

```
1. Specific code: <file>:<line> — <≤80-char excerpt or function/class reference>
2. Standard expectation: <project convention / ADR-NNNN / rule name> — what the diff should have done
3. Gap: <one-line concrete delta between expectation and actual>
4. Suggested fix: <concrete diff direction, ≤2 sentences; not vague advice>
```

**Placeholder grammar:** angle-bracketed tokens (`<file>`, `<line>`, `<project convention / ADR-NNNN / rule name>`) are filled by the consuming agent at emission time. Do not emit unfilled placeholders.

**Mandatory completeness rule:** if any of the 4 steps cannot be filled with concrete content, the finding is speculative per CLAUDE.md §4 no-fabrication and must be dropped, not softened or emitted with a vague placeholder.

**No-skip / no-compression rule:** the 4-step chain is mandatory per finding regardless of perceived obviousness. A finding the consuming agent considers "too obvious for a chain" is signal that the chain will be short, not that it can be skipped. Even a one-line typo in a comment requires all 4 steps (1. the specific line, 2. the rule it violates, 3. the delta, 4. the concrete fix). Emitting a `@@PR-COMMENT` block without all 4 steps is a structural violation — the consuming agent's self-check at methodology step 8 must reject any block missing a step and re-emit before final `@@VERDICT`.

**Re-grep step 4 before emitting:** step 4 (suggested fix) is the primary hedge-language vector. Re-grep step 4 text against Element D's canonical banned-vague-fill list before emitting. A step 4 that reads "clean this up", "be more careful", or "consider refactoring" is a hedge-language violation.

## Output blocks

The consuming agent `gh-pr-reviewer` emits two block types only. Do not reference any other block type.

**Per-finding block:**
```
@@PR-COMMENT BEGIN
file: <path>
line: <integer>
tone-tag: <constructive | blocker | nit>
severity: <integer 0-100>
cot_1_specific_code: <file>:<line> — <excerpt>
cot_2_standard_expectation: <project convention / ADR-NNNN / rule name>
cot_3_gap: <one-line concrete delta>
cot_4_suggested_fix: <concrete diff direction, ≤2 sentences>
@@PR-COMMENT END
```

**Audit verdict block** (one per PR audit, after all `@@PR-COMMENT` blocks):
```
@@VERDICT BEGIN
verdict: <APPROVE | REQUEST_CHANGES | REJECT | HOLD | ABORT>
lane: gh-pr-reviewer
report: <relative-path | none>
findings: <integer>
@@FINDING 1
severity: <0-100>
file: <relative-path | n/a>
line: <integer | 0>
category: <test | other | governance | manifest>
summary: <one-line, ≤200 chars, no newlines>
@@VERDICT END
```

`@@VERDICT` structure and verdict-to-findings consistency rules follow `docs/specs/verdict-schema.md` (5-value verdict enum: `APPROVE` | `REQUEST_CHANGES` | `REJECT` | `HOLD` | `ABORT`). Category enum strict canonical subset: `test | other | governance | manifest`. No other category values are valid (post-sweep canonicalization per Phase D handoff §5 lesson #1; matches biz-process-builder / biz-process-reviewer / data-power-query-developer / data-vba-developer / fin-transaction-categorizer).

## When this skill PAUSEs

When a finding requires looking up third-party documentation (GitHub Actions docs, gh CLI docs, GitHub REST API docs), emit:

```
PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

Stop there. The `<subject>` placeholder is filled by the consuming agent with the specific reference subject. Do not paraphrase the PAUSE text. The scheduled-annotation wrapper is mandatory at every emission site — any deviation breaks future `research-docs-lookup` routing per ADR-0027 Consequences clause.

Domain-bounded WebFetch to `github.com` is permitted only for PR-cited issue and spec URLs (per ADR-0029 case-a exemption). Third-party documentation references outside that bound use the PAUSE shape.

## Anti-patterns

- **Softening a blocker to constructive to avoid a `REQUEST_CHANGES` verdict.** The higher-severity-wins rule in Element A is binding. A finding that crosses the security-boundary or correctness-defect threshold stays blocker even if the PR is otherwise polished.
- **Emitting a 4-step CoT chain with a vague step 4.** "Clean this up", "be more careful", "consider refactoring" are hedge-language violations. Re-grep step 4 against Element D's canonical list before emitting.
- **Skipping the hedge-language re-grep at methodology step 4.** The re-grep is mechanical (canonical list, verbatim token matching). Skipping it lets hedge tokens leak into emitted comments.
- **Coercing CI status synonyms inconsistently.** The canonical CI status enum is 5 values. Coerce gh CLI output to canonical at parse time, not at emit time. Do not emit `passing`, `failed`, or `in-progress`.
- **Mixing contributor and maintainer tone registers within a single audit.** The Element F matrix is per-audit, not per-finding. Pick the register once per PR and hold it.
- **Referencing block types that don't exist.** `gh-pr-reviewer` emits `@@PR-COMMENT` and `@@VERDICT` only. Do not reference `@@PR-REVIEW`, `@@FINDING-SUMMARY`, or any other block shape.
- **Paraphrasing the ADR-0027 PAUSE shape.** The scheduled-annotation wrapper is canonical verbatim. Any deviation breaks the future `research-docs-lookup` routing per ADR-0027 Consequences clause.
- **Emitting a 4-step CoT chain with unfilled placeholders.** A finding without all four steps filled concretely is speculative per CLAUDE.md §4 no-fabrication and must be dropped.
- **Using tone-tag synonyms.** The tone-tag enum is `constructive`, `blocker`, `nit` — literals only. `polish`, `improvement`, `issue`, `concern` are forbidden synonyms.

## Output guidance

### Semantic guidance

- Every `@@PR-COMMENT` block carries all four CoT chain fields filled with concrete content. No field carries a vague or generic description.
- Severity is a literal integer 0–100. No symbolic levels.
- Tone-tag is one of the three canonical literals. No synonyms.
- CI status values in the `@@VERDICT` summary are canonical enum values. No gh CLI synonyms.
- No hedge language in any emitted block. Re-grep against Element D's canonical banned-vague-fill list at methodology step 4 and again at methodology step 8 (via `verification-before-completion`).
- Tone register is selected once per PR and held across all findings. Same severity, different framing — tone does not modulate severity.
- A finding with a speculative step 4 is dropped, not softened. Capability honesty per CLAUDE.md §4.
- No employer, client, project, software product (beyond `gh` / GitHub per ADR-0029 case-a exemption), or internal convention names in output. Per `rules/ai-dev-conventions.md` identifying-info ban + ADR-0023 case-b.

### Tool guidance

Primary tool surface under this skill:

- `gh pr view <num> --json title,body,state,isDraft,headRefName,baseRefName` — PR metadata fetch.
- `gh pr diff <num>` — diff fetch for inline finding classification.
- `gh pr checks <num> --json name,status,conclusion` — CI status fetch; coerce to canonical CI status enum at parse time.

Domain-bounded WebFetch to `github.com` is permitted for PR-cited issue and spec URLs (ADR-0029 case-a exemption). For any third-party documentation reference (gh CLI docs, GitHub Actions docs, GitHub REST API docs), emit the ADR-0027 PAUSE shape verbatim and stop. Do not WebFetch third-party docs directly.

**No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.

## When NOT to use this skill

- Non-PR code review → `dev-code-reviewer` (downstream sequential pair per `docs/specs/audit-pairing-matrix.md` line 30).
- Pre-completion claim verification → `verification-before-completion`.
- SOP body audit → `biz-sop-discipline`.
- Language-specific code review (M transforms) → `m-language-discipline`.
- Language-specific code review (VBA macros) → `vba-language-discipline`.
- Audit-pairing resolution → `audit-pairing-lookup` reads the matrix.
- `/codex:*` dispatch reflex decisions → `codex-routing-reflex` is orchestrator-consumed per ADR-0028; out of lane here.
- Looking up third-party documentation (gh CLI docs, GitHub Actions docs, GitHub REST API docs) → emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]` (ADR-0027).
