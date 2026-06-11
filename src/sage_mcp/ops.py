"""sage_mcp.ops — orchestration seam shared by the CLI and MCP surfaces.

Owns the resilient SEARCH policy (semantic validation, query sanitization,
vector-disabled routing, transient-index self-heal) and the REGISTRY lookup
(repo-root resolution + build/search), so the surfaces only decode their
transport and format the result.

Dependency direction: ``surfaces (cli / mcp_server) → sage.ops → library``.
ops never imports a surface. The Nook ``backend`` owns the cross-call cache
lifecycle (``backend.health()`` / ``backend.reset()``); ops is stateless policy
that drives it. See ADR-0073 (the seam) and ADR-0074 (backend health/reset).
"""

import logging
import time
from pathlib import Path

from .config import sanitize_optional_name
from .query_sanitizer import sanitize_query
from .searcher import search_memories

logger = logging.getLogger(__name__)

# #1315: after a bulk write the HNSW segment metadata lags the committed SQLite
# rows for a flush window; a one-shot retry after this pause self-heals.
_TRANSIENT_SLEEP = 2.0


def _error(kind: str, message: str) -> dict:
    """Build a one-channel error result (ADR-0073 fork 4).

    ``kind`` is ``"validation"`` (bad caller input) or ``"backend"`` (storage /
    index failure). Surfaces do a single ``"error" in result`` check and map
    ``error_kind`` to their own reporting (MCP returns the dict; the CLI maps it
    to an exit code).
    """
    return {"error": message, "error_kind": kind}


def _is_transient_index_error(result) -> bool:
    """True for the #1315 HNSW flush-window error that self-heals on retry.

    Chroma returns "Internal error: Error finding id" while the binary segment
    metadata trails the committed SQLite rows after a bulk mine.
    """
    if not isinstance(result, dict):
        return False
    err = result.get("error", "")
    return isinstance(err, str) and ("Error finding id" in err or "Internal error" in err)


def search(
    *,
    query,
    nook_path,
    collection_name=None,
    wing=None,
    room=None,
    n_results=5,
    max_distance=0.0,
    candidate_strategy="vector",
    agents=None,
    search_fn=search_memories,
    backend=None,
    sleep_fn=time.sleep,
):
    """Resilient search shared by every surface.

    Owns the semantic concerns the surfaces used to each carry: wing/room name
    validation, query sanitization (#333), vector-disabled routing (#1222), and
    the transient-index self-heal (#1315/#1322). Returns the ``search_memories``
    dict plus resilience metadata, or a one-channel error dict
    (``{"error", "error_kind"}``) — it never raises for caller input.

    The Nook ``backend`` owns the lifecycle: ``backend.health()`` is the
    #1222-safe pre-open divergence probe, ``backend.reset()`` drops the stale
    client before the retry. Defaults to the process-wide backend the search
    path uses; tests inject a fake backend (ADR-0074).
    """
    # 1. Semantic validation (surfaces have already transport-decoded).
    try:
        wing = sanitize_optional_name(wing, "wing")
        room = sanitize_optional_name(room, "room")
    except ValueError as exc:
        return _error("validation", str(exc))

    # 2. Query sanitization (#333 — strip system-prompt contamination).
    sanitized = sanitize_query(query)
    clean_query = sanitized["clean_query"]

    # 3. Vector-disabled probe (#1222 — backend.health() is the safe sqlite/pickle
    #    read before we touch chromadb, so a diverged index routes to the BM25
    #    fallback rather than segfaulting the host on segment load).
    if backend is None:
        from .nook import default_backend

        backend = default_backend()
    status = backend.health(nook_path, collection_name)
    vector_disabled, vd_reason = status.vector_disabled, status.detail

    def _run():
        return search_fn(
            clean_query,
            nook_path=nook_path,
            wing=wing,
            room=room,
            n_results=n_results,
            max_distance=max_distance,
            vector_disabled=vector_disabled,
            candidate_strategy=candidate_strategy,
            collection_name=collection_name,
            agents=agents,
        )

    result = _run()

    # 4. Transient-index self-heal: drop caches, let the segment settle, re-probe,
    #    retry once. The caller never sees the transient unless the retry also
    #    fails. Both attempts use identical kwargs (including collection_name).
    if _is_transient_index_error(result):
        backend.reset(nook_path)
        sleep_fn(_TRANSIENT_SLEEP)
        status = backend.health(nook_path, collection_name)
        vector_disabled, vd_reason = status.vector_disabled, status.detail
        result = _run()
        if isinstance(result, dict) and not _is_transient_index_error(result):
            result["index_recovered"] = True

    # 5. Tag backend errors and attach resilience metadata for transparency.
    if not isinstance(result, dict):
        return result
    if "error" in result and "error_kind" not in result:
        result["error_kind"] = "backend"
    if vector_disabled:
        result["vector_disabled"] = True
        result["vector_disabled_reason"] = vd_reason
    if sanitized.get("was_sanitized"):
        result["query_sanitized"] = True
        result["sanitizer"] = {
            "method": sanitized["method"],
            "original_length": sanitized["original_length"],
            "clean_length": sanitized["clean_length"],
            "clean_query": clean_query,
        }
    return result


def resolve_repo_root():
    """Resolve the sage repo root from this package's install location.

    Walks up from ``src/sage_mcp/`` looking for the directory that contains
    ``agents/`` or ``skills/`` (the framework's own roster), checking the repo
    root first. Returns the path string, or ``None`` if not found. This is the
    shared resolver for the registry surfaces (CLI ``cmd_registry`` and MCP
    ``tool_registry_search``); other commands that resolve a repo root have not
    yet been migrated to it.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent.parent, here.parent, here):
        if (candidate / "agents").is_dir() or (candidate / "skills").is_dir():
            return str(candidate)
    return None


def registry_search(*, query="", kind=None, limit=10, repo_root=None, force_rebuild=False):
    """Search the on-disk agent/skill/script registry.

    Resolves the repo root (once), builds the registry, and searches it.
    Returns ``{"query", "kind_filter", "total_indexed", "results", "count"}`` or
    a one-channel error dict. Output keys match the prior MCP tool response.
    """
    from .extensions.skill_registry import build_registry, search_registry

    if kind is not None and kind not in ("agent", "skill", "script"):
        return _error(
            "validation", f"Invalid kind {kind!r}. Must be 'agent', 'skill', or 'script'."
        )

    limit = max(1, min(limit or 10, 50))

    root = repo_root or resolve_repo_root()
    if not root:
        return _error(
            "validation",
            "Could not auto-detect repo root. Pass repo_root explicitly "
            "(the directory containing agents/ and skills/).",
        )

    try:
        entries = build_registry(root, force_rebuild=force_rebuild)
    except Exception as exc:
        logger.exception("registry build failed (root=%s)", root)
        return _error("backend", f"Registry build failed: {exc}")

    try:
        results = search_registry(entries, query=query, kind=kind, limit=limit)
    except Exception as exc:
        logger.exception("registry search failed")
        return _error("backend", f"Registry search failed: {exc}")

    return {
        "query": query,
        "kind_filter": kind,
        "total_indexed": len(entries),
        "results": results,
        "count": len(results),
    }
