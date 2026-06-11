"""Storage backend implementations for sage.

Public surface:

* :class:`BaseCollection` — per-collection read/write contract.
* :class:`BaseBackend` — per-nook factory contract.
* :class:`NookRef` — value object identifying a nook for a backend.
* :class:`QueryResult` / :class:`GetResult` — typed read returns.
* Error classes: :class:`NookNotFoundError`, :class:`BackendClosedError`,
  :class:`UnsupportedFilterError`, :class:`DimensionMismatchError`,
  :class:`EmbedderIdentityMismatchError`.
* Registry: :func:`get_backend`, :func:`register`, :func:`available_backends`,
  :func:`resolve_backend_for_nook`.
* In-tree Chroma default: :class:`ChromaBackend`, :class:`ChromaCollection`.
"""

from .base import (
    BackendClosedError,
    BackendError,
    BaseBackend,
    BaseCollection,
    CollectionNotInitializedError,
    DimensionMismatchError,
    EmbedderIdentityMismatchError,
    GetResult,
    HealthStatus,
    NookNotFoundError,
    NookRef,
    QueryResult,
    UnsupportedFilterError,
)
from .chroma import ChromaBackend, ChromaCollection
from .registry import (
    available_backends,
    get_backend,
    get_backend_class,
    register,
    reset_backends,
    resolve_backend_for_nook,
    unregister,
)

__all__ = [
    "BackendClosedError",
    "BackendError",
    "BaseBackend",
    "BaseCollection",
    "ChromaBackend",
    "ChromaCollection",
    "CollectionNotInitializedError",
    "DimensionMismatchError",
    "EmbedderIdentityMismatchError",
    "GetResult",
    "HealthStatus",
    "NookNotFoundError",
    "NookRef",
    "QueryResult",
    "UnsupportedFilterError",
    "available_backends",
    "get_backend",
    "get_backend_class",
    "register",
    "reset_backends",
    "resolve_backend_for_nook",
    "unregister",
]
