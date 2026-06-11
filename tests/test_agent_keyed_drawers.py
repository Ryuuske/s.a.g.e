"""Round-trip smoke test for the agent-keyed drawer extension.

Writes two drawers with distinct agents tags via the live ``tool_add_drawer``
write path, then verifies the agents filter on ``tool_search`` returns only
the drawers tagged with the requested agent. The point of the test is the
serialize → store → deserialize → filter loop, not search relevance — so
each drawer carries a unique distinctive keyword that the vector + BM25
hybrid will always rank as a hit when queried directly.
"""

from __future__ import annotations

import pytest


def _patch_mcp_server(monkeypatch, config, kg):
    """Mirror of the helper in test_mcp_server.py — patch globals."""
    from sage_mcp import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **kw: kg)


@pytest.fixture
def two_agent_keyed_drawers(monkeypatch, config, kg):
    """Add two drawers with disjoint agents lists and yield their handles."""
    _patch_mcp_server(monkeypatch, config, kg)
    from sage_mcp.mcp_server import tool_add_drawer

    reviewer_drawer = tool_add_drawer(
        wing="agent_keyed_smoke",
        room="reviews",
        content=(
            "Unique-marker XYLOPHONIC content from the reviewer drawer. "
            "Reviewing aidev-code-reviewer.md for the missing input-validation slot. "
            "Notes: the lane discipline section needs a refusal example."
        ),
        agents=["aidev-code-reviewer", "aidev-adversarial-auditor"],
    )
    docs_drawer = tool_add_drawer(
        wing="agent_keyed_smoke",
        room="docs",
        content=(
            "Unique-marker GLYPTODON content from the docs-keeper drawer. "
            "Auditing docs/handoff/LATEST.md for stale references after the "
            "aidev-code-reviewer rename."
        ),
        agents=["docs-keeper"],
    )
    assert reviewer_drawer["success"] is True, reviewer_drawer
    assert docs_drawer["success"] is True, docs_drawer
    return reviewer_drawer, docs_drawer


def test_serializer_helpers_round_trip():
    """Bare helper round-trip: list[str] → JSON blob → list[str]."""
    from sage_mcp.extensions.agent_keyed_drawers import (
        deserialize_agents,
        serialize_agents,
    )

    agents = ["aidev-code-reviewer", "aidev-adversarial-auditor"]
    assert deserialize_agents(serialize_agents(agents)) == agents
    # Empty list serialises and survives a round trip.
    assert deserialize_agents(serialize_agents([])) == []
    # None / bad input degrade to [].
    assert deserialize_agents(None) == []
    assert deserialize_agents("not json") == []


def test_add_drawer_stores_agents_metadata(two_agent_keyed_drawers, monkeypatch, config, kg):
    """tool_get_drawer surfaces the agents list it was written with."""
    _patch_mcp_server(monkeypatch, config, kg)
    from sage_mcp.mcp_server import tool_get_drawer

    reviewer_drawer, docs_drawer = two_agent_keyed_drawers

    fetched_reviewer = tool_get_drawer(reviewer_drawer["drawer_id"])
    assert set(fetched_reviewer["agents"]) == {
        "aidev-code-reviewer",
        "aidev-adversarial-auditor",
    }

    fetched_docs = tool_get_drawer(docs_drawer["drawer_id"])
    assert fetched_docs["agents"] == ["docs-keeper"]


def test_search_with_agent_filter_returns_only_matching(
    two_agent_keyed_drawers, monkeypatch, config, kg
):
    """nook_search with agents=[reviewer] returns the reviewer drawer only."""
    _patch_mcp_server(monkeypatch, config, kg)
    from sage_mcp.mcp_server import tool_search

    result = tool_search(
        query="reviewer aidev-code-reviewer input-validation",
        wing="agent_keyed_smoke",
        agents=["aidev-code-reviewer"],
        max_distance=0.0,  # disable distance cutoff so smoke test isn't flaky
    )
    hits = result.get("results") or []
    assert hits, f"expected at least one hit, got {result}"
    # Every returned drawer must carry the requested agent.
    for hit in hits:
        assert "aidev-code-reviewer" in hit["agents"], hit
    # The docs-keeper drawer must NOT appear.
    assert all("docs-keeper" not in hit["agents"] for hit in hits), hits


def test_search_without_agent_filter_returns_both(two_agent_keyed_drawers, monkeypatch, config, kg):
    """nook_search without an agents filter sees every drawer in the wing."""
    _patch_mcp_server(monkeypatch, config, kg)
    from sage_mcp.mcp_server import tool_search

    result = tool_search(
        query="unique-marker content",
        wing="agent_keyed_smoke",
        max_distance=0.0,
        limit=10,
    )
    hits = result.get("results") or []
    agent_sets = [tuple(sorted(h["agents"])) for h in hits]
    # Both distinct agent-sets present in the unfiltered result.
    assert ("aidev-adversarial-auditor", "aidev-code-reviewer") in agent_sets, agent_sets
    assert ("docs-keeper",) in agent_sets, agent_sets


