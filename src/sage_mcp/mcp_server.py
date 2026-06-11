#!/usr/bin/env python3
"""
sage MCP Server — read/write nook access for Claude Code
================================================================
Install: claude mcp add sage -- sage-mcp [--nook /path/to/nook]

Tools (read):
  nook_status          — total drawers, wing/room breakdown
  nook_list_wings      — all wings with drawer counts
  nook_list_rooms      — rooms within a wing
  nook_get_taxonomy    — full wing → room → count tree
  nook_search          — semantic search, optional wing/room filter
  nook_check_duplicate — check if content already exists before filing

Tools (write):
  nook_add_drawer      — file verbatim content into a wing/room
  nook_delete_drawer   — remove a drawer by ID

Tools (maintenance):
  nook_reconnect       — force cache invalidation and reconnect after external writes
"""

import os
import sys

# --- MCP stdio protection (issue #225) -----------------------------------
# The MCP protocol multiplexes JSON-RPC over stdio: stdout MUST carry only
# valid JSON-RPC messages, stderr is for human-readable logs. Some
# transitive dependencies (chromadb → onnxruntime, posthog telemetry) print
# banners and error messages directly to stdout — sometimes at C level —
# which breaks Claude Desktop's JSON parser. Redirect stdout → stderr at
# both the Python and file-descriptor level before heavy imports, then
# restore the real stdout in main() before entering the protocol loop.
_REAL_STDOUT = sys.stdout
_REAL_STDOUT_FD = None
try:
    _REAL_STDOUT_FD = os.dup(1)
    os.dup2(2, 1)
except (OSError, AttributeError):
    # Environments without fd-level stdio (embedded interpreters, some test
    # harnesses). The Python-level redirect below still applies.
    pass
sys.stdout = sys.stderr

import argparse  # noqa: E402  (deferred until after stdio protection above)
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import hashlib  # noqa: E402
import sqlite3  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402

from .config import (  # noqa: E402
    SageConfig,
    sanitize_kg_value,
    sanitize_name,
    sanitize_optional_name,
    sanitize_content,
    sanitize_iso_temporal,
    strip_lone_surrogates,
)
from .secret_scrub import scrub_secrets  # noqa: E402
from .version import __version__  # noqa: E402
from chromadb.errors import NotFoundError as _ChromaNotFoundError  # noqa: E402

from .backends.chroma import (  # noqa: E402
    ChromaBackend,
    ChromaCollection,
    _HNSW_BLOAT_GUARD,
    _close_client,
    _pin_hnsw_threads,
)
from .nook_graph import (  # noqa: E402
    traverse,
    find_tunnels,
    graph_stats,
    create_tunnel,
    list_tunnels,
    delete_tunnel,
    follow_tunnels,
    invalidate_graph_cache,
)

from .knowledge_graph import KnowledgeGraph, DEFAULT_KG_PATH  # noqa: E402


