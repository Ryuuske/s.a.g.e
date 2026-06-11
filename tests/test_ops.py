"""Tests for sage.ops — the search + registry orchestration seam (ADR-0073/0074).

The resilience policy (vector-disabled routing, transient-index self-heal) and
the one-channel error contract are exercised with an injected fake Backend — no
real ChromaDB, no real sleep. Per ADR-0074 ops.search consumes a backend object
(backend.health()/backend.reset()), not injected probe/reset functions.
"""

from sage_mcp import ops
from sage_mcp.backends.base import HealthStatus


# ── helpers ──────────────────────────────────────────────────────────────


class _SpySearch:
    """A fake search_fn that records calls and replays scripted results."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = []

    def __call__(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return self._results[min(len(self.calls) - 1, len(self._results) - 1)]


class _FakeBackend:
    """A fake Nook backend: fixed health() status + a reset() spy."""

    def __init__(self, *, vector_disabled=False, reason=""):
        self._status = HealthStatus(ok=True, detail=reason, vector_disabled=vector_disabled)
        self.health_calls = 0
        self.resets = []

    def health(self, nook_path=None, collection_name=None):
        self.health_calls += 1
        return self._status

    def reset(self, nook_path):
        self.resets.append(nook_path)


def _ok(**extra):
    return {"results": [{"text": "ok", "wing": "w", "room": "r"}], **extra}


_TRANSIENT = {"error": "Search error: Internal error: Error finding id"}


# ── ops.search: validation ─────────────────────────────────────────────────


def test_search_rejects_bad_wing_without_searching():
    spy = _SpySearch(_ok())
    result = ops.search(
        query="q", nook_path="/n", wing="../etc", search_fn=spy, backend=_FakeBackend()
    )
    assert result["error_kind"] == "validation"
    assert "error" in result
    assert spy.calls == []  # search must not run on bad input


def test_search_blank_filters_become_none():
    spy = _SpySearch(_ok())
    ops.search(
        query="q", nook_path="/n", wing="   ", room=None, search_fn=spy, backend=_FakeBackend()
    )
    assert spy.calls[0]["wing"] is None
    assert spy.calls[0]["room"] is None


# ── ops.search: vector-disabled routing (from backend.health) ───────────────


def test_search_routes_vector_disabled_and_tags_metadata():
    spy = _SpySearch(_ok())
    be = _FakeBackend(vector_disabled=True, reason="hnsw diverged")
    result = ops.search(query="q", nook_path="/n", search_fn=spy, backend=be)
    assert spy.calls[0]["vector_disabled"] is True
    assert result["vector_disabled"] is True
    assert result["vector_disabled_reason"] == "hnsw diverged"


def test_search_resolves_default_backend_when_none(monkeypatch):
    """When no backend is passed, ops.search uses nook.default_backend()."""
    import sage_mcp.nook as nook

    be = _FakeBackend()
    monkeypatch.setattr(nook, "default_backend", lambda: be)
    spy = _SpySearch(_ok())
    ops.search(query="q", nook_path="/n", search_fn=spy)
    assert be.health_calls == 1


# ── ops.search: transient-index self-heal via backend.reset (#1315) ─────────


def test_search_retries_once_on_transient_then_succeeds():
    spy = _SpySearch(_TRANSIENT, _ok())
    be = _FakeBackend()
    sleeps = {"n": 0}
    result = ops.search(
        query="q",
        nook_path="/n",
        search_fn=spy,
        backend=be,
        sleep_fn=lambda _: sleeps.__setitem__("n", sleeps["n"] + 1),
    )
    assert len(spy.calls) == 2
    assert be.resets == ["/n"]  # one reset, on the right nook
    assert sleeps["n"] == 1
    assert be.health_calls == 2  # initial probe + re-probe before retry
    assert result.get("index_recovered") is True
    assert "results" in result


def test_search_does_not_retry_non_transient_error():
    spy = _SpySearch({"error": "Search error: invalid query syntax"})
    be = _FakeBackend()
    result = ops.search(
        query="q", nook_path="/n", search_fn=spy, backend=be, sleep_fn=lambda _: None
    )
    assert len(spy.calls) == 1
    assert be.resets == []
    assert "index_recovered" not in result
    assert result["error_kind"] == "backend"


def test_search_surfaces_second_error_if_retry_also_transient():
    spy = _SpySearch(_TRANSIENT, _TRANSIENT)
    be = _FakeBackend()
    result = ops.search(
        query="q", nook_path="/n", search_fn=spy, backend=be, sleep_fn=lambda _: None
    )
    assert len(spy.calls) == 2
    assert be.resets == ["/n"]
    assert "error" in result
    assert "index_recovered" not in result
    assert result["error_kind"] == "backend"


def test_search_retry_preserves_collection_name():
    """The #1315 retry reuses identical kwargs, including collection_name."""
    spy = _SpySearch(_TRANSIENT, _ok())
    ops.search(
        query="q",
        nook_path="/n",
        collection_name="custom_coll",
        search_fn=spy,
        backend=_FakeBackend(),
        sleep_fn=lambda _: None,
    )
    assert len(spy.calls) == 2
    assert spy.calls[0]["collection_name"] == "custom_coll"
    assert spy.calls[1]["collection_name"] == "custom_coll"


