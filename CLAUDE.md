# CLAUDE.md

Entry-point stub. The full orchestrator framework — operating principles,
dual-auditor protocol, agent roster, skill catalogue — lives at
`claude-md/CLAUDE.md` (the canonical agent-instruction source) and is
installed to `~/.claude/CLAUDE.md` by `install.sh` / `install.ps1`.

Repo map, doc ownership, and key-files index: `docs/index.md`.
Mission and design principles: `docs/concepts/mission.md`.
Work items live ONLY in `internal/BACKLOG.md` (B-### IDs).
This repo's decision log: `internal/decisions/` (sequential ADRs,
generated index); destination repos keep the `docs/decisions/` convention.

## Setup

```bash
uv sync --extra dev
```

## Commands

```bash
uv run pytest -q                  # tests
uv run pytest --cov=sage_mcp --cov-report=term-missing
uv run ruff check . && uv run ruff format .
python3 scripts/gen_docs.py       # regenerate docs/reference/ + AGENTS.md
python3 scripts/gen_adr_index.py  # dev repo only (regenerates internal/decisions/README.md)
```

## Conventions

- Python: snake_case functions, PascalCase classes; ruff (`E,F,W,C901`,
  max-complexity 25); ruff format, double quotes; coverage ≥85%.
- Commits: conventional (`feat:`/`fix:`/`refactor:`/`test:`/`docs:`/`ci:`),
  one logical change per commit.
- Tests: `tests/test_*.py`, fixtures in `tests/conftest.py`.
- `docs/reference/` and `AGENTS.md` are GENERATED — edit sources, run
  `scripts/gen_docs.py`, never hand-edit the output.
- Every new file is born classified SHIP or LOCAL (`src/sage_mcp/export.py`
  allowlist is the ship surface).
