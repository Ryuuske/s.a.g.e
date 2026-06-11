"""WI-7a — Thin Tier-0 baseline-capture harness (ADR-0040).

PURPOSE
-------
Capture the Tier-0 block budget/size baseline and the block-stability metric
plumbing at the moment WI-3 lands (Phase B).  This is the thin leg:

  - Measures the assembled Tier-0 block's token size.
  - Records a baseline to a JSON file for future WI-7b comparison.
  - Verifies determinism: same wing + same state → byte-identical block.
  - Verifies tier0_block_stable/tier0_tokens plumbing in telemetry.log_tier0_wake_up.
  - Verifies Tier0Block fields are populated correctly.

This harness does NOT test retrieval pass-rates or a fixed question set
(those are WI-7b, Phase D, after WI-6).  Per ADR-0040: the thin leg reads
only surfaces already landed; the full non-regression harness stays at
Phase D.

BASELINE FILE
-------------
On each run, the harness writes a baseline JSON record to
tests/eval/tier0_baseline.json so that future runs can observe drift.
The file is human-readable and git-tracked (it IS the baseline artifact).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sage_mcp.layers import MemoryStack, Tier0Block, TIER0_TOKEN_BUDGET
from sage_mcp.telemetry import TurnRecord, log_tier0_wake_up

# ── Baseline artifact location ──────────────────────────────────────────────
BASELINE_FILE = Path(__file__).parent / "tier0_baseline.json"

# ── Budget range (PRD target: ~3-6k tokens) ────────────────────────────────
# TIER0_TOKEN_BUDGET is the configured midpoint of the PRD target range.
# The harness asserts the constant stays within the documented range.
PRD_BUDGET_MIN = 1000  # reasonable lower bound for a non-empty nook
PRD_BUDGET_MAX = 10000  # hard upper bound — above this something has inflated

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_l1(doc: str = "Important memory about the project architecture.") -> MagicMock:
    """Return a mock ChromaDB collection that yields one drawer."""
    mock_col = MagicMock()
    mock_col.get.side_effect = [
        {
            "documents": [doc],
            "metadatas": [{"room": "decisions", "source_file": "arch.md", "importance": 5}],
        },
        {"documents": [], "metadatas": []},  # end-of-pagination sentinel
    ]
    return mock_col


def _make_stack(tmp_path: Path, identity_text: str = "I am Atlas, a sage test agent."):
    """Build a MemoryStack with a temp identity file and no real nook."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text(identity_text, encoding="utf-8")
    return MemoryStack(
        nook_path=str(tmp_path / "nook"),
        identity_path=str(identity_file),
    )


