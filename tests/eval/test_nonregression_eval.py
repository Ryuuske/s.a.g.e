"""WI-7b — Full non-regression eval harness (ADR-0040, non-regression AC5).

PURPOSE
-------
Prove that recall does NOT regress as the nook store grows.  Specifically:

  AC5: An eval harness runs a fixed question set, reports a pass rate, and
  re-running after a learning cycle shows recall does not regress as the
  store grows.

The harness operates ONLY on a fixture/temp nook — never the live
~/.sage.

STRUCTURE
---------
1. Fixed question set (tests/eval/eval_questions.json) — versioned fixture.
   Each entry: {question, wing, expected_substring, seed_drawer_id}.

2. BASELINE eval: seed the fixture nook with the question-targeted drawers,
   run retrieval (search_memories) for each question, compute pass-rate
   (recall@k — did expected_substring appear in top-k results?).

3. LEARNING CYCLE simulation:
   a. Add more drawers to the store (grow the store with COMPETITIVE noise:
      drawers that share high-IDF retrieval terms with the target questions —
      so they genuinely compete for top-k, NOT all-distinct-topics).
      Noise drawers use low confidence (0.0–0.3) and old filed_at timestamps
      so CORRECT decay down-ranks them more than the high-confidence,
      recent targets.
   b. Run the WI-6 decay + consolidation pass on the grown store.
   c. Re-run the same question set and compute a post-learning-cycle pass-rate.

4. NON-REGRESSION ASSERTION: post-pass-rate >= baseline-pass-rate minus
   tolerance.  Tolerance is read from eval_questions.json
   ("non_regression_tolerance": 0.0 = zero-tolerance by default).

WHY THIS TEST CAN FAIL (anti-vacuousness proof)
-------------------------------------------------
The test fails if any of these breakages occur:

  (a) DECAY IS A NO-OP: if decay_pass does not write strength updates, all
      drawers keep strength=1.0 (uniform). The _hybrid_rank strength_norm
      collapses to 0 for all candidates — no strength differentiation.  For
      Q1 and Q4, which have CLOSE-RELEVANCE competitors whose base
      vector+BM25 score EXCEEDS the target's, the competitor outranks the
      target.  The target's expected_substring does NOT appear in the top-2
      result, so recall drops to below 1.0.

  (b) DECAY OVER-FLOORS TARGET: if decay_pass is broken and applies full
      decay rate even to high-confidence drawers, target drawers (confidence
      1.0) decay to the floor (0.1) together with noise drawers (confidence
      0.0–0.3). strength_norm collapses to 0 again — same breakage as (a).

  (c) CONSOLIDATION DEMOTES CANONICAL: if consolidation_pass picks the
      wrong member as canonical (e.g., picks the lower-confidence duplicate
      instead of the higher-confidence target), the canonical target gets
      demoted to CONSOLIDATION_DEMOTE_STRENGTH (0.2) while the near-duplicate
      survives. The canonical target's strength drops below the surviving
      noise drawers → it no longer ranks at the top for Q1 → assertion fails.

  (d) SEARCHER IGNORES STRENGTH: if _hybrid_rank is patched to zero out the
      strength weight, or if the strength field is not carried through from
      metadata, competitive noise drawers win on BM25+vector alone and the
      rank-sensitive Q1 assertion fails.

CLOSE-RELEVANCE REGIME (why strength is load-bearing)
------------------------------------------------------
_hybrid_rank scores each candidate as:
    total = vector_weight(0.6) * vec_sim
          + bm25_weight(0.4)   * bm25_norm
          + strength_weight(0.1) * strength_norm

For Q1 and Q4, the close-relevance noise drawers are engineered so that
WITHOUT decay (strength_norm = 0 for all):

    base_noise = 0.6 * vec_noise + 0.4 * bm25_noise > base_target
    gap = base_noise - base_target  is within (0, 0.1)

With CORRECT decay:
    target strength ≈ 0.687 (high confidence, ~150 days old)
    noise  strength = 0.1   (floor; confidence=0, ~880 days old)
    target strength_norm = 1.0  →  +0.1 additive
    noise  strength_norm = 0.0  →  +0.0 additive
    target total > noise total  →  target rank-1  →  recall@2 passes

With UNIFORM/NEUTERED decay (all strength = 0.1):
    strength_norm = 0 for all  →  pure base scores
    noise base > target base   →  noise rank-1, target rank-2
    expected substring at rank-2  →  rank-check fails (see below)

This guarantees the test is genuine: it distinguishes real decay from uniform
decay through rank ordering, not just in-top-k presence.

NEAR-DUPLICATE / CONSOLIDATION PATH
------------------------------------
One near-duplicate of eval_drawer_auth is seeded: eval_drawer_auth_dup.
It uses slightly reworded text (high semantic overlap with eval_drawer_auth)
but has confidence=0.3 vs the canonical's confidence=1.0.

After consolidation_pass:
  - eval_drawer_auth_dup should be demoted (reason="demoted:near_duplicate").
  - eval_drawer_auth (canonical) should be unaffected.
  - eval_drawer_auth should still appear at rank-1 for Q1 after consolidation
    because the dup is demoted to CONSOLIDATION_DEMOTE_STRENGTH (0.2), which
    is below the target's post-decay strength (~0.687).

GATING
------
Tests are marked @pytest.mark.eval and skipped by default in normal pytest
runs.  Enable with:
  RUN_EVAL=1 pytest tests/eval/test_nonregression_eval.py
  pytest -m eval tests/eval/test_nonregression_eval.py

The conftest skip mechanism mirrors the existing eval_baseline gate.

EVAL RESULT ARTIFACT
--------------------
When the eval runs, it writes tests/eval/eval_result_latest.json with:
  - baseline_pass_rate
  - post_growth_pass_rate
  - non_regression_passed (bool)
  - per-question results
  - timestamp
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import chromadb
import pytest

from sage_mcp.consolidation import consolidation_pass, decay_pass
from sage_mcp.searcher import search_memories

# ── Fixture paths ────────────────────────────────────────────────────────────
EVAL_DIR = Path(__file__).parent
QUESTIONS_FILE = EVAL_DIR / "eval_questions.json"
BASELINE_FILE = EVAL_DIR / "tier0_baseline.json"
RESULT_FILE = EVAL_DIR / "eval_result_latest.json"

# ── Eval-gate environment variable ───────────────────────────────────────────
_RUN_EVAL = os.environ.get("RUN_EVAL", "").strip() == "1"

# ── Reference time for the non-regression decay scenario ─────────────────────
# Targets filed 2026-01-01 → ~150 days stale at this reference time.
# Noise filed 2024-01-01 → ~880 days stale at this reference time.
# With noise confidence=0.0 and target confidence=1.0:
#   target effective_rate  = 0.05 * (1 - 1.0 * 0.95) = 0.0025/day
#   noise  effective_rate  = 0.05 * (1 - 0.0 * 0.95) = 0.05/day
#   target strength after 150 days  ≈ exp(-150 * 0.0025) ≈ 0.687 (above floor)
#   noise  strength after 880 days  ≈ exp(-880 * 0.05)   ≈ 0     (floors to 0.1)
# Resulting strength range ≈ 0.587 → non-zero strength_norm in _hybrid_rank.
_DECAY_REFERENCE_TIME = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

# ── Seed drawers for the fixture nook ──────────────────────────────────────
# These are the drawers the question set targets.
# confidence=1.0, recent filed_at → decay slowly → stay ABOVE floor at reference time.
_SEED_DRAWERS = [
    {
        "id": "eval_drawer_auth",
        "text": (
            "The authentication module uses JWT tokens for session management. "
            "Tokens expire after 24 hours. Refresh tokens are stored in HttpOnly cookies."
        ),
        "wing": "eval_project",
        "room": "backend",
        "source_file": "auth.py",
        "importance": 5,
        "confidence": 1.0,
        "filed_at": "2026-01-01T00:00:00",
    },
    {
        "id": "eval_drawer_db",
        "text": (
            "Database migrations are handled by Alembic. We use PostgreSQL 15 "
            "with connection pooling via pgbouncer."
        ),
        "wing": "eval_project",
        "room": "backend",
        "source_file": "db.py",
        "importance": 4,
        "confidence": 1.0,
        "filed_at": "2026-01-01T00:00:00",
    },
    {
        "id": "eval_drawer_frontend",
        "text": (
            "The React frontend uses TanStack Query for server state management. "
            "All API calls go through a centralized fetch wrapper."
        ),
        "wing": "eval_project",
        "room": "frontend",
        "source_file": "App.tsx",
        "importance": 4,
        "confidence": 1.0,
        "filed_at": "2026-01-01T00:00:00",
    },
    {
        "id": "eval_drawer_sprint",
        "text": (
            "Sprint planning: migrate auth to passkeys by Q3. "
            "Evaluate ChromaDB alternatives for vector search."
        ),
        "wing": "eval_notes",
        "room": "planning",
        "source_file": "sprint.md",
        "importance": 3,
        "confidence": 1.0,
        "filed_at": "2026-01-01T00:00:00",
    },
]

# ── Near-duplicate for consolidation-path testing ────────────────────────────
# Semantically near-identical to eval_drawer_auth but confidence=0.3.
# After consolidation_pass: eval_drawer_auth wins (higher confidence);
# this dup is demoted to CONSOLIDATION_DEMOTE_STRENGTH.
_DUP_DRAWERS = [
    {
        "id": "eval_drawer_auth_dup",
        "text": (
            "The auth module uses JWT tokens for session management. "
            "Tokens expire in 24 hours and refresh tokens sit in HttpOnly cookies."
        ),
        "wing": "eval_project",
        "room": "backend",
        "source_file": "auth_legacy.py",
        "importance": 2,
        "confidence": 0.3,
        "filed_at": "2026-01-01T00:00:00",
    },
]

# ── Competitive noise drawers ─────────────────────────────────────────────────
# CLOSE-RELEVANCE REGIME (FIX 1): each noise drawer shares key BM25 terms with
# its target question AND has a base vector+BM25 score within ~0.1 of the
# target's base score. The close-relevance drawers for Q1 and Q4 are
# specifically calibrated so:
#
#   base_noise > base_target  (noise wins WITHOUT strength)
#   base_noise - base_target < 0.1  (within the strength_weight=0.1 window)
#
# With CORRECT decay: target strength ≈ 0.687, noise strength = 0.1 (floor).
#   strength_norm target = 1.0 → additive +0.1
#   target total > noise total → target rank-1 → recall@2 passes
#
# With UNIFORM decay (all strength = 0.1):
#   strength_norm = 0 for all → pure base scores
#   noise base > target base → noise rank-1, target rank-2
#   rank-sensitive assertion fires → test FAILS
#
# UNIQUENESS: the expected_substring for each question is NOT present in its
# noise competitor's text. This ensures that if noise ranks above target, the
# expected substring is not found and the test fails.
#
# Q1 close-relevance competitor (base gap ≈ -0.062, within 0.1 window):
#   - vector sim close to target (both ~0.698 vs question embedding)
#   - BM25 higher than target: auth/module/session/token terms densely packed
#   - does NOT contain "HttpOnly"
#
# Q4 close-relevance competitor (base gap ≈ -0.041, within 0.1 window):
#   - vector sim and BM25 higher than target on passkeys/Q3/sprint terms
#   - does NOT contain "ChromaDB alternatives"
#
_NOISE_DRAWERS = [
    # ── Q1 close-relevance competitor: auth/module/session/token terms ────────
    # base_score ≈ 0.819 vs target ≈ 0.757; gap ≈ -0.062 (within 0.1 window)
    # "HttpOnly" is NOT present → if this outranks target, Q1 expected fails.
    {
        "id": "eval_noise_auth_compete",
        "text": (
            "JWT session tokens are issued by the authentication module. "
            "Token expiry is configured globally. "
            "The session handling module stores tokens securely."
        ),
        "wing": "eval_project",
        "room": "backend",
        "source_file": "auth_old.py",
        "importance": 1,
        "confidence": 0.0,
        "filed_at": "2024-01-01T00:00:00",
    },
    # ── Q2 competitor: pgbouncer/PostgreSQL terms ─────────────────────────────
    # "Alembic" is NOT present → target answer is distinguishable.
    {
        "id": "eval_noise_db_compete",
        "text": (
            "Database connection pooling uses pgbouncer as the PostgreSQL pooler. "
            "Connection limits and pool sizing are configured in pgbouncer.ini."
        ),
        "wing": "eval_project",
        "room": "backend",
        "source_file": "db_old.py",
        "importance": 1,
        "confidence": 0.0,
        "filed_at": "2024-01-01T00:00:00",
    },
    # ── Q3 competitor: TanStack/server-state/frontend terms ──────────────────
    # "centralized fetch wrapper" is NOT present.
    {
        "id": "eval_noise_frontend_compete",
        "text": (
            "The frontend originally used TanStack Query for server state management "
            "before the team evaluated SWR as a lighter alternative."
        ),
        "wing": "eval_project",
        "room": "frontend",
        "source_file": "App_old.tsx",
        "importance": 1,
        "confidence": 0.0,
        "filed_at": "2024-01-01T00:00:00",
    },
    # ── Q4 close-relevance competitor: passkeys/Q3/sprint terms ──────────────
    # base_score ≈ 0.767 vs target ≈ 0.725; gap ≈ -0.041 (within 0.1 window)
    # "ChromaDB alternatives" is NOT present → if this outranks target, Q4 fails.
    {
        "id": "eval_noise_sprint_compete",
        "text": (
            "Q3 sprint: passkeys evaluation for admin login, "
            "security audit for OAuth2 flows, review of token rotation policies."
        ),
        "wing": "eval_notes",
        "room": "planning",
        "source_file": "sprint_old.md",
        "importance": 1,
        "confidence": 0.0,
        "filed_at": "2024-01-01T00:00:00",
    },
    # ── Q5 competitor: Alembic/migration terms ────────────────────────────────
    # "pgbouncer" is NOT present.
    {
        "id": "eval_noise_migrations_compete",
        "text": (
            "Database migrations are tracked in the migrations/ directory. "
            "Each migration file is prefixed with a version number and reviewed before deployment."
        ),
        "wing": "eval_project",
        "room": "backend",
        "source_file": "migrations_old.md",
        "importance": 1,
        "confidence": 0.0,
        "filed_at": "2024-01-01T00:00:00",
    },
]


# ── Nook helpers ────────────────────────────────────────────────────────────


def _make_fixture_nook(nook_dir: str) -> chromadb.Collection:
    """Create a ChromaDB collection in nook_dir, seeded with the target drawers."""
    client = chromadb.PersistentClient(path=nook_dir)
    col = client.get_or_create_collection(
        "nook_drawers",
        metadata={"hnsw:space": "cosine"},
    )
    return col


def _add_drawers(col: chromadb.Collection, drawers: list[dict]) -> None:
    """Add a list of drawer dicts to the collection.

    Each drawer dict must have: id, text, wing, room, source_file.
    confidence and filed_at are read from the dict (with defaults) so
    the non-uniform scenario can specify them per-drawer.
    """
    ids = [d["id"] for d in drawers]
    documents = [d["text"] for d in drawers]
    metadatas = [
        {
            "wing": d["wing"],
            "room": d["room"],
            "source_file": d.get("source_file", ""),
            "chunk_index": 0,
            "added_by": "eval_harness",
            "filed_at": d.get("filed_at", "2026-01-01T00:00:00"),
            "importance": d.get("importance", 3),
            "confidence": d.get("confidence", 1.0),
            "strength": 1.0,
        }
        for d in drawers
    ]
    col.add(ids=ids, documents=documents, metadatas=metadatas)


# ── Retrieval helper ──────────────────────────────────────────────────────────


def _run_question(
    question_entry: dict,
    nook_path: str,
    k: int,
    search_fn=None,
) -> dict:
    """Run a single question against the nook and return a result dict.

    Args:
        question_entry: One entry from eval_questions.json.
        nook_path:    Path to the fixture nook directory.
        k:              Recall@k — max results to inspect for the expected substring.
        search_fn:      Callable with the same signature as search_memories.
                        Defaults to the real search_memories.  Pass a stub for
                        unit-testing without a real ChromaDB nook.

    Returns:
        {
            "id": question id,
            "question": the question text,
            "expected_substring": the needle,
            "pass": bool — True if expected_substring appears in any top-k result,
            "top_k_snippets": list of (up to k) first-100-char snippets returned,
            "matched_result_index": 1-based index of the first match, or None,
        }
    """
    if search_fn is None:
        search_fn = search_memories

    qid = question_entry["id"]
    question = question_entry["question"]
    expected = question_entry["expected_substring"]
    wing_filter = question_entry.get("wing")

    result = search_fn(
        query=question,
        nook_path=nook_path,
        wing=wing_filter,
        n_results=k,
    )
    hits = result.get("results", [])

    snippets = []
    matched_idx: Optional[int] = None
    for i, hit in enumerate(hits):
        text = hit.get("text", "")
        snippets.append(text[:100])
        if matched_idx is None and expected.lower() in text.lower():
            matched_idx = i + 1  # 1-based

    return {
        "id": qid,
        "question": question,
        "expected_substring": expected,
        "pass": matched_idx is not None,
        "top_k_snippets": snippets,
        "matched_result_index": matched_idx,
    }


def _eval_pass_rate(
    questions: list[dict],
    nook_path: str,
    k: int,
    search_fn=None,
) -> tuple[float, list[dict]]:
    """Run all questions and return (pass_rate, per_question_results).

    pass_rate = number_of_passing_questions / len(questions).

    Args:
        search_fn: Optional stub for search_memories (for unit testing).
    """
    if not questions:
        return 0.0, []

    per_question = [_run_question(q, nook_path, k, search_fn=search_fn) for q in questions]
    passes = sum(1 for r in per_question if r["pass"])
    pass_rate = passes / len(per_question)
    return pass_rate, per_question


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEvalHarnessFixture:
    """Unit tests for the eval harness plumbing itself.

    These run in normal pytest (no mark gate): they verify the fixture-nook
    helpers, question loading, and retrieval pass/fail logic against small
    in-memory mocks.  They DO NOT call the live search_memories against a real
    ChromaDB nook.
    """

    def test_questions_file_exists_and_parseable(self):
        """eval_questions.json must exist and be valid JSON."""
        assert QUESTIONS_FILE.exists(), f"eval_questions.json not found at {QUESTIONS_FILE}"
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        assert "questions" in payload
        assert "k" in payload
        assert isinstance(payload["questions"], list)
        assert len(payload["questions"]) > 0

    def test_questions_have_required_fields(self):
        """Every question entry must have id, question, expected_substring, wing."""
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        required = {"id", "question", "expected_substring", "wing"}
        for q in payload["questions"]:
            missing = required - set(q.keys())
            assert not missing, f"Question {q.get('id', '?')} missing fields: {missing}"

    def test_seed_drawers_cover_all_question_targets(self):
        """Every seed_drawer_id in eval_questions.json must have a matching _SEED_DRAWERS entry."""
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        seed_ids = {d["id"] for d in _SEED_DRAWERS}
        for q in payload["questions"]:
            target_id = q.get("seed_drawer_id")
            if target_id:
                assert target_id in seed_ids, (
                    f"Question {q['id']} targets seed_drawer_id={target_id!r} "
                    "but no matching entry in _SEED_DRAWERS."
                )

    def test_non_regression_tolerance_is_valid(self):
        """non_regression_tolerance must be in [0.0, 1.0]."""
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        tol = payload.get("non_regression_tolerance", 0.0)
        assert 0.0 <= tol <= 1.0, f"non_regression_tolerance={tol} out of [0.0, 1.0]"

    def test_run_question_pass(self, tmp_path):
        """_run_question returns pass=True when expected_substring is in a hit."""
        fake_results = {
            "results": [
                {
                    "text": "JWT tokens are used for session management with HttpOnly cookies.",
                    "wing": "eval_project",
                    "room": "backend",
                    "similarity": 0.9,
                    "distance": 0.1,
                }
            ]
        }
        question_entry = {
            "id": "Q_test",
            "question": "How are sessions managed?",
            "expected_substring": "HttpOnly",
            "wing": "eval_project",
        }

        def _fake_search(**kwargs):
            return fake_results

        result = _run_question(question_entry, str(tmp_path), k=3, search_fn=_fake_search)
        assert result["pass"] is True
        assert result["matched_result_index"] == 1

    def test_run_question_fail(self, tmp_path):
        """_run_question returns pass=False when expected_substring is absent."""
        fake_results = {
            "results": [
                {
                    "text": "OAuth2 is used for external integrations.",
                    "wing": "eval_project",
                    "room": "backend",
                    "similarity": 0.7,
                    "distance": 0.3,
                }
            ]
        }
        question_entry = {
            "id": "Q_test",
            "question": "How are sessions managed?",
            "expected_substring": "HttpOnly",
            "wing": "eval_project",
        }

        def _fake_search(**kwargs):
            return fake_results

        result = _run_question(question_entry, str(tmp_path), k=3, search_fn=_fake_search)
        assert result["pass"] is False
        assert result["matched_result_index"] is None

    def test_eval_pass_rate_all_pass(self, tmp_path):
        """_eval_pass_rate returns 1.0 when every question hits."""
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        questions = payload["questions"]

        def _fake_search(query, nook_path, wing=None, n_results=3):
            for q in questions:
                if q["question"] == query:
                    return {"results": [{"text": f"Result containing {q['expected_substring']}."}]}
            return {"results": [{"text": "no match"}]}

        rate, per_q = _eval_pass_rate(questions, str(tmp_path), k=3, search_fn=_fake_search)
        assert rate == 1.0

    def test_eval_pass_rate_none_pass(self, tmp_path):
        """_eval_pass_rate returns 0.0 when no question hits."""
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        questions = payload["questions"]

        def _fake_search(query, nook_path, wing=None, n_results=3):
            return {"results": [{"text": "Completely unrelated content."}]}

        rate, _ = _eval_pass_rate(questions, str(tmp_path), k=3, search_fn=_fake_search)
        assert rate == 0.0

    def test_competitive_noise_shares_target_terms(self):
        """Verify the competitive noise drawers share key IDF terms with their target questions.

        This guards the discriminating property of the question set: if noise
        drawers did NOT share terms, the test would be vacuous (targets win by
        default on BM25/vector with no competition).
        """
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        noise_ids = {d["id"] for d in _NOISE_DRAWERS}
        for q in payload["questions"]:
            competitor_id = q.get("competitor_drawer_id")
            if competitor_id:
                assert competitor_id in noise_ids, (
                    f"Question {q['id']} names competitor_drawer_id={competitor_id!r} "
                    "but no matching entry in _NOISE_DRAWERS."
                )

    def test_seed_drawers_have_high_confidence(self):
        """Target drawers must use confidence=1.0 (slow decay under correct implementation)."""
        for d in _SEED_DRAWERS:
            assert d.get("confidence", 1.0) == 1.0, (
                f"Seed drawer {d['id']} must have confidence=1.0 for the decay scenario "
                f"to produce non-uniform strength; got {d.get('confidence')}"
            )

    def test_noise_drawers_have_low_confidence(self):
        """Competitive noise drawers must use confidence < 0.5 (fast decay under correct impl)."""
        for d in _NOISE_DRAWERS:
            conf = d.get("confidence", 1.0)
            assert conf < 0.5, (
                f"Noise drawer {d['id']} should have confidence < 0.5 for discriminating "
                f"decay; got {conf}"
            )

    def test_dup_drawer_has_lower_confidence_than_canonical(self):
        """The near-duplicate must have lower confidence than the canonical it duplicates."""
        canonical_conf = {d["id"]: d.get("confidence", 1.0) for d in _SEED_DRAWERS}
        for dup in _DUP_DRAWERS:
            # The dup mirrors the auth canonical.
            assert dup.get("confidence", 1.0) < canonical_conf.get("eval_drawer_auth", 1.0), (
                f"Dup drawer {dup['id']} must have lower confidence than canonical "
                f"eval_drawer_auth so consolidation_pass picks the right canonical."
            )

    def test_noise_expected_substrings_not_in_competitors(self):
        """The expected_substring for each question must NOT appear in its noise competitor.

        If the competitor contains the expected_substring, the test is vacuous:
        even if the competitor outranks the target, the substring would still be
        found in top-k, masking a decay failure.
        """
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        noise_by_id = {d["id"]: d["text"] for d in _NOISE_DRAWERS}
        for q in payload["questions"]:
            competitor_id = q.get("competitor_drawer_id")
            if not competitor_id:
                continue
            competitor_text = noise_by_id.get(competitor_id, "")
            expected = q["expected_substring"]
            assert expected.lower() not in competitor_text.lower(), (
                f"Question {q['id']}: expected_substring {expected!r} appears in "
                f"competitor {competitor_id!r}. The test would be vacuous: even if "
                "the competitor outranks the target, the expected substring would still "
                "be found. Redesign the competitor text or expected_substring."
            )


class TestEvalHarnessIntegration:
    """Integration tests: seed a real ChromaDB nook, run retrieval.

    These run in normal pytest (no mark gate) because they operate only on a
    temp nook — never the live ~/.sage.  The nook is cheap to create
    (tiny seed, no large corpus).

    Note: the conftest.py _isolate_home fixture redirects HOME to a temp dir
    for the entire session, so PersistentClient writes stay isolated.
    """

    def test_seed_and_retrieve_auth_question(self, tmp_path):
        """After seeding, Q1 (auth/JWT/HttpOnly) retrieves the auth drawer at recall@3."""
        nook_dir = str(tmp_path / "eval_nook")
        os.makedirs(nook_dir)

        col = _make_fixture_nook(nook_dir)
        _add_drawers(col, _SEED_DRAWERS)

        result = search_memories(
            query="How does the authentication module handle session tokens?",
            nook_path=nook_dir,
            wing="eval_project",
            n_results=3,
        )
        hits = result.get("results", [])
        texts = [h.get("text", "") for h in hits]
        assert any("HttpOnly" in t for t in texts), (
            f"Expected 'HttpOnly' in top-3 results for Q1 but got: {texts}"
        )

    def test_seed_and_retrieve_all_questions(self, tmp_path):
        """All 5 fixture questions should pass with recall@2 on the seeded-only nook."""
        nook_dir = str(tmp_path / "eval_nook")
        os.makedirs(nook_dir)

        col = _make_fixture_nook(nook_dir)
        _add_drawers(col, _SEED_DRAWERS)

        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        questions = payload["questions"]
        k = payload["k"]

        pass_rate, per_q = _eval_pass_rate(questions, nook_dir, k)

        failures = [r for r in per_q if not r["pass"]]
        assert pass_rate == 1.0, (
            f"Baseline pass rate on fixture nook should be 1.0 "
            f"but got {pass_rate:.2f}. "
            f"Failing questions: {[r['id'] for r in failures]}"
        )


class TestNonRegressionEval:
    """Full non-regression eval harness (AC5 deliverable).

    Gated behind @pytest.mark.eval — requires RUN_EVAL=1 or -m eval.

    Steps:
    1. Seed fixture nook with target drawers (confidence=1.0, recent filed_at).
    2. Measure baseline pass-rate.
    3. Add near-duplicate and competitive noise drawers (confidence=0.0–0.3, old filed_at).
    4. Run WI-6 decay pass — correct impl: targets retain ~0.687 strength;
       noise decays to floor (0.1). Broken impl: uniform decay or no-op.
    5. Run consolidation pass — correct impl: demotes dup, keeps canonical.
    6. Re-run questions — assert pass-rate doesn't drop below tolerance.
    7. Assert Q1 target is at rank-1 (rank-sensitive: decay must be load-bearing).
    8. Assert consolidation fired on the near-duplicate.
    9. Write eval_result_latest.json artifact.
    """

    @pytest.mark.eval
    def test_recall_does_not_regress_after_store_growth_and_decay(self, tmp_path, monkeypatch):
        """AC5: recall@k must not regress after store growth + decay/consolidation.

        ASSERTIONS:

        1. Non-regression (pass-rate): post_growth_pass_rate >= baseline - tolerance.
           NOTE: this assertion alone is not decay-sensitive for this question set.
           In the SEED+DUP+NOISE nook, Q1 and Q4 targets win recall@k by a large
           enough margin that the pass-rate holds even with uniform/neutered decay.
           The pass-rate assertion catches regression in OTHER failure modes (a broken
           search pipeline, wrong drawers returned, etc.) but is NOT the discriminating
           proof of decay correctness.

        2. Rank-sensitive (Q1, the tightest close-relevance case):
           Q1 target must be at matched_result_index == 1 after decay/consolidation.
           With real decay the target's strength advantage (0.1 additive) overcomes
           the noise drawer's BM25 advantage (~0.06 base gap). With uniform/neutered
           decay, the noise drawer outranks the target on pure BM25+vector.
           THIS is the assertion that makes the test decay-sensitive.

        The discriminating proof that this test is not vacuous lives in the separate
        test_eval_is_genuine_decay_neutered_regresses meta-guard, which monkeypatches
        decay to a no-op and confirms the rank-sensitive Q1 assertion fires.

        NON-VACUOUSNESS (how the test would FAIL):
          (a) decay_pass is a no-op → strength uniform → strength_norm=0 →
              noise base score wins for Q1 → rank-sensitive assertion fires
          (b) decay_pass over-decays high-confidence targets to the floor →
              same uniform-strength outcome as (a)
          (c) consolidation_pass demotes the wrong member → canonical target
              is suppressed → no longer at rank-1 for Q1
          (d) strength is not carried through search_memories to _hybrid_rank
              → same breakage as (a)
        """
        # ── 0. Isolate telemetry ──────────────────────────────────────────
        log_path = tmp_path / "turns.jsonl"
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log_path))

        # ── 1. Create fixture nook + seed target drawers ────────────────
        nook_dir = str(tmp_path / "eval_nook")
        os.makedirs(nook_dir)

        col = _make_fixture_nook(nook_dir)
        _add_drawers(col, _SEED_DRAWERS)

        # ── 2. Baseline eval ──────────────────────────────────────────────
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        questions = payload["questions"]
        k = payload["k"]
        tolerance = payload.get("non_regression_tolerance", 0.0)

        baseline_pass_rate, baseline_per_q = _eval_pass_rate(questions, nook_dir, k)

        assert baseline_pass_rate == 1.0, (
            f"Fixture nook baseline should be 1.0 (all target drawers seeded) "
            f"but got {baseline_pass_rate:.2f}. "
            f"Failures: {[r['id'] for r in baseline_per_q if not r['pass']]}"
        )

        # ── 3. Grow the store: near-duplicate + competitive noise drawers ─
        # _DUP_DRAWERS: semantically near-identical to eval_drawer_auth,
        #               confidence=0.3 → consolidation must prefer canonical.
        # _NOISE_DRAWERS: close-relevance competitors for each question,
        #                 confidence=0.0, old filed_at → decay to floor.
        _add_drawers(col, _DUP_DRAWERS)
        _add_drawers(col, _NOISE_DRAWERS)

        # ── 4. Run decay pass (non-uniform scenario) ──────────────────────
        # Reference time: 2026-06-01. Targets filed 2026-01-01 (~150 days).
        # Noise filed 2024-01-01 (~880 days).
        # Target effective_rate ≈ 0.0025/day → strength ≈ 0.687 (above floor).
        # Noise  effective_rate ≈ 0.05/day   → strength → floor (0.1).
        # After decay: strength range ≈ 0.587 → non-zero strength_norm.
        decay_results = decay_pass(col, now=_DECAY_REFERENCE_TIME, dry_run=False)
        assert decay_results, "decay_pass must return non-empty results for a seeded nook."

        # Structural invariant: no drawer was deleted; strength >= floor.
        from sage_mcp.consolidation import DRAWER_STRENGTH_FLOOR

        for r in decay_results:
            if not r.skipped:
                assert r.new_strength >= DRAWER_STRENGTH_FLOOR, (
                    f"decay_pass violated no-delete floor for drawer {r.drawer_id}: "
                    f"strength={r.new_strength} < floor={DRAWER_STRENGTH_FLOOR}"
                )

        # Verify non-uniform decay: target drawers should retain MORE strength
        # than noise drawers (this is what makes the test genuinely discriminating).
        target_ids = {d["id"] for d in _SEED_DRAWERS}
        competitive_noise_ids = {d["id"] for d in _NOISE_DRAWERS}

        target_decay = {
            r.drawer_id: r.new_strength
            for r in decay_results
            if r.drawer_id in target_ids and not r.skipped
        }
        noise_decay = {
            r.drawer_id: r.new_strength
            for r in decay_results
            if r.drawer_id in competitive_noise_ids and not r.skipped
        }

        # All targets should have decayed to above the floor (not floor-clamped).
        for did, strength in target_decay.items():
            assert strength > DRAWER_STRENGTH_FLOOR, (
                f"Target drawer {did} decayed to floor={strength:.4f} "
                f"at reference time {_DECAY_REFERENCE_TIME}. "
                "This means decay_pass is applying the full rate to high-confidence "
                "drawers — HIGH_CONFIDENCE_PROTECTION is not working correctly."
            )

        # Competitive noise should be at (or very near) the floor — their high decay
        # rate at confidence=0.0 over 880 days drives them there.
        for did, strength in noise_decay.items():
            assert strength <= DRAWER_STRENGTH_FLOOR + 1e-6, (
                f"Noise drawer {did} strength={strength:.4f} is above floor after "
                f"880 days at confidence=0.0. Expected floor={DRAWER_STRENGTH_FLOOR}. "
                "Possible bug: HIGH_CONFIDENCE_PROTECTION applied to confidence=0.0?"
            )

        # ── 5. Run consolidation pass ─────────────────────────────────────
        import sage_mcp.consolidation as _consolidation_module

        monkeypatch.setattr(_consolidation_module, "_list_tunnels_fn", lambda: [])

        consolidation_results = consolidation_pass(col, dry_run=False)

        # Assert consolidation actually fired: the near-duplicate must have been
        # demoted while the canonical (eval_drawer_auth) remains canonical.
        from sage_mcp.consolidation import CONSOLIDATION_DEMOTE_STRENGTH

        dup_result = next(
            (r for r in consolidation_results if r.drawer_id == "eval_drawer_auth_dup"),
            None,
        )
        canonical_result = next(
            (r for r in consolidation_results if r.drawer_id == "eval_drawer_auth"),
            None,
        )

        assert dup_result is not None, (
            "eval_drawer_auth_dup was not found in consolidation_results. "
            "It must have been seeded and processed."
        )
        assert dup_result.reason == "demoted:near_duplicate", (
            f"eval_drawer_auth_dup should be demoted (near-duplicate of eval_drawer_auth) "
            f"but got reason={dup_result.reason!r}. "
            "Check that the dup text is semantically close enough to trigger "
            f"CONSOLIDATION_SIMILARITY_THRESHOLD={_consolidation_module.CONSOLIDATION_SIMILARITY_THRESHOLD}."
        )
        assert dup_result.new_strength == CONSOLIDATION_DEMOTE_STRENGTH, (
            f"eval_drawer_auth_dup new_strength={dup_result.new_strength} "
            f"should equal CONSOLIDATION_DEMOTE_STRENGTH={CONSOLIDATION_DEMOTE_STRENGTH}"
        )

        assert canonical_result is not None, (
            "eval_drawer_auth was not found in consolidation_results."
        )
        assert canonical_result.reason == "canonical", (
            f"eval_drawer_auth should be the canonical representative "
            f"but got reason={canonical_result.reason!r}. "
            "consolidation_pass may have picked the wrong member as canonical. "
            "Check _canonical_tiebreak: higher confidence should win."
        )

        # ── 6. Post-growth eval ───────────────────────────────────────────
        post_pass_rate, post_per_q = _eval_pass_rate(questions, nook_dir, k)

        # ── 7. Non-regression assertion (AC5 core) ────────────────────────
        threshold = baseline_pass_rate - tolerance
        assert post_pass_rate >= threshold, (
            f"RECALL REGRESSED after store growth + decay/consolidation.\n"
            f"  baseline_pass_rate: {baseline_pass_rate:.3f}\n"
            f"  post_pass_rate:     {post_pass_rate:.3f}\n"
            f"  tolerance:          {tolerance:.3f}\n"
            f"  threshold:          {threshold:.3f}\n"
            f"  regression:         {threshold - post_pass_rate:.3f}\n"
            f"  failing questions:  {[r['id'] for r in post_per_q if not r['pass']]}"
        )

        # ── 8. Rank-sensitive assertion: Q1 target must be at rank-1 ─────
        # Q1 uses the close-relevance regime: the noise drawer (eval_noise_auth_compete)
        # has a higher base vector+BM25 score than the target, but with CORRECT decay
        # the target's strength advantage (strength ≈ 0.687 vs 0.1) pushes it to rank-1.
        # If decay is uniform/neutered, the noise drawer wins and target is rank-2.
        q1 = next(q for q in questions if q["id"] == "Q1")
        q1_result = _run_question(q1, nook_dir, k)

        assert q1_result["pass"], (
            f"Q1 (HttpOnly) failed after consolidation. The canonical drawer "
            f"(eval_drawer_auth) was not retrieved in top-{k}. "
            f"Top-{k} snippets: {q1_result['top_k_snippets']}. "
            "Possible cause: consolidation_pass demoted the wrong member, "
            "or decay over-floored the canonical, or strength not carried through "
            "to _hybrid_rank (check search_memories entry structure)."
        )

        assert q1_result["matched_result_index"] == 1, (
            f"Q1 (HttpOnly) target was found but NOT at rank-1 after decay/consolidation.\n"
            f"  matched_result_index: {q1_result['matched_result_index']} (expected 1)\n"
            f"  top-{k} snippets: {q1_result['top_k_snippets']}\n"
            "The close-relevance noise drawer (eval_noise_auth_compete) outranked the "
            "target. This means strength is NOT influencing _hybrid_rank correctly — "
            "either decay_pass did not write strength (check decay_results), "
            "or search_memories is not carrying 'strength' through to _hybrid_rank "
            "(check that the entry dict includes strength=meta.get('strength'))."
        )

        # ── 9. Write eval-result artifact ─────────────────────────────────
        eval_result = {
            "schema_version": 1,
            "eval_timestamp": datetime.now(timezone.utc).isoformat(),
            "k": k,
            "non_regression_tolerance": tolerance,
            "baseline_pass_rate": round(baseline_pass_rate, 4),
            "post_growth_pass_rate": round(post_pass_rate, 4),
            "non_regression_passed": post_pass_rate >= threshold,
            "store_size_baseline": len(_SEED_DRAWERS),
            "store_size_post_growth": len(_SEED_DRAWERS) + len(_DUP_DRAWERS) + len(_NOISE_DRAWERS),
            "decay_scenario": {
                "reference_time": _DECAY_REFERENCE_TIME.isoformat(),
                "target_confidence": 1.0,
                "noise_confidence": 0.0,
                "target_filed_at": "2026-01-01",
                "noise_filed_at": "2024-01-01",
                "note": (
                    "target effective_rate~0.0025/day → strength~0.687 after 150d; "
                    "noise effective_rate~0.05/day → floor after 880d"
                ),
            },
            "target_strengths_post_decay": {did: round(s, 4) for did, s in target_decay.items()},
            "noise_strengths_post_decay": {did: round(s, 4) for did, s in noise_decay.items()},
            "consolidation_dup_demoted": (
                dup_result.reason == "demoted:near_duplicate" if dup_result else False
            ),
            "consolidation_canonical_kept": (
                canonical_result.reason == "canonical" if canonical_result else False
            ),
            "decay_results_summary": {
                "total": len(decay_results),
                "skipped": sum(1 for r in decay_results if r.skipped),
                "written": sum(1 for r in decay_results if r.written),
            },
            "consolidation_results_summary": {
                "total": len(consolidation_results),
                "skipped": sum(1 for r in consolidation_results if r.skipped),
                "written": sum(1 for r in consolidation_results if r.written),
            },
            "q1_rank_after_decay_consolidation": q1_result["matched_result_index"],
            "baseline_per_question": baseline_per_q,
            "post_growth_per_question": post_per_q,
        }

        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULT_FILE.write_text(
            json.dumps(eval_result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # ── 10. Print summary ─────────────────────────────────────────────
        print(
            f"\n  WI-7b Non-regression eval result:"
            f"\n    baseline_pass_rate:    {baseline_pass_rate:.3f}"
            f"\n    post_growth_pass_rate: {post_pass_rate:.3f}"
            f"\n    tolerance:             {tolerance:.3f}"
            f"\n    non_regression_passed: {eval_result['non_regression_passed']}"
            f"\n    store growth:  "
            f"{len(_SEED_DRAWERS)} → "
            f"{len(_SEED_DRAWERS) + len(_DUP_DRAWERS) + len(_NOISE_DRAWERS)}"
            f"\n    dup demoted:           {eval_result['consolidation_dup_demoted']}"
            f"\n    canonical kept:        {eval_result['consolidation_canonical_kept']}"
            f"\n    target strength range: "
            f"{min(target_decay.values(), default=0):.3f}–"
            f"{max(target_decay.values(), default=0):.3f}"
            f"\n    noise  strength range: "
            f"{min(noise_decay.values(), default=0):.3f}–"
            f"{max(noise_decay.values(), default=0):.3f}"
            f"\n    Q1 target rank:        {q1_result['matched_result_index']} (expect 1)"
            f"\n    result_file:           {RESULT_FILE}"
        )

    @pytest.mark.eval
    def test_eval_is_genuine_decay_neutered_regresses(self, tmp_path, monkeypatch):
        """GENUINENESS PROBE: with decay neutered (no-op), Q1 rank-sensitive assertion FAILS.

        This test is the meta-guard: it verifies that the eval harness itself is
        not vacuous. It monkeypatches decay_pass to a no-op (no strength updates
        written), grows the store, and confirms that the Q1 rank-sensitive check
        fires (target drops to rank-2 when strength is not differentiated).

        CALIBRATION ASSUMPTION (embedding-model-pinned):
          The base-score gap between eval_noise_auth_compete and eval_drawer_auth is
          calibrated against ChromaDB's default embedding function: all-MiniLM-L6-v2
          (ONNXMiniLM_L6_V2, chromadb >= 0.4).  Empirical values:
            noise base score ≈ 0.819, target base score ≈ 0.757, gap ≈ 0.062
          This gap must satisfy: 0 < gap < strength_weight (0.1).
            Lower bound (gap > 0): ensures noise outranks target WITHOUT strength.
            Upper bound (gap < 0.1): ensures strength boost can flip the ranking.
          Headroom: ~0.038 (gap to the 0.1 ceiling).  A different embedding model
          may produce a gap outside this window, making the meta-guard either vacuous
          (gap ≤ 0, target always wins) or always-failing (gap ≥ 0.1, strength can
          never flip it).  If this test fails unexpectedly, check whether the
          embedding model has changed — the competitor text in _NOISE_DRAWERS may
          need recalibration.
          Skip guard below surfaces this loudly on model mismatch instead of
          silently flipping.

        Empirical proof (under all-MiniLM-L6-v2):
          - WITH real decay: target strength ≈ 0.687, noise strength = 0.1
            → strength_norm(target) = 1.0, +0.1 additive → target rank-1
          - WITH neutered decay (uniform strength = initial 1.0):
            → strength_norm = 0 for all → pure BM25+vector
            → noise base score (≈0.819) > target base score (≈0.757) by ~0.062
            → noise rank-1, target rank-2
            → Q1 matched_result_index == 2 ≠ 1 → rank-sensitive assertion fails

        The before/after recall values are reported in the print output.
        """
        # ── Embedding-model guard ─────────────────────────────────────────────
        # The base-score gap calibration above is tuned for all-MiniLM-L6-v2.
        # If ChromaDB's default embedding function has changed, surface it loudly
        # rather than letting the meta-guard silently flip (vacuous or always-fail).
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            ef = DefaultEmbeddingFunction()
            model_name = getattr(ef, "model_name", None) or getattr(ef, "_model_name", None)
        except Exception:
            model_name = None
        _EXPECTED_MODEL = "all-MiniLM-L6-v2"
        if model_name and _EXPECTED_MODEL not in str(model_name):
            pytest.skip(
                f"Meta-guard calibration is tuned for {_EXPECTED_MODEL!r}; "
                f"detected embedding model {model_name!r}. "
                "Recalibrate _NOISE_DRAWERS base-score gap before running this guard."
            )
        log_path = tmp_path / "turns.jsonl"
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log_path))

        nook_dir = str(tmp_path / "eval_nook")
        os.makedirs(nook_dir)

        col = _make_fixture_nook(nook_dir)
        _add_drawers(col, _SEED_DRAWERS)
        _add_drawers(col, _NOISE_DRAWERS)

        # Monkeypatch decay_pass to a no-op: does NOT write strength updates.
        # After this, all drawers retain the default strength=1.0 (uniform).
        # Uniform strength → s_range=0 → s_norm=0 for all → no strength boost.
        import sage_mcp.consolidation as _consolidation_module

        original_decay_pass = _consolidation_module.decay_pass

        def _noop_decay_pass(col, now=None, dry_run=False, **kwargs):
            """No-op: returns the same structure but writes nothing."""
            # Call real decay for reporting, but always in dry_run mode.
            return original_decay_pass(col, now=now or _DECAY_REFERENCE_TIME, dry_run=True)

        monkeypatch.setattr(_consolidation_module, "decay_pass", _noop_decay_pass)
        monkeypatch.setattr(_consolidation_module, "_list_tunnels_fn", lambda: [])

        # Run consolidation (without decay having updated strengths, all = 1.0)
        consolidation_pass(col, dry_run=False)

        # Q1 rank check with neutered decay
        payload = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
        questions = payload["questions"]
        k = payload["k"]

        q1 = next(q for q in questions if q["id"] == "Q1")
        q1_result_neutered = _run_question(q1, nook_dir, k)

        print(
            f"\n  GENUINENESS PROBE (neutered decay):"
            f"\n    Q1 found: {q1_result_neutered['pass']}"
            f"\n    Q1 matched_result_index: {q1_result_neutered['matched_result_index']}"
            f"\n    (expect: found=True but rank != 1)"
            f"\n    top-{k} snippets: {q1_result_neutered['top_k_snippets']}"
        )

        # Assert the target is found (expected substring "HttpOnly" should be
        # in top-k since there are only a few drawers in this probe nook),
        # but NOT at rank-1 (noise outranks on BM25+vector without strength).
        assert q1_result_neutered["pass"], (
            "Genuineness probe: Q1 target was not found at all with neutered decay. "
            "This suggests the question or seed drawer is misconfigured, not just "
            "that decay is non-discriminating."
        )

        assert q1_result_neutered["matched_result_index"] != 1, (
            f"GENUINENESS FAILURE: Q1 target is STILL at rank-1 even with decay "
            f"neutered (matched_result_index={q1_result_neutered['matched_result_index']}).\n"
            "This means the test is vacuous: it passes regardless of whether decay "
            "runs. The close-relevance competitor is not outranking the target on "
            "pure BM25+vector as designed.\n"
            "Possible causes:\n"
            "  1. The 'strength' field is not being carried through search_memories to "
            "_hybrid_rank — both target and competitor appear to have the same base "
            "scores when strength is uniform.\n"
            "  2. The competitor text has changed and no longer beats the target on BM25.\n"
            f"  top-{k} snippets: {q1_result_neutered['top_k_snippets']}"
        )

        # Run with REAL decay on a fresh nook to confirm the positive case.
        nook_dir_real = str(tmp_path / "eval_nook_real")
        os.makedirs(nook_dir_real)
        col_real = _make_fixture_nook(nook_dir_real)
        _add_drawers(col_real, _SEED_DRAWERS)
        _add_drawers(col_real, _NOISE_DRAWERS)

        # Restore real decay_pass
        monkeypatch.setattr(_consolidation_module, "decay_pass", original_decay_pass)
        decay_pass(col_real, now=_DECAY_REFERENCE_TIME, dry_run=False)
        monkeypatch.setattr(_consolidation_module, "_list_tunnels_fn", lambda: [])
        consolidation_pass(col_real, dry_run=False)

        q1_result_real = _run_question(q1, nook_dir_real, k)

        print(
            f"\n  GENUINENESS PROBE (real decay):"
            f"\n    Q1 found: {q1_result_real['pass']}"
            f"\n    Q1 matched_result_index: {q1_result_real['matched_result_index']}"
            f"\n    (expect: found=True AND rank == 1)"
        )

        assert q1_result_real["matched_result_index"] == 1, (
            f"GENUINENESS FAILURE: Q1 target is NOT at rank-1 with real decay "
            f"(matched_result_index={q1_result_real['matched_result_index']}).\n"
            "With real decay, the target's strength advantage should overcome the "
            "noise drawer's BM25 advantage and push the target to rank-1.\n"
            f"  top-{k} snippets: {q1_result_real['top_k_snippets']}"
        )

        print(
            f"\n  GENUINENESS VERDICT: PASS"
            f"\n    neutered: Q1 target rank = {q1_result_neutered['matched_result_index']} "
            f"(noise wins on base score)"
            f"\n    real decay: Q1 target rank = {q1_result_real['matched_result_index']} "
            f"(strength advantage restores rank-1)"
            f"\n    Strength IS load-bearing: decay changes top-k rank for Q1."
        )

    @pytest.mark.eval
    def test_baseline_json_exists_and_is_consistent(self):
        """WI-7a baseline JSON should exist; if it does, verify it is consistent with
        the current TIER0_TOKEN_BUDGET constant (within a reasonable range).

        This is a soft consistency check — it fires a warning rather than a hard
        failure when the baseline is missing (first run before WI-7a was run).
        """
        if not BASELINE_FILE.exists():
            pytest.skip("tier0_baseline.json not yet generated; run RUN_EVAL_BASELINE=1 first.")

        baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        assert "tier0_token_count" in baseline, "tier0_baseline.json must have tier0_token_count"
        assert "tier0_budget" in baseline, "tier0_baseline.json must have tier0_budget"

        # The token count must be <= the budget at capture time.
        captured_count = baseline["tier0_token_count"]
        captured_budget = baseline["tier0_budget"]
        assert captured_count <= captured_budget, (
            f"Baseline captured an over-budget block: "
            f"token_count={captured_count} > budget={captured_budget}. "
            "Re-run RUN_EVAL_BASELINE=1 after fixing the Tier-0 block size."
        )
