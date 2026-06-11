<!--
scope-owned: statusline fields + illustrative samples
audience: users
source: hand
review-trigger: statusline change
-->

# S.A.G.E. statusline + Codex budget

A Claude Code statusline that shows folder, git branch, model, Claude
session %, Codex 5h %, and Codex weekly %. Plus a `codex-budget` skill
and SessionStart hook the orchestrator consults before invoking any
`/codex:*`.

## What you see

At the bottom of every Claude Code session:

```
<repo>  ⎇ <branch>  <model>  C 16%·39m  Cwk 20%·2d12h  X 1%·4h59m  Xwk 2%·4d04h   (illustrative)
```

Label key:

```
C / Cwk — Claude 5h + weekly session quota (same data as /status, delivered via Claude Code's statusline stdin).
X / Xwk — Codex 5h + weekly rate-limit window (from codex app-server).
```

- `Ryuuske/s.a.g.e` — `<user>/<repo>` if cwd is under `~/dev/github/`; otherwise the folder basename.
- `⎇ main` — current git branch (dropped outside a repo).
- `<model>` — the model name as reported by the Claude Code statusline payload (whatever is current at runtime).
- `C / Cwk` — % used and time-to-reset for Claude session and weekly windows.
- `X / Xwk` — % used and time-to-reset for Codex 5h and weekly windows.

Colors: green &lt;60%, yellow 60–85%, red &gt;85%.

## Install

### WSL / Linux

```
./install.sh
```

Requires `python3` on `PATH`. Will install agents, skills, statusline,
hooks, and patch `~/.claude/settings.json` to register the statusline
+ SessionStart hook.

### Native Windows

```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Requires Python 3 (any of `py`, `python3`, `python`). Windows Claude
Code reads `%USERPROFILE%\.claude\` — different from WSL's
`~/.claude/`, so install once per environment.

### Dry run (either platform)

```
./install.sh --dry-run
.\install.ps1 -DryRun
```

## How it works

Two data sources, each cached under `~/.cache/sage/`:

1. **Claude window** — read directly from Claude Code's statusline stdin
   JSON: `rate_limits.five_hour.used_percentage` (5h session) and
   `rate_limits.seven_day.used_percentage` (weekly). Same data source as
   `/status`. Reset times come from the matching `resets_at` epoch fields.
   When the data is absent (first render of a new session, or
   non-subscriber accounts), the statusline shows `—` and reads the
   last-known-good from `~/.cache/sage/claude.json` if fresher than
   5 minutes.
2. **Codex window** — spawns `codex app-server`, sends a JSON-RPC
   `account/rateLimits/read`, exits. Returns the same RateLimitSnapshot
   the Codex plugin already uses, including primary (300min) and
   secondary (10080min / weekly) windows, plus `planType` and credits.
   Cached 30s.

Claude data arrives synchronously from stdin (no subprocess). The Codex
fetch is the only networked call; its result is cached 30s. Both fall
back gracefully when their data source is unavailable, never blocking the
statusline.

## Orchestrator integration

Two channels feed Codex budget data to the orchestrator:

1. **SessionStart hook** — once per session, `hooks/inject-codex-budget.py`
   prints a single-line summary into context:
   `codex_budget plan=prolite 5h=0%/4h59m weekly=2%/4d04h`.
2. **On-demand skill** — the `codex-budget` skill runs
   `~/.claude/statusline/bin/sage-codex-budget.py --pretty` for fresh JSON. It encodes the
   refuse/ask thresholds the orchestrator applies before invoking any
   `/codex:*`.

Mechanical rule encoded in the skill:

| primary used | weekly used | rate-limit-reached / stale | decision |
| ---          | ---         | ---                        | ---      |
| any          | any         | true / stale               | **Refuse** + escalate |
| any          | &gt; 90        | —                          | **Refuse** this invocation |
| &gt; 95         | any         | —                          | **Refuse** + escalate |
| &gt; 80         | any         | —                          | **Ask** the User |
| any          | &gt; 75        | —                          | **Ask** the User |
| else         | else        | —                          | Proceed silently |

For `free`/`go`/`plus`/`prolite` plans with zero credits, **Ask** is
upgraded to **Refuse** — the rate-limit reset is the only thing
standing between you and a hard hit.

## Manual inspection

```
~/.claude/statusline/bin/sage-codex-budget.py --pretty           # use cached if fresh
~/.claude/statusline/bin/sage-codex-budget.py --refresh --pretty # force a live sample
```

## Uninstall

```
# Restore settings.json from the backup the installer made:
mv ~/.claude/settings.json.sage.bak ~/.claude/settings.json

# Remove the installed files:
rm -rf ~/.claude/statusline ~/.claude/hooks/inject-codex-budget.py
rm -rf ~/.claude/skills/codex-budget
```

On Windows substitute `Remove-Item -Recurse -Force` and
`%USERPROFILE%\.claude\` for the paths.

## Troubleshooting

- **Statusline shows `C —`.** Claude Code hasn't received the first API response of this session yet, OR your plan doesn't expose rate-limit headers. The last-known-good cache (5 min TTL) will serve data from previous renders if available.
- **Statusline shows `X —  Xwk —`.** Codex data source is unreachable or data is stale. Both segments render `—` symmetrically with the Claude side. Check that `codex` is on PATH.
- **Cold render &gt; 5s.** Cache files at `~/.cache/sage/` are missing AND both fetches are running cold. Subsequent renders should be ~30ms once cache is warm.
- **`Codex budget` line not appearing at session start.** Verify the SessionStart hook entry is present in `~/.claude/settings.json` (the installer adds it idempotently; re-run if missing).
- **Wrong folder shown.** Statusline reads `workspace.current_dir` from Claude Code's stdin JSON. If it's wrong, Claude Code reported a different cwd than expected.
