"""Cache layer for Claude rate-limit window data.

Data is produced by parsing Claude Code's stdin JSON (see claude_stdin.py)
and persisted here so the statusline can serve a last-known-good value when
`rate_limits` is absent from stdin (e.g. first render of a new session, or
non-subscriber accounts where the block is never emitted).

Cache schema version 3 — schema was bumped from 2 when the data source
switched from ccusage cost-burn derivation to Claude Code's own stdin JSON.
Old schema-2 caches are invalidated on read (intentional).
"""

from __future__ import annotations

import json
import time
from typing import Any

from format import CACHE_DIR, CACHE_SCHEMA, atomic_write_json, read_cache

CACHE_PATH = CACHE_DIR / "claude.json"
DEFAULT_TTL_SECONDS = 300  # 5 minutes


def _read_existing_cache_raw() -> dict[str, Any] | None:
    """Read the cache file ignoring TTL, for merge purposes only.

    Returns the cached dict if schema matches; None otherwise.
    Does not raise.
    """
    try:
        raw = CACHE_PATH.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != CACHE_SCHEMA:
        return None
    return payload


def _merge_window(incoming: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    """Merge two window dicts per-field.

    For each of ``used_percent`` and ``resets_at``: prefer the incoming
    value when non-None; fall back to the cached value otherwise.  This
    prevents a valid cached ``resets_at`` from being replaced by None when
    the incoming update carries a fresh ``used_percent`` but a rejected
    (None) reset timestamp.
    """
    out = dict(incoming)
    for field in ("used_percent", "resets_at"):
        if out.get(field) is None and cached.get(field) is not None:
            out[field] = cached[field]
    return out


def write_window(payload_data: dict[str, Any]) -> None:
    """Persist a successfully-parsed window to claude.json.

    Merges incoming data with any existing cached value so that a partial
    update (e.g. only five_hour populated, seven_day absent) does not
    overwrite a previously-cached seven_day window with None.

    Per-field merge rule: for each of ``used_percent`` and ``resets_at``
    independently, prefer the incoming value when non-None; otherwise keep
    the cached value.  This ensures a valid cached ``resets_at`` is never
    erased by an incoming None even when ``used_percent`` is fresh.

    Writes ``schema=3`` (enforced by ``atomic_write_json`` via
    ``CACHE_SCHEMA`` in format.py) and an ``updated_at`` field.
    """
    existing = _read_existing_cache_raw()

    merged: dict[str, Any] = {}
    for key in ("primary", "secondary"):
        incoming_window = payload_data.get(key) or {}
        cached_window = (existing or {}).get(key) or {}
        merged[key] = _merge_window(incoming_window, cached_window)

    atomic_write_json(CACHE_PATH, {**merged, "updated_at": int(time.time())})


def read_window(max_age_seconds: int = DEFAULT_TTL_SECONDS) -> dict[str, Any] | None:
    """Read the cached window if younger than ``max_age_seconds``; else None.

    Returns the full cache dict (including ``primary`` and ``secondary``
    sub-dicts) or None when the file is missing, too old, or has an
    incompatible schema version.
    """
    return read_cache(CACHE_PATH, max_age_seconds)
