"""Regression guard: CI runs full Contract-A gating with no advisory escape hatch.

Phase-1 adversarial audit finding F2 (sev 35) noted that nothing mechanically
prevented re-introduction of --advisory-contracts A in ci.yml, which would
silently downgrade Contract-A violations from blocking to advisory.

This test locks the state established by commit 235f156:
  - .github/workflows/ci.yml MUST NOT contain "--advisory-contracts"
    (the temporary escape hatch must stay gone).
  - .github/workflows/ci.yml MUST invoke
    "measure_framework_surface.py --check"
    (the framework-surface gate must be present and in full blocking mode).

Both assertions are deterministic file-content reads — no subprocess, no
network, no LLM. They fail the moment ci.yml is edited to reintroduce the
advisory flag or remove the gate step.
"""

from __future__ import annotations

from pathlib import Path

CI_YML = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _ci_text() -> str:
    assert CI_YML.exists(), f"CI workflow not found at {CI_YML}"
    return CI_YML.read_text(encoding="utf-8")


def test_ci_does_not_contain_advisory_contracts_flag():
    """ci.yml must not contain '--advisory-contracts'.

    The flag is the temporary escape hatch that was removed in commit 235f156
    (ADR-0085).  Re-introducing it would silently downgrade Contract-A
    violations from blocking to advisory, defeating the gate.
    """
    text = _ci_text()
    assert "--advisory-contracts" not in text, (
        "ci.yml contains '--advisory-contracts' — the temporary escape hatch "
        "must stay removed (ADR-0085, P5 commit 235f156).  "
        "Full Contract-A gating must be unconditional."
    )


def test_ci_invokes_framework_surface_check():
    """ci.yml must invoke 'measure_framework_surface.py --check'.

    This confirms the framework-surface gate step is present and runs in full
    blocking mode (no advisory flag softening the exit code).
    """
    text = _ci_text()
    assert "measure_framework_surface.py --check" in text, (
        "ci.yml does not invoke 'measure_framework_surface.py --check' — "
        "the Contract-A/C gate step is missing or has been renamed/removed.  "
        "Restore the 'framework-surface --check' step."
    )
