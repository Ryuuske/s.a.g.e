---
name: verification-before-completion
description: "Use when about to claim work is complete, fixed, passing, or ready — before any \"done\" message, commit, push, or task-mark-complete. Triggers when finishing a task with a verification command (tests, lint, build, smoke run), before pushing, or on \"is it done?\" Not for purely informational tasks with nothing executable to verify, or end-to-end S.A.G.E.-run step verification with PASS/FAIL evidence (install, MCP, mining, search) — use e2e-evidence-discipline for those."
---

# Verification Before Completion

You do not claim something is done until you have run the check that proves it. The discipline is mechanical: name the claim, run the command, paste the output, then assert. In that order.

This skill is the operational arm of CLAUDE.md §4 (no fabrication, especially the "capabilities" clause): "never claim a fix addresses something it doesn't." A claim with no run behind it is a guess wearing a confident face. The cost of being wrong is the User finding out at a worse moment than you would have.

## When this skill binds

You enter this skill whenever you are about to say or write any of:

- "Fixed."
- "Done."
- "Tests are passing."
- "Ready to merge."
- "This should work now."
- "I think this resolves the issue."
- Marking an active-plan task complete.
- Pushing to any branch.
- Closing the conversation with "all set."

If any of those are coming out of your fingers, stop and run this skill first.

You do NOT need this skill for:
- Answering a factual question with no executable assertion ("what does this regex match?").
- Drafting documentation, plans, or designs that haven't been executed against code.
- Acknowledging a User message ("got it, working on it").

## The check

For each claim you're about to make, identify the *specific command* that would falsify it if it were wrong. Then run that command.

Examples of claim → falsifying command:

| Claim | Falsifying command |
|---|---|
| "The test passes." | `<the project test command, scoped to that test>` |
| "All tests pass." | `<the full test suite command>` |
| "Lint is clean." | `<the project lint command on the changed files>` |
| "The build works." | `<the project build command>` |
| "The CLI handles empty input." | `<the project test or a direct invocation>` |
| "The script doesn't leave temp files." | `ls /tmp/<expected-prefix>* 2>&1; echo "exit: $?"` after a run |
| "The migration is reversible." | Run the up migration, then the down, then a diff of the schema |
| "No secrets in the commit." | `<the project's forbidden-patterns grep>` against the diff |

The project's test, lint, build, and run commands live in the destination repo's project manifest (`package.json` scripts, `Makefile` targets, `pyproject.toml` tool config, etc.).

## The output rule

Paste the actual command output before stating the conclusion, not after. The order matters:

```
$ pytest tests/test_parser.py -v
======================== 7 passed in 0.42s ========================

Confirmed: all parser tests pass.
```

NOT:

```
All parser tests pass.

(Output omitted for brevity.)
```

The first form is verifiable by the User in two seconds — they read the output, then the claim. The second form is unfalsifiable without re-running the command yourself, which means it carries no weight. Future-you reading the commit message or audit log needs the evidence inline.

For long outputs, you may trim *intermediate* lines but never the result line (the `N passed`, `0 errors`, `exit 0`, `Build successful`, etc.). Mark the trim explicitly: `<... 142 lines elided ...>`.

## When verification fails

If the command returns non-zero, the claim is false. Period. Do not:

- Talk around it ("the test is mostly passing").
- Cite an unrelated reason it's OK ("this failure is pre-existing").
- Retry until green by changing the inputs without understanding why.

Instead:

1. Switch to `systematic-debugging` if the cause isn't immediately obvious.
2. If the failure *is* pre-existing — confirmed by checking out the previous commit and seeing the same failure — say so explicitly with the evidence. Surface it as a separate finding, not as a reason to claim "done."
3. If the failure is your work: fix it. Then re-run verification. Do not declare done from the next attempt; declare done from the one that's actually green.

## Special cases

### Pushing to a protected branch

CLAUDE.md §9 requires showing the User the complete diff before pushing to `main`. Verification before that push includes:

1. Full test suite — green.
2. Full lint run — green.
3. Build (if applicable) — green.
4. `git diff origin/main...HEAD` — shown inline to the User.
5. The User's explicit approval — received.

All five before the `git push`. Any one missing, the push doesn't happen.

### Marking active-plan tasks complete

A task in `<repo>/docs/active-plan.md` or `<repo>/.development/plans/active.md` (per ADR-0006, the latter for AI-dev work) marked `[x]` is a claim. The claim is "this acceptance criterion is satisfied." Verification:

- Re-read the acceptance criterion as written in the plan.
- Run the test or check that demonstrates it.
- Paste the output (or reference the commit + the test name) in the same edit that flips the checkbox.

A `[x]` without evidence elsewhere in the repo is a lie waiting to be found.

### Smoke runs

For tasks that don't have a unit-test-level proof but do have an end-to-end one (a CLI tool that runs, a script that produces a file, a server that responds), the smoke run *is* the verification. Run it. Capture exit code. Capture observable output (a line of stdout, a file's existence, an HTTP status). Paste those in.

## Anti-patterns

- **"It should work."** Should is not a verification command. Run the actual thing.
- **"Tests pass on my machine."** If "my machine" is the only place they pass, the project is broken; surface that as a finding.
- **"I'll verify after the commit."** No. Verify, then commit. The commit message can then reference the green run with confidence.
- **Pasting output from the wrong run.** When you re-run after a fix, paste the *new* output, not the stale one. Easy to do accidentally; check the timestamps and the output of an obvious-changing detail (test count, file count, line count).
- **Verifying once and assuming forever.** If subsequent edits could have invalidated the result (any change to source, tests, dependencies, config), re-verify. Stale green is no green.

## Handoff

When verification passes, the work product includes:

- The command output, pasted in the relevant message, commit body, or audit report.
- The corresponding `[x]` or completion message, written *after* the output.
- For protected-branch pushes: the diff + the User's explicit approval, both visible in the session.

Future-you (and the User, and the dev-code-reviewer agent) will read these and trust them. Earn that trust every time.
