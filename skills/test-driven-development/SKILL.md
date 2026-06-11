---
name: test-driven-development
description: Use when implementing any function or behavior with a definable input/output contract, before writing the implementation. Triggers when dev-code-implementer begins a task that adds or modifies behavior, when the User asks "what test should I write," or when a function's correctness is non-obvious. Do not use for refactors that preserve behavior, one-line config or copy changes, exploratory spikes, or pure visual/styling changes.
---

# Test-Driven Development

You write the failing test before the implementation. Always. The discipline is RED → GREEN → REFACTOR, in that order, with no skips.

This skill operationalizes the "no fabrication" rule (CLAUDE.md §4): code that has never failed and never passed is code whose behavior is asserted, not verified. TDD turns assertion into evidence.

## When this skill binds

This skill is in scope whenever you are about to write or modify code that has a definable contract — inputs produce outputs, or actions produce side effects. That covers almost all real implementation work.

It is NOT in scope for:
- Pure refactors where every existing test must continue to pass unchanged (the existing tests are the contract; don't add more).
- One-line config, copy, or style changes (no behavior to test).
- Exploratory spikes that will be thrown away (mark the spike clearly; tests come when the spike graduates).
- Visual-only UI changes (use the dev-ux-designer agent's review, not unit tests).

If you find yourself in a grey area, default to "TDD applies." Over-testing is recoverable; skipped tests become permanent dark matter.

## The cycle

### RED — write a failing test

1. Identify the smallest piece of behavior you can specify.
2. Write a test that exercises that behavior and would pass if the behavior were implemented correctly.
3. Run the test. **Watch it fail.** Read the failure message.
4. Confirm the failure is for the *right reason* — the function doesn't exist, the return value is wrong, the side effect didn't happen. A test that fails because of an import error or syntax mistake hasn't shown you anything yet; fix the test until it fails for the correct reason.

Do not skip step 3. A "RED" you never observed is not RED — it's a guess.

### Compile-time RED is not RED

A test that has been written but not compiled and executed is not RED — it is a draft. A true RED is a test that the project's test command actually ran and that failed for the expected reason (the function is absent, the return value is wrong, the side effect did not happen). A test that "would fail if run" is a guess, not evidence. Concretely: (1) the test file is reachable by the project's configured test command (discovered per the "What counts as a test" section, not assumed); (2) the command was invoked; (3) the runner reported this test failing — not a collection error, not a compile/syntax error, not a skipped test. A collection or compilation error means the runner never reached your assertion, so you have observed nothing about the behavior under test. Fix the test until the runner executes it and reports a genuine assertion failure; only then have you observed RED.

### GREEN — write the minimal code to pass

1. Write the simplest code that could make the test pass. Do not anticipate the next test.
2. Run the test. Watch it pass.
3. Run the entire test suite (not just the new test). Watch all pre-existing tests pass.
4. Commit. The commit message names the behavior the test specifies, e.g., `feat(parser): handle empty input by returning EmptyResult`.

"Simplest code that could pass" is a hard constraint, not a stylistic preference. If you write more, you have written untested code — by definition, because the test wasn't yet exercising it.

### REFACTOR — clean up without changing behavior

1. With the test green, refactor freely: rename, extract, deduplicate, restructure.
2. After each change, re-run the test suite. Stay green continuously.
3. When done, commit the refactor separately from the feature commit. (CLAUDE.md §9 atomic-commit rule applies.)

If a refactor breaks a test, revert it. The refactor was not behavior-preserving.

## What counts as a "test"

A test is a file under the project's configured test path that the project's test command executes. Discover both from the destination repo's project manifest (`package.json` scripts, `Makefile` targets, `pyproject.toml` tool config, etc.). If the project has no test command configured, stop and ask the User which framework to use before writing anything — do not invent one.

Tests live next to or mirror the production code per the project's convention. Do not introduce a new test layout without an ADR.

## Anti-patterns (do not do these)

- **Writing the implementation first, then the test.** This produces tests that match whatever the code does, including its bugs. The point of writing the test first is that the test embodies your *intent*, independent of the code.
- **Asserting on internal state.** Test observable behavior — return values, emitted events, persisted records, rendered output. Tests on private fields break on every refactor and discourage cleanup.
- **One mega-test per feature.** Many small tests beat one long test: each names a single behavior, fails for one reason, and survives refactors independently.
- **Skipping RED because "the test is obviously right."** No test is obviously right. The RED run is the only proof that the test actually exercises what you think it does.
- **Committing without running the full suite.** A new green test next to a broken pre-existing test is a worse state than the one you started in. Surface the breakage before adding to it.
- **Using `pytest -x` (or equivalent) and not noticing other failures.** Run the full suite at GREEN before committing. Stop-on-first-failure is fine *during* iteration; never at commit.
- **Counting an unrun (compile-time) test as RED.** A test that the runner never executed — because it failed to compile, errored during collection, or was skipped — has shown you nothing. RED is a runtime event: the runner ran the test and reported a genuine assertion failure for the expected reason.

## Handoff

When you finish a TDD cycle, the work product is at minimum:
- One commit with a new test and the production code that makes it pass.
- A clean test suite — all tests passing, no skips you introduced.
- A line in the orchestrator's plan-progress note: which acceptance criterion this cycle delivered.

If the cycle is part of a larger task with a plan, mark the relevant acceptance criterion satisfied. If the cycle revealed that the plan is wrong (e.g., the test you wanted to write doesn't fit the approved interface), STOP and escalate — do not silently change the interface.

## When to call in dev-test-engineer

This skill governs the cycle. The `dev-test-engineer` agent governs *suite adequacy* — coverage on changed files, missing edge cases, brittleness. They are complementary:

- During the cycle: this skill is sufficient.
- Before merging: dispatch `dev-test-engineer` against the diff to check for cases this cycle didn't think to cover.

That handoff is the orchestrator's responsibility, not yours mid-cycle.