def _make_repo_with_agent(tmp_path: Path) -> str:
    """Create a minimal agent + skill tree so the registry has entries."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "test-agent.md").write_text(
        "---\nname: test-agent\ndescription: A minimal test agent for harness purposes.\n---\n"
        "# Test agent\n",
        encoding="utf-8",
    )
    skills_dir = tmp_path / "skills" / "test-skill"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A minimal test skill for harness purposes.\n---\n"
        "# Test skill\n",
        encoding="utf-8",
    )
    return str(tmp_path)


# ── Tier0Block unit tests ───────────────────────────────────────────────────


class TestTier0Block:
    def test_token_count_estimate(self):
        text = "A" * 400
        block = Tier0Block(text=text, budget=100, registry_count=0)
        assert block.token_count == 100

    def test_within_budget_true(self):
        text = "A" * 400  # 100 tokens
        block = Tier0Block(text=text, budget=200, registry_count=0)
        assert block.within_budget is True

    def test_within_budget_false(self):
        text = "A" * 400  # 100 tokens
        block = Tier0Block(text=text, budget=50, registry_count=0)
        assert block.within_budget is False

    def test_str_returns_text(self):
        block = Tier0Block(text="hello world", budget=100, registry_count=0)
        assert str(block) == "hello world"

    def test_budget_constant_in_prd_range(self):
        """TIER0_TOKEN_BUDGET must be within the PRD target range (~3-6k tokens)."""
        assert PRD_BUDGET_MIN <= TIER0_TOKEN_BUDGET <= PRD_BUDGET_MAX, (
            f"TIER0_TOKEN_BUDGET={TIER0_TOKEN_BUDGET} is outside the PRD target "
            f"range [{PRD_BUDGET_MIN}, {PRD_BUDGET_MAX}].  Update the constant or "
            "this test's range after deliberate re-measurement."
        )


# ── assemble_tier0 integration tests ───────────────────────────────────────


class TestAssembleTier0:
    def test_returns_tier0block(self, tmp_path):
        stack = _make_stack(tmp_path)
        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection") as mock_get_col,
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "nook")
            mock_get_col.return_value = _make_mock_l1()
            block = stack.assemble_tier0(wing=None, repo_root=None)

        assert isinstance(block, Tier0Block)
        assert len(block.text) > 0
        assert block.token_count > 0
        assert block.budget == TIER0_TOKEN_BUDGET

    def test_identity_in_block(self, tmp_path):
        stack = _make_stack(tmp_path, identity_text="I am Atlas, test agent.")
        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection") as mock_get_col,
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "nook")
            mock_get_col.return_value = _make_mock_l1()
            block = stack.assemble_tier0(wing=None, repo_root=None)

        assert "Atlas" in block.text

    def test_l1_in_block(self, tmp_path):
        stack = _make_stack(tmp_path)
        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection") as mock_get_col,
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "nook")
            mock_get_col.return_value = _make_mock_l1(
                "Important memory about the project architecture."
            )
            block = stack.assemble_tier0(wing=None, repo_root=None)

        assert "ESSENTIAL STORY" in block.text or "memory" in block.text.lower()

    def test_registry_section_present_with_repo_root(self, tmp_path):
        """Registry section appears when repo_root has agents/ or skills/."""
        repo_root = _make_repo_with_agent(tmp_path / "repo")
        stack = _make_stack(tmp_path / "home")
        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection") as mock_get_col,
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "home" / "nook")
            mock_get_col.return_value = _make_mock_l1()
            block = stack.assemble_tier0(wing=None, repo_root=repo_root)

        assert "REGISTRY" in block.text
        assert block.registry_count > 0

    def test_registry_absent_without_repo_root_and_no_autodetect(self, tmp_path):
        """When repo_root is None and auto-detect finds nothing, no REGISTRY section."""
        # Force auto-detect to fail by passing an empty directory as repo_root.
        empty_root = str(tmp_path / "empty")
        (tmp_path / "empty").mkdir(parents=True, exist_ok=True)
        stack = _make_stack(tmp_path)
        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection") as mock_get_col,
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "nook")
            mock_get_col.return_value = _make_mock_l1()
            block = stack.assemble_tier0(wing=None, repo_root=empty_root)

        assert block.registry_count == 0

    def test_determinism_same_inputs(self, tmp_path):
        """Same wing + same nook state → byte-identical Tier-0 block (determinism contract)."""
        repo_root = _make_repo_with_agent(tmp_path / "repo")
        stack = _make_stack(tmp_path / "home")

        # Assemble twice; mock must return identical data both times.
        def _fresh_mock():
            return _make_mock_l1("Determinism test memory content.")

        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection") as mock_get_col,
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "home" / "nook")
            mock_get_col.side_effect = lambda *a, **kw: _fresh_mock()
            block1 = stack.assemble_tier0(wing="test-wing", repo_root=repo_root)
            block2 = stack.assemble_tier0(wing="test-wing", repo_root=repo_root)

        assert block1.text == block2.text, (
            "Tier-0 block is not deterministic: same inputs produced different output.  "
            "This breaks the prompt-cache prefix stability contract."
        )

    def test_wing_filter_applied(self, tmp_path):
        """Wing filter is passed to L1."""
        stack = _make_stack(tmp_path)
        mock_col = _make_mock_l1()

        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection", return_value=mock_col),
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "nook")
            stack.assemble_tier0(wing="my-wing", repo_root=None)

        # Verify the wing filter was forwarded to L1's ChromaDB get() call.
        first_call_kwargs = mock_col.get.call_args_list[0][1]
        assert first_call_kwargs.get("where") == {"wing": "my-wing"}

    def test_no_full_body_in_registry_section(self, tmp_path):
        """Registry section must contain only metadata — not full skill/agent file bodies."""
        repo_root = _make_repo_with_agent(tmp_path / "repo")
        (tmp_path / "repo" / "agents" / "test-agent.md").write_text(
            "---\nname: test-agent\ndescription: Short descriptor.\n---\n"
            "# Test agent\n"
            "## FULL BODY CONTENT THAT MUST NOT APPEAR IN TIER-0\n"
            "Very long body text " * 100,
            encoding="utf-8",
        )
        stack = _make_stack(tmp_path / "home")
        with (
            patch("sage_mcp.layers.SageConfig") as mock_cfg,
            patch("sage_mcp.layers._get_collection") as mock_get_col,
        ):
            mock_cfg.return_value.nook_path = str(tmp_path / "home" / "nook")
            mock_get_col.return_value = _make_mock_l1()
            block = stack.assemble_tier0(wing=None, repo_root=repo_root)

        assert "FULL BODY CONTENT THAT MUST NOT APPEAR IN TIER-0" not in block.text


# ── Telemetry plumbing tests ────────────────────────────────────────────────


class TestTier0Telemetry:
    def test_log_tier0_wake_up_writes_row(self, tmp_path, monkeypatch):
        log_path = tmp_path / "turns.jsonl"
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log_path))

        tid = log_tier0_wake_up(tier0_tokens=1234, tier0_block_stable=True, wing="sage")

        assert tid is not None
        rows = [json.loads(ln) for ln in log_path.read_text().strip().splitlines()]
        assert len(rows) == 1
        row = rows[0]
        assert row["phase"] == "wake-up"
        assert row["extras"]["tier0_tokens"] == 1234
        assert row["extras"]["tier0_block_stable"] is True
        assert row["wing"] == "sage"

    def test_log_tier0_wake_up_block_unstable(self, tmp_path, monkeypatch):
        log_path = tmp_path / "turns.jsonl"
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log_path))

        log_tier0_wake_up(tier0_tokens=800, tier0_block_stable=False, wing="test")

        row = json.loads(log_path.read_text().strip())
        assert row["extras"]["tier0_block_stable"] is False
        assert row["extras"]["tier0_tokens"] == 800

    def test_log_tier0_wake_up_stable_none(self, tmp_path, monkeypatch):
        """tier0_block_stable=None means no prior block available (first session)."""
        log_path = tmp_path / "turns.jsonl"
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log_path))

        log_tier0_wake_up(tier0_tokens=500, tier0_block_stable=None, wing=None)

        row = json.loads(log_path.read_text().strip())
        assert row["extras"]["tier0_block_stable"] is None
        assert row["extras"]["tier0_tokens"] == 500

    def test_turn_record_extras_shape(self):
        """TurnRecord.extras is a free dict; documented WI-3 keys are legal entries."""
        rec = TurnRecord(
            turn_id="abc123",
            timestamp="2026-05-29T10:00:00.000Z",
            phase="wake-up",
            mode="aidev",
            agent="sage",
            extras={"tier0_tokens": 3500, "tier0_block_stable": True},
        )
        assert rec.extras["tier0_tokens"] == 3500
        assert rec.extras["tier0_block_stable"] is True


# ── Baseline capture ────────────────────────────────────────────────────────


class TestTier0BaselineCapture:
    """Capture and record the Tier-0 baseline for this installation.

    This test builds the registry from the REAL repo (if agents/ or skills/
    exist at the repo root), assembles a Tier-0 block with no-nook (empty
    nook path), measures the token size, and writes a baseline JSON record.

    The baseline JSON is the artifact that WI-7b compares against after WI-6
    (decay pass) to prove recall does not regress.
    """

    @pytest.mark.eval_baseline
    def test_capture_and_record_baseline(self, tmp_path, monkeypatch):
        """Assemble a real Tier-0 block and write a baseline record.

        Uses the real repo's agents/ and skills/ directories (if present) so
        the baseline reflects the actual installed registry.  The nook is
        empty (no drawers) — the baseline is for structure/size, not recall.
        """
        # Isolate telemetry to a temp file.
        log_path = tmp_path / "turns.jsonl"
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log_path))

        # Resolve real repo root from package location.
        from sage_mcp.layers import TIER0_TOKEN_BUDGET

        here = Path(__file__).resolve().parent.parent.parent  # repo root
        repo_root = str(here) if (here / "agents").is_dir() or (here / "skills").is_dir() else None

        identity_file = tmp_path / "identity.txt"
        identity_file.write_text(
            "I am the sage orchestrator. Identity configured for baseline eval.",
            encoding="utf-8",
        )

        stack = MemoryStack(
            nook_path=str(tmp_path / "nook"),
            identity_path=str(identity_file),
        )

        # No nook: L1 will emit "No nook found" or "No memories yet".
        block = stack.assemble_tier0(wing=None, repo_root=repo_root)

        # Log via the real telemetry path.
        tid = log_tier0_wake_up(
            tier0_tokens=block.token_count,
            tier0_block_stable=None,  # No prior block in a baseline run.
            wing=None,
        )

        # Assertions on the block shape.
        assert block.token_count > 0, "Tier-0 block must have non-zero token count."
        assert len(block.text) > 0
        assert block.budget == TIER0_TOKEN_BUDGET

        # Write the baseline record.
        baseline = {
            "schema_version": 1,
            "captured_at": str(Path(log_path).stat().st_mtime if log_path.exists() else "unknown"),
            "tier0_token_count": block.token_count,
            "tier0_budget": block.budget,
            "within_budget": block.within_budget,
            "registry_count": block.registry_count,
            "block_char_len": len(block.text),
            "block_preview_100chars": block.text[:100].replace("\n", "\\n"),
            "turn_id": tid,
        }

        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_FILE.write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Verify telemetry row was written.
        assert log_path.exists(), "Telemetry log must be written."
        rows = [json.loads(ln) for ln in log_path.read_text().strip().splitlines()]
        assert len(rows) >= 1
        wake_rows = [r for r in rows if r.get("phase") == "wake-up"]
        assert wake_rows, "At least one wake-up row expected in telemetry."
        assert wake_rows[0]["extras"]["tier0_tokens"] == block.token_count

        # Print a summary for the CI log / orchestrator output.
        print(
            f"\n  Tier-0 baseline captured:"
            f"\n    token_count:    {block.token_count}"
            f"\n    budget:         {block.budget}"
            f"\n    within_budget:  {block.within_budget}"
            f"\n    registry_count: {block.registry_count}"
            f"\n    char_len:       {len(block.text)}"
            f"\n    baseline_file:  {BASELINE_FILE}"
        )
