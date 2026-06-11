"""sage_mcp.estate.command — the ``sage estate --json`` CLI subcommand (Phase 3).

**Standalone-subprocess read path (ADR-0003).** A CLI invocation is a SECOND OS
process and a SECOND ChromaDB client. It MUST NOT open the live ``~/.sage`` store
concurrently with the running sage process (hooks / MCP server / mining may be
writing, and ChromaDB's ``PersistentClient`` can mutate-on-open while the HNSW
index is a memory-mapped file outside SQLite's WAL lock). So this command:

1. Pre-checks ``<nook>/chroma.sqlite3`` and degrades gracefully if absent (never
   constructs a client on an absent DB).
2. **Copy-snapshots the store dir to a temp path and opens the COPY**, never the
   live path — single-writer safety without a second concurrent live client.
3. Opens the copy through the guarded ``_open_collection_or_explain`` (``create=False``).
4. Fetches drawer metadata only (no bodies), builds the schema-valid model, and
   prints it. ``build_estate_model`` schema-validates before returning, so a
   leaked field or enum drift raises — mapped here to a non-zero exit.

The PREFERRED single-client path is the in-process ``nook_estate`` MCP tool
(``sage_mcp.mcp_server.tool_estate``); this CLI is the standalone fallback the plan
lists for scripting and debugging.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any


def _resolve_wing_config() -> dict[str, Any]:
    """Resolve wing_config via the canonical resolver (env → ~/.sage → repo template).

    Falls back to an empty-but-valid config so wing-type resolution degrades to
    ``unknown`` rather than crashing when no config is installed.
    """
    try:
        from sage_mcp.extensions.wing_registry import load_config

        return load_config()
    except Exception:
        return {"version": 1, "wing_types": {}, "wings": {}}


def _snapshot_vector_disabled(nook_path: str, collection_name: str) -> bool:
    """Pure sqlite+pickle HNSW-capacity probe (never opens Chroma).

    Returns True when the #1222 HNSW/sqlite divergence is present, so the caller
    degrades instead of risking a segfault opening the divergent copy. Mirrors
    the in-process tool_estate guard for the standalone CLI (PR #34 review).
    Fails OPEN — a probe failure must not block an otherwise-healthy read.
    """
    try:
        from sage_mcp.nook import default_backend

        status = default_backend().health(nook_path, collection_name)
        return bool(getattr(status, "vector_disabled", False))
    except Exception:
        return False


def _read_kg_stats(kg_path: str) -> tuple[int, int]:
    """Return (entities, triples) from a KG sqlite WITHOUT creating or mutating it.

    Delegates to the shared ``knowledge_graph.read_counts_readonly`` helper (the
    SAME one the in-process MCP tool uses) so both read paths honour the ADR-0003
    read-only contract identically — opening ``mode=ro`` and running only
    ``SELECT count(*)``, never constructing a mutating ``KnowledgeGraph``
    (PR #34 ultra-review). The CLI passes the KG's actual location
    (``~/.sage/knowledge_graph.sqlite3`` for the default nook, or
    ``<nook>/knowledge_graph.sqlite3`` under ``--nook``). Fail-soft to (0, 0).
    """
    from sage_mcp.knowledge_graph import read_counts_readonly

    return read_counts_readonly(kg_path)


def _read_live_tunnels(nook_path: str) -> list[dict[str, Any]]:
    """Read the live tunnels.json and map to schema-shaped tunnel edges.

    tunnels.json is a small, atomically-written (tmp+replace) JSON that is a
    SIBLING of the nook dir — NOT part of the chroma store and NOT captured by
    the nook-dir copytree snapshot — so the CLI reads the LIVE file directly
    (a plain read of an atomically-written file is consistent and never mutates).
    Without this, the standalone CLI always emitted ``tunnels: []`` even for
    nooks with real cross-wing passages (PR #34 review). Fail-soft to [].
    """
    from sage_mcp.estate.adapter.nook import build_tunnels

    try:
        tunnels_path = os.path.join(os.path.dirname(os.path.normpath(nook_path)), "tunnels.json")
        if not os.path.isfile(tunnels_path):
            return []
        with open(tunnels_path, encoding="utf-8") as f:
            records = json.load(f)
        return build_tunnels(records if isinstance(records, list) else [])
    except Exception:
        return []


def _fetch_metadata_no_bodies(col) -> list[dict[str, Any]]:
    """Paginate ``col.get`` for metadata only — never documents/bodies (ADR-0003).

    Mirrors ``mcp_server._fetch_all_metadata`` but inlined to keep the CLI
    decoupled from the MCP server module (and its heavier import graph).
    """
    total = col.count()
    rows: list[dict[str, Any]] = []
    offset = 0
    while offset < total:
        batch = col.get(include=["metadatas"], limit=1000, offset=offset)
        metas = batch.get("metadatas") or []
        if not metas:
            break
        rows.extend(m or {} for m in metas)
        offset += len(metas)
    return rows


def build_estate_model_via_snapshot(
    nook_path: str,
    collection_name: str,
    *,
    snapshot_root: str | None = None,
    max_open_retries: int = 3,
    retry_backoff_s: float = 0.1,
    kg_path: str | None = None,
) -> dict[str, Any]:
    """Copy-snapshot the live store, open the COPY read-only, build the model.

    Returns a ``{"available": False, "reason": ...}`` degradation envelope when
    the nook is absent, busy, or unreadable. Raises (jsonschema.ValidationError
    or any build error) on schema drift — the caller maps that to a non-zero exit.

    **Snapshot consistency (ADR-0003 §2; Codex adversarial review, fold round 2).**
    The estate model reads METADATA ONLY (``col.count()`` + ``col.get(include=
    ["metadatas"])``) — sourced from SQLite, not the HNSW index. ``copytree``
    copies the ``chroma.sqlite3`` + ``-wal`` + ``-shm`` triple together, so the
    opened copy WAL-recovers to a consistent metadata state even under concurrent
    writes. A copy taken mid-write can still fail to OPEN (a half-written HNSW
    segment); that is handled by a bounded retry that takes a FRESH copy and, on
    persistent failure, returns a path-free ``store busy — retry`` envelope. We
    deliberately do NOT hold the exclusive ``mine_nook_lock`` during the copy —
    that would abort a concurrent ``sage mine``, and a read-only view must never
    disrupt writers.

    *snapshot_root* overrides the temp parent dir (test injection); every
    snapshot is removed before return.
    """
    from sage_mcp.estate.redact import redact_string
    from sage_mcp.nook import _open_collection_or_explain

    # 1. Pre-check: never construct a client on an absent DB.
    #    The reason is emitted in the (exported) model JSON, so it must NOT carry
    #    the absolute nook path (a home path) — keep it path-free (ADR-0003).
    if not os.path.isfile(os.path.join(nook_path, "chroma.sqlite3")):
        return {
            "available": False,
            "reason": "nook has no chroma.sqlite3 yet (run: sage mine <dir>)",
        }

    last_reason = "nook not available"
    for attempt in range(max(1, max_open_retries)):
        # 2. Copy-snapshot to a FRESH temp dir each attempt; open the COPY.
        tmp_parent = tempfile.mkdtemp(prefix="sage_estate_", dir=snapshot_root)
        snapshot_nook = os.path.join(tmp_parent, "nook")
        try:
            col = None
            msgs: list[str] = []
            try:
                shutil.copytree(nook_path, snapshot_nook)
                if _snapshot_vector_disabled(snapshot_nook, collection_name):
                    # The copy preserves the divergent HNSW files; opening Chroma
                    # on them can segfault (#1222). Degrade like the MCP path —
                    # persistent state, so don't retry (PR #34 review).
                    return {
                        "available": False,
                        "reason": "vector index unavailable (HNSW/sqlite divergence) — run: sage repair",
                    }
                col = _open_collection_or_explain(
                    snapshot_nook, collection_name=collection_name, out=msgs.append
                )
            except (OSError, shutil.Error) as exc:
                # A live write can make copytree itself raise mid-traversal (a file
                # vanishing under it, a torn segment). Treat as transient "store
                # busy" and retry with a fresh snapshot — never escape as a build
                # failure (Codex fold round 3). Type name only — no path/value leak.
                last_reason = f"store busy (copy: {type(exc).__name__})"
            if col is None:
                # copytree raised OR the copy opened as None — mark for retry.
                # NOTE: do not `continue` here — that would jump past the backoff
                # below (the `finally` still runs, but the post-loop-body sleep
                # would be skipped). Set a flag and let control fall through so
                # cleanup (finally) then backoff both happen (Codex fold round 4).
                if msgs:
                    last_reason = " ".join(m.strip() for m in msgs if m.strip()) or last_reason
            else:
                try:
                    metadata_rows = _fetch_metadata_no_bodies(col)
                except Exception as exc:
                    # A torn copy can OPEN but then fail on the first count()/get()
                    # (SQLite/Chroma only detects it on the metadata query). Treat
                    # as transient store contention and retry — NOT schema drift
                    # (PR #34 review). Type name only — no path/value leak.
                    last_reason = f"store busy (read: {type(exc).__name__})"
                else:
                    telemetry_rows: list[dict[str, Any]] = []
                    try:
                        from sage_mcp.telemetry import read_recent

                        telemetry_rows = read_recent(limit=1000)
                    except Exception:
                        pass

                    from sage_mcp.estate.adapter.estate_model import build_estate_model

                    # KG counts from the KG's ACTUAL location (read-only). The
                    # default nook's KG is ~/.sage/knowledge_graph.sqlite3 — a
                    # SIBLING of the nook, NOT inside the snapshot — so read the
                    # resolved live path, not snapshot_nook (PR #34 review). The
                    # caller (cmd_estate) resolves it; fall back to the in-nook
                    # location for direct callers / --nook-colocated layouts.
                    resolved_kg = (
                        kg_path
                        if kg_path is not None
                        else os.path.join(nook_path, "knowledge_graph.sqlite3")
                    )
                    kg_entities, kg_relations = _read_kg_stats(resolved_kg)

                    # build_estate_model raises (schema drift) propagate → exit 1;
                    # only store-contention is retried above.
                    return build_estate_model(
                        metadata_rows,
                        _resolve_wing_config(),
                        telemetry_rows=telemetry_rows,
                        tunnels=_read_live_tunnels(nook_path),
                        kg_entities=kg_entities,
                        kg_relations=kg_relations,
                    )
        finally:
            # Release the cached Chroma client for this snapshot BEFORE rmtree.
            # ChromaBackend caches a PersistentClient whose Rust/SQLite file lock
            # is held until close; on Windows shutil.rmtree(ignore_errors=True)
            # would otherwise leave every successful snapshot dir behind
            # (PR #34 review). No-op when nothing was opened.
            try:
                from sage_mcp.nook import default_backend
                from sage_mcp.repair import _close_chroma_handles

                _close_chroma_handles(snapshot_nook, backend=default_backend())
            except Exception:
                pass
            shutil.rmtree(tmp_parent, ignore_errors=True)

        # Reached only on a failed attempt (success returns inside the try).
        # Cleanup already ran in `finally`; back off before the next retry.
        if attempt < max_open_retries - 1:
            time.sleep(retry_backoff_s)

    return {"available": False, "reason": redact_string(f"store busy — retry ({last_reason})")}


def cmd_estate(args) -> None:
    """``sage estate`` entry point. Prints the estate model JSON to stdout.

    Exit codes: 0 on success OR graceful degradation (a valid ``available:false``
    answer); 1 on schema-validation drift or any build failure (plan 3.4).
    """
    from sage_mcp.config import SageConfig

    cfg = SageConfig()
    nook_override = getattr(args, "nook", None)
    nook_path = os.path.expanduser(nook_override) if nook_override else cfg.nook_path
    collection_name = cfg.collection_name

    # Resolve the KG's actual location like sage does: inside the nook when a
    # custom --nook is given, else the default ~/.sage/knowledge_graph.sqlite3.
    if nook_override:
        kg_path = os.path.join(nook_path, "knowledge_graph.sqlite3")
    else:
        from sage_mcp.knowledge_graph import DEFAULT_KG_PATH

        kg_path = DEFAULT_KG_PATH

    try:
        model = build_estate_model_via_snapshot(nook_path, collection_name, kg_path=kg_path)
    except Exception as exc:  # schema drift or any build failure → non-zero exit
        # Redact before printing — a schema/validation failure's exc text can echo
        # the offending instance (a leaked body, token, or home path that
        # validation was meant to catch). The MCP path already redacts build
        # failures; the CLI stderr must too (PR #34 review).
        from sage_mcp.estate.redact import redact_string

        print(f"estate: model build failed: {redact_string(str(exc))}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(model, indent=2, ensure_ascii=False))
