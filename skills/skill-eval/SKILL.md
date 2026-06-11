---
name: skill-eval
description: "Use to eval S.A.G.E.'s agents/skills against a baseline — skill-trigger fire/no-fire accuracy and auditor verdict-stability. Triggers on 'run a trigger-accuracy eval', 'check this skill fires', 'is this verdict stable', 'establish a baseline'. Uses pass@k/pass^k + code-based/model-based graders; composes with verdict_parser + telemetry. Not for e2e PASS (e2e-evidence-discipline), gating a done claim (verification-before-completion), or authoring a skill (skill-creation)."
---

# Skill Eval

This skill encodes the procedure for measuring how reliably S.A.G.E.'s agents and skills behave against a stored expected-behavior baseline. The primary consumer is `aidev-eval-engineer`. Two distinct eval classes are covered: skill-trigger accuracy (does a skill load when it should, and stay silent when it should not?) and auditor verdict stability (does a repeated run of the same diff produce the same verdict and severity spread?). The skill-procedure boundary is clear: this file encodes the scoring mechanics, grader taxonomy, baseline format, and output contract; `aidev-eval-engineer` owns the lane, dispatch, and divergence-routing decisions. These are complementary — procedure and lane must not duplicate each other (ADR-0036).

## Two eval classes

### 1. Skill-trigger accuracy

Fire cases and no-fire cases are both mandatory. A suite that tests only fire cases cannot catch over-triggering — a skill that loads on adjacent phrasings it should ignore will pass fire-only evals while failing in production. For each case:

- **Fire case** (`case_kind: trigger-fire`): a phrasing that should load the skill. The expected result is the skill loading and the consuming agent applying its procedure.
- **No-fire case** (`case_kind: trigger-no-fire`): a phrasing that resembles the trigger but should not load the skill. The expected result is the skill staying silent and the agent handling the request without the skill's procedure.

Minimum viable suite: ≥2 fire cases + ≥2 no-fire cases per skill under eval.

### 2. Auditor verdict stability

The same diff, submitted N times, should produce consistent verdict and severity from the same auditor. Stability is measured with pass^k (all k runs agreed), not pass@k (at least one run agreed). Instability is calibration drift — the eval records which field diverged: verdict label, severity score, or both.

Per case (`case_kind: verdict-stability`): record `runs` (the k count), `verdict_set` (the distinct verdict labels seen), `severity_spread` (max − min severity score across runs). A stable auditor has `|verdict_set| = 1` and `severity_spread ≤ tolerance` defined in the suite's baseline.

## Grader taxonomy

A grader scores one case. Two types:

- **Code-based (deterministic):** substring match, JSON-equality, exit-code check, regex. No model judgment involved. The result is reproducible given identical input. Use for trigger-fire/no-fire cases where the oracle is a presence or absence of a known string.
- **Model-based (judged):** a model reads the agent output and decides whether it matches intent. Required when the expected result is a semantic property ("the agent cited a line from the file") rather than a fixed string. A model-based grader's verdict is itself drift-bearing — record which model and version graded each case so drift in the grader does not silently alter baseline comparisons.

Do not grade a model-based case as if it were a deterministic oracle. If the expected result admits more than one acceptable phrasing, the grader is model-based.

## pass@k and pass^k

- **pass@k:** at least one of k runs matched the expected result. Measures whether the behavior is achievable.
- **pass^k:** all k runs matched the expected result. Measures whether the behavior is reliable.

Verdict-stability evals use pass^k. A stability claim backed only by pass@k says "it worked once" — that is not stability. Trigger-accuracy evals that require deterministic graders should also use pass^k; model-based trigger evals may use pass@k with `k ≥ 3` and explicit tolerance.

## Eval suite harness

The eval suite harness (`tests/eval/<suite>/` directory and its `baseline.json`) is **consumer-authored per eval** — it is not pre-built or shipped as part of S.A.G.E.. When `aidev-eval-engineer` is tasked with a new eval, it (or `aidev-skill-creator`, if the eval cases are complex enough to warrant a skill) authors the suite directory, the case fixtures, and the baseline file before any run. A suite that does not yet exist cannot be invoked.

