<!--
scope-owned: release runbook: version locations, gates
audience: maintainer
source: hand
review-trigger: release-process change
-->

# Releasing S.A.G.E.

S.A.G.E. is a single-operator system; releases are local milestone
tags on `main`, not PyPI publishes. The repo intentionally has no
publish-on-tag CI job. This file documents the procedure the operator
runs to cut a tagged release.

## Pre-release checklist

Run from the repo root before cutting a release tag.

### 1. Verify `sage-mcp` entry point alignment

The plugin configs reference `sage-mcp` as the MCP server command,
which resolves to a console script declared under `[project.scripts]` in
`pyproject.toml`. If these disagree, a fresh install ships a plugin
config pointing at a binary that was never installed.

```bash
grep -r sage-mcp pyproject.toml .claude-plugin
```

Expected — one line per file:

```
pyproject.toml:sage-mcp = "sage_mcp.mcp_server:main"
.claude-plugin/plugin.json:      "command": "sage-mcp"
.mcp.json:    "command": "sage-mcp"
```

If `pyproject.toml` has no match, **stop** — the entry point is missing
and any fresh `pip install` will ship a broken plugin config.

### 2. Version consistency

The version string lives in four places and must agree:

```bash
grep -E '^version|^__version__|"version"' \
    pyproject.toml src/sage_mcp/version.py \
    .claude-plugin/plugin.json .claude-plugin/marketplace.json
head -12 CHANGELOG.md
```

Expected — all four show the same `X.Y.Z`, and `CHANGELOG.md`'s most-
recent header is `## [X.Y.Z] - YYYY-MM-DD` (or `## [Unreleased]` if
this run is going to cut it). The plugin-manifest pair
(`plugin.json` + `marketplace.json`) MUST bump on every release so
`claude plugin update sage@sage` resolves the new version (no
uninstall/reinstall dance). `tests/test_version_consistency.py` enforces
all four agree — a forgotten manifest bump is a red test, not a silent
drift.

### 3. Tests + lint clean

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

All four must pass before tagging.

### 4. Install dry-run smoke

```bash
export CLAUDE_DIR=$(mktemp -d -t sage_release_XXXX)
bash install.sh --dry-run --no-per-repo-claude-md --claude-md=no
```

Exit 0 and no missing-file errors.

### 5. Prerequisite documentation and post-install post-check verification

Per ADR-0026 (`.development/decisions/0026-mcp-bootstrap-documented-prerequisite.md`),
the `sage-mcp` binary is a documented prerequisite — not installed by the
plugin payload. Before tagging, verify that the documentation and the install-
script post-check are current and consistent.

**5a. Confirm prerequisite docs name both install commands and the PATH outcome.**

`README.md` and `.claude-plugin/README.md` must each name `uv tool install
S.A.G.E. from the local clone (`bash install.sh`), must NOT name a PyPI
install command — distribution is GitHub-only by decision (record 0097) — and must state
that either command places `sage-mcp` on PATH. One-line check:

```bash
grep -n "install sage\b\|sage-mcp" \
    README.md .claude-plugin/README.md
```

Expected — at least two hits per file, covering both install commands and the
`sage-mcp` PATH reference. If either file is missing a line, update it
before tagging. The ordering must be `uv tool install` first, `pip install`
second, matching the ADR-0026 preference hierarchy.

**5b. Confirm install scripts emit the actionable error when `sage-mcp` is absent.**

`install.sh` and `install.ps1` must detect `sage-mcp` on PATH post-install
and emit the ADR-0026 actionable error message (`sage-mcp not found on PATH
after install — install the S.A.G.E. package from the local clone (pip install -e .) and
re-run install`) when the binary is absent. This post-check is implemented by D.0.4
(two commits: D.0.4a for `install.sh`, D.0.4b for `install.ps1`). The live sandbox
verification is D.0.5.

Verify the post-check is present in both scripts:

```bash
grep -n "sage-mcp not found on PATH" install.sh install.ps1
```

Expected — one match per file. If a script is missing the check, D.0.4 has not yet
landed; do not tag until it does.

## Cutting the release

> Throughout this section and Rollback, `<X.Y.Z>` is the NEW version — decided by a
> semver bump classification (patch/minor/major) via `gh-release-manager` PLAN mode
> over the release diff range — and `<YYYY-MM-DD>` is today's UTC date. Replace both
> before running any command; never reuse a version that already has a dated
> CHANGELOG section.

### 1. Promote the CHANGELOG `[Unreleased]` section

In `CHANGELOG.md`, change:

```markdown
## [Unreleased]
```

to (using today's UTC date):

```markdown
## [<X.Y.Z>] - <YYYY-MM-DD>
```

Commit:

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): cut [<X.Y.Z>] - <YYYY-MM-DD>"
```

### 2. Tag

```bash
git tag -a v<X.Y.Z> -m "v<X.Y.Z> — release"
git tag --list                  # confirm only the new tag is present
```

### 3. Push (operator-driven)

Per ADR-0011, S.A.G.E. ships **no** destructive-command hook —
force-push protection is the destination repo's choice and
responsibility. A normal non-force push to `main` followed by a tag
push is the supported path. If history was rewritten (only ever done
with explicit User decision — see ADR-0094 / §8 override precedent),
the operator pushes from a terminal outside Claude Code if their local
`~/.claude/` setup includes their own destructive-command guard:

```bash
git push origin main
git push origin v<X.Y.Z>
```

### 4. Verify on GitHub

- `git ls-remote origin refs/tags/v<X.Y.Z>` returns the new tag SHA
- `git ls-remote origin refs/heads/main` matches local HEAD
- GitHub Actions CI on the pushed `main` is green

### 5. Reset the changelog for the next cycle

In `CHANGELOG.md`, insert a fresh `## [Unreleased]` heading above the
just-cut `[<X.Y.Z>]` section, then commit and push:

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): reopen [Unreleased] for next cycle"
git push origin main
```

## Rollback

If a tag was created prematurely:

```bash
git tag -d v<X.Y.Z>
git push origin :refs/tags/v<X.Y.Z>      # delete remote tag (non-destructive on main)
```

Tag deletion is reversible — `git reflog` preserves the commit; re-tag
when the issue is fixed.