def _init_logging() -> None:
    """Root-logger init: always stderr, optionally append to ``SAGE_LOG_FILE``.

    Stderr-only is the default. When ``SAGE_LOG_FILE`` is set, a
    ``FileHandler`` is attached so MCP-client failures that the client
    does not surface (e.g. the ``-32000`` cold-load timeout in #1495)
    remain diagnosable from the file.

    Failure modes:

    * Invalid path (missing directory, no perms, Windows NUL byte) →
      stderr-only with a warning. The env var must not become a new
      server-start failure surface — that would defeat the diagnostic
      goal. ``ValueError`` is included in the catch because Windows
      raises it for paths with embedded NUL bytes, not ``OSError``.
    * Root logger already configured (host app embedding the server,
      transitive imports touching ``logging``) → ``force=True`` resets
      the handlers so SAGE_LOG_FILE's contract holds regardless
      of what touched root logging first. Without ``force=True``,
      ``basicConfig`` is a no-op when handlers exist and the env var
      silently does nothing — exactly the diagnostic black hole #1495
      exists to close.
    * Concurrent writers (multiple ``sage-mcp`` processes pointing
      at the same path) interleave at the line level. The handler uses
      append mode so nothing is overwritten, but operators running
      Claude Code + Claude Desktop simultaneously should give each
      process its own log path.

    ``delay=True`` is intentionally NOT set: deferring the open means an
    invalid path raises at ``emit()`` time (unhandled), defeating the
    fail-soft contract. With eager open the same error surfaces inside
    ``FileHandler.__init__`` and lands in our ``except`` below.

    Module-level invocation: this function runs at import time, preserving
    the side effect of the previous module-level ``logging.basicConfig``
    call. Callers that import ``sage_mcp.mcp_server`` for introspection
    (``TOOLS`` dict, handler functions) inherit the reset; this matches
    pre-PR behaviour and is intentional for an MCP entry-point module.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    # SAGE_LOG_FILE is operator-supplied and opt-in; this is a
    # local-first server (CLAUDE.md design principle), so no path
    # sanitization — the operator's process UID is the trust boundary.
    log_file = os.environ.get("SAGE_LOG_FILE", "").strip()
    file_handler_error: Exception | None = None
    if log_file:
        try:
            handlers.append(logging.FileHandler(log_file, mode="a", encoding="utf-8"))
            # Match the WAL file's 0o600 permission (see `_wal_log` at line 428):
            # log records can carry diagnostic detail the operator considers
            # sensitive; restricting to owner-only is consistent with the rest
            # of the local-first server's file-mode policy. Best-effort: the
            # chmod can fail on filesystems that don't honor POSIX modes
            # (Windows, network shares) — that's not a startup-blocker.
            try:
                os.chmod(log_file, 0o600)
            except OSError:
                pass
        except (OSError, ValueError) as exc:
            # Fail-soft: see "Invalid path" failure mode above. Broad on
            # (OSError, ValueError) because Windows raises ValueError for
            # NUL-byte paths while POSIX uses OSError for missing-dir / EPERM.
            file_handler_error = exc
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=handlers, force=True)
    if file_handler_error is not None:
        logging.getLogger("nook_mcp").warning(
            "SAGE_LOG_FILE=%r could not be opened (%s); using stderr only",
            log_file,
            file_handler_error,
        )


_init_logging()
logger = logging.getLogger("nook_mcp")


def _parse_args():
    parser = argparse.ArgumentParser(description="sage MCP Server")
    parser.add_argument(
        "--nook",
        metavar="PATH",
        help="Path to the nook directory (overrides config file and env var)",
    )
    args, unknown = parser.parse_known_args()
    if unknown:
        logger.debug("Ignoring unknown args: %s", unknown)
    return args


_args = _parse_args()

if _args.nook:
    os.environ["SAGE_NOOK_PATH"] = os.path.abspath(_args.nook)

_config = SageConfig()

_kg_by_path: dict[str, KnowledgeGraph] = {}
_kg_cache_lock = threading.Lock()
_nook_flag_given: bool = bool(_args.nook)

# MCP server idle auto-exit (#1552).  Stale MCP servers from ended Claude
# Code sessions do not self-terminate, accumulating ChromaDB/HNSW file
# handles on Windows.  When SAGE_MCP_IDLE_HOURS is set (or defaults
# to 8 h), a background daemon thread exits the process once no request
# has been handled for that long.  Set to 0 to disable.
_MCP_IDLE_HOURS_ENV = "SAGE_MCP_IDLE_HOURS"
_MCP_IDLE_HOURS_DEFAULT = 8.0
_last_request_time: float = time.monotonic()


def _mcp_idle_timeout_secs() -> float:
    """Return the configured MCP idle timeout in seconds (0 = disabled)."""
    raw = os.environ.get(_MCP_IDLE_HOURS_ENV, "")
    if raw:
        try:
            hours = float(raw)
            return max(0.0, hours) * 3600
        except ValueError:
            return 0.0
    return _MCP_IDLE_HOURS_DEFAULT * 3600


def _resolve_kg_path() -> str:
    if _nook_flag_given:
        return os.path.join(_config.nook_path, "knowledge_graph.sqlite3")
    return DEFAULT_KG_PATH


def _canonicalize_kg_path(path: str) -> str:
    """Canonicalize a KG cache key so aliases collapse onto one entry.

    ``realpath`` resolves symlinks: two tenants pointing at the same
    SQLite file via different layouts (``/srv/A`` and
    ``/srv/link-to-A``) hit a single cached ``KnowledgeGraph`` rather
    than opening duplicate connections. ``normcase`` normalizes Windows
    drive-letter casing (``C:\\nook`` vs ``c:\\nook``) and
    path-separator style; on POSIX it returns the input unchanged.
    """
    return os.path.normcase(os.path.realpath(path))


def _get_kg(canonical_path=None) -> KnowledgeGraph:
    """Return the cached ``KnowledgeGraph`` for the resolved nook.

    When ``canonical_path`` is ``None`` (default), the path is resolved
    from module state and canonicalized. Callers like :func:`_call_kg`
    that have already captured a canonical key before entering a retry
    loop should pass it through here so the dict insertion uses the same
    key the caller will later use for eviction. Recomputing the key
    inside this function would let ``SAGE_NOOK_PATH`` rotation,
    a symlink remap, or a mount remap between the captured value and
    this call drift the insert and evict keys apart, stranding a closed
    handle under one key while the lookup probes another.
    """
    path = (
        canonical_path if canonical_path is not None else _canonicalize_kg_path(_resolve_kg_path())
    )
    kg = _kg_by_path.get(path)
    if kg is not None:
        return kg
    with _kg_cache_lock:
        kg = _kg_by_path.get(path)
        if kg is None:
            kg = KnowledgeGraph(db_path=path)
            _kg_by_path[path] = kg
    return kg


def _call_kg(op):
    """Run ``op(kg)`` against the cached KG with one-shot retry on close.

    Race we're guarding against: a handler grabs ``kg = _get_kg()`` and is
    about to call ``kg.add_triple(...)`` when ``tool_reconnect`` fires on
    another thread, drains ``_kg_by_path``, and closes the underlying
    sqlite3.Connection. The handler's call then raises
    ``sqlite3.ProgrammingError: Cannot operate on a closed database`` and
    bubbles up as a -32000 to the MCP client even though the user just
    asked for a reconnect.

    Catch that single class of error, evict the stale entry from the
    cache (only if it still points at the closed instance — another
    thread may have already replaced it), and try once more with a fresh
    KG. Beyond one retry give up: a second close means we're losing a
    sustained race we won't win in this loop, and a hung loop is worse
    than a clear failure surface.

    The canonical path is captured once at the top and threaded through
    every ``_get_kg`` call plus the eviction lookup. Doing canonicalize
    only here means an ``OSError`` from ``realpath`` (transient Windows
    junction loss, broken mount) surfaces cleanly before any handler
    runs instead of masking a ``sqlite3.ProgrammingError`` mid-retry.
    Passing the captured key through to ``_get_kg`` also locks the
    insert key to the evict key even if FS or env state mutates between
    attempts, preventing a closed handle from leaking under a stale
    key the lookup no longer matches.
    """
    path = _canonicalize_kg_path(_resolve_kg_path())
    for attempt in range(2):
        kg = _get_kg(path)
        try:
            return op(kg)
        except sqlite3.ProgrammingError:
            if attempt == 0:
                with _kg_cache_lock:
                    if _kg_by_path.get(path) is kg:
                        _kg_by_path.pop(path, None)
                continue
            raise


_client_cache = None
_collection_cache = None
_nook_db_inode = 0  # inode of chroma.sqlite3 at cache time
_nook_db_mtime = 0.0  # mtime of chroma.sqlite3 at cache time


# ── Vector-search disabled flag (#1222) ──────────────────────────────────
# Set when ``hnsw_capacity_status`` reports a divergence between sqlite
# and the HNSW segment large enough that chromadb would segfault on
# segment load. While this is set, vector-shaped tools (``search``,
# ``check_duplicate``) route to the sqlite-only BM25 fallback in
# :func:`sage_mcp.searcher._bm25_only_via_sqlite`. Cleared after a
# successful repair via :func:`tool_reconnect` (which re-runs the probe).
_vector_disabled = False
_vector_disabled_reason = ""
# Optional[dict] (not ``dict | None``) keeps Python 3.9 import-time
# parsing happy — PEP 604 unions in annotations only became unconditional
# at module-eval time in 3.10.
_vector_capacity_status: Optional[dict] = None


def _refresh_vector_disabled_flag() -> None:
    """Re-run the HNSW capacity probe and update the module-level flag.

    Called from :func:`_get_client` whenever the client cache is rebuilt
    (first open or nook replacement). Cheap — pure sqlite + pickle
    read, no chromadb interaction. Never raises: a probe that crashes
    would defeat the point.
    """
    global _vector_disabled, _vector_disabled_reason, _vector_capacity_status
    try:
        from .nook import default_backend

        status = default_backend().health(_config.nook_path, _config.collection_name)
    except Exception:
        logger.debug("HNSW capacity probe raised", exc_info=True)
        return
    # health() fails open per-call (probe exception → ok=True, capacity=None).
    # At this sticky-global layer, a transient probe failure must NOT clear a
    # previously-detected divergence — preserve the prior flag rather than
    # flipping a diverged nook back to vector-enabled on a flaky read (#1222).
    if status.capacity is None:
        logger.debug("HNSW capacity probe returned no data; preserving prior flag")
        return
    # capacity carries the raw sqlite/hnsw counts that tool_status surfaces.
    _vector_capacity_status = status.capacity
    if status.vector_disabled:
        if not _vector_disabled:
            logger.warning(
                "HNSW capacity divergence detected (%s) — routing search to "
                "BM25-only sqlite fallback. Run `sage repair` to restore "
                "vector search.",
                status.detail or "unknown",
            )
        _vector_disabled = True
        _vector_disabled_reason = status.detail
    else:
        if _vector_disabled:
            logger.info(
                "HNSW capacity within tolerance (%s) — vector search re-enabled",
                status.detail or "",
            )
        _vector_disabled = False
        _vector_disabled_reason = ""


# ==================== WRITE-AHEAD LOG ====================
# Every write operation is logged to a JSONL file before execution.
# This provides an audit trail for detecting memory poisoning and
# enables review/rollback of writes from external or untrusted sources.
#
# The WAL dir/file are created LAZILY on first use (_ensure_wal) so that
# importing this module does not create ~/.sage/wal in a redirected HOME
# (e.g. the test suite, which redirects HOME before importing sage modules).
# Preserving the TOCTOU-safe single-syscall creation semantics: os.open
# O_CREAT|O_WRONLY with mode 0o600 creates the file if absent or opens it
# if present in a single kernel call — no race between existence-check and
# open.  See ADR-0095 (store isolation hardening).

_wal_file_cache: Optional[Path] = None  # cached after first _ensure_wal() call


def _ensure_wal() -> Path:
    """Return the WAL file path, creating the dir+file on first call.

    Resolves expanduser at call time so a test-suite HOME redirect is
    honoured rather than the module-load-time HOME.  The result is cached
    in ``_wal_file_cache`` so repeated calls cost only a dict lookup.
    """
    global _wal_file_cache
    if _wal_file_cache is not None:
        return _wal_file_cache
    wal_dir = Path(os.path.expanduser("~/.sage/wal"))
    wal_dir.mkdir(parents=True, exist_ok=True)
    try:
        wal_dir.chmod(0o700)
    except (OSError, NotImplementedError):
        pass
    wal_file = wal_dir / "write_log.jsonl"
    # Atomically create WAL file with restricted permissions (no TOCTOU race).
    # os.open with O_CREAT|O_WRONLY and mode 0o600 creates the file if absent
    # or opens it if present, both in a single syscall.
    try:
        _fd = os.open(str(wal_file), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(_fd)
    except (OSError, NotImplementedError):
        pass
    _wal_file_cache = wal_file
    return wal_file


# Keys whose values should be redacted in WAL entries to avoid logging sensitive content
_WAL_REDACT_KEYS = frozenset(
    {"content", "content_preview", "document", "entry", "entry_preview", "query", "text"}
)


def _wal_log(operation: str, params: dict, result: dict = None):
    """Append a write operation to the write-ahead log."""
    # Redact sensitive content from params before logging
    safe_params = {}
    for k, v in params.items():
        if k in _WAL_REDACT_KEYS:
            safe_params[k] = f"[REDACTED {len(v)} chars]" if isinstance(v, str) else "[REDACTED]"
        else:
            safe_params[k] = v
    entry = {
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "params": safe_params,
        "result": result,
    }
    try:
        wal_file = _ensure_wal()
        fd = os.open(str(wal_file), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        logger.exception("WAL write failed")


def _get_client():
    """Return a ChromaDB PersistentClient, reconnecting if the database changed on disk.

    Detects nook rebuilds (repair/nuke/purge) by checking the inode of
    chroma.sqlite3.  A full rebuild replaces the file, changing the inode.
    Also detects external writes (scripts, CLI) via mtime changes — the
    inode check alone misses in-place modifications that invalidate the
    in-memory HNSW index.

    Note: FAT/exFAT may return 0 for st_ino — the ``current_inode != 0``
    guard skips reconnect detection on those filesystems (safe fallback).
    """
    global \
        _client_cache, \
        _collection_cache, \
        _nook_db_inode, \
        _nook_db_mtime, \
        _metadata_cache, \
        _metadata_cache_time
    db_path = os.path.join(_config.nook_path, "chroma.sqlite3")
    try:
        st = os.stat(db_path)
        current_inode = st.st_ino
        current_mtime = st.st_mtime
    except OSError:
        current_inode = 0
        current_mtime = 0.0

    # If the DB file disappeared (e.g. during rebuild) but we have a cached
    # collection, invalidate so we don't serve stale data.  Without this,
    # both stored and current values are 0 on the first call after deletion,
    # making inode_changed and mtime_changed both False.
    if not os.path.isfile(db_path) and _collection_cache is not None:
        _close_client(_client_cache)
        _client_cache = None
        _collection_cache = None
        _nook_db_inode = 0
        _nook_db_mtime = 0.0
        # Fall through to normal reconnect which will handle missing DB

    inode_changed = current_inode != 0 and current_inode != _nook_db_inode
    mtime_changed = current_mtime != 0.0 and abs(current_mtime - _nook_db_mtime) > 0.01

    if _client_cache is None or inode_changed or mtime_changed:
        # NOTE: do NOT _close_client(_client_cache) here. mtime_changed fires
        # mid-run once a write touches chroma.sqlite3; closing the live client
        # tears down the RustBindingsAPI that outstanding collection handles
        # still use. The old client is dropped from the cache below and GC'd
        # when its last handle releases. (Reverts a harmful audit "fix"; the
        # missing-DB branch above keeps its close — there the DB is gone.)
        # Run the HNSW capacity probe BEFORE chromadb opens the segment —
        # if the index is severely undersized, segment load can segfault
        # the whole MCP server (#1222). The probe is pure sqlite +
        # metadata-pickle read; never touches the HNSW binary files.
        _refresh_vector_disabled_flag()
        _client_cache = ChromaBackend.make_client(_config.nook_path)
        _collection_cache = None
        _metadata_cache = None
        _metadata_cache_time = 0
        # An inode/mtime change means the nook db was rewritten by another
        # process (CLI mine, sync, sweep, repair). The chromadb client cache
        # rebuilt above, but nook_graph holds its OWN module-level cache
        # of (nodes, edges) that doesn't share state with chromadb. Without
        # this invalidate, nook_traverse / nook_graph_stats serve up to
        # 60s of stale topology after the external write. (Pass 5 Cat 25 F1)
        if inode_changed or mtime_changed:
            invalidate_graph_cache()
        _nook_db_inode = current_inode
        _nook_db_mtime = current_mtime
    return _client_cache


def _get_collection(create=False):
    """Return the ChromaDB collection, caching the client between calls.

    On failure, log the exception and retry once after clearing the client
    and collection caches. Tools were silently returning ``None`` when a
    cached client/collection went stale — typically after the chromadb
    rust bindings invalidated a handle following an out-of-band write —
    leaving the LLM with no diagnostic and no recovery path. The retry
    forces ``_get_client()`` to rebuild from scratch (which re-runs
    ``quarantine_stale_hnsw`` per #1322), so the second attempt heals the
    common stale-handle / stale-HNSW case automatically.
    """
    global _client_cache, _collection_cache, _metadata_cache, _metadata_cache_time
    for attempt in range(2):
        try:
            client = _get_client()
            # ChromaDB 1.x persists the EF *identity* (its ``name()``) with the
            # collection but not the EF *instance/configuration*. So a reader or
            # writer that omits ``embedding_function=`` silently gets chromadb's
            # built-in ``DefaultEmbeddingFunction`` — its ``name()`` matches the
            # one we spoof in ``sage.embedding`` (both report ``"default"``,
            # the identity check passes), but the *provider list* is chromadb's
            # default rather than the user's resolved device. On bleeding-edge
            # interpreters (#1299: python 3.14 + chromadb 1.5.x on Apple Silicon)
            # that default provider selection can SIGSEGV the host process on
            # first ``col.add()``. The miner / Stop hook ingest path avoids this
            # because it routes through ``ChromaBackend.get_collection``, which
            # resolves the EF via ``ChromaBackend._resolve_embedding_function``;
            # the MCP server bypassed that abstraction. Resolve the EF inside the
            # branches that actually open a collection so warm-cache reads stay
            # zero-cost. Reuse the backend helper so the two call sites can't
            # drift on logging or fallback semantics.
            if create:
                ef = ChromaBackend._resolve_embedding_function()
                ef_kwargs = {"embedding_function": ef} if ef is not None else {}
                # hnsw:num_threads=1 disables ChromaDB's multi-threaded ParallelFor
                # HNSW insert path, which has a race in repairConnectionsForUpdate /
                # addPoint (see issues #974, #965). Set via metadata on fresh
                # collections and re-applied via _pin_hnsw_threads() for older
                # nooks whose collections were created before this fix (the
                # runtime config does not persist cross-process in chromadb 1.5.x,
                # so the retrofit runs every time _get_collection opens a cache).
                #
                # ChromaDB 1.5.x's Rust binding SIGSEGVs when get_or_create_collection
                # is called with metadata that differs from what's stored. The split
                # below skips the metadata-comparison codepath for existing
                # collections, mirroring the backend-layer fix from #1262.
                try:
                    raw = client.get_collection(_config.collection_name, **ef_kwargs)
                except _ChromaNotFoundError:
                    raw = client.create_collection(
                        _config.collection_name,
                        metadata={
                            "hnsw:space": "cosine",
                            "hnsw:num_threads": 1,
                            **_HNSW_BLOAT_GUARD,
                        },
                        **ef_kwargs,
                    )
                _pin_hnsw_threads(raw)
                _collection_cache = ChromaCollection(raw, nook_path=_config.nook_path)
                _metadata_cache = None
                _metadata_cache_time = 0
            elif _collection_cache is None:
                ef = ChromaBackend._resolve_embedding_function()
                ef_kwargs = {"embedding_function": ef} if ef is not None else {}
                raw = client.get_collection(_config.collection_name, **ef_kwargs)
                _pin_hnsw_threads(raw)
                _collection_cache = ChromaCollection(raw, nook_path=_config.nook_path)
                _metadata_cache = None
                _metadata_cache_time = 0
            return _collection_cache
        except Exception:
            logger.exception(
                "_get_collection attempt %d/2 failed (nook=%s, create=%s)",
                attempt + 1,
                _config.nook_path,
                create,
            )
            if attempt == 0:
                # Reset all caches so the next attempt forces _get_client()
                # to rebuild the chromadb client from scratch — that path
                # re-runs quarantine_stale_hnsw (#1322) and reopens the
                # collection cleanly, healing the common stale-handle case.
                _client_cache = None
                _collection_cache = None
                _metadata_cache = None
                _metadata_cache_time = 0
    return None


def _no_nook():
    return {
        "error": "No nook found",
        "hint": "Run: sage init <dir> && sage mine <dir>",
    }


# ==================== HELPERS ====================


def _safe_meta(meta):
    """Coerce a Chroma metadata value to a dict.

    ChromaDB's ``col.get()`` / ``col.query()`` can return ``None`` for the
    metadata cell of a partially-flushed row (or any row written without
    metadata in older formats). Indexing the result then yields ``None``,
    and downstream ``.get(...)`` calls raise::

        AttributeError: 'NoneType' object has no attribute 'get'

    This bug bricked the embeddings_queue cleanup path in issue #1426 —
    the handler crashed before reaching the ``DELETE FROM embeddings_queue``
    step, so the queue grew without bound while writes kept appearing
    successful.

    Centralizing the coercion through this helper makes the contract
    explicit and keeps the fix self-documenting at every call site:
    *metadata is always a dict by the time it leaves the boundary*.
    """
    return meta if isinstance(meta, dict) else {}


def _fetch_all_metadata(col, where=None):
    """Paginate col.get() to avoid the 10K silent truncation limit."""
    total = col.count()
    all_meta = []
    offset = 0
    while offset < total:
        kwargs = {"include": ["metadatas"], "limit": 1000, "offset": offset}
        if where:
            kwargs["where"] = where
        batch = col.get(**kwargs)
        if not batch["metadatas"]:
            break
        all_meta.extend(batch["metadatas"])
        offset += len(batch["metadatas"])
    return all_meta


_metadata_cache = None
_metadata_cache_time = 0
_METADATA_CACHE_TTL = 5.0  # seconds
_MAX_RESULTS = 100  # upper bound for search/list limit params


def _get_cached_metadata(col, where=None):
    """Return cached metadata if fresh, else fetch and cache."""
    global _metadata_cache, _metadata_cache_time
    now = time.time()
    if (
        where is None
        and _metadata_cache is not None
        and (now - _metadata_cache_time) < _METADATA_CACHE_TTL
    ):
        return _metadata_cache
    result = _fetch_all_metadata(col, where=where)
    if where is None:
        _metadata_cache = result
        _metadata_cache_time = now
    return result


def _sanitize_optional_name(value: str = None, field_name: str = "name") -> str:
    """Validate optional wing/room-style filters (shared impl in config)."""
    return sanitize_optional_name(value, field_name)


# ==================== READ TOOLS ====================


def _tool_status_via_sqlite() -> dict:
    """Pure-sqlite status reader for the #1222 fallback path.

    When the HNSW capacity probe detects divergence, opening the chromadb
    persistent client can segfault. This reader pulls the same wing/room
    breakdown directly from ``embedding_metadata`` so the operator still
    gets a working status response — and crucially the
    ``vector_disabled`` flag — without us touching the vector segment.
    """
    import sqlite3 as _sqlite3

    db_path = os.path.join(_config.nook_path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        return _no_nook()
    collection_name = _config.collection_name

    wings: dict = {}
    rooms: dict = {}
    total = 0
    try:
        conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM embeddings e
                JOIN segments s ON e.segment_id = s.id
                JOIN collections c ON s.collection = c.id
                WHERE c.name = ?
                """,
                (collection_name,),
            ).fetchone()
            total = int(row[0]) if row and row[0] is not None else 0
            for key, target in (("wing", wings), ("room", rooms)):
                for value, count in conn.execute(
                    """
                    SELECT em.string_value, COUNT(*)
                    FROM embedding_metadata em
                    JOIN embeddings e ON em.id = e.id
                    JOIN segments s ON e.segment_id = s.id
                    JOIN collections c ON s.collection = c.id
                    WHERE c.name = ?
                      AND em.key = ?
                      AND em.string_value IS NOT NULL
                    GROUP BY em.string_value
                    """,
                    (collection_name, key),
                ):
                    target[value] = count
        finally:
            conn.close()
    except _sqlite3.Error:
        logger.exception("tool_status sqlite fallback read failed")

    result = {
        "total_drawers": total,
        "wings": wings,
        "rooms": rooms,
        "protocol": NOOK_PROTOCOL,
        "vector_disabled": True,
        "vector_disabled_reason": _vector_disabled_reason,
    }
    if _vector_capacity_status:
        result["hnsw_capacity"] = {
            "sqlite_count": _vector_capacity_status.get("sqlite_count"),
            "hnsw_count": _vector_capacity_status.get("hnsw_count"),
            "divergence": _vector_capacity_status.get("divergence"),
        }
    return result