def test_search_with_unknown_agent_returns_no_hits(
    two_agent_keyed_drawers, monkeypatch, config, kg
):
    """Filtering by an agent name that didn't touch any drawer yields []."""
    _patch_mcp_server(monkeypatch, config, kg)
    from sage_mcp.mcp_server import tool_search

    result = tool_search(
        query="unique-marker content",
        wing="agent_keyed_smoke",
        agents=["aidev-no-such-agent"],
        max_distance=0.0,
    )
    assert (result.get("results") or []) == []


def test_idempotent_refile_accumulates_agents(monkeypatch, config, kg):
    """When the same content is filed twice by different agents, the second
    call hits the idempotency early-return — but it must merge the new
    agent name into the stored agents list rather than silently dropping
    it. 'Every agent that touched this drawer' wins over 'first agent
    that filed it'."""
    _patch_mcp_server(monkeypatch, config, kg)
    from sage_mcp.mcp_server import tool_add_drawer, tool_get_drawer

    body = (
        "Idempotent-marker MORDANT content for the accumulation test. "
        "The same body filed twice by two agents must end up tagged with both."
    )
    first = tool_add_drawer(
        wing="accumulate_smoke",
        room="reviews",
        content=body,
        agents=["aidev-code-reviewer"],
    )
    second = tool_add_drawer(
        wing="accumulate_smoke",
        room="reviews",
        content=body,
        agents=["aidev-adversarial-auditor"],
    )
    assert first["success"] is True
    assert second["success"] is True
    # Second call hit the idempotency branch — same drawer_id back.
    assert second.get("reason") == "already_exists"
    assert second["drawer_id"] == first["drawer_id"]

    fetched = tool_get_drawer(first["drawer_id"])
    assert set(fetched["agents"]) == {
        "aidev-code-reviewer",
        "aidev-adversarial-auditor",
    }, fetched


def test_miner_propagates_agents_to_every_drawer(monkeypatch, config, kg, tmp_path):
    """A project-file mine with --agents=[X] tags every resulting drawer
    with agents=[X], so nook_search agents=[X] later finds the mined
    drawers. This closes the 'recall its own past work' loop for content
    the agent mined (vs. content it tool_add_drawered)."""
    from sage_mcp.miner import mine

    _patch_mcp_server(monkeypatch, config, kg)

    project_dir = tmp_path / "tiny_project"
    project_dir.mkdir()
    (project_dir / "intro.md").write_text(
        "Miner agents-propagation smoke test.\n"
        "Unique marker JUVENESCENT body content with enough words to make\n"
        "a real chunk pass through chunking and embedding the rest of the\n"
        "way to a stored drawer in the nook collection.\n",
        encoding="utf-8",
    )

    mine(
        project_dir=str(project_dir),
        nook_path=config.nook_path,
        wing_override="miner_agents_smoke",
        agent="orchestrator",
        agents=["aidev-code-reviewer"],
        respect_gitignore=False,
    )

    from sage_mcp.mcp_server import tool_search

    result = tool_search(
        query="juvenescent miner smoke",
        wing="miner_agents_smoke",
        agents=["aidev-code-reviewer"],
        max_distance=0.0,
        limit=10,
    )
    hits = [h for h in (result.get("results") or []) if "JUVENESCENT" in (h.get("text") or "")]
    assert hits, f"miner agents tag missed: {result}"
    for hit in hits:
        assert "aidev-code-reviewer" in hit["agents"], hit


def test_diary_write_makes_entries_agent_keyed_searchable(monkeypatch, config, kg):
    """Diary entries written by nook_diary_write must be reachable via
    nook_search agents=[<agent_name>] — closing the 'any specialist can
    recall its own past work' contract on the diary path. Diaries are the
    canonical 'agent's own past work'; if they were invisible to the
    agents filter, the feature would miss its primary use case."""
    _patch_mcp_server(monkeypatch, config, kg)
    from sage_mcp.mcp_server import tool_diary_write, tool_search

    diary_result = tool_diary_write(
        agent_name="aidev-code-reviewer",
        entry=(
            "Unique-marker AURIFEROUS diary content. "
            "Reviewed the lane discipline section of aidev-code-reviewer.md "
            "and flagged the missing refusal example."
        ),
        topic="lane-discipline",
    )
    assert diary_result["success"] is True, diary_result

    # diary_write lowercases the agent name (see #1243), so the agents
    # filter has to match the lowercased canonical form.
    result = tool_search(
        query="auriferous lane discipline",
        agents=["aidev-code-reviewer"],
        max_distance=0.0,
        limit=10,
    )
    hits = result.get("results") or []
    diary_hits = [h for h in hits if "AURIFEROUS" in (h.get("text") or "")]
    assert diary_hits, f"diary entry not reached by agents filter: {hits}"
    for hit in diary_hits:
        assert "aidev-code-reviewer" in hit["agents"], hit


# ── Additional agent_keyed_drawers unit tests (relocated from test_bootstrap.py) ─


def test_agent_keyed_drawers_non_list_json():
    """deserialize_agents returns [] when blob is valid JSON but not a list."""
    from sage_mcp.extensions.agent_keyed_drawers import deserialize_agents

    # Valid JSON but not a list → returns [] (line 39)
    assert deserialize_agents('{"key": "value"}') == []
    assert deserialize_agents('"just a string"') == []
    assert deserialize_agents("42") == []

    # Valid list → returns items
    assert deserialize_agents('["agent-a", "agent-b"]') == ["agent-a", "agent-b"]
