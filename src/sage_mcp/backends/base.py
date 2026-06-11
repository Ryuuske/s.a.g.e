"""Storage backend contract for sage.

This module defines the surface every storage backend must implement:

* ``BaseCollection`` — the per-collection read/write interface, kwargs-only.
* ``BaseBackend`` — the per-nook factory, addressed by ``NookRef``.
* ``QueryResult`` / ``GetResult`` — typed result dataclasses that replace the
  Chroma dict shape as the canonical return type.
* Error classes + ``HealthStatus`` — uniform across backends.

This is the v1 cleanup: full typed results, ``NookRef``,
registry-ready ABC. Embedder injection, maintenance hooks, and the full
conformance suite land in follow-up PRs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Optional


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BackendError(Exception):
    """Base class for every storage-backend error raised by core."""


class NookNotFoundError(BackendError, FileNotFoundError):
    """Raised when ``get_collection(create=False)`` is called on a missing nook.

    Subclass of ``FileNotFoundError`` so callers that catch the latter
    (pre-#413 seam) keep working unchanged.
    """


class CollectionNotInitializedError(NookNotFoundError):
    """Raised when the nook exists on disk but the requested collection has
    never been created (e.g. ``init`` ran but ``mine`` has not).

    Distinct from :class:`NookNotFoundError`: the nook dir and DB are
    present and valid, only the collection has not been bootstrapped yet.
    Subclass of :class:`NookNotFoundError` (and therefore
    :class:`FileNotFoundError`) so callers catching either parent
    keep working unchanged.
    """


class BackendClosedError(BackendError):
    """Raised when a backend method is called after ``close()``."""


class UnsupportedFilterError(BackendError):
    """Raised when a where-clause uses an operator the backend does not implement.

    Silent dropping of unknown operators is forbidden by spec.
    """


class DimensionMismatchError(BackendError):
    """Raised when the embedding dimension on write does not match the collection."""


class EmbedderIdentityMismatchError(BackendError):
    """Raised when the stored embedder model name differs from the current one."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NookRef:
    """A handle to a nook, consumed by backends.

    ``id`` is always present and is the key backends use to cache handles.
    ``local_path`` is populated for filesystem-rooted nooks.
    ``namespace`` is used by server-mode backends for tenant / prefix routing.
    """

    id: str
    local_path: Optional[str] = None
    namespace: Optional[str] = None


@dataclass(frozen=True)
class HealthStatus:
    """Backend health for a nook.

    ``ok`` is liveness — can the backend serve this nook at all (False when the
    backend is closed/unusable). ``vector_disabled`` is orthogonal: the backend
    is alive but the vector index is diverged, so callers should route to the
    BM25 fallback (#1222). ``capacity`` carries the raw probe detail
    (``sqlite_count`` / ``hnsw_count`` / ``divergence``) for status reporting.
    """

    ok: bool
    detail: str = ""
    vector_disabled: bool = False
    capacity: Optional[dict] = None

    @classmethod
    def healthy(cls, detail: str = "", capacity: Optional[dict] = None) -> "HealthStatus":
        return cls(ok=True, detail=detail, vector_disabled=False, capacity=capacity)

    @classmethod
    def unhealthy(cls, detail: str) -> "HealthStatus":
        return cls(ok=False, detail=detail)

    @classmethod
    def degraded(cls, detail: str, capacity: Optional[dict] = None) -> "HealthStatus":
        """Alive, but the vector index is diverged — route to the BM25 fallback."""
        return cls(ok=True, detail=detail, vector_disabled=True, capacity=capacity)


_TYPED_RESULT_FIELDS = ("ids", "documents", "metadatas", "distances", "embeddings")


class _DictCompatMixin:
    """Transitional dict-protocol access for typed results.

    The spec is attribute access (``result.ids``). The ``result["ids"]``
    and ``result.get("ids")`` forms are retained as a migration shim for callers
    that predate the typed interface and are scheduled for removal in a follow-
    up cleanup. New code MUST use attribute access.
    """

    def __getitem__(self, key: str):
        if key in _TYPED_RESULT_FIELDS:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default=None):
        if key in _TYPED_RESULT_FIELDS:
            val = getattr(self, key, default)
            return default if val is None else val
        return default

    def __contains__(self, key: object) -> bool:
        return key in _TYPED_RESULT_FIELDS and getattr(self, key, None) is not None


