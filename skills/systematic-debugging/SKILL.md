---
name: systematic-debugging
description: Use when encountering any bug, test failure, or unexpected behavior, before proposing or applying a fix. Triggers when a previously-passing test now fails, when production behavior diverges from expected, when a stack trace appears in output, or when a "quick fix" would patch a symptom without identifying its cause. Do not use for obvious-cause compilation errors (typos, missing imports, syntax issues) — fix those directly and move on.
---

# Systematic Debugging

You investigate before you fix. Patching a symptom without identifying its cause produces fixes that drift, hide the real bug, or break elsewhere. This skill enforces a four-phase loop — observe, hypothesize, verify, repair — that keeps you honest about what you actually know versus what you're assuming.

The discipline connects directly to CLAUDE.md §4 (no fabrication): a fix you can't explain mechanistically is a fix you're guessing at. Guessed fixes belong in ADRs as "we tried this and it worked, but we don't know why" — they don't belong shipped without that flag.

## When this skill binds

You enter this skill the moment any of these are true:

- A test that was green is now red.
- An error or stack trace appears that you don't immediately understand at a mechanical level.
- The User reports behavior that contradicts your model of how the system works.
- You catch yourself about to write `try/except` that swallows an error without understanding it.
- You catch yourself adding a `sleep()`, a retry, or a "this seems to work" line.

If any of those fire, stop whatever you're doing and run this skill before continuing.

You do NOT need this skill for:
- A typo or missing import the compiler or linter has already named.
- A test failure where the assertion message tells you the exact line and exact wrong value, and the fix is obvious (e.g., off-by-one you can see).
- A behavior change that is the *intended* result of your current commit (run the test, update the assertion, move on).

## Phase 1 — Observe

Capture the failure precisely before forming any theory.

1. **Reproduce it.** Run the failing test, command, or scenario. Capture exact output — error message, stack trace, exit code, observable state. Paste it into your working notes, do not paraphrase.
2. **Establish the reproducer.** Is it deterministic? Run it three times. If it's flaky, note the failure rate. Flaky and consistent failures need different strategies and you must know which you have.
3. **Bound the change.** When did this last work? `git log -- <suspect-file>`, `git bisect`, or "did the previous commit pass." Narrow the suspect range.
4. **State what is true, what is observed, and what is assumed.** Three separate lists. Most debugging sessions go wrong because someone moved an assumption into the "true" column without evidence.

Do not proceed to Phase 2 until you can paste the exact failure output and name the last known-good state.

## Phase 2 — Hypothesize

Generate at least two competing explanations. One hypothesis is not investigation — it's commitment.

Each hypothesis must:
- Name a specific mechanism. "Race condition" is not specific; "two writers hit `cache.set()` between the read and the write" is.
- Be falsifiable by a check you can actually run. If you can't think of how to disprove it, you can't yet think of how to prove it either.
- Make a prediction. "If this hypothesis is correct, then I should see X when I do Y." If you can't predict, you don't yet understand.

If you have only one hypothesis, force a second. The second is usually weaker — that's fine, it sets the bar for the first.

## Phase 3 — Verify

For each hypothesis, run the check that would falsify it.

1. **Run the smallest discriminating experiment.** Add a log line, run the command with `-v`, attach a debugger, write a one-line test that pinpoints the assumption. Smallest = fastest feedback = most hypotheses tested per hour.
2. **Read the actual evidence.** Not what you expected to see — what's actually printed, returned, in the database, in the log file. Re-read the stack trace from the bottom up. The line that failed is rarely the line that's wrong.
3. **Update the three lists from Phase 1.** What moved from "assumed" to "true"? What moved from "true" to "actually we were wrong about that"?

If no hypothesis survives, generate new ones from what you now know. Do not skip back to Phase 4 with a still-falsifiable theory.

## Phase 4 — Repair

You have one surviving hypothesis, mechanistically explained, with evidence. Now you fix.