def tool_status():
    # Run the safe sqlite/pickle probe before we touch chromadb. In the
    # #1222 failure mode, opening the persistent client to call .count()
    # can segfault — short-circuit to a pure-sqlite path when divergence
    # is detected so status stays reachable.
    db_exists = os.path.isfile(os.path.join(_config.nook_path, "chroma.sqlite3"))
    _refresh_vector_disabled_flag()

    if _vector_disabled:
        return _tool_status_via_sqlite()

    # Use create=True only when a nook DB already exists on disk -- this
    # bootstraps the ChromaDB collection on a valid-but-empty nook without
    # accidentally creating a nook in a non-existent directory (#830).
    col = _get_collection(create=db_exists)
    if not col:
        return _no_nook()
    count = col.count()
    wings = {}
    rooms = {}
    result = {
        "total_drawers": count,
        "wings": wings,
        "rooms": rooms,
        "protocol": NOOK_PROTOCOL,
    }
    try:
        all_meta = _get_cached_metadata(col)
        for m in all_meta:
            m = m or {}
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            wings[w] = wings.get(w, 0) + 1
            rooms[r] = rooms.get(r, 0) + 1
    except Exception as e:
        logger.exception("tool_status metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


NOOK_PROTOCOL = """IMPORTANT — sage Memory Protocol:
1. ON WAKE-UP: Call nook_status to load nook overview.
2. BEFORE RESPONDING about any person, project, or past event: call nook_kg_query or nook_search FIRST. Never guess — verify.
3. IF UNSURE about a fact (name, gender, age, relationship): say "let me check" and query the nook. Wrong is worse than slow.
4. AFTER EACH SESSION: call nook_diary_write to record what happened, what you learned, what matters.
5. WHEN FACTS CHANGE: call nook_kg_invalidate on the old fact, nook_kg_add for the new one.

This protocol ensures the AI KNOWS before it speaks. Storage is not memory — but storage + this protocol = memory."""


def tool_list_wings():
    col = _get_collection()
    if not col:
        return _no_nook()
    wings = {}
    result = {"wings": wings}
    try:
        all_meta = _get_cached_metadata(col)
        for m in all_meta:
            m = m or {}
            w = m.get("wing", "unknown")
            wings[w] = wings.get(w, 0) + 1
    except Exception as e:
        logger.exception("tool_list_wings metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


def tool_list_rooms(wing: str = None):
    try:
        wing = _sanitize_optional_name(wing, "wing")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection()
    if not col:
        return _no_nook()
    rooms = {}
    result = {"wing": wing or "all", "rooms": rooms}
    try:
        where = {"wing": wing} if wing else None
        all_meta = _fetch_all_metadata(col, where=where)
        for m in all_meta:
            m = m or {}
            r = m.get("room", "unknown")
            rooms[r] = rooms.get(r, 0) + 1
    except Exception as e:
        logger.exception("tool_list_rooms metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


def tool_get_taxonomy():
    col = _get_collection()
    if not col:
        return _no_nook()
    taxonomy = {}
    result = {"taxonomy": taxonomy}
    try:
        all_meta = _get_cached_metadata(col)
        for m in all_meta:
            m = m or {}
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            if w not in taxonomy:
                taxonomy[w] = {}
            taxonomy[w][r] = taxonomy[w].get(r, 0) + 1
    except Exception as e:
        logger.exception("tool_get_taxonomy metadata fetch failed")
        result["error"] = str(e)
        result["partial"] = True
    return result


def tool_search(
    query: str,
    limit: int = 5,
    wing: str = None,
    room: str = None,
    max_distance: float = 1.5,
    context: str = None,
    agents: list = None,
):
    from . import ops

    limit = max(1, min(limit, _MAX_RESULTS))
    # Transport decode: agents arrives over JSON-RPC and must be a list of
    # strings; drop empties. (Name validation + query sanitization + the
    # vector-disabled probe + transient retry now live in ops.search.)
    if agents is not None:
        if not isinstance(agents, list):
            return {"error": "agents must be a list of strings", "error_kind": "validation"}
        agents = [str(a) for a in agents if a] or None
    result = ops.search(
        query=query,
        nook_path=_config.nook_path,
        collection_name=_config.collection_name,
        wing=wing,
        room=room,
        n_results=limit,
        max_distance=max_distance,
        agents=agents,
    )
    # Echo the context flag only on a successful result dict — never decorate an
    # error response, and stay defensive if ops ever returns a non-dict.
    if context and isinstance(result, dict) and "error" not in result:
        result["context_received"] = True
    return result


def tool_check_duplicate(content: str, threshold: float = 0.9):
    _refresh_vector_disabled_flag()
    if _vector_disabled:
        # Without a usable HNSW we can't compute cosine similarity for
        # near-duplicate detection. Report the limitation rather than
        # silently returning "not a duplicate" — false negatives here
        # would let the AI re-file content the nook already holds.
        return {
            "is_duplicate": False,
            "matches": [],
            "vector_disabled": True,
            "vector_disabled_reason": _vector_disabled_reason,
            "hint": ("duplicate detection requires vector search; run `sage repair` to restore"),
        }
    col = _get_collection()
    if not col:
        return _no_nook()
    try:
        content = sanitize_content(content)
    except ValueError as exc:
        return {"is_duplicate": False, "matches": [], "error": str(exc)}
    try:
        results = col.query(
            query_texts=[content],
            n_results=5,
            include=["metadatas", "documents", "distances"],
        )
        duplicates = []
        if results["ids"] and results["ids"][0]:
            for i, drawer_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i]
                similarity = round(max(0.0, 1 - dist), 3)
                if similarity >= threshold:
                    # Chroma 1.5.x can return None for partially-flushed rows;
                    # coerce to empty sentinels so downstream .get() is safe.
                    meta = _safe_meta(results["metadatas"][0][i])
                    doc = results["documents"][0][i] or ""
                    duplicates.append(
                        {
                            "id": drawer_id,
                            "wing": meta.get("wing", "?"),
                            "room": meta.get("room", "?"),
                            "similarity": similarity,
                            "content": doc[:200] + "..." if len(doc) > 200 else doc,
                        }
                    )
        return {
            "is_duplicate": len(duplicates) > 0,
            "matches": duplicates,
        }
    except Exception:
        logger.exception("check_duplicate failed")
        return {"error": "Duplicate check failed"}


def tool_traverse_graph(start_room: str, max_hops: int = 2):
    """Walk the nook graph from a room. Find connected ideas across wings."""
    max_hops = max(1, min(max_hops, 10))
    col = _get_collection()
    if not col:
        return _no_nook()
    return traverse(start_room, col=col, max_hops=max_hops)


def tool_find_tunnels(wing_a: str = None, wing_b: str = None):
    """Find rooms that bridge two wings — the hallways connecting domains."""
    try:
        wing_a = _sanitize_optional_name(wing_a, "wing_a")
        wing_b = _sanitize_optional_name(wing_b, "wing_b")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection()
    if not col:
        return _no_nook()
    return find_tunnels(wing_a, wing_b, col=col)


def tool_graph_stats():
    """Nook graph overview: nodes, tunnels, edges, connectivity."""
    col = _get_collection()
    if not col:
        return _no_nook()
    return graph_stats(col=col)


def tool_create_tunnel(
    source_wing: str,
    source_room: str,
    target_wing: str,
    target_room: str,
    label: str = "",
    source_drawer_id: str = None,
    target_drawer_id: str = None,
):
    """Create an explicit cross-wing tunnel between two nook locations.

    Use when you notice content in one project relates to another project.
    Example: an API design discussion in project_api connects to the
    database schema in project_database.
    """
    # sanitize_name, require_registered_wing, and create_tunnel raise
    # ValueError / WingNotRegisteredError for invalid endpoints (empty
    # names, unregistered wings, missing rooms). Catch both so the real
    # reason is surfaced instead of escaping and being wrapped as the
    # opaque "Internal tool error" (#1473), mirroring sibling tools.
    from .extensions.wing_registry import (
        WingNotRegisteredError,
        require_registered_wing,
    )

    try:
        source_wing = sanitize_name(source_wing, "source_wing")
        require_registered_wing(source_wing)
        source_room = sanitize_name(source_room, "source_room")
        target_wing = sanitize_name(target_wing, "target_wing")
        require_registered_wing(target_wing)
        target_room = sanitize_name(target_room, "target_room")
        # Pass3 Cat2: sanitize free-form provenance so a malformed label
        # or drawer-id token doesn't slip into tunnel metadata unchecked.
        if label:
            label = sanitize_content(label, max_length=256)
        if source_drawer_id:
            source_drawer_id = sanitize_name(source_drawer_id, "source_drawer_id")
        if target_drawer_id:
            target_drawer_id = sanitize_name(target_drawer_id, "target_drawer_id")
        result = create_tunnel(
            source_wing,
            source_room,
            target_wing,
            target_room,
            label=label,
            source_drawer_id=source_drawer_id,
            target_drawer_id=target_drawer_id,
        )
        # The graph cache is keyed by collection-level topology; a new
        # cross-wing tunnel changes that. Without an explicit invalidate
        # nook_traverse / nook_graph_stats would serve a stale snapshot
        # for up to 60s after the write. (Pass 3 Cat 13 F1)
        invalidate_graph_cache()
        return result
    except (ValueError, WingNotRegisteredError) as e:
        return {"error": str(e)}


def tool_list_tunnels(wing: str = None):
    """List all explicit cross-wing tunnels, optionally filtered by wing."""
    try:
        wing = _sanitize_optional_name(wing, "wing")
    except ValueError as e:
        return {"error": str(e)}
    return list_tunnels(wing)


def tool_delete_tunnel(tunnel_id: str):
    """Delete an explicit tunnel by its ID."""
    if not tunnel_id or not isinstance(tunnel_id, str):
        return {"error": "tunnel_id is required"}
    result = delete_tunnel(tunnel_id)
    invalidate_graph_cache()  # Pass 3 Cat 13 F1
    return result


def tool_follow_tunnels(wing: str, room: str):
    """Follow explicit tunnels from a room to see connected drawers in other wings."""
    try:
        wing = sanitize_name(wing, "wing")
        room = sanitize_name(room, "room")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection()
    return follow_tunnels(wing, room, col=col)


# ==================== WRITE TOOLS ====================


def _merge_agents_on_existing_drawer(
    col, drawer_id: str, new_agents: list, *, chunked: bool
) -> None:
    """Accumulate ``new_agents`` into the stored ``agents`` metadata of an
    existing drawer (single-doc or every chunk of a chunked drawer).

    Best-effort: any exception is swallowed so the idempotent-re-file path
    that calls this helper still returns success to the caller. A merge
    miss is acceptable degradation; a crash here would be a regression.

    Order is preserved (dict.fromkeys dedupe) so the "first agent that
    filed this" is always at the head of the list.
    """
    if not new_agents:
        return
    from .extensions.agent_keyed_drawers import deserialize_agents, serialize_agents

    try:
        if chunked:
            rows = col.get(
                where={"parent_drawer_id": drawer_id},
                include=["metadatas"],
            )
        else:
            rows = col.get(ids=[drawer_id], include=["metadatas"])
        ids = list(getattr(rows, "ids", None) or rows.get("ids", []) or [])
        metas = list(getattr(rows, "metadatas", None) or rows.get("metadatas", []) or [])
        if not ids:
            return
        update_ids: list = []
        update_metas: list = []
        for row_id, stored_meta in zip(ids, metas):
            stored_meta = dict(stored_meta or {})
            stored_agents = deserialize_agents(stored_meta.get("agents"))
            merged = list(dict.fromkeys([*stored_agents, *new_agents]))
            if set(merged) == set(stored_agents):
                continue
            stored_meta["agents"] = serialize_agents(merged)
            update_ids.append(row_id)
            update_metas.append(stored_meta)
        if update_ids:
            col.update(ids=update_ids, metadatas=update_metas)
    except Exception:
        logger.debug("agents merge on idempotent re-file failed for %s", drawer_id, exc_info=True)


def tool_add_drawer(
    wing: str,
    room: str,
    content: str,
    source_file: str = None,
    added_by: str = "mcp",
    agents: list = None,
    hall: str = None,
    confidence: float = 1.0,
):
    """File verbatim content into a wing/room. Checks for duplicates first.

    Content above ``chunk_size`` is split into bounded per-chunk drawers
    via a single batched upsert. Each chunk carries ``parent_drawer_id``
    linkage and ``chunk_index`` metadata so search can rejoin them. The
    returned ``drawer_id`` is the LOGICAL group handle on the chunked
    path; physical drawer ids are in ``chunk_ids`` (#1539). To delete
    or fetch the underlying drawers, iterate ``chunk_ids`` or query by
    ``parent_drawer_id`` — ``tool_get_drawer(drawer_id)`` and
    ``tool_delete_drawer(drawer_id)`` report "not found" on the chunked
    path because no row is stored under the logical group id.

    ``agents`` (sage extension) — list of agent names that touched
    this content. Stored JSON-encoded under metadata key ``"agents"``;
    used by ``nook_search`` / ``sage recall`` to filter drawers
    to those produced by specific agents.

    ``hall`` (sage extension) — explicit hall classification
    (handoff / in-flight / audits / decisions / plans / facts / etc.,
    per the wing_types taxonomy in wing_config.json). When omitted the
    drawer carries no hall tag and downstream readers fall back to
    content-derived classification. The session-end / pre-compact
    hooks pass ``hall="handoff"`` so wake-up retrieval scoped to
    handoff hall surfaces them.

    ``confidence`` (WI-5) — a float in [0.0, 1.0] representing how
    reliable / certain the writer considers this drawer's content.
    Defaults to 1.0 (fully confident). Stored as metadata so WI-6
    decay can weight down low-confidence drawers without touching
    durable identity facts in Personal/core (hall=core). Writers
    that are uncertain about a fact should pass a lower value (e.g.
    0.5 for a MERGE-CANDIDATE that needs future verification).
    """
    global _metadata_cache
    try:
        wing = sanitize_name(wing, "wing")
        room = sanitize_name(room, "room")
        content = sanitize_content(content)
        content = scrub_secrets(content)  # PRD §13: no secret tokens in persisted drawers
        if source_file:
            source_file = strip_lone_surrogates(source_file)
        added_by = strip_lone_surrogates(added_by)
        if hall is not None:
            hall = sanitize_name(hall, "hall")
        # Coerce / validate agents list. None and [] both serialise to "[]".
        if agents is None:
            agents_list = []
        elif isinstance(agents, list):
            agents_list = [strip_lone_surrogates(str(a)) for a in agents if a]
        else:
            return {"success": False, "error": "agents must be a list of strings"}
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Validate confidence: must be a float in [0.0, 1.0]. Coerce int to float.
    # WI-5: confidence is stored in drawer metadata so WI-6 decay can weight it.
    # None is treated as "unspecified" and coerced to the default (1.0) rather
    # than rejected — a caller that passes confidence=None explicitly means the
    # same as omitting it. NaN, inf, out-of-range, and non-numeric values still
    # produce a hard error.
    if confidence is None:
        confidence_val = 1.0
    else:
        try:
            confidence_val = float(confidence)
        except (TypeError, ValueError):
            return {"success": False, "error": "confidence must be a float in [0.0, 1.0]"}
        import math

        if math.isnan(confidence_val) or math.isinf(confidence_val):
            return {"success": False, "error": "confidence must be a finite float in [0.0, 1.0]"}
        if not (0.0 <= confidence_val <= 1.0):
            return {"success": False, "error": "confidence must be in [0.0, 1.0]"}

    # Explicit wing registration (Phase 4). Reject unregistered wings so
    # typos surface immediately instead of silently creating a phantom
    # wing that search will never find. Diary writes (`wing_<agent>`)
    # bypass this gate via the registry's prefix allowance.
    from .extensions.wing_registry import (
        WingNotRegisteredError,
        require_registered_wing,
    )

    try:
        require_registered_wing(wing)
    except WingNotRegisteredError as e:
        return {"success": False, "error": str(e)}

    col = _get_collection(create=True)
    if not col:
        return _no_nook()

    drawer_id = (
        f"drawer_{wing}_{room}_{hashlib.sha256((wing + room + content).encode()).hexdigest()[:24]}"
    )

    _wal_log(
        "add_drawer",
        {
            "drawer_id": drawer_id,
            "wing": wing,
            "room": room,
            "added_by": added_by,
            "content_length": len(content),
            "content_preview": content[:200],
        },
    )

    chunk_size = _config.chunk_size
    from .extensions.agent_keyed_drawers import serialize_agents

    from .consolidation import DRAWER_STRENGTH_DEFAULT as _STRENGTH_DEFAULT

    base_meta = {
        "wing": wing,
        "room": room,
        "source_file": source_file or "",
        "added_by": added_by,
        "filed_at": datetime.now(timezone.utc).isoformat(),
        "agents": serialize_agents(agents_list),
        "confidence": confidence_val,  # WI-5: drawer-level confidence tag
        "strength": _STRENGTH_DEFAULT,  # WI-6: initial strength; recomputed from provenance by decay_pass
    }
    if hall:
        base_meta["hall"] = hall

    # Idempotency. Three cases to detect a prior committed write:
    # (a) Single-doc path: drawer_id row exists (the only id used).
    # (b) Chunked path: probe the LAST chunk id — its presence implies
    #     every earlier chunk also landed, since the batched upsert
    #     is all-or-nothing.
    # (c) Pre-#1539 single-row write of oversized content under
    #     drawer_id: probe drawer_id alongside the last chunk id so a
    #     re-call with identical oversized content does not duplicate
    #     the original row by adding fresh chunks under different ids.
    if len(content) <= chunk_size:
        idempotency_probe_ids = [drawer_id]
    else:
        last_chunk_idx = (len(content) - 1) // chunk_size
        idempotency_probe_ids = [drawer_id, f"{drawer_id}_chunk_{last_chunk_idx:06d}"]
    try:
        existing = col.get(ids=idempotency_probe_ids, include=[])
        if existing.ids:
            # Idempotent re-file: accumulate any new agent names into the
            # stored agents list so the "every agent that touched this drawer"
            # semantics isn't lost when the same content is filed twice by
            # different agents. Best-effort: a merge failure must NOT break
            # idempotency, so we always return the same success shape.
            if agents_list:
                _merge_agents_on_existing_drawer(
                    col, drawer_id, agents_list, chunked=(len(content) > chunk_size)
                )
            return {"success": True, "reason": "already_exists", "drawer_id": drawer_id}
    except Exception:
        logger.debug("Idempotency pre-check failed for %s", idempotency_probe_ids, exc_info=True)

    try:
        if len(content) <= chunk_size:
            col.upsert(
                ids=[drawer_id],
                documents=[content],
                metadatas=[{**base_meta, "chunk_index": 0}],
            )
            inserted = col.get(ids=[drawer_id], include=[])
            if not inserted.ids:
                raise RuntimeError(
                    "Drawer write was acknowledged but the new ID is not readable. "
                    "The nook index may be stale; run reconnect or repair."
                )
            _metadata_cache = None
            invalidate_graph_cache()  # Pass 3 Cat 13 F1
            logger.info(f"Filed drawer: {drawer_id} → {wing}/{room}")
            return {
                "success": True,
                "drawer_id": drawer_id,
                "wing": wing,
                "room": room,
                "chunks": 1,
            }

        # Oversized content: split into bounded per-chunk drawers so the
        # embedding model never sees a document above ``chunk_size``.
        # Single batched ``upsert`` so the embedding pass either commits
        # every chunk or none — no half-written nook if the embedding
        # model fails mid-loop (#1539).
        chunk_ids: list[str] = []
        chunk_docs: list[str] = []
        chunk_metas: list[dict] = []
        for i in range(0, len(content), chunk_size):
            chunk_idx = i // chunk_size
            chunk_ids.append(f"{drawer_id}_chunk_{chunk_idx:06d}")
            chunk_docs.append(content[i : i + chunk_size])
            chunk_metas.append(
                {**base_meta, "chunk_index": chunk_idx, "parent_drawer_id": drawer_id}
            )
        col.upsert(ids=chunk_ids, documents=chunk_docs, metadatas=chunk_metas)
        # Probe the LAST chunk id, not the first — its presence confirms
        # the whole batch landed, not just the leading row.
        inserted = col.get(ids=[chunk_ids[-1]], include=[])
        if not inserted.ids:
            raise RuntimeError(
                "Drawer write was acknowledged but the new ID is not readable. "
                "The nook index may be stale; run reconnect or repair."
            )
        _metadata_cache = None
        invalidate_graph_cache()  # Pass 3 Cat 13 F1
        logger.info(f"Filed drawer: {drawer_id} → {wing}/{room} ({len(chunk_ids)} chunks)")
        return {
            "success": True,
            "drawer_id": drawer_id,
            "wing": wing,
            "room": room,
            "chunks": len(chunk_ids),
            "chunk_ids": chunk_ids,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_delete_drawer(drawer_id: str):
    """Delete a single drawer by ID."""
    global _metadata_cache
    col = _get_collection()
    if not col:
        return _no_nook()
    existing = col.get(ids=[drawer_id])
    if not existing["ids"]:
        return {"success": False, "error": f"Drawer not found: {drawer_id}"}

    # Log the deletion with the content being removed for audit trail
    deleted_content = existing.get("documents", [""])[0] if existing.get("documents") else ""
    deleted_meta = _safe_meta(
        existing.get("metadatas", [{}])[0] if existing.get("metadatas") else {}
    )
    _wal_log(
        "delete_drawer",
        {
            "drawer_id": drawer_id,
            "deleted_meta": deleted_meta,
            "content_preview": deleted_content[:200],
        },
    )

    try:
        col.delete(ids=[drawer_id])
        _metadata_cache = None
        invalidate_graph_cache()  # Pass 3 Cat 13 F1
        logger.info(f"Deleted drawer: {drawer_id}")
        return {"success": True, "drawer_id": drawer_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_sync(project_dir: str = None, wing: str = None, apply: bool = False):
    """Prune drawers whose source files are gitignored, missing, or moved (#1252)."""
    global _metadata_cache
    from .nook import MineAlreadyRunning
    from .sync import sync_nook

    if not _config.nook_path:
        np = _no_nook()
        return {"success": False, "error": np.get("error", "no nook"), "hint": np.get("hint")}
    project_dirs = [project_dir] if project_dir else None
    try:
        try:
            report = sync_nook(
                nook_path=_config.nook_path,
                project_dirs=project_dirs,
                wing=wing,
                dry_run=not apply,
                wal_log=_wal_log,
            )
            return {"success": True, **report}
        # Order matters: typed handlers must precede the bare Exception
        # below, otherwise MineAlreadyRunning and ValueError fall into the
        # generic "sync failed" branch and break the structured-error tests.
        except MineAlreadyRunning as exc:
            return {
                "success": False,
                "error": f"another mine is in progress: {exc}",
                "error_class": "LockHeldByOtherProcess",
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"sync failed: {exc}"}
    finally:
        if apply:
            _metadata_cache = None
            invalidate_graph_cache()  # Pass 4 Cat 19 F3 — sync prunes drawers


def tool_get_drawer(drawer_id: str):
    """Fetch a single drawer by ID. Returns full content and metadata."""
    col = _get_collection()
    if not col:
        return _no_nook()
    try:
        result = col.get(ids=[drawer_id], include=["documents", "metadatas"])
        if not result["ids"]:
            return {"error": f"Drawer not found: {drawer_id}"}
        meta = _safe_meta(result["metadatas"][0])
        doc = result["documents"][0]
        # source_file is the absolute filesystem path written by the
        # miners. Reduce to its basename before handing it to the MCP
        # client — same threat model as the nook_path leak fix:
        # nested-agent / multi-server topologies treat the client as a
        # separate trust domain. Basename preserves citation utility.
        # Mirrors the searcher.search_memories() return shape.
        safe_meta = dict(meta) if meta else {}
        if safe_meta.get("source_file"):
            safe_meta["source_file"] = Path(safe_meta["source_file"]).name
        # Decode the JSON-encoded agents blob into a list before returning.
        from .extensions.agent_keyed_drawers import deserialize_agents

        agents_list = deserialize_agents(safe_meta.get("agents"))
        safe_meta["agents"] = agents_list
        return {
            "drawer_id": drawer_id,
            "content": doc,
            "wing": safe_meta.get("wing", ""),
            "room": safe_meta.get("room", ""),
            "agents": agents_list,
            "metadata": safe_meta,
        }
    except Exception as e:
        return {"error": str(e)}


def tool_list_drawers(wing: str = None, room: str = None, limit: int = 20, offset: int = 0):
    """List drawers with pagination. Optional wing/room filter."""
    limit = max(1, min(limit, _MAX_RESULTS))
    offset = max(0, offset)
    try:
        wing = _sanitize_optional_name(wing, "wing")
        room = _sanitize_optional_name(room, "room")
    except ValueError as e:
        return {"error": str(e)}
    col = _get_collection()
    if not col:
        return _no_nook()
    try:
        where = None
        conditions = []
        if wing:
            conditions.append({"wing": wing})
        if room:
            conditions.append({"room": room})
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        kwargs = {"include": ["documents", "metadatas"], "limit": limit, "offset": offset}
        if where:
            kwargs["where"] = where
        result = col.get(**kwargs)

        # Compute total matching drawers for pagination.
        if where:
            total_result = col.get(where=where, include=[])
            total = len(total_result["ids"])
        else:
            total = col.count()

        from .extensions.agent_keyed_drawers import deserialize_agents

        drawers = []
        for i, did in enumerate(result["ids"]):
            meta = _safe_meta(result["metadatas"][i])
            doc = result["documents"][i]
            drawers.append(
                {
                    "drawer_id": did,
                    "wing": meta.get("wing", ""),
                    "room": meta.get("room", ""),
                    "agents": deserialize_agents(meta.get("agents")),
                    "content_preview": doc[:200] + "..." if len(doc) > 200 else doc,
                }
            )
        return {
            "drawers": drawers,
            "total": total,
            "count": len(drawers),
            "offset": offset,
            "limit": limit,
        }
    except Exception as e:
        return {"error": str(e)}


def tool_update_drawer(drawer_id: str, content: str = None, wing: str = None, room: str = None):
    """Update an existing drawer's content and/or metadata."""
    global _metadata_cache

    if content is None and wing is None and room is None:
        return {"success": True, "drawer_id": drawer_id, "noop": True}

    col = _get_collection()
    if not col:
        return _no_nook()
    try:
        existing = col.get(ids=[drawer_id], include=["documents", "metadatas"])
        if not existing["ids"]:
            return {"success": False, "error": f"Drawer not found: {drawer_id}"}

        old_meta = _safe_meta(existing["metadatas"][0])
        old_doc = existing["documents"][0]

        new_doc = old_doc
        if content is not None:
            try:
                new_doc = sanitize_content(content)
                new_doc = scrub_secrets(new_doc)  # ADR-0042: write-boundary scrub
            except ValueError as e:
                return {"success": False, "error": str(e)}

        new_meta = dict(old_meta)
        if wing is not None:
            try:
                new_meta["wing"] = sanitize_name(wing, "wing")
            except ValueError as e:
                return {"success": False, "error": str(e)}
        if room is not None:
            try:
                new_meta["room"] = sanitize_name(room, "room")
            except ValueError as e:
                return {"success": False, "error": str(e)}

        _wal_log(
            "update_drawer",
            {
                "drawer_id": drawer_id,
                "old_wing": old_meta.get("wing", ""),
                "old_room": old_meta.get("room", ""),
                "new_wing": new_meta.get("wing", ""),
                "new_room": new_meta.get("room", ""),
                "content_changed": content is not None,
                "content_preview": new_doc[:200] if content is not None else None,
            },
        )

        update_kwargs = {"ids": [drawer_id]}
        if content is not None:
            update_kwargs["documents"] = [new_doc]
        update_kwargs["metadatas"] = [new_meta]
        col.update(**update_kwargs)

        _metadata_cache = None
        invalidate_graph_cache()  # Pass 4 Cat 19 F1 — wing/room mutation changes topology

        logger.info(f"Updated drawer: {drawer_id}")
        return {
            "success": True,
            "drawer_id": drawer_id,
            "wing": new_meta.get("wing", ""),
            "room": new_meta.get("room", ""),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== KNOWLEDGE GRAPH ====================


def tool_kg_query(entity: str, as_of: str = None, direction: str = "both"):
    """Query the knowledge graph for an entity's relationships."""
    try:
        entity = sanitize_kg_value(entity, "entity")
        as_of = sanitize_iso_temporal(as_of, "as_of")
    except ValueError as e:
        return {"error": str(e)}

    if direction not in ("outgoing", "incoming", "both"):
        return {"error": "direction must be 'outgoing', 'incoming', or 'both'"}

    results = _call_kg(lambda kg: kg.query_entity(entity, as_of=as_of, direction=direction))
    return {"entity": entity, "as_of": as_of, "facts": results, "count": len(results)}


def tool_kg_add(
    subject: str,
    predicate: str,
    object: str,
    valid_from: str = None,
    valid_to: str = None,
    source_closet: str = None,
    source_file: str = None,
    source_drawer_id: str = None,
):
    """Add a relationship to the knowledge graph.

    All temporal and provenance fields are optional. ``valid_to`` lets callers
    backfill historical facts with a known end date/time in a single call
    instead of a separate ``kg_invalidate`` call.

    Temporal values accept either ``YYYY-MM-DD`` or canonical UTC datetimes in
    the form ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    try:
        subject = sanitize_kg_value(subject, "subject")
        predicate = sanitize_name(predicate, "predicate")
        object = sanitize_kg_value(object, "object")
        # ADR-0042: scrub secrets from KG triple values at the write boundary.
        # Applied after sanitization so validation errors surface first.
        subject = scrub_secrets(subject)
        object = scrub_secrets(object)
        valid_from = sanitize_iso_temporal(valid_from, "valid_from")
        valid_to = sanitize_iso_temporal(valid_to, "valid_to")
        # Pass3 Cat2: provenance fields are optional, but a payload that
        # contains nulls or oversized strings would corrupt the WAL and
        # the triple metadata. sanitize_kg_value enforces the same length
        # cap as subject/object; source_drawer_id uses the stricter name
        # rule because it's an internal identifier.
        if source_closet is not None:
            source_closet = sanitize_kg_value(source_closet, "source_closet")
        if source_file is not None:
            source_file = sanitize_kg_value(source_file, "source_file")
        if source_drawer_id is not None:
            source_drawer_id = sanitize_name(source_drawer_id, "source_drawer_id")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    _wal_log(
        "kg_add",
        {
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "source_closet": source_closet,
            "source_file": source_file,
            "source_drawer_id": source_drawer_id,
        },
    )

    triple_id = _call_kg(
        lambda kg: kg.add_triple(
            subject,
            predicate,
            object,
            valid_from=valid_from,
            valid_to=valid_to,
            source_closet=source_closet,
            source_file=source_file,
            source_drawer_id=source_drawer_id,
        )
    )
    return {"success": True, "triple_id": triple_id, "fact": f"{subject} → {predicate} → {object}"}


def tool_kg_invalidate(subject: str, predicate: str, object: str, ended: str = None):
    """Mark a fact as no longer true.

    Returns the actual ``ended`` date/time that was stored. When the caller
    omits ``ended``, the underlying graph stamps ``date.today()`` and the
    response reflects that resolved value.

    Temporal values accept either ``YYYY-MM-DD`` or canonical UTC datetimes in
    the form ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    try:
        subject = sanitize_kg_value(subject, "subject")
        predicate = sanitize_name(predicate, "predicate")
        object = sanitize_kg_value(object, "object")
        ended = sanitize_iso_temporal(ended, "ended")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    resolved_ended = ended or date.today().isoformat()

    _wal_log(
        "kg_invalidate",
        {
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "ended": resolved_ended,
        },
    )

    _call_kg(lambda kg: kg.invalidate(subject, predicate, object, ended=resolved_ended))
    return {
        "success": True,
        "fact": f"{subject} → {predicate} → {object}",
        "ended": resolved_ended,
    }


def tool_kg_timeline(entity: str = None):
    """Get chronological timeline of facts, optionally for one entity."""
    if entity is not None:
        try:
            entity = sanitize_kg_value(entity, "entity")
        except ValueError as e:
            return {"error": str(e)}
    results = _call_kg(lambda kg: kg.timeline(entity))
    return {"entity": entity or "all", "timeline": results, "count": len(results)}


def tool_kg_stats():
    """Knowledge graph overview: entities, triples, relationship types."""
    return _call_kg(lambda kg: kg.stats())


# ==================== AGENT DIARY ====================


def tool_diary_write(agent_name: str, entry: str, topic: str = "general", wing: str = ""):
    """
    Write a diary entry for this agent. Entries are timestamped and
    accumulate over time in a diary room.

    This is the agent's personal journal — observations, thoughts,
    what it worked on, what it noticed, what it thinks matters.

    Note: ``agent_name`` is normalized to lowercase before storage so
    that diary reads are case-insensitive (see #1243). "Claude",
    "claude", and "CLAUDE" all resolve to the same agent.
    """
    from .extensions.wing_registry import (
        WingNotRegisteredError,
        require_registered_wing,
    )

    try:
        agent_name = sanitize_name(agent_name, "agent_name").lower()
        entry = sanitize_content(entry)
        entry = scrub_secrets(entry)  # ADR-0042: write-boundary scrub
        topic = sanitize_name(topic, "topic")
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if wing:
        try:
            wing = sanitize_name(wing)
            require_registered_wing(wing)
        except (ValueError, WingNotRegisteredError) as e:
            return {"success": False, "error": str(e)}
    else:
        wing = f"wing_{agent_name.replace(' ', '_')}"
    room = "diary"
    col = _get_collection(create=True)
    if not col:
        return _no_nook()

    now = datetime.now()
    entry_id = (
        f"diary_{wing}_{now.strftime('%Y%m%d_%H%M%S%f')}_"
        f"{hashlib.sha256(entry.encode()).hexdigest()[:12]}"
    )

    _wal_log(
        "diary_write",
        {
            "agent_name": agent_name,
            "topic": topic,
            "entry_id": entry_id,
            "entry_preview": entry[:200],
        },
    )

    try:
        from .extensions.agent_keyed_drawers import serialize_agents

        base_metadata = {
            "wing": wing,
            "room": room,
            "hall": "hall_diary",
            "topic": topic,
            "type": "diary_entry",
            # Older diary-specific singular field. Kept for diary-read
            # tooling that filters by `agent`.
            "agent": agent_name,
            # sage agent-keyed extension. Setting agents=[agent_name]
            # here ensures that nook_search(agents=[X]) finds X's diary
            # entries alongside any drawers tagged with X by tool_add_drawer.
            # "Recall its own past work" depends on diary being included.
            "agents": serialize_agents([agent_name]),
            "filed_at": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
        }
        chunk_size = _config.chunk_size
        if len(entry) <= chunk_size:
            col.add(
                ids=[entry_id],
                documents=[entry],
                metadatas=[{**base_metadata, "chunk_index": 0}],
            )
            invalidate_graph_cache()  # Pass 4 Cat 19 F2 — diary adds wing/room edges
            logger.info(f"Diary entry: {entry_id} → {wing}/diary/{topic}")
            return {
                "success": True,
                "entry_id": entry_id,
                "agent": agent_name,
                "topic": topic,
                "timestamp": now.isoformat(),
                "chunks": 1,
            }

        # Oversized entry: split into bounded per-chunk drawers so the
        # embedding model never sees a document above ``chunk_size``.
        # Every chunk carries ``parent_entry_id`` so search can rejoin
        # them and ``chunk_index`` for ordered reconstruction (#1539).
        # Note on ``entry_id`` in the return value: for the chunked
        # path the returned ``entry_id`` is the LOGICAL group handle
        # (no drawer is stored under that exact id). The physical
        # drawer ids are in ``chunk_ids``. Callers wanting to fetch
        # by id should iterate ``chunk_ids``; callers wanting to
        # query by metadata can filter on ``parent_entry_id``.
        # Use a single batched ``add`` so the embedding pass either
        # commits all chunks or none — avoids a half-written nook
        # if the embedding model fails mid-loop. ``col.add`` (not
        # ``upsert``) is intentional here: ``entry_id`` is timestamp-
        # based with microsecond precision, so every call generates a
        # fresh id and a duplicate is by definition a same-microsecond
        # clash that should surface as an error rather than silently
        # overwrite the prior entry (cf. ``tool_add_drawer`` whose
        # content-hash ids are deliberately idempotent and use upsert).
        chunk_ids: list[str] = []
        chunk_docs: list[str] = []
        chunk_metas: list[dict] = []
        for i in range(0, len(entry), chunk_size):
            chunk_idx = i // chunk_size
            chunk_ids.append(f"{entry_id}_chunk_{chunk_idx:06d}")
            chunk_docs.append(entry[i : i + chunk_size])
            chunk_metas.append(
                {
                    **base_metadata,
                    "chunk_index": chunk_idx,
                    "parent_entry_id": entry_id,
                }
            )
        col.add(ids=chunk_ids, documents=chunk_docs, metadatas=chunk_metas)
        invalidate_graph_cache()  # Pass 4 Cat 19 F2 — diary chunked write
        logger.info(f"Diary entry: {entry_id} → {wing}/diary/{topic} ({len(chunk_ids)} chunks)")
        return {
            "success": True,
            "entry_id": entry_id,
            "agent": agent_name,
            "topic": topic,
            "timestamp": now.isoformat(),
            "chunks": len(chunk_ids),
            "chunk_ids": chunk_ids,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_diary_read(agent_name: str, last_n: int = 10, wing: str = ""):
    """
    Read an agent's recent diary entries. Returns the last N entries
    in chronological order — the agent's personal journal.

    When ``wing`` is provided, reads only from that wing. When ``wing``
    is empty or omitted, returns entries from every wing this agent has
    written to. Diary writes from hooks land in project-derived wings
    (``wing_<project>``), so requiring a specific wing on read would
    silo those entries from agent-initiated reads.

    Note: ``agent_name`` is normalized to lowercase before filtering so
    that reads are case-insensitive.
    """
    try:
        agent_name = sanitize_name(agent_name, "agent_name").lower()
        if wing:
            wing = sanitize_name(wing)
    except ValueError as e:
        return {"error": str(e)}
    last_n = max(1, min(last_n, 100))
    col = _get_collection()
    if not col:
        return _no_nook()

    # Build filter: always scope by agent + room=diary. Wing is optional —
    # when empty, return entries across all wings for this agent (matches
    # the #1097 empty-string-as-no-filter convention for LLM ergonomics).
    conditions = [{"room": "diary"}, {"agent": agent_name}]
    if wing:
        conditions.insert(0, {"wing": wing})

    try:
        results = col.get(
            where={"$and": conditions},
            include=["documents", "metadatas"],
            limit=10000,
        )

        if not results["ids"]:
            return {"agent": agent_name, "entries": [], "message": "No diary entries yet."}

        # Combine and sort by timestamp
        from .extensions.agent_keyed_drawers import deserialize_agents

        entries = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            meta = _safe_meta(meta)
            entries.append(
                {
                    "date": meta.get("date", ""),
                    "timestamp": meta.get("filed_at", ""),
                    "topic": meta.get("topic", ""),
                    # Surface the agents list for parity with tool_get_drawer
                    # and tool_list_drawers. Diary entries written by
                    # tool_diary_write now carry agents=[agent_name]; older
                    # entries deserialise to [].
                    "agents": deserialize_agents(meta.get("agents")),
                    "content": doc,
                }
            )

        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        entries = entries[:last_n]

        return {
            "agent": agent_name,
            "entries": entries,
            "total": len(results["ids"]),
            "showing": len(entries),
        }
    except Exception:
        logger.exception("diary_read failed")
        return {"error": "Failed to read diary entries"}


def tool_hook_settings(silent_save: bool = None, desktop_toast: bool = None):
    """
    Get or set hook behavior settings.

    - silent_save: True = stop hook saves directly (no MCP clutter),
      False = old-style blocking MCP calls. Default: True.
    - desktop_toast: True = show notify-send desktop toast on save,
      False = terminal-only notification. Default: False.

    Call with no arguments to see current settings.
    """
    from .config import SageConfig

    try:
        config = SageConfig()
    except Exception as e:
        return {"success": False, "error": str(e)}

    changed = []
    if silent_save is not None:
        config.set_hook_setting("silent_save", silent_save)
        changed.append(f"silent_save → {silent_save}")
    if desktop_toast is not None:
        config.set_hook_setting("desktop_toast", desktop_toast)
        changed.append(f"desktop_toast → {desktop_toast}")

    # Re-read to return current state
    try:
        config = SageConfig()
    except Exception:
        logger.debug("Could not re-read config after update", exc_info=True)

    result = {
        "success": True,
        "settings": {
            "silent_save": config.hook_silent_save,
            "desktop_toast": config.hook_desktop_toast,
        },
    }
    if changed:
        result["updated"] = changed
    return result


def tool_memories_filed_away():
    """Acknowledge the latest silent checkpoint. Returns a short summary."""
    state_dir = Path.home() / ".sage" / "hook_state"
    ack_file = state_dir / "last_checkpoint"
    if not ack_file.is_file():
        return {
            "status": "quiet",
            "message": "No recent journal entry",
            "count": 0,
            "timestamp": None,
        }
    try:
        data = json.loads(ack_file.read_text(encoding="utf-8"))
        ack_file.unlink(missing_ok=True)
        msgs = data.get("msgs", 0)
        return {
            "status": "ok",
            "message": f"\u2726 {msgs} messages tucked into drawers",
            "count": msgs,
            "timestamp": data.get("ts", None),
        }
    except (json.JSONDecodeError, OSError):
        ack_file.unlink(missing_ok=True)
        return {
            "status": "error",
            "message": "\u2726 Journal entry filed in the nook",
            "count": 0,
            "timestamp": None,
        }


# ==================== SETTINGS TOOLS ====================


def tool_reconnect():
    """Force the MCP server to drop cached ChromaDB + KnowledgeGraph state.

    Use after external scripts or CLI commands modify the nook database
    or replace ``knowledge_graph.sqlite3`` directly, which can leave the
    in-memory HNSW index stale or pin a closed-on-disk SQLite connection.
    """
    global \
        _client_cache, \
        _collection_cache, \
        _nook_db_inode, \
        _nook_db_mtime, \
        _vector_disabled, \
        _vector_disabled_reason
    from . import nook as nook_module

    close_errors = []
    try:
        nook_module._DEFAULT_BACKEND.close_nook(_config.nook_path)
    except Exception as exc:
        logger.debug("Failed to close shared nook backend during reconnect", exc_info=True)
        close_errors.append(f"backend close_nook failed: {exc}")
    try:
        from chromadb.api.client import SharedSystemClient

        clear_system_cache = getattr(SharedSystemClient, "clear_system_cache", None)
        if callable(clear_system_cache):
            clear_system_cache()
        else:
            logger.debug(
                "SharedSystemClient.clear_system_cache is unavailable; skipping shared Chroma cache clear during reconnect"
            )
    except Exception as exc:
        logger.debug(
            "Failed to clear Chroma shared system cache during reconnect",
            exc_info=True,
        )
        close_errors.append(f"shared Chroma cache clear failed: {exc}")
    _client_cache = None
    _collection_cache = None
    _nook_db_inode = 0
    _nook_db_mtime = 0.0
    # Force probe re-run on next _get_client by clearing the flag now;
    # _refresh_vector_disabled_flag will re-set it if the divergence
    # still applies after the reconnect.
    _vector_disabled = False
    _vector_disabled_reason = ""
    # Drain the per-path KnowledgeGraph cache so a replaced sqlite file is
    # reopened on the next tool call rather than served from a stale handle.
    with _kg_cache_lock:
        for kg in _kg_by_path.values():
            try:
                kg.close()
            except Exception:
                pass
        _kg_by_path.clear()
    # nook_graph's cache is module-level in nook_graph.py; clear it so
    # nook_traverse / nook_graph_stats don't serve stale topology after
    # an explicit operator-requested reconnect. (Pass 5 Cat 25 F2)
    invalidate_graph_cache()
    try:
        col = _get_collection()
        if col is None:
            result = {
                "success": False,
                "message": "No nook found after reconnect",
                "drawers": 0,
                "vector_disabled": _vector_disabled,
            }
            if close_errors:
                result["error"] = "; ".join(close_errors)
            return result
        if close_errors:
            return {
                "success": False,
                "message": "Reconnect reopened the nook but failed to fully reset cached handles",
                "drawers": col.count(),
                "vector_disabled": _vector_disabled,
                "vector_disabled_reason": _vector_disabled_reason,
                "error": "; ".join(close_errors),
            }
        return {
            "success": True,
            "message": "Reconnected to nook",
            "drawers": col.count(),
            "vector_disabled": _vector_disabled,
            "vector_disabled_reason": _vector_disabled_reason,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== SKILL / AGENT / SCRIPT REGISTRY ====================


def tool_registry_search(
    query: str = "",
    kind: str = None,
    limit: int = 10,
    repo_root: str = None,
    force_rebuild: bool = False,
):
    """Search the on-disk skill/agent/script metadata registry.

    The registry indexes lightweight descriptors (name, kind, one_line,
    triggers, path) for every ``agents/*.md`` (kind=agent),
    ``skills/*/SKILL.md`` (kind=skill), and ``scripts/*`` non-binary file
    (kind=script) found under ``repo_root``.

    Full artifact bodies are NEVER stored in the registry — only metadata
    pointers.  To load the actual skill or agent file, use the ``path``
    field in the returned entry and read the file from disk directly.

    ``query``        — keyword search across name, one_line, and triggers.
                       Empty string returns all entries (up to ``limit``).
    ``kind``         — filter by "agent", "skill", or "script" (optional).
    ``limit``        — max results to return (default 10, max 50).
    ``repo_root``    — path to the sage repo root.  Defaults to the
                       directory that contains the installed ``sage``
                       package (i.e. the installed plugin's own repo root).
    ``force_rebuild``— bypass the in-process cache and re-scan from disk.
    """
    from . import ops

    # Repo-root resolution, kind validation, build, and search all live in
    # ops.registry_search — the single copy shared with the CLI surface.
    return ops.registry_search(
        query=query,
        kind=kind,
        limit=limit,
        repo_root=repo_root,
        force_rebuild=force_rebuild,
    )


# ==================== ESTATE TOOL ====================


def tool_estate(level: int = 0) -> dict:
    """Read the live Nook and emit a schema-valid Estate Model JSON.

    Uses the in-process collection (one client, no concurrent open — ADR-0003).
    Gracefully degrades to ``{"available": false, "reason": ...}`` when the
    Nook is absent or the store is unavailable.

    ``level`` (0-3) bounds enumeration depth:
      0  — structure + counts only (default)
      1–3 — reserved for future drawer-level detail (same as 0 in Phase 3)
    """
    level = max(0, min(int(level), 3))

    # Degradation reasons are emitted in the (exported) model JSON, so any
    # exception text must be redacted — str(exc) from Chroma/fs/jsonschema can
    # carry absolute paths or offending values (Codex adversarial review, fold
    # round 2; ADR-0003 no-home-path/no-secret contract).
    from .estate.redact import redact_string

    # ── 0. Existence pre-check (read-only contract) ──────────────────────────
    # _get_collection → _get_client → ChromaBackend.make_client constructs a
    # PersistentClient with NO precheck, and PersistentClient lazily CREATES
    # chroma.sqlite3 on an absent nook. So opening here to get a graceful
    # "unavailable" answer would MUTATE the nook dir — violating create=False /
    # read-only (ADR-0003; PR #34 review). Guard exactly like the CLI and
    # ChromaBackend.get_collection(create=False): never open an absent DB.
    import os as _os

    db_path = _os.path.join(_config.nook_path, "chroma.sqlite3")
    if not _os.path.isfile(db_path):
        return {"available": False, "reason": "nook not initialized (run: sage mine <dir>)"}

    # Vector-index divergence guard (#1222): opening the Chroma client when the
    # HNSW/sqlite state has diverged can SEGFAULT the MCP server. tool_status
    # short-circuits to a sqlite-only path in this state; the estate read
    # degrades gracefully rather than opening (PR #34 review).
    _refresh_vector_disabled_flag()
    if _vector_disabled:
        return {
            "available": False,
            "reason": "vector index unavailable (HNSW/sqlite divergence) — run: sage repair",
        }

    # ── 1. Guarded collection open (in-process, create=False) ────────────────
    col = _get_collection(create=False)
    if col is None:
        return {"available": False, "reason": "nook not available or not initialized"}

    # ── 2. Fetch metadata (no bodies) ────────────────────────────────────────
    try:
        metadata_rows = _get_cached_metadata(col)
    except Exception as exc:
        logger.exception("nook_estate: metadata fetch failed")
        return {"available": False, "reason": redact_string(f"metadata fetch failed: {exc}")}

    # ── 3. Wing config (canonical resolver: env → ~/.sage → repo template) ─────
    try:
        from .extensions.wing_registry import load_config as _load_wing_config

        wing_config: dict = _load_wing_config()
    except Exception:
        # Fail-soft to an empty-but-valid config so wing-type resolution degrades
        # to "unknown" rather than crashing (robust under a wheel install).
        wing_config = {"version": 1, "wing_types": {}, "wings": {}}

    # ── 4. KG stats (read-only) ───────────────────────────────────────────────
    # ADR-0003 read-only contract: do NOT call tool_kg_stats → _get_kg →
    # KnowledgeGraph(), whose constructor mkdir/chmod/_init_db/WAL/migrations
    # MUTATE even an EXISTING KG (and CREATE an absent one). Use the shared
    # read-only sqlite helper — the SAME one the CLI uses — so both paths read
    # the KG identically without writing (PR #34 ultra-review: path-parity +
    # read-only fix). Absent/locked → (0, 0).
    try:
        from .knowledge_graph import read_counts_readonly

        kg_entities, kg_relations = read_counts_readonly(_resolve_kg_path())
    except Exception:
        kg_entities, kg_relations = 0, 0

    # ── 5. Tunnels (fail-soft) ─────────────────────────────────────────────────
    # Endpoint wing ids MUST mirror the nook builder's redacted ids
    # (f"wing:{redact_string(name)}") or the renderer can't link them; the name
    # and the fallback id are user-derived free strings and must be redacted too
    # (schema $def says REDACTED — free strings pass validation regardless). The
    # endpoint pair is canonical-sorted (schema: UNORDERED, canonical-sorted).
    # (redact_string imported at the top of this function.)
    # tool_list_tunnels returns a LIST of records (nook_graph.list_tunnels) with
    # NESTED source/target {wing,room}. Map via the shared helper so the MCP tool
    # and the CLI can't drift on redaction/canonical-endpoint mapping (PR #34).
    raw_tunnels: list[dict] = []
    try:
        from .estate.adapter.nook import build_tunnels

        t_result = tool_list_tunnels()
        raw_tunnels = build_tunnels(t_result if isinstance(t_result, list) else [])
    except Exception:
        pass

    # ── 6. Telemetry rows for governance (fail-soft) ──────────────────────────
    telemetry_rows: list[dict] = []
    try:
        from .telemetry import read_recent

        telemetry_rows = read_recent(limit=1000)
    except Exception:
        pass

    # ── 7. Build estate model ─────────────────────────────────────────────────
    try:
        from .estate.adapter.estate_model import build_estate_model

        model = build_estate_model(
            metadata_rows,
            wing_config,
            telemetry_rows=telemetry_rows,
            kg_entities=kg_entities,
            kg_relations=kg_relations,
            tunnels=raw_tunnels,
        )
        return model
    except Exception as exc:
        logger.exception("nook_estate: build_estate_model failed")
        return {"available": False, "reason": redact_string(f"model build failed: {exc}")}


# ==================== MCP PROTOCOL ====================

TOOLS = {
    "nook_status": {
        "description": "Nook overview — total drawers, wing and room counts",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_status,
    },
    "nook_list_wings": {
        "description": "List all wings with drawer counts",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_list_wings,
    },
    "nook_list_rooms": {
        "description": "List rooms within a wing (or all rooms if no wing given)",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing to list rooms for (optional)"},
            },
        },
        "handler": tool_list_rooms,
    },
    "nook_get_taxonomy": {
        "description": "Full taxonomy: wing → room → drawer count",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_get_taxonomy,
    },
    "nook_kg_query": {
        "description": "Query the knowledge graph for an entity's relationships. Returns typed facts with temporal validity. E.g. 'Max' → child_of Alice, loves chess, does swimming. Filter by date with as_of to see what was true at a point in time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity to query (e.g. 'Max', 'MyProject', 'Alice')",
                },
                "as_of": {
                    "type": "string",
                    "description": "Date/datetime filter — only facts valid at this time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, optional)",
                },
                "direction": {
                    "type": "string",
                    "description": "outgoing (entity→?), incoming (?→entity), or both (default: both)",
                },
            },
            "required": ["entity"],
        },
        "handler": tool_kg_query,
    },
    "nook_kg_add": {
        "description": "Add a fact to the knowledge graph. Subject → predicate → object with optional time window. E.g. ('Max', 'started_school', 'Year 7', valid_from='2026-09-01'). Pass valid_to to backfill an already-ended historical fact in a single call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The entity doing/being something"},
                "predicate": {
                    "type": "string",
                    "description": "The relationship type (e.g. 'loves', 'works_on', 'daughter_of')",
                },
                "object": {"type": "string", "description": "The entity being connected to"},
                "valid_from": {
                    "type": "string",
                    "description": "When this became true (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, optional)",
                },
                "valid_to": {
                    "type": "string",
                    "description": "When this stopped being true (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, optional). Use for backfilling already-ended historical facts.",
                },
                "source_closet": {
                    "type": "string",
                    "description": "Closet ID where this fact appears (optional)",
                },
                "source_file": {
                    "type": "string",
                    "description": "Source file path the fact was extracted from (optional)",
                },
                "source_drawer_id": {
                    "type": "string",
                    "description": "Drawer ID the fact was extracted from (optional, provenance)",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_add,
    },
    "nook_kg_invalidate": {
        "description": "Mark a fact as no longer true. E.g. ankle injury resolved, job ended, moved house.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Entity"},
                "predicate": {"type": "string", "description": "Relationship"},
                "object": {"type": "string", "description": "Connected entity"},
                "ended": {
                    "type": "string",
                    "description": "When it stopped being true (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, default: today)",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_invalidate,
    },
    "nook_kg_timeline": {
        "description": "Chronological timeline of facts. Shows the story of an entity (or everything) in order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity to get timeline for (optional — omit for full timeline)",
                },
            },
        },
        "handler": tool_kg_timeline,
    },
    "nook_kg_stats": {
        "description": "Knowledge graph overview: entities, triples, current vs expired facts, relationship types.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_kg_stats,
    },
    "nook_traverse": {
        "description": "Walk the nook graph from a room. Shows connected ideas across wings — the tunnels. Like following a thread through the nook: start at 'chromadb-setup' in wing_code, discover it connects to wing_myproject (planning) and wing_user (feelings about it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_room": {
                    "type": "string",
                    "description": "Room to start from (e.g. 'chromadb-setup', 'riley-school')",
                },
                "max_hops": {
                    "type": "integer",
                    "description": "How many connections to follow (default: 2)",
                },
            },
            "required": ["start_room"],
        },
        "handler": tool_traverse_graph,
    },
    "nook_find_tunnels": {
        "description": "Find rooms that bridge two wings — the hallways connecting different domains. E.g. what topics connect wing_code to wing_team?",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string", "description": "First wing (optional)"},
                "wing_b": {"type": "string", "description": "Second wing (optional)"},
            },
        },
        "handler": tool_find_tunnels,
    },
    "nook_graph_stats": {
        "description": "Nook graph overview: total rooms, tunnel connections, edges between wings.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_graph_stats,
    },
    "nook_create_tunnel": {
        "description": "Create a cross-wing tunnel linking two nook locations. Use when content in one project relates to another — e.g., an API design in project_api connects to a database schema in project_database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_wing": {"type": "string", "description": "Wing of the source"},
                "source_room": {"type": "string", "description": "Room in the source wing"},
                "target_wing": {"type": "string", "description": "Wing of the target"},
                "target_room": {"type": "string", "description": "Room in the target wing"},
                "label": {"type": "string", "description": "Description of the connection"},
                "source_drawer_id": {
                    "type": "string",
                    "description": "Optional specific drawer ID",
                },
                "target_drawer_id": {
                    "type": "string",
                    "description": "Optional specific drawer ID",
                },
            },
            "required": ["source_wing", "source_room", "target_wing", "target_room"],
        },
        "handler": tool_create_tunnel,
    },
    "nook_list_tunnels": {
        "description": "List all explicit cross-wing tunnels. Optionally filter by wing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {
                    "type": "string",
                    "description": "Filter tunnels by wing (shows tunnels where wing is source or target)",
                },
            },
        },
        "handler": tool_list_tunnels,
    },
    "nook_delete_tunnel": {
        "description": "Delete an explicit tunnel by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tunnel_id": {"type": "string", "description": "Tunnel ID to delete"},
            },
            "required": ["tunnel_id"],
        },
        "handler": tool_delete_tunnel,
    },
    "nook_follow_tunnels": {
        "description": "Follow tunnels from a room to see what it connects to in other wings. Returns connected rooms with drawer previews.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing to start from"},
                "room": {"type": "string", "description": "Room to follow tunnels from"},
            },
            "required": ["wing", "room"],
        },
        "handler": tool_follow_tunnels,
    },
    "nook_search": {
        "description": "Semantic search. Returns verbatim drawer content with similarity scores. IMPORTANT: 'query' must contain ONLY search keywords. Use 'context' for background. Results with cosine distance > max_distance are filtered out. Pass 'agents' to scope results to drawers touched by specific agent names (e.g. recall your own past work).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short search query ONLY — keywords or a question. Max 250 chars.",
                    "maxLength": 250,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                    "minimum": 1,
                    "maximum": 100,
                },
                "wing": {"type": "string", "description": "Filter by wing (optional)"},
                "room": {"type": "string", "description": "Filter by room (optional)"},
                "max_distance": {
                    "type": "number",
                    "description": "Max cosine distance threshold (0=identical, 2=opposite). Results further than this are dropped. Lower = stricter. Default 1.5. Set to 0 to disable.",
                },
                "context": {
                    "type": "string",
                    "description": "Background context for the search (optional). NOT used for embedding — only for future re-ranking.",
                },
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter results to drawers tagged with any of these agent names (optional). Empty / omitted = no agent filter.",
                },
            },
            "required": ["query"],
        },
        "handler": tool_search,
    },
    "nook_check_duplicate": {
        "description": "Check if content already exists in the nook before filing",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Content to check (max 100,000 chars per sanitize_content)",
                    "maxLength": 100_000,
                },
                "threshold": {
                    "type": "number",
                    "description": "Similarity threshold 0-1 (default 0.9)",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
            "required": ["content"],
        },
        "handler": tool_check_duplicate,
    },
    "nook_add_drawer": {
        "description": "File verbatim content into the nook. Checks for duplicates first. Optionally tag the drawer with one or more agent names via 'agents' so nook_search can later recall this content scoped to those agents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing (project name)"},
                "room": {
                    "type": "string",
                    "description": "Room (aspect: backend, decisions, meetings...)",
                },
                "content": {
                    "type": "string",
                    "description": "Verbatim content to store — exact words, never summarized",
                },
                "source_file": {"type": "string", "description": "Where this came from (optional)"},
                "added_by": {"type": "string", "description": "Who is filing this (default: mcp)"},
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of agents that touched this content (optional). Used by nook_search agents-filter so each agent can recall its own past work.",
                },
                "hall": {
                    "type": "string",
                    "description": "Hall classification per the wing_types taxonomy (handoff / in-flight / audits / decisions / plans / facts). Optional; wake-up retrieval scoped to specific halls filters on this.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Writer confidence in this drawer's content (0.0–1.0, default 1.0). WI-5: stored in metadata so WI-6 decay can weight low-confidence drawers. Personal/core identity facts should use 1.0; merge-candidate or uncertain drawers may use lower values.",
                },
            },
            "required": ["wing", "room", "content"],
        },
        "handler": tool_add_drawer,
    },
    "nook_delete_drawer": {
        "description": "Delete a drawer by ID. Irreversible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer to delete"},
            },
            "required": ["drawer_id"],
        },
        "handler": tool_delete_drawer,
    },
    "nook_sync": {
        "description": "Prune drawers whose source files are gitignored, deleted, or moved. Returns dry-run report by default; pass apply=true to commit deletions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_dir": {
                    "type": "string",
                    "description": "Project root to scope the sync (optional; auto-detected from drawer metadata if omitted)",
                },
                "wing": {"type": "string", "description": "Limit to one wing (optional)"},
                "apply": {
                    "type": "boolean",
                    "description": "Actually delete drawers; default is dry-run preview",
                },
            },
        },
        "handler": tool_sync,
    },
    "nook_get_drawer": {
        "description": "Fetch a single drawer by ID — returns full content and metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer to fetch"},
            },
            "required": ["drawer_id"],
        },
        "handler": tool_get_drawer,
    },
    "nook_list_drawers": {
        "description": "List drawers with pagination. Optional wing/room filter. Returns IDs, wings, rooms, content previews, and total matching count for pagination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Filter by wing (optional)"},
                "room": {"type": "string", "description": "Filter by room (optional)"},
                "limit": {
                    "type": "integer",
                    "description": "Max results per page (default 20, max 100)",
                    "minimum": 1,
                    "maximum": 100,
                },
                "offset": {
                    "type": "integer",
                    "description": "Offset for pagination (default 0)",
                    "minimum": 0,
                },
            },
        },
        "handler": tool_list_drawers,
    },
    "nook_update_drawer": {
        "description": "Update an existing drawer's content and/or metadata (wing, room). Fetches existing drawer first; returns error if not found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer to update"},
                "content": {
                    "type": "string",
                    "description": "New content (optional — omit to keep existing)",
                },
                "wing": {
                    "type": "string",
                    "description": "New wing (optional — omit to keep existing)",
                },
                "room": {
                    "type": "string",
                    "description": "New room (optional — omit to keep existing)",
                },
            },
            "required": ["drawer_id"],
        },
        "handler": tool_update_drawer,
    },
    "nook_diary_write": {
        "description": "Write to your personal agent diary as plain text. Your observations, thoughts, what you worked on, what matters. Each agent has their own diary with full history. Entries are auto-tagged with agents=[agent_name] so nook_search agents=[X] surfaces X's own diary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Your name — each agent gets their own diary wing",
                },
                "entry": {
                    "type": "string",
                    "description": "Your diary entry as plain text",
                },
                "topic": {
                    "type": "string",
                    "description": "Topic tag (optional, default: general)",
                },
                "wing": {
                    "type": "string",
                    "description": "Target wing for this diary entry (optional). If omitted, uses wing_{agent_name}. Use this to write diary entries to a project wing instead of an agent-specific wing.",
                },
            },
            "required": ["agent_name", "entry"],
        },
        "handler": tool_diary_write,
    },
    "nook_diary_read": {
        "description": "Read your recent diary entries. See what past versions of yourself recorded — your journal across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Your name — each agent gets their own diary wing",
                },
                "last_n": {
                    "type": "integer",
                    "description": "Number of recent entries to read (default: 10)",
                },
                "wing": {
                    "type": "string",
                    "description": "Wing to read diary entries from (optional). If omitted, reads from wing_{agent_name}.",
                },
            },
            "required": ["agent_name"],
        },
        "handler": tool_diary_read,
    },
    "nook_hook_settings": {
        "description": (
            "Get or set hook behavior. silent_save: True = save directly "
            "(no MCP clutter), False = old-style blocking. desktop_toast: "
            "True = show desktop notification. Call with no args to view."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "silent_save": {
                    "type": "boolean",
                    "description": "True = silent direct save, False = blocking MCP calls",
                },
                "desktop_toast": {
                    "type": "boolean",
                    "description": "True = show desktop toast via notify-send",
                },
            },
        },
        "handler": tool_hook_settings,
    },
    "nook_memories_filed_away": {
        "description": "Check if a recent nook checkpoint was saved. Returns message count and timestamp.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_memories_filed_away,
    },
    "nook_reconnect": {
        "description": (
            "Force reconnect to the nook database. Use after external scripts or CLI commands"
            " modified the nook directly, which can leave the in-memory HNSW index stale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
        "handler": tool_reconnect,
    },
    "nook_registry_search": {
        "description": (
            "Search the skill/agent/script metadata registry. Returns lightweight descriptors "
            "(name, kind, one_line, triggers, path) — never full artifact bodies. "
            "Use to discover whether a skill, agent, or script exists before loading it from disk. "
            "The 'path' field in each result is the relative on-disk path of the artifact. "
            "Filter by kind='agent', 'skill', or 'script'. Empty query returns all entries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keyword search across name, one_line, and triggers. "
                        "Empty string returns all entries (up to limit)."
                    ),
                },
                "kind": {
                    "type": "string",
                    "description": "Filter by artifact kind: 'agent', 'skill', or 'script' (optional).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10, max 50).",
                    "minimum": 1,
                    "maximum": 50,
                },
                "repo_root": {
                    "type": "string",
                    "description": (
                        "Path to the sage repo root containing agents/ and skills/. "
                        "Auto-detected from the installed package location when omitted."
                    ),
                },
                "force_rebuild": {
                    "type": "boolean",
                    "description": "Re-scan from disk, bypassing the in-process cache (default false).",
                },
            },
        },
        "handler": tool_registry_search,
    },
    "nook_estate": {
        "description": (
            "Read the live Nook estate and emit a schema-valid Estate Model snapshot. "
            "Returns the full sage environment as a structured JSON model: Nook (palace) with "
            "wings/rooms/hall_counts, Workshop with agents/armory, property health, grounds, and "
            "outbuildings. NO drawer bodies — counts, sizes, and structural metadata only. "
            "Use `level` (0-3) to bound enumeration depth (0 = structure+counts, default)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "description": (
                        "Enumeration depth: 0 = structure+counts (default), 1-3 = reserved for "
                        "future drawer-level detail. Bounds the response size."
                    ),
                    "minimum": 0,
                    "maximum": 3,
                },
            },
        },
        "handler": tool_estate,
    },
}