@dataclass(frozen=True)
class QueryResult(_DictCompatMixin):
    """Typed return from ``BaseCollection.query``.

    Outer list dimension = number of query vectors / texts.
    Inner list dimension = hits per query (may be zero).

    Fields not in ``include=`` at the call site are populated with empty lists
    of the correct outer shape (never ``None``), except ``embeddings`` which
    is ``None`` when not requested.
    """

    ids: list[list[str]]
    documents: list[list[str]]
    metadatas: list[list[dict]]
    distances: list[list[float]]
    embeddings: Optional[list[list[list[float]]]] = None

    @classmethod
    def empty(cls, num_queries: int = 1, embeddings_requested: bool = False) -> "QueryResult":
        """Construct an all-empty result preserving outer dimension.

        When ``embeddings_requested`` is True, ``embeddings`` preserves the outer
        query dimension with empty hit lists (matching the spec's rule that fields
        requested via ``include=`` carry the outer shape even when empty). When
        False, ``embeddings`` stays ``None`` to signal the field was not requested.
        """
        empty_outer = [[] for _ in range(num_queries)]
        return cls(
            ids=[[] for _ in range(num_queries)],
            documents=[[] for _ in range(num_queries)],
            metadatas=[[] for _ in range(num_queries)],
            distances=[[] for _ in range(num_queries)],
            embeddings=empty_outer if embeddings_requested else None,
        )


@dataclass(frozen=True)
class GetResult(_DictCompatMixin):
    """Typed return from ``BaseCollection.get``."""

    ids: list[str]
    documents: list[str]
    metadatas: list[dict]
    embeddings: Optional[list[list[float]]] = None

    @classmethod
    def empty(cls) -> "GetResult":
        return cls(ids=[], documents=[], metadatas=[], embeddings=None)


# ---------------------------------------------------------------------------
# Collection contract
# ---------------------------------------------------------------------------


class BaseCollection(ABC):
    """Per-collection read/write surface every backend must implement."""

    @abstractmethod
    def add(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: Optional[list[dict]] = None,
        embeddings: Optional[list[list[float]]] = None,
    ) -> None: ...

    @abstractmethod
    def upsert(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: Optional[list[dict]] = None,
        embeddings: Optional[list[list[float]]] = None,
    ) -> None: ...

    @abstractmethod
    def query(
        self,
        *,
        query_texts: Optional[list[str]] = None,
        query_embeddings: Optional[list[list[float]]] = None,
        n_results: int = 10,
        where: Optional[dict] = None,
        where_document: Optional[dict] = None,
        include: Optional[list[str]] = None,
    ) -> QueryResult: ...

    @abstractmethod
    def get(
        self,
        *,
        ids: Optional[list[str]] = None,
        where: Optional[dict] = None,
        where_document: Optional[dict] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        include: Optional[list[str]] = None,
    ) -> GetResult: ...

    @abstractmethod
    def delete(
        self,
        *,
        ids: Optional[list[str]] = None,
        where: Optional[dict] = None,
    ) -> None: ...

    @abstractmethod
    def count(self) -> int: ...

    # ------------------------------------------------------------------
    # Optional methods with ABC defaults (spec)
    # ------------------------------------------------------------------

    def estimated_count(self) -> int:
        return self.count()

    def close(self) -> None:
        return None

    def health(self) -> HealthStatus:
        return HealthStatus.healthy()

    def update(
        self,
        *,
        ids: list[str],
        documents: Optional[list[str]] = None,
        metadatas: Optional[list[dict]] = None,
        embeddings: Optional[list[list[float]]] = None,
    ) -> None:
        """Default non-atomic update: get + merge + upsert.

        Backends advertising ``supports_update`` MUST override with an atomic
        single-round-trip implementation.
        """
        if documents is None and metadatas is None and embeddings is None:
            raise ValueError("update requires at least one of documents, metadatas, embeddings")

        n = len(ids)
        for label, value in (
            ("documents", documents),
            ("metadatas", metadatas),
            ("embeddings", embeddings),
        ):
            if value is not None and len(value) != n:
                raise ValueError(f"{label} length {len(value)} does not match ids length {n}")

        existing = self.get(ids=ids, include=["documents", "metadatas"])
        by_id = {
            rid: (existing.documents[i], existing.metadatas[i])
            for i, rid in enumerate(existing.ids)
        }
        merged_docs: list[str] = []
        merged_metas: list[dict] = []
        for i, rid in enumerate(ids):
            prev_doc, prev_meta = by_id.get(rid, ("", {}))
            merged_docs.append(documents[i] if documents is not None else prev_doc)
            new_meta = dict(prev_meta or {})
            if metadatas is not None:
                new_meta.update(metadatas[i] or {})
            merged_metas.append(new_meta)
        self.upsert(
            documents=merged_docs,
            ids=list(ids),
            metadatas=merged_metas,
            embeddings=embeddings,
        )