1. **State the root cause in one sentence.** Not the symptom — the cause. "The query returned no rows because the cache was populated before the migration ran" is a cause. "Tests fail" is a symptom.
2. **Identify the minimal change that addresses the cause.** Often this is one line. Resist the temptation to also clean up surrounding code in the same commit — that's the CLAUDE.md §9 atomic-commit rule.
3. **Write a test that reproduces the bug.** This test must fail without your fix and pass with it. (Without this, you've fixed *a* bug, not *the* bug — and you have nothing to prevent regression.)
4. **Apply the fix. Run the new test — green. Run the full suite — green.**
5. **Commit with a message that names the cause, not just the file.** Example: `fix(auth): refresh token cache after migration to avoid stale-row miss`.

## Phase 5 — Diagnosing a running multi-agent / Nook system

When the failure is not in one process but in the interaction of agents, the Nook, and tool calls, the observe→hypothesize→verify→repair loop still holds — but three S.A.G.E.-specific failure classes need their own discriminating checks before you form a hypothesis:

**(a) Memory contamination in the Nook.** Symptom: an agent reasons from a "fact" that was never stored verbatim. Check: trace the claimed fact back to its drawer/diary source. A distilled or paraphrased pseudo-fact that re-entered as if it were a stored fact is contamination — this is precisely the failure S.A.G.E.'s verbatim-no-summarize thesis exists to prevent, so its presence means a write path summarized where it should have stored verbatim. Locate the offending write, not just the bad read.

**(b) A hallucinated tool/nook_* call.** Symptom: an agent's narrative claims it ran a nook_* query, dispatched a subagent, or read a file, but no corresponding call appears in the transcript. Check: match every claimed call to an actual invocation record in the transcript. A claimed-but-absent call is hallucinated execution — treat the agent's downstream conclusions as unverified, not as evidence. Use `~/.sage/telemetry/turns.jsonl` for turn/verdict-level corroboration (which agent ran, what verdict it emitted) — telemetry is turn-granular, not per-tool-call, so the transcript is the authoritative source for individual tool invocations. The fix is in the agent's tool-discipline, not in the data it claimed to fetch.

**(c) Hidden retry loops.** Symptom: latency, duplicated side effects, or cost with no obvious cause. Check: count invocations per logical step in the transcript; a step that fires N times where it should fire once is a hidden retry. Use `~/.sage/telemetry/turns.jsonl` to correlate agent/verdict-level turn counts, which can confirm an agent fired unexpectedly many times. Bound the loop (where does the retry re-enter?) before patching — silencing the symptom leaves the loop spinning.

For all three: the discriminating evidence is the transcript (per-tool-call fidelity) and `~/.sage/telemetry/turns.jsonl` (turn/verdict-level corroboration), both read-only — read what actually happened across the agents, not what each agent claims happened, then return to Phase 2 with a falsifiable hypothesis.

## What this skill stops you from doing

- **Patching the symptom.** Wrapping the failing line in `try/except` so the error is silenced is not a fix. Add the suppression *after* you understand and decide it's correct; never as the route to understanding.
- **Stab-in-the-dark "fixes."** Changing things and re-running until green produces code that works by coincidence. The fix that lands without a hypothesis is the bug that recurs in production.
- **Whack-a-mole.** Fixing one occurrence and not asking whether the same cause has other expressions. If the cause is "stale cache after migration," every other consumer of that cache is also affected.
- **Mystery fixes.** A fix you can't explain mechanistically gets an ADR with status `accepted-with-uncertainty` and a follow-up task to understand it later. Do not just ship it and move on.
- **Trusting an agent's self-report over the transcript.** In a multi-agent run, an agent's claim that it ran a query or got a result is a hypothesis, not evidence — the transcript (per-tool-call record) and `~/.sage/telemetry/turns.jsonl` (turn/verdict-level record) are the evidence. A conclusion built on a hallucinated tool call recurs the moment you act on it.

## When to escalate

Escalate to the User (CLAUDE.md §7) if any of these are true after Phase 3:

- The bug is in a dependency you don't control, and the fix requires either pinning to an old version, vendoring, or filing it with the dependency maintainer.
- The bug reveals that the approved plan or design is incorrect.
- The repair requires a destructive operation (data migration, schema change, deletion).
- Phase 3 took more than ~1 hour of investigation and you still have no surviving hypothesis. The User can decide whether to keep digging or descope.

## Handoff

When the cycle completes, the work product is:

- A commit with the root-cause fix and the regression test, message names the cause.
- A line added to `<repo>/docs/active-plan.md` or `<repo>/docs/plans/active.md` (per ADR-0006, the latter for AI-dev work) under "follow-up debt" if the cause exposed a broader fragility worth addressing later.
- An ADR if the fix involved a non-obvious mechanism, an unintuitive trade-off, or accepted uncertainty.

The User should not have to ask "what was wrong" — the commit message and ADR answer that for future-you.
