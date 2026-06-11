"""Smoke tests for scripts/measure_framework_surface.py.

Contract A checks token-budget (agent ≤150 tok, skill ≤120 tok) and the
line-number-reference ban (_LINE_REF).  The trigger-enumeration check
(_TRIGGER_ENUM) was removed per ADR-0085; enumeration is now permitted within
the token budget.

The roster is now conformant (P5 trim complete) — --check exits 0 on the
trimmed tree.

These tests assert:
  1. --check exits 0 on the conformant tree (Contract A + C both PASS).
  2. --format json --check includes a "check" key with "exit_code": 0.
  3. --format human (no --check) exits 0 and produces human-readable output.
  4. --format json (no --check) exits 0 and produces well-formed JSON with
     the expected top-level keys.
  5. (Synthetic) token-cap fires on an over-budget description; --advisory-contracts
     unknown letters are rejected with an error.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "measure_framework_surface.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def test_check_exits_zero_on_conformant_tree():
    """Post-trim roster conforms to Contract A and C → --check exits 0."""
    result = _run("--check")
    assert result.returncode == 0, (
        f"Expected exit 0 (conformant roster post-P5 trim), got {result.returncode}.\n"
        f"stdout: {result.stdout[:500]}"
    )
    assert "Contract A — PASS" in result.stdout
    assert "Contract C — PASS" in result.stdout
    assert "Overall: PASS" in result.stdout


def test_check_json_has_check_key_no_violations():
    """--check --format json includes 'check' key with exit_code 0 and no violations."""
    result = _run("--check", "--format", "json")
    assert result.returncode == 0, f"Expected exit 0 (conformant roster), got {result.returncode}."
    data = json.loads(result.stdout)
    assert "check" in data, "'check' key missing from JSON output"
    check = data["check"]
    assert "exit_code" in check
    assert check["exit_code"] == 0
    assert "contract_a_violations" in check
    assert len(check["contract_a_violations"]) == 0, (
        f"Expected no Contract A violations on conformant tree; "
        f"got: {check['contract_a_violations']}"
    )
    assert "contract_b_advisory" in check
    assert "contract_c_violations" in check
    assert len(check["contract_c_violations"]) == 0, (
        f"Expected no Contract C violations; got: {check['contract_c_violations']}"
    )


def test_human_format_no_check_exits_zero():
    """Human format without --check should exit 0 and emit summary lines."""
    result = _run("--format", "human")
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\nstderr: {result.stderr}"
    )
    assert "Framework surface" in result.stdout
    assert "agents" in result.stdout
    assert "skills" in result.stdout


def test_json_format_no_check_exits_zero():
    """JSON format without --check should exit 0 and produce well-formed JSON."""
    result = _run("--format", "json")
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\nstderr: {result.stderr}"
    )
    data = json.loads(result.stdout)
    for key in (
        "agent_count",
        "skill_count",
        "model_tier_distribution",
        "agents",
        "skills",
        "overlap_pairs",
    ):
        assert key in data, f"Expected key '{key}' in JSON output"
    # The current tree has 36 agents and 20 skills; assert plausible counts
    assert data["agent_count"] > 0
    assert data["skill_count"] > 0
    # No 'check' key when --check is absent
    assert "check" not in data


# ---------------------------------------------------------------------------
# Tests for --advisory-contracts flag
# ---------------------------------------------------------------------------


def test_advisory_contracts_a_exits_zero():
    """--check --advisory-contracts A exits 0 on the conformant tree.

    Contract A is clean, so making A advisory changes nothing — still exit 0.
    """
    result = _run("--check", "--advisory-contracts", "A")
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\nstdout: {result.stdout[:800]}"
    )
    # Contract C must still show PASS
    assert "Contract C — PASS" in result.stdout
    # Overall must be PASS
    assert "Overall: PASS" in result.stdout


def test_advisory_contracts_a_json_exit_zero():
    """--check --advisory-contracts A --format json: exit 0, contract_a_advisory is True."""
    result = _run("--check", "--advisory-contracts", "A", "--format", "json")
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}."
    data = json.loads(result.stdout)
    check = data["check"]
    assert check["exit_code"] == 0, "exit_code in JSON must be 0"
    assert check["contract_a_advisory"] is True, (
        "contract_a_advisory must be True when A is advisory"
    )
    assert check["contract_c_advisory"] is False, (
        "contract_c_advisory must be False (C still blocks)"
    )


def test_no_advisory_flag_passes_on_conformant_tree():
    """Without --advisory-contracts the default behavior is unchanged: A and C both block.

    The roster is now conformant, so --check exits 0 with no advisory flag.
    """
    result = _run("--check")
    assert result.returncode == 0, (
        f"Expected exit 0 (conformant roster, no advisory flag), got {result.returncode}.\n"
        f"stdout: {result.stdout[:500]}"
    )
    assert "Overall: PASS" in result.stdout


# ---------------------------------------------------------------------------
# Synthetic tests — token-cap fires on over-budget description (ADR-0035 / ADR-0085)
# ---------------------------------------------------------------------------


def _make_minimal_repo(tmp_path: Path, agent_desc: str) -> Path:
    """Build a minimal repo with one agent carrying the given description."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)
    agent_text = (
        "---\n"
        f"name: test-agent\n"
        f"description: {agent_desc}\n"
        "model: sonnet\n"
        "---\n\n"
        "# Test Agent\n"
        "Lane: testing.\n"
    )
    (agents_dir / "test-agent.md").write_text(agent_text, encoding="utf-8")
    return tmp_path


