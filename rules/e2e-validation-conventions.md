# End-to-end validation conventions

These conventions apply when running a full new-user end-to-end (E2E) validation of
S.A.G.E. — install through multi-session use — or any equivalent black-box exercise of
the package, MCP server, installer, or Nook. The `test-*` agent family executes this
work; the orchestrator sequences it.

## The integrity line (non-negotiable)

Amending agents, skills, rules, or tool grants to improve the test harness is allowed
and expected. **Doctoring S.A.G.E.'s actual behavior to fake a pass is forbidden.** A S.A.G.E.
CLI / MCP / installer / Nook defect is recorded as a FINDING with quoted evidence —
never patched away to turn a red step green. This applies to S.A.G.E. state inside the sandbox
too: editing the jail's `sage` config.json, dedup thresholds, or Nook rows to coax a recall
hit is doctoring, not setup. The ONLY artifacts that count as harness (and are fair to
amend) are the `test-*` agents, their skills, this and sibling rules, and tool grants. If a
fix is ambiguous between "harness improvement" and "behavior doctoring", the agent halts and
surfaces the question to the orchestrator rather than guessing.

## Evidence over claims

No command output, no PASS. Every PASS/FAIL verdict quotes the real stdout/stderr that
proves it, with the exact command that produced it. "I ran X and it worked" without the
captured output is not evidence and does not satisfy a verdict. Absence of evidence is
recorded as UNVERIFIED, never silently upgraded to PASS.

## Sandbox-first for destructive state

Any step that writes package state, Nook data, a crontab entry, or Claude Code config
runs inside a proven sandbox before it runs anywhere real. "Proven" means the isolation
boundaries are echoed and shown to resolve inside the jail BEFORE the destructive command
runs. If isolation cannot be guaranteed for install / Nook / cron, the run halts and the
orchestrator asks the User — it does not proceed on a hope.

## Follow the documented path

The new-user path under test is the one the docs describe (`docs/guides/onboarding.md`,
`README.md`, `CLAUDE.md`). Deviating from the documented commands is itself a finding:
if a step needs an undocumented manual action to succeed, that gap is recorded, not
papered over.

## Teardown proves non-interference

A sandboxed run ends by proving the real environment is untouched: the real `~/.claude`,
crontab, and Nook are diffed or stat-compared against pre-run snapshots. Intentional
live additions (the authored agents/skills/rules) are the only permitted delta and are
enumerated explicitly.