def test_search_retry_nondict_result_does_not_crash():
    """A non-dict retry result is returned as-is, not mutated (guard order)."""
    spy = _SpySearch(_TRANSIENT, None)
    result = ops.search(
        query="q", nook_path="/n", search_fn=spy, backend=_FakeBackend(), sleep_fn=lambda _: None
    )
    assert result is None
    assert len(spy.calls) == 2


# ── ops.search: sanitizer metadata ─────────────────────────────────────────


def test_search_attaches_sanitizer_metadata(monkeypatch):
    monkeypatch.setattr(
        ops,
        "sanitize_query",
        lambda q: {
            "clean_query": "clean",
            "was_sanitized": True,
            "method": "strip",
            "original_length": 10,
            "clean_length": 5,
        },
    )
    spy = _SpySearch(_ok())
    result = ops.search(query="dirty", nook_path="/n", search_fn=spy, backend=_FakeBackend())
    assert spy.calls[0]["query"] == "clean"
    assert result["query_sanitized"] is True
    assert result["sanitizer"]["clean_query"] == "clean"


def test_search_passes_through_params(monkeypatch):
    monkeypatch.setattr(
        ops,
        "sanitize_query",
        lambda q: {
            "clean_query": q,
            "was_sanitized": False,
            "method": "",
            "original_length": 0,
            "clean_length": 0,
        },
    )
    spy = _SpySearch(_ok())
    ops.search(
        query="q",
        nook_path="/n",
        collection_name="coll",
        n_results=7,
        max_distance=0.8,
        candidate_strategy="union",
        agents=["a"],
        search_fn=spy,
        backend=_FakeBackend(),
    )
    call = spy.calls[0]
    assert call["collection_name"] == "coll"
    assert call["n_results"] == 7
    assert call["max_distance"] == 0.8
    assert call["candidate_strategy"] == "union"
    assert call["agents"] == ["a"]


# ── ops.registry_search ─────────────────────────────────────────────────────


def test_registry_search_rejects_invalid_kind():
    result = ops.registry_search(kind="widget", repo_root="/anything")
    assert result["error_kind"] == "validation"


def test_registry_search_unresolvable_root(monkeypatch):
    monkeypatch.setattr(ops, "resolve_repo_root", lambda: None)
    result = ops.registry_search(query="x")
    assert result["error_kind"] == "validation"


def test_registry_search_happy_path(monkeypatch):
    import sage_mcp.extensions.skill_registry as reg

    entries = [
        {"kind": "agent", "name": "a1", "one_line": "x"},
        {"kind": "skill", "name": "s1", "one_line": "y"},
    ]
    monkeypatch.setattr(reg, "build_registry", lambda root, force_rebuild=False: entries)
    monkeypatch.setattr(reg, "search_registry", lambda e, query, kind, limit: e[:1])

    result = ops.registry_search(query="a", repo_root="/repo")
    assert result["total_indexed"] == 2
    assert result["count"] == 1
    assert result["results"] == entries[:1]
    assert "error" not in result


def test_registry_search_build_failure_is_backend_error(monkeypatch):
    import sage_mcp.extensions.skill_registry as reg

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(reg, "build_registry", boom)
    result = ops.registry_search(query="a", repo_root="/repo")
    assert result["error_kind"] == "backend"
    assert "disk gone" in result["error"]


def test_registry_search_limit_none_clamps_no_crash(monkeypatch):
    import sage_mcp.extensions.skill_registry as reg

    seen = {}
    monkeypatch.setattr(reg, "build_registry", lambda root, force_rebuild=False: [])
    monkeypatch.setattr(
        reg, "search_registry", lambda e, query, kind, limit: seen.update(limit=limit) or []
    )
    result = ops.registry_search(query="x", limit=None, repo_root="/repo")
    assert "error" not in result
    assert seen["limit"] == 10


def test_resolve_repo_root_finds_this_repo():
    from pathlib import Path

    root = ops.resolve_repo_root()
    assert root is not None
    assert (Path(root) / "agents").is_dir() or (Path(root) / "skills").is_dir()


def test_sanitize_optional_name_shared_contract():
    from sage_mcp.config import sanitize_optional_name

    assert sanitize_optional_name(None, "wing") is None
    assert sanitize_optional_name("   ", "wing") is None
    assert sanitize_optional_name("backend", "room") == "backend"
    import pytest

    with pytest.raises(ValueError):
        sanitize_optional_name("../etc", "wing")
