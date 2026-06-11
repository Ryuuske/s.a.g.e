---
name: e2e-evidence-discipline
description: "Use when verifying any step of an end-to-end S.A.G.E. run (install, MCP boot, mining, search, agent dispatch, cross-session memory) and assigning a PASS/FAIL verdict with quoted evidence. Triggers on \"verify this step\", \"did the install work\", \"prove the drawer landed\". Not for designing the test crew (agent-creation), the integrity-line/sandbox conventions (rules/e2e-validation-conventions.md), or generic pre-done verification (verification-before-completion)."
---

# E2E evidence discipline

This skill encodes how a `test-*` agent earns a PASS. The rule is simple and absolute:
**no command output, no PASS.** This skill is the playbook the crew follows when turning
raw command runs into defensible verdicts.

## The verdict contract

Every step under test resolves to exactly one of three verdicts:

- **PASS** — the step did what the docs claim, and the agent can quote the exact command
  plus the real stdout/stderr that proves it.
- **FAIL** — the step did not do what the docs claim. Quote the command and the failing
  output. A FAIL is a finding, not a defect to silently fix.
- **UNVERIFIED** — the agent could not produce evidence either way (command not run,
  output not captured, blocked by a missing capability). Never upgrade UNVERIFIED to PASS.

There is no fourth state. "Probably fine" is UNVERIFIED.

## Capturing evidence

1. **Run the real command.** Not a paraphrase, not a dry-run substituted for the real
   thing (unless the dry-run IS the step under test). Capture both stdout and stderr and
   the exit code.
2. **Quote verbatim.** The evidence block is the literal output, trimmed to the relevant
   lines, with the command shown above it. Do not summarize output and call it evidence.
3. **Tie evidence to the claim.** State which documented claim the output proves. "S.A.G.E.
   --version prints a version" → quote the version line. "drawer lands in the handoff
   hall" → quote the listing that shows it there with the right hall + agents key.
4. **Record the negative space.** If a documented claim could not be exercised, say so
   explicitly as UNVERIFIED with the reason — do not omit it.

## The integrity gate

Before recording any verdict, apply the integrity line (`rules/e2e-validation-conventions.md`):
a S.A.G.E. behavior defect is a FINDING. If you find yourself about to edit S.A.G.E. source, a
config default, test data, or even the SANDBOX's own S.A.G.E. config.json / Nook rows to make a
red step go green, STOP — that is doctoring, not testing. Harness fixes (the test-* agents,
skills, rules, tool grants) are fair game; S.A.G.E.'s behavior under test — in the real env or
the jail — is not. When unsure which side of the line a fix sits on,
return `PAUSE: integrity-line question — <the fix> on sage behavior or harness?` to the
orchestrator.

## Output shape

Each verified step returns:

```
STEP: <short name>
CLAIM: <the documented behavior being checked>
CMD: <exact command run>
EVIDENCE:
<verbatim stdout/stderr, relevant lines>
EXIT: <code>
VERDICT: PASS | FAIL | UNVERIFIED
NOTE: <one line — finding, caveat, or doc-vs-reality gap; omit if none>
```

Multiple steps stack as repeated blocks. The orchestrator parses these into the PASS/FAIL
table; keep them machine-clean.

## Anti-patterns

- **Claiming PASS from a clean exit alone.** Exit 0 with empty or wrong output is not a
  PASS for a claim about output content. Check what the claim actually asserts.
- **Summarizing output as evidence.** "It listed three drawers" is a claim; the quoted
  listing is the evidence. Paste the lines.
- **Silently fixing a defect to get a green.** That is the integrity-line violation this
  skill exists to prevent. Record the FAIL.
- **Dropping UNVERIFIED steps from the report.** A claim you could not test is signal, not
  noise. Surface it.
