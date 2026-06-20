<!--
scope-owned: plugin packaging notes
audience: devs
source: hand
review-trigger: plugin change
-->

# S.A.G.E. Claude Code Plugin

A Claude Code plugin wrapping the S.A.G.E. memory system — a single-user verbatim memory store with agent-keyed drawer metadata so any agent can recall its own past work.

## Prerequisites

- Python 3.12+
- `sage-mcp` on PATH — this is what the plugin's `.mcp.json` invokes; without it
  the MCP server will not start. The supported way to get `sage-mcp` on PATH
  today is the local-clone install below, whose `install.sh` runs
  `pip install -e .` — see
  [Local Clone (the supported install path)](#local-clone-the-supported-install-path).

## Installation

### Local Clone (the supported install path)

S.A.G.E. is distributed via GitHub (plugin marketplace + git install;
decision record 0097). Install from a local clone via the bundled installer:

```bash
git clone https://github.com/Ryuuske/s.a.g.e ~/sage/github/Ryuuske/s.a.g.e
cd ~/sage/github/Ryuuske/s.a.g.e
bash install.sh
```

The installer:
1. Installs the spine into `~/.claude/`: CLAUDE.md, rules, statusline, SessionStart
   hooks, docs, agent-catalog.json, and the per-repo stub.  The plugin
   serves agents/skills/hooks/MCP directly from the marketplace directory source —
   no copy into `~/.claude/` is required for those.
2. Runs `pip install -e .` into the active venv.
3. Registers the repo's marketplace, then installs the plugin by name:
   `claude plugin marketplace add <repo>` followed by `claude plugin install sage@sage`.

The plugin's `.mcp.json` handles MCP server registration automatically when
the plugin loads — no manual `claude mcp add` is needed.

### Dev mode (testing without installing)

```bash
bash install.sh --dev-mode
```

This prints the command to run Claude Code with the plugin directory
instead of installing it permanently:

```
claude --plugin-dir /path/to/sage
```

### Upgrading from prior installs

If you previously ran the v0.1.0 installer and your Claude Code state retains
a user-scope S.A.G.E. MCP registration from a pre-0.2.0 installer release,
run `claude mcp remove sage -s user` once before or after installing.
This allows the plugin's `.mcp.json` to become the single source of truth.
New installations and post-0.2.0 installations require no action — the installer
no longer auto-removes user-scope registrations.

### Marketplace path (future)

When S.A.G.E. is published to a marketplace, the install path will be:

```bash
claude plugin marketplace add github:Ryuuske/s.a.g.e
claude plugin install sage@sage
```

`sage-mcp` lands on PATH via either supported path:

```bash
git clone https://github.com/Ryuuske/s.a.g.e && cd s.a.g.e && bash install.sh
# or, no-clone:
uv tool install git+https://github.com/Ryuuske/s.a.g.e
```

Distribution is GitHub-only (decision record 0097). Do not pip-install the
PyPI names `sage`/`sage-mcp` — both are unrelated third-party packages.

## Post-Install Setup

`install.sh` handles MCP wiring and `pip install -e .` from the local
clone. To populate the nook in one step — discover repos, register
wings, mine, and build the registry — run:

```bash
sage bootstrap
```

Preview without writing anything: `sage bootstrap --dry-run`

Skip mining (register only): `sage bootstrap --no-mine`

To onboard a single project interactively, run from inside Claude Code:

```
/sage:init <path-to-project>
```

This guides you through wing registration, room detection, and first
mine. See `docs/concepts/mission.md` for the wings/rooms/drawers model.

## Available Slash Commands

| Command | Description |
|---------|-------------|
| `/sage:help` | Show available tools, skills, and architecture |
| `/sage:init` | Set up S.A.G.E. — install, configure MCP, onboard |
| `/sage:search` | Search your memories across the nook (supports `--agent`) |
| `/sage:mine` | Mine projects and conversations into the nook (supports `--agents`) |
| `/sage:status` | Show nook overview — wings, rooms, drawer counts |

## Hooks

S.A.G.E. registers two hooks that run automatically. Both are orchestrator-gated
emergency-only fallbacks — they only fire when the orchestrator's Keeper agent
has NOT been dispatched in the last 30 minutes (per `~/.sage/last_keeper_dispatch`):

- **Stop** — On session end, if `~/.sage/current_wing` is set and the
  Keeper skip-check fails, files one emergency drawer with the last ~4000 chars
  of the session's transcript so the next wake-up can recover context.
- **PreCompact** — Same emergency-drawer fallback as Stop, fired before Claude
  Code compacts the session context. The drawer lands in the `handoff-precompact`
  room of the current wing.

Neither hook fires periodically or attempts semantic importance-detection — both
are last-resort safety nets that defer to the Keeper when she's been dispatched.

`install.sh` also installs a SessionStart hook (`claude-wakeup-sessionstart.py`) that surfaces the last fire and next scheduled fire from `~/.cache/claude-wakeup/state.json` — silent when absent. See `installer-assets/README-claude-wakeup.md` for the full background-automation how-to.

## MCP Server

The plugin automatically configures a local MCP server (`sage-mcp`) exposing 29 `nook_*` tools for storing, searching, and managing memories. No manual MCP setup is required — `/sage:init` handles it.

## Full Documentation

See the main [README](../README.md) and `docs/concepts/mission.md` for project scope.
