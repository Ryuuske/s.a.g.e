<!--
scope-owned: new-operator end-to-end walkthrough
audience: users
source: hand
review-trigger: install-flow change
-->

# Onboarding — your first run with S.A.G.E.

This is the first-run path for someone who just got S.A.G.E. and wants a working
orchestrator with a populated memory Nook. It assumes nothing beyond a terminal,
Python 3.12+, and (for the agent/skill layer) Claude Code. Five steps, each with a
verify line so you know it worked before moving on.

> Honest note on install: S.A.G.E. is distributed via GitHub only (decision
> record 0097) — local clone + `install.sh`, or no-clone
> `uv tool install git+https://github.com/Ryuuske/s.a.g.e`. Do not pip-install
> the PyPI names `sage`/`sage-mcp`; both are unrelated packages.

## 1. Install the package + CLI

```bash
git clone <your-sage-checkout> sage && cd sage
bash install.sh          # runs `pip install -e .` → puts `sage` + `sage-mcp` on PATH
```

**Verify:** `sage --version` prints a version, and `sage-mcp --help` resolves.
If `sage-mcp` is not found, re-open your shell or check that `install.sh` finished —
the MCP server must be on PATH before (or right after) the Claude Code plugin loads
(see `docs/decisions/0026-mcp-bootstrap-documented-prerequisite.md`).

## 2. Bootstrap the Nook in one command

`sage bootstrap` takes you from zero to a populated memory store: it discovers your
dev repos, registers them as wings, mines them into the Nook, and builds the registry.

```bash
sage bootstrap --dry-run   # preview: which wings get registered + which repos get mined
sage bootstrap             # do it (prompts before mining; add --yes to skip the prompt)
```

By default it scans `~/dev/github/<owner>/<repo>` and `~/dev/projects/<name>`; pass
`--root <path>` (repeatable) to point it elsewhere, or `--no-mine` to register wings
without mining yet.

**Verify:** `sage wing list` shows your registered wings; the command exits 0.

## 3. Confirm the Nook answers

```bash
sage recall "anything you just mined" --results 3
```

**Verify:** you get drawers back (not an empty result). If a repo carries ADRs under
`docs/decisions/`, they classify into a `decisions` room when a repo `sage.yaml`
defines one — `sage recall "<a decision you wrote>" --wing <repo>` should surface it.

## 4. Wire the orchestrator (Claude Code)

S.A.G.E. ships its orchestrator framework as a Claude Code plugin + a `~/.claude/CLAUDE.md`
spine. `install.sh` installs the spine (CLAUDE.md, rules, statusline, SessionStart hooks,
docs, agent-catalog.json, per-repo stub) into `~/.claude/`. The plugin serves agents,
skills, hooks, and the MCP server directly from the marketplace directory source — no
copy step is needed for those. Start a Claude Code session in a repo S.A.G.E. registered.

**Verify (the wake-up):** at session start, the SessionStart hook injects a compact
Tier-0 context block; ask the orchestrator "what's in my Nook for this wing" and it returns
your recent handoffs / decisions via the Keeper — that round-trip confirms the agent
layer, the memory layer, and the hooks are all live.

## 5. What you have now

- A **verbatim memory Nook** at `~/.sage/nook`, mined from your repos, queryable by
  `sage recall` and by the orchestrator through the `aidev-keeper`.
- The **S.A.G.E. orchestrator**: a calm, regulation-centric operations lead (CLAUDE.md
  §18) that plans before it acts (§2), routes work to specialist agents, runs every
  change through a dual-auditor pair (§16), and keeps you out of low-level approvals.
- The **autonomy loop** (`skills/autonomy-loop`) for approved multi-phase builds:
  branch → audit → fold → self-merge, behind machine-floor self-halts.

## Where to go next

- `README.md` — install detail + the Distribution section.
- `docs/concepts/mission.md` — what S.A.G.E. is for and its design principles.
- `docs/reference/agent-roster.md` — the specialist agents and how the orchestrator routes to them.
- `docs/decisions/` — the ADR trail (every non-trivial decision, append-only).

If something in steps 1–4 does not verify, re-run that step's command with the
preview/`--help` flag first — every S.A.G.E. entry point is designed to fail loud, not
silent.
