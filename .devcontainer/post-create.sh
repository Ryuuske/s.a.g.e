#!/usr/bin/env bash
set -euo pipefail

echo "=== sage Dev Container Setup ==="

pip install -e ".[dev]"

# ruff is pinned exactly in pyproject.toml (0.15.14), so the `pip install -e
# ".[dev]"` line above already gives contributors the same version CI runs.

pip install pre-commit
pre-commit install

echo ""
echo "=== Verification ==="
echo "python: $(python --version)"
echo "pytest: $(python -m pytest --version 2>&1 | head -1)"
echo "ruff:   $(python -m ruff --version 2>&1 | head -1)"
echo ""
echo "Ready. Run: pytest tests/ -v"