# ---------------------------------------------------------------------------
# Backend contract
# ---------------------------------------------------------------------------


class BaseBackend(ABC):
    """Long-lived factory serving many nooks.

    Instances are lightweight on construction — no I/O, no network. All
    connection work is deferred to ``get_collection``. Instances are thread-
    safe for concurrent ``get_collection`` calls across different nooks.
    """

    name: ClassVar[str]
    spec_version: ClassVar[str] = "1.0"
    capabilities: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def get_collection(
        self,
        *,
        nook: NookRef,
        collection_name: str,
        create: bool = False,
        options: Optional[dict] = None,
    ) -> BaseCollection: ...

    def close_nook(self, nook: NookRef) -> None:
        """Evict cached handles for a single nook. Default: no-op."""
        return None

    def close(self) -> None:
        """Shut down the entire backend. Default: no-op."""
        return None

    def health(
        self, nook_path: Optional[str] = None, collection_name: Optional[str] = None
    ) -> HealthStatus:
        """Probe the backend's health for a nook.

        MUST be side-effect-free and MUST NOT construct a heavy client (callers
        rely on this as a pre-open safety probe, #1222). Default: healthy.
        """
        return HealthStatus.healthy()

    def reset(self, nook_path: str) -> None:
        """Drop any cached handle for ``nook_path`` so the next open rebuilds
        against fresh disk state. Default: no-op."""
        return None

    # Optional detection hint used by selection priority:
    @classmethod
    def detect(cls, path: str) -> bool:  # pragma: no cover - default hook
        return False


# ---------------------------------------------------------------------------
# Adapter utilities
# ---------------------------------------------------------------------------


# Keys the Chroma ``include=`` parameter accepts.
_VALID_INCLUDE_KEYS = frozenset({"documents", "metadatas", "distances", "embeddings"})


@dataclass
class _IncludeSpec:
    """Resolve an ``include=`` parameter with spec-mandated defaults."""

    documents: bool = True
    metadatas: bool = True
    distances: bool = True  # only meaningful for query
    embeddings: bool = False

    @classmethod
    def resolve(
        cls, include: Optional[list[str]], *, default_distances: bool = True
    ) -> "_IncludeSpec":
        if include is None:
            return cls(
                documents=True,
                metadatas=True,
                distances=default_distances,
                embeddings=False,
            )
        keys = {k for k in include if k in _VALID_INCLUDE_KEYS}
        return cls(
            documents="documents" in keys,
            metadatas="metadatas" in keys,
            distances="distances" in keys,
            embeddings="embeddings" in keys,
        )