SUPPORTED_PROTOCOL_VERSIONS = [
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
]


def _internal_tool_error(req_id, tool_name: str, exc: BaseException = None) -> dict:
    logger.exception(f"Tool error in {tool_name}")
    error: dict = {"code": -32000, "message": "Internal tool error"}
    if exc is not None:
        error["data"] = {
            "error_class": type(exc).__name__,
            "message": str(exc),
        }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": error,
    }


def handle_request(request):
    global _last_request_time
    if not isinstance(request, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    _last_request_time = time.monotonic()
    method = request.get("method") or ""
    params = request.get("params") or {}
    req_id = request.get("id")

    if method == "initialize":
        client_version = params.get("protocolVersion", SUPPORTED_PROTOCOL_VERSIONS[-1])
        negotiated = (
            client_version
            if client_version in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sage", "version": __version__},
            },
        }
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method.startswith("notifications/"):
        # Notifications (no id) never get a response per JSON-RPC spec
        return None
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {"name": n, "description": t["description"], "inputSchema": t["input_schema"]}
                    for n, t in TOOLS.items()
                ]
            },
        }
    elif method == "tools/call":
        if not isinstance(params, dict) or "name" not in params:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": "Invalid params: 'name' is required for tools/call",
                },
            }
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        # Whitelist arguments to declared schema properties only.
        # Prevents callers from spoofing internal params like added_by/source_file.
        # Skip filtering if handler explicitly accepts **kwargs (pass-through).
        # Default to filtering on inspect failure (safe fallback).
        import inspect

        schema_props = TOOLS[tool_name]["input_schema"].get("properties", {})
        try:
            handler = TOOLS[tool_name]["handler"]
            sig = inspect.signature(handler)
            accepts_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        except (ValueError, TypeError):
            accepts_var_keyword = False
        if not accepts_var_keyword:
            # An unknown kwarg here is almost always a wrong parameter *name*
            # (e.g. text= instead of content=). Silently dropping it makes the
            # cause surface only indirectly as a later "Missing required 'X'",
            # so name it explicitly — symmetric with the missing-required path
            # below. wait_for_previous is an internal transport kwarg in no
            # tool schema; it is popped before dispatch further down, so it
            # must not be reported as unknown here.
            unknown = [k for k in tool_args if k not in schema_props and k != "wait_for_previous"]
            if unknown:
                quoted = ", ".join(f"'{k}'" for k in unknown)
                word = "parameter" if len(unknown) == 1 else "parameters"
                logger.debug("Tool %s: unknown %s %s", tool_name, word, quoted)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": f"Unknown {word} {quoted} for tool {tool_name}",
                    },
                }
            tool_args = {k: v for k, v in tool_args.items() if k in schema_props}
        # Coerce argument types based on input_schema.
        # MCP JSON transport may deliver integers as floats or strings;
        # ChromaDB and Python slicing require native int.
        for key, value in list(tool_args.items()):
            prop_schema = schema_props.get(key, {})
            declared_type = prop_schema.get("type")
            try:
                if declared_type == "integer" and not isinstance(value, int):
                    tool_args[key] = int(value)
                elif declared_type == "number" and not isinstance(value, (int, float)):
                    tool_args[key] = float(value)
            except (ValueError, TypeError):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Invalid value for parameter '{key}'"},
                }
        tool_args.pop("wait_for_previous", None)
        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}
                    ]
                },
            }
        except TypeError as e:
            # Qualname match prevents leaking internal helper/param names raised
            # inside the handler body — see test_handler_internal_signature_shape_stays_generic.
            msg = str(e)
            handler = TOOLS[tool_name]["handler"]
            handler_qn = getattr(handler, "__qualname__", None) or getattr(handler, "__name__", "")
            # Qualname can include "<locals>" for nested defs and "<lambda>"
            # for lambdas — accept Python's TypeError emit verbatim.
            m_missing = re.match(
                r"^([\w\.<>]+)\(\) missing \d+ required "
                r"(?:positional |keyword-only )?arguments?: (.+)$",
                msg,
            )
            if m_missing and m_missing.group(1) == handler_qn:
                names = re.findall(r"'(\w+)'", m_missing.group(2))
                if names:
                    quoted = ", ".join(f"'{n}'" for n in names)
                    word = "parameter" if len(names) == 1 else "parameters"
                    logger.debug("Tool %s: missing required %s %s", tool_name, word, quoted)
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": f"Missing required {word} {quoted} for tool {tool_name}",
                        },
                    }
            return _internal_tool_error(req_id, tool_name, e)
        except Exception as exc:
            return _internal_tool_error(req_id, tool_name, exc)

    # Notifications (missing id) must never get a response
    if req_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def _restore_stdout():
    """Restore real stdout for MCP JSON-RPC output (see issue #225)."""
    global _REAL_STDOUT, _REAL_STDOUT_FD
    if _REAL_STDOUT_FD is not None:
        try:
            os.dup2(_REAL_STDOUT_FD, 1)
            os.close(_REAL_STDOUT_FD)
        except OSError:
            pass
        _REAL_STDOUT_FD = None
    sys.stdout = _REAL_STDOUT


