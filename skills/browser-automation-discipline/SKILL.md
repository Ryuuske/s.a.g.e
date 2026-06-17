---
name: browser-automation-discipline
description: "Use when authoring or repairing browser automation: automate a browser flow, scrape/extract from a page, fix a broken selector or flaky browser flow, build a scheduled browser job, or self-QA a running app. Decides committed Playwright script vs Playwright MCP. Not for running the existing e2e suite (dev-e2e-runner), non-browser code, or skill design (skill-creation)."
---

# Browser Automation Discipline

This skill encodes when a browser-touching agent uses a committed Playwright script versus the Playwright MCP server, plus the pinning and secrets safety rules that bind either path. The primary consumer is `dev-browser-operator`.

## Scope

This skill governs the core routing decision (MCP vs committed script), the explore-then-land pattern for recurring jobs, the long-run context discipline, and the two safety rules (pin everything; secrets via env). It is not a Playwright API tutorial — it links the decision, not the API surface.

## When this skill binds

Fire this skill when any of the following is true:

- You are automating a browser flow (one-off or recurring).
- You are scraping or extracting data from a page.
- You are repairing a broken selector or debugging a flaky browser flow (co-load `systematic-debugging`).
- You are building a scheduled or recurring browser job.
- You are self-QA-ing a running app in a browser.

Do NOT fire this skill for:

- Running the existing committed e2e suite → `dev-e2e-runner`.
- Designing or modifying a skill → `skill-creation`.
- Non-browser scripts or backend code → the general code implementer (no browser surface, no binding).

## The routing decision

**State the routing call explicitly before acting.** One line: `cadence: <once|repeats> | surface: <MCP|script> | rationale: <why>`.

The rule is binary:

- **Runs more than once → committed Playwright script.** The script is the deliverable. MCP live-actions are not a reproducible artifact; a repeatable job delivered as MCP actions cannot be re-run, reviewed, or scheduled.
- **One-off / interactive → Playwright MCP tools.** Navigate, snapshot, evaluate, click — use the MCP server directly. No script is produced unless the cadence changes.

Wait for the brief to name the cadence. If cadence is absent, surface `PAUSE: cadence not stated — once or repeats?` before routing.

## Explore-then-land (recurring jobs)

For any job that ends in a committed script:

1. Use the Playwright MCP tools to explore the DOM, verify selectors, and confirm the flow works end-to-end.
2. Once the path is confirmed, commit the Playwright script (project venv, pinned deps, secrets via env var).
3. Run the committed script via Bash to verify it executes cleanly.
4. The script is the deliverable — the MCP exploration is scaffolding only.

Never leave a repeatable job as a transcript of live MCP actions. That is the central failure mode this skill exists to prevent.

## Long-autonomous-run rule

In long autonomous runs, prefer scripts to conserve context. Spin up the MCP tools only for the specific moment that needs them (selector repair, visual verification), then return to scripts. Holding MCP tools open across a long session burns context and risks losing the work state.

## Safety rules

**Pin package versions and browser binaries.** The project lockfile is the contract. An unpinned Playwright script breaks silently when the upstream version changes. Pin `playwright==<version>` and run `playwright install` to pin browser binaries at the same time.

**Secrets via env var, never inline.** Credentials, API keys, session tokens — the script receives the env-var NAME; the value lives in the operator's environment. Per CLAUDE.md §12. A hardcoded credential in a committed script is a leak in the git history.

## Anti-patterns

1. **Leaving a repeatable job as live MCP actions.** If the task runs more than once, a script is the required deliverable. MCP actions are not reproducible.
2. **Unpinned package version or browser binaries.** Breaks silently. Pin both in the same step.
3. **Hardcoded inline credentials.** Env-var name in the script; value in the environment. No exceptions. Per CLAUDE.md §12.
4. **Waiting for the User to say "use Playwright" before routing.** The routing decision belongs to this skill. The cadence in the brief is the signal; the agent reads it and decides.
5. **Burning context holding MCP tools resident in long runs.** Commit a script for the recurring work and drop back to scripts. MCP is for the moment, not the session.

## Output guidance

### Formatting guidance

State the routing decision explicitly before acting: `cadence | surface | rationale`. Script-mode deliverable is a committed, pinned script at a `WHERE`-named path — not an MCP-action transcript. Include the pinned-dep comment and the repro command.

### Semantic guidance

Decision-first. No hedge on the routing call. Never present MCP live-actions as a repeatable deliverable. If the cadence is ambiguous, PAUSE — do not guess.

### Tool guidance

- **Playwright MCP tools** (user scope) — one-off interactive work: navigate, click, snapshot, evaluate, wait_for, console_messages, network_requests, take_screenshot.
- **Committed Playwright scripts** — recurring work: authored as files in the project's scripts/automation directory, pinned deps, secrets via env.
- **Long runs** prefer scripts; MCP only for the needed moment.
- Playwright is the only product name permitted in browser automation artifacts. No target client, product, or site names — those arrive via brief, never encoded in files.

## When NOT to use this skill

- Running the existing committed e2e suite → `dev-e2e-runner`.
- Non-browser code or scripts → general code implementer; this skill does not bind without a browser surface.
- Skill design → `skill-creation`.