def test_token_cap_fires_on_over_budget_description():
    """Contract A token-cap fires when description exceeds 150 tokens (600 chars).

    Enumeration alone (with _TRIGGER_ENUM removed per ADR-0085) must NOT produce
    a violation — only over-budget length does.
    """
    # Build a description that is over the 150-token (600-char) cap.
    # It deliberately includes trigger-like enumeration to confirm _TRIGGER_ENUM is gone.
    over_budget = (
        "Use to test token-cap enforcement. "
        "Triggers when a unit test fails, when CI reports a red build, "
        "when the User asks for a diagnostic, or when a regression is detected. "
        "Do not use for code authoring (dev-code-implementer). "
        + "x"
        * 400  # padding to push well over 150 tokens
    )
    assert len(over_budget) > 600, "Precondition: description must be >600 chars (150 tok)"

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_minimal_repo(Path(tmp), over_budget)
        result = _run("--check", "--repo", str(repo))

    assert result.returncode == 1, (
        f"Expected exit 1 (over-budget description), got {result.returncode}.\n"
        f"stdout: {result.stdout}"
    )
    assert "over budget" in result.stdout, (
        f"Expected 'over budget' in Contract A output; got: {result.stdout[:500]}"
    )


def test_trigger_enumeration_alone_does_not_fire():
    """A description with trigger enumeration but within the 150-token budget must PASS.

    ADR-0085 removed _TRIGGER_ENUM; enumeration is permitted within the token budget.
    """
    # Under 600 chars, contains a canonical Triggers: style list.
    within_budget = (
        "Use to review code. "
        "Triggers after a change lands, before push, or when the User asks for review. "
        "Do not use for code authoring."
    )
    assert len(within_budget) <= 600, "Precondition: description must be ≤600 chars"

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_minimal_repo(Path(tmp), within_budget)
        result = _run("--check", "--repo", str(repo))

    # Contract A must PASS (C may or may not raise on the synthetic roster — not our concern)
    assert "Contract A — PASS" in result.stdout, (
        f"Contract A must PASS for trigger-enumeration within budget (ADR-0085).\n"
        f"stdout: {result.stdout[:500]}"
    )


# ---------------------------------------------------------------------------
# F1 — --advisory-contracts allowlist (Phase-1 adversarial finding, sev 45)
# ---------------------------------------------------------------------------


def test_advisory_contracts_unknown_letter_errors():
    """An unknown letter in --advisory-contracts must produce a non-zero exit and error message.

    F1: a typo or unsupported letter (e.g. 'Z', 'D') should be caught at parse time
    rather than silently passed through as a no-op that could mask real violations.
    """
    result = _run("--check", "--advisory-contracts", "Z")
    assert result.returncode != 0, (
        f"Expected non-zero exit for unknown contract letter 'Z', got {result.returncode}.\n"
        f"stderr: {result.stderr[:400]}"
    )
    # argparse error messages go to stderr
    assert "Z" in result.stderr or "Z" in result.stdout, (
        f"Error message must name the unknown letter 'Z'.\nstderr: {result.stderr[:400]}"
    )


def test_advisory_contracts_c_always_blocks():
    """Contract C cannot be made advisory — it must always block.

    F1: --advisory-contracts C must error at parse time (non-zero exit, error names C).
    """
    result = _run("--check", "--advisory-contracts", "C")
    assert result.returncode != 0, (
        f"Expected non-zero exit when C is passed to --advisory-contracts, "
        f"got {result.returncode}.\nstderr: {result.stderr[:400]}"
    )
    assert "C" in result.stderr or "C" in result.stdout, (
        f"Error message must reference contract 'C'.\nstderr: {result.stderr[:400]}"
    )


def test_advisory_contracts_valid_letter_accepted():
    """Valid contract letter 'A' is accepted without error."""
    result = _run("--check", "--advisory-contracts", "A")
    # returncode 0 because both Contract A and Contract C are clean on the conformant tree.
    assert result.returncode == 0, (
        f"Expected exit 0 (A advisory, no C violations), got {result.returncode}.\n"
        f"stdout: {result.stdout[:400]}\nstderr: {result.stderr[:400]}"
    )


def test_advisory_contracts_multiple_valid_letters_accepted():
    """Multiple valid contract letters ('A,B') are accepted without error."""
    result = _run("--check", "--advisory-contracts", "A,B")
    assert result.returncode == 0, (
        f"Expected exit 0 (A+B advisory), got {result.returncode}.\n"
        f"stdout: {result.stdout[:400]}\nstderr: {result.stderr[:400]}"
    )


def test_advisory_contracts_mixed_valid_invalid_errors():
    """A mix of a valid and an invalid letter must still error on the invalid letter."""
    result = _run("--check", "--advisory-contracts", "A,X")
    assert result.returncode != 0, (
        f"Expected non-zero exit for unknown letter 'X' mixed with valid 'A'.\n"
        f"stderr: {result.stderr[:400]}"
    )
    assert "X" in result.stderr or "X" in result.stdout, (
        f"Error message must name the unknown letter 'X'.\nstderr: {result.stderr[:400]}"
    )
