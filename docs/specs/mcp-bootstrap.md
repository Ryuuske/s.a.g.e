<!--
scope-owned: MCP server PATH prerequisite design (documented prerequisite over postinstall auto-magic)
audience: users + devs
source: hand
review-trigger: install/MCP bootstrap change
-->

# MCP server bootstrap — documented prerequisite

Promoted 2026-06-10 from decision record 0026 (S.A.G.E. dev-repo log) so the shipped
docs are self-contained (shipped docs may only cite shipped docs). Names are normalized to today's `sage` / `sage-mcp`; the original decision
predates the rename.

## Context

`claude plugin install` on a fresh marketplace install copies the plugin payload
(`.claude-plugin/`, `agents/`, `skills/`, `hooks/`, `.mcp.json`) and runs no
install-time bootstrap of the underlying Python package —
`.claude-plugin/plugin.json` declares no postinstall lifecycle field, and on
machines without a prior local-clone install the `sage-mcp` binary is
observed to be absent post-install. The plugin's `.mcp.json` declares `command:
sage-mcp`, which is the
`pyproject.toml` `[project.scripts]` entry point and resolves only if the
`sage` Python package is installed and its console script directory is on
PATH. On a fresh marketplace install without separate `pip install` of the S.A.G.E. package,
Claude Code attempts to launch the MCP server and fails with "command not found,"
producing a broken plugin out of the box. Three options were evaluated: (A) a
postinstall hook that runs `uv sync` or `pip install` into a plugin-local venv and
rewrites `.mcp.json` to point at the local interpreter; (B) document the
prerequisite (installing the S.A.G.E. package so its console scripts land on PATH) in
README.md and RELEASING.md, and have install scripts emit a clear actionable
error if `sage-mcp` is absent post-install; (C) change `.mcp.json` to
`python -m sage_mcp.mcp_server` so the failure surfaces as an ImportError
rather than a missing-binary error. Option A is the install-time auto-magic class
ADR-0024 retired ("explicit documentation over auto-magic when auto-magic creates
fragility"). Option C does not actually self-bootstrap; it relabels the failure
surface from missing-binary to ImportError without removing the underlying
prerequisite, and its "self-bootstrapping" label fails CLAUDE.md §4's capability-
honesty rule. The aidev-arbiter dispatch (this ADR) selected Option B.

## Decision

The S.A.G.E. MCP server bootstrap on fresh marketplace installs is a
documented prerequisite. Users install the S.A.G.E. Python package via
an install of the S.A.G.E. package (today: `pip install -e .` from the local clone) before or
immediately after `claude plugin install`. The `.mcp.json` `command:
sage-mcp` invocation contract is preserved unchanged. README.md and
`docs/RELEASING.md` document the prerequisite in a "Prerequisites" section near
the top of the install instructions, naming both `uv tool install` and
`pip install` paths and stating the expected PATH outcome. `install.sh` and
`install.ps1` (the maintainer/local-clone install path, which continues to run
`pip install -e .` for editable development installs) additionally detect
`sage-mcp` on PATH post-install and emit a clear actionable error
message (`sage-mcp not found on PATH after install — run 'uv tool
install the S.A.G.E. package (local clone: pip install -e .)' and re-run install`) if the
binary is absent. The marketplace install path itself ships no postinstall hook
and creates no plugin-local venv; bootstrap is the user's documented step.

## Consequences

Enables: zero install-time auto-magic surface — no postinstall hook, no
uv-vs-pip detection, no plugin-local venv path resolution, no `.mcp.json`
rewrite. The install-time bug class that produced Phase R Codex findings (3 of 6
install-script portability bugs) is structurally avoided because the
marketplace install adds no new install-time code path. The install.sh + plugin
payload split established by the Session A install.sh rewrite is preserved.
Plugin behavior is deterministic across host shapes — if the prerequisite is
satisfied, the plugin works; if not, the install script surfaces a clear
actionable error. CLAUDE.md §4 capability-honesty is preserved: the plugin
claims nothing about self-bootstrapping. ADR-0024's "explicit documentation over
auto-magic" principle is applied to a second surface (MCP bootstrap), extending
the precedent. Forecloses: shipping a postinstall hook or any install-time
Python-environment manipulation through the plugin payload; rewriting `.mcp.json`
to a `python -m` invocation pattern that relabels failure modes without removing
the prerequisite; any future "self-contained plugin" claim that depends on
install-time auto-magic. This decision should be re-evaluated by superseding ADR
if `claude plugin install` adds first-class postinstall lifecycle support, or if a
future Claude Code release ships a documented plugin-install hook contract that
removes the host-Python-state coupling identified in this ADR's Context. Accepts
as cost: a one-time manual step per fresh
marketplace install (an install of the S.A.G.E. package that places `sage-mcp` on PATH)
— bounded, transparent, and documented. Users on marketplace install paths who
miss the prerequisite see a "command not found" error from Claude Code at MCP
launch; the README.md prerequisite section and the install-script post-check
error message both name the remediation command directly.