## Baseline

Baselines live at `tests/eval/<suite>/baseline.json`. The JSON is keyed by case id. Each entry records:

```json
{
  "case_id": "<suite>/<case-slug>",
  "case_kind": "trigger-fire | trigger-no-fire | verdict-stability",
  "expected": "<fire|no-fire|{verdict, severity_score}>",
  "grader": "code-based | model-based",
  "grader_version": "<model-id or 'deterministic'>"
}
```

An **establishing run** writes this file for the first time. It reports `verdict: ESTABLISHED` and asserts nothing about regression — there was no prior baseline to regress against. Do not call an establishing run a PASS.

A **regression run** compares observed results against the existing baseline. Any case where observed diverges from expected (beyond tolerance) is a regression finding.

## Compose with S.A.G.E. telemetry (read-only)

Use `~/.sage/telemetry/turns.jsonl` and `sage verdict log` reads as supplementary evidence for verdict-stability runs — telemetry records what each agent actually emitted, which resolves ambiguity when the transcript and the agent's self-report disagree. Parse each run's `@@VERDICT` block via `src/sage_mcp/verdict_parser.py` (invoke through the consumer-authored suite once it exists; do not re-implement the parser inline). Telemetry access is read-only — no writes, no mutations.

## Output

The eval result populates `aidev-eval-engineer`'s existing `@@EVAL RESULT` block. Do not introduce a new output block. Per-case fields within the block:

```
@@CASE <case_id>
result: PASS | FAIL | AMBIGUOUS
case_kind: trigger-fire | trigger-no-fire | verdict-stability
expected: <baseline expectation>
actual: <observed>
divergence: agent-regressed | eval-stale | spec-ambiguous | none
```

For verdict-stability cases, add:
```
runs: <k>
verdict_set: [<distinct verdicts seen>]
severity_spread: <max - min>
```

Only FAIL and AMBIGUOUS cases emit a `@@CASE` block; PASS cases count in the aggregate.

## Anti-patterns

- **Editing the artifact under eval or the baseline to turn red into green.** This destroys the regression signal the next run depends on. When a case diverges, classify it: `agent-regressed` routes to `aidev-code-implementer`; `eval-stale` routes to eval maintenance; `spec-ambiguous` routes to `aidev-planner` or the User. Route the divergence; do not paper over it.
- **Calling an establishing run a PASS.** With no prior baseline, the run cannot assert regression status. Verdict is ESTABLISHED.
- **Reporting verdict stability with pass@k instead of pass^k.** pass@k says the behavior was achievable; pass^k says it was reliable. Stability requires pass^k.
- **Omitting no-fire cases from a trigger-accuracy suite.** A fire-only suite cannot detect over-triggering. Both case kinds are required to produce a defensible trigger-accuracy verdict.
- **Grading a model-based case as a deterministic oracle.** If the correct answer admits multiple phrasings, only a model-based grader can judge it. Scoring it with substring match manufactures false passes or false fails.

## When NOT to use this skill

- Earning a PASS for a single end-to-end run step → `e2e-evidence-discipline`
- Gating a "done" or commit claim → `verification-before-completion`
- Driving RED→GREEN on production code → `test-driven-development`
- Authoring or modifying a SKILL.md file → `skill-creation`

## Tool guidance

CoT: logic-heavy — scoring divergence requires classifying `agent-regressed | eval-stale | spec-ambiguous`; calibration-drift attribution requires tracing grader model versions and severity spreads before drawing a conclusion.

Tools: Read, Grep, Glob, Bash. Bash is bounded: invoke `uv run pytest tests/eval/<suite>` (only after the consumer has authored the suite) and read `~/.sage/telemetry/turns.jsonl`. No `git` mutation, no writes to any artifact or baseline, no network calls.