_WARMUP_TRUTHY = {"1", "true", "yes", "on"}
_WARMUP_FALSY = {"", "0", "false", "no", "off"}
# Sentinel text for the warmup query. Distinctive so it cannot semantically
# match real drawer content (e.g. a nook containing notes about "warmup"
# routines) and is greppable in chromadb debug logs if the team ever adds
# request instrumentation. Single non-empty string is enough to trigger
# ChromaDB's ONNXMiniLM_L6_V2.__call__ → _download_model_if_not_exists +
# InferenceSession.
_WARMUP_PROBE_TEXT = "__nook_warmup_probe__"


def _describe_device_safe() -> str:
    """Return ``embedding.describe_device()`` value or ``"unknown"`` on failure.

    Used only inside warmup-failure log lines; the import is deferred so
    that an embedding-stack import error cannot itself crash the warmup
    diagnostic path.
    """
    try:
        from .embedding import describe_device

        return describe_device()
    except Exception:  # fail-soft: see docstring — log-message helper must not crash
        return "unknown"


def _maybe_eager_warmup_embedder() -> None:
    """Pre-load embedder + HNSW segment at startup when ``SAGE_EAGER_WARMUP`` is truthy.

    The first MCP tool call that touches chromadb (``diary_write``,
    ``add_drawer``, ``search``) otherwise pays two compounding cold-load
    costs that together can exceed the MCP client timeout and surface as
    ``-32000`` "Internal tool error" with no recoverable trace on the
    agent side (#1495):

    1. ONNX/CoreML embedder init in :func:`sage.embedding.get_embedding_function`
       (5–30s on first inference; ChromaDB's ``ONNXMiniLM_L6_V2.__call__``
       triggers ``_download_model_if_not_exists`` + ``InferenceSession``).
    2. HNSW segment cold-load (reading ``data_level0.bin`` into RAM on
       first collection operation; seconds on nooks of 50k+ drawers).

    Warming via :func:`_get_collection`'s collection-then-query path
    covers BOTH in a single startup-phase call — mirroring the reporter's
    proposal in #1495 — so users with large existing nooks see the
    same benefit as users on the embedder-only cost path.

    Truthy parsing accepts ``1/true/yes/on`` (case-insensitive); falsy
    set ``0/false/no/off`` and empty/whitespace are silently off; any
    other value logs a warning and stays off so typos like ``tru`` do
    not silently disable the feature.

    Fresh-install guard (pre-check, NOT a catch): ``_get_collection``'s
    retry layer absorbs ``_ChromaNotFoundError`` and returns ``None`` while
    also materialising ``chroma.sqlite3`` on disk via the chromadb client
    constructor. To preserve the documented "no nook yet → nothing to
    warm" contract WITHOUT writing nook scaffolding before
    ``sage init`` (which would violate CLAUDE.md "Incremental only"),
    we test for ``chroma.sqlite3`` ourselves before touching the chromadb
    client. Operators who set ``SAGE_EAGER_WARMUP=1`` in their MCP
    config and launch the server before running ``sage init`` get a
    single INFO line and no on-disk side effect.

    Fail-soft beyond the fresh-install pre-check:

    * **Backend open failure** (nook path misconfigured, file locked,
      corrupted HNSW that ``quarantine_stale_hnsw`` cannot recover) →
      log exception with device + nook context and return. The next
      embedding-requiring call sees the same fail mode it would have
      without warmup.
    * **`_get_collection` retried and returned None** → nook exists
      but chromadb cannot open the collection (rare; usually a stale
      sqlite + segment-files mismatch surfaced by `_get_client` rebuild).
      A warning suffices because the retry layer already wrote two
      tracebacks with the underlying chromadb error class.
    * **Query failure** (network failure during ONNX model download,
      provider init crash, runtime decoder error) → log exception with
      device + nook context and return. Same fail-mode preservation.

    Note: on an existing nook with an empty collection (created via
    ``sage init`` but never written to), ``col.query`` succeeds but
    returns ``{'ids': [[]]}`` without reading any HNSW segment — the
    embedder warms but there is no HNSW segment to load. The success log
    still says ``embedder + HNSW ready`` because the no-HNSW-segment case
    has zero cold-load cost; nothing was skipped that the first real tool
    call would have paid.
    """
    raw = os.environ.get("SAGE_EAGER_WARMUP", "").strip().lower()
    if raw in _WARMUP_FALSY:
        return
    if raw not in _WARMUP_TRUTHY:
        logger.warning(
            "SAGE_EAGER_WARMUP=%r is not recognized (use one of %s); warmup disabled",
            raw,
            sorted(_WARMUP_TRUTHY | (_WARMUP_FALSY - {""})),
        )
        return
    nook_path = _config.nook_path
    db_path = os.path.join(nook_path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        # Pre-check (NOT a try/except on _ChromaNotFoundError, which never
        # propagates out of _get_collection — see docstring). No nook
        # file means nothing to warm AND avoids the chromadb-client
        # side effect of materialising the nook dir.
        logger.info(
            "SAGE_EAGER_WARMUP=%s: no nook at %s — nothing to warm",
            raw,
            nook_path,
        )
        return
    # Cache device once: _describe_device_safe re-imports embedding stack
    # each call, which is wasteful inside a function that already paid
    # that cost via the warmup query below.
    device = _describe_device_safe()
    try:
        col = _get_collection(create=False)
    except Exception as exc:  # fail-soft per docstring — broad on purpose
        logger.exception(
            "SAGE_EAGER_WARMUP=%s: collection open failed (nook=%s, device=%s, error=%s)",
            raw,
            nook_path,
            device,
            type(exc).__name__,
        )
        return
    if col is None:
        logger.warning(
            "SAGE_EAGER_WARMUP=%s: _get_collection returned None for nook=%s — see prior log lines",
            raw,
            nook_path,
        )
        return
    try:
        col.query(query_texts=[_WARMUP_PROBE_TEXT], n_results=1)
    except Exception as exc:  # fail-soft per docstring — broad on purpose
        logger.exception(
            "SAGE_EAGER_WARMUP=%s: warmup query failed (nook=%s, device=%s, error=%s)",
            raw,
            nook_path,
            device,
            type(exc).__name__,
        )
    else:
        logger.info(
            "SAGE_EAGER_WARMUP=%s: embedder + HNSW ready (nook=%s, device=%s)",
            raw,
            nook_path,
            device,
        )


def _start_idle_exit_watchdog() -> None:
    """Start a daemon thread that exits the process after an idle period.

    When no request has been handled for ``SAGE_MCP_IDLE_HOURS``
    (default 8 h), the thread terminates the process so that stale MCP
    servers from ended Claude Code sessions do not accumulate ChromaDB /
    HNSW file handles on Windows (#1552).

    Set ``SAGE_MCP_IDLE_HOURS=0`` to disable the watchdog.
    """
    timeout = _mcp_idle_timeout_secs()
    if timeout <= 0:
        return
    check_interval = min(60.0, timeout / 4)

    def _watchdog() -> None:
        while True:
            time.sleep(check_interval)
            idle = time.monotonic() - _last_request_time
            if idle >= timeout:
                logger.info(
                    "MCP server idle for %.1f h (limit %.1f h); exiting to release file handles.",
                    idle / 3600,
                    timeout / 3600,
                )
                os._exit(0)

    t = threading.Thread(target=_watchdog, name="mcp-idle-watchdog", daemon=True)
    t.start()


def main():
    """MCP server entry point for the ``sage-mcp`` console script.

    Side effect: pops ``PYTHONPATH`` from ``os.environ`` (see #1423) so
    any subprocess this server spawns inherits a clean env. Host
    applications that call ``main()`` programmatically should be aware
    that the parent process loses ``PYTHONPATH`` as well. Library imports
    (``import sage_mcp.searcher`` from a host app) do NOT trigger this
    side effect; only the CLI/MCP entry points pop the env var.
    """
    # Drop leaked PYTHONPATH so any subprocess this server spawns starts
    # with a clean env. The sys.path filter in sage/__init__.py
    # already protects this process from the same ABI mismatch; here we
    # extend the protection to children.
    os.environ.pop("PYTHONPATH", None)
    _restore_stdout()
    # Force UTF-8 on stdio. MCP JSON-RPC is UTF-8, but Python on Windows
    # defaults stdin/stdout to the system codepage (e.g. cp1251), which
    # corrupts non-ASCII payloads and surfaces as generic -32000 errors on
    # Cyrillic/CJK content. See PEP 540.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass
    logger.info("sage MCP Server starting...")
    # Pre-flight: probe HNSW capacity before any tool call so the warning
    # is visible at startup rather than on first use (#1222). Pure
    # filesystem read; never opens a chromadb client.
    _refresh_vector_disabled_flag()
    # Opt-in: pre-load the embedder so the first chromadb-write tool call
    # does not pay the ONNX/CoreML cold-load tax under the MCP client
    # timeout (#1495). Default off — preserves current startup latency.
    _maybe_eager_warmup_embedder()
    # Idle auto-exit: release ChromaDB file handles from stale servers
    # that outlived their Claude Code session (#1552).
    _start_idle_exit_watchdog()
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Server error: {e}")


if __name__ == "__main__":
    main()
