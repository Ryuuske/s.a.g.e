<!--
scope-owned: front door: what S.A.G.E. is, install, quickstart
audience: public users
source: hand
review-trigger: every release; install-flow change
-->

# S.A.G.E.

A single-user, local-first verbatim memory framework with agent-keyed drawer metadata and an absorbed multi-agent orchestrator. Maintained by Ryuuske / ZiSaStudios; designed to run on a single operator's workspace.

## Quickstart

The supported install is a local clone plus the bundled installer:

```bash
git clone https://github.com/Ryuuske/s.a.g.e
cd sage
bash install.sh
```

`install.sh` runs `pip install -e .`, which places the `sage` CLI and the
`sage-mcp` console-script on PATH — so the local-clone install already satisfies
the `sage-mcp` PATH prerequisite the plugin depends on (see Prerequisites below).

### Distribution

S.A.G.E. is distributed via GitHub only — by decision, not omission (dev
decision record 0097). No-clone install, equivalent to the local path:

```bash
uv tool install git+https://github.com/Ryuuske/s.a.g.e    # puts sage + sage-mcp on PATH
```

Do not `pip install sage` / `pip install sage-mcp` — both are unrelated strangers' packages.
(The PyPI names belong to third-party projects; installing them does not give you S.A.G.E.)

## Prerequisites

The plugin's `.mcp.json` invokes `sage-mcp`, the console-script entry point
declared in `pyproject.toml`. `claude plugin install` copies the plugin payload but
does not install the Python package, so `sage-mcp` must be on PATH before
(or immediately after) plugin install — the marketplace install path does not add it
automatically. The mechanism that puts `sage-mcp` on PATH is the local-clone
`install.sh` (it runs `pip install -e .`, an editable install). Do not attempt a
PyPI install — see "Distribution" above. Without `sage-mcp` on PATH the MCP server will
fail to start with "command not found." See
`docs/specs/mcp-bootstrap.md` for the design
rationale (documented prerequisite over postinstall auto-magic).

See `docs/index.md` for the documentation map and `claude-md/CLAUDE.md` for the orchestrator framework.

## Background automation

`install.sh` wires a cron entry that fires `claude-wakeup` ~60s after each Claude Max 5h budget reset, keeping budget windows running back-to-back automatically. Linux-only at launch; `install.ps1` emits a benign skip-message on Windows. See `installer-assets/README-claude-wakeup.md` for setup details, timing constants, and known quirks.
