"""Shared rendering + cache helpers for sage statusline."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path.home() / ".cache" / "sage"

CACHE_SCHEMA = 3  # bumped: Claude data now read from stdin rate_limits (was ccusage cost-burn)

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
MAGENTA = "\x1b[35m"
BLUE = "\x1b[34m"
GREY = "\x1b[90m"


def color_for_percent(percent: float) -> str:
    if percent >= 85:
        return RED
    if percent >= 60:
        return YELLOW
    return GREEN


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def reset_in(epoch_seconds: int | None) -> str:
    if epoch_seconds is None:
        return "—"
    return format_duration(epoch_seconds - time.time())


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "schema": CACHE_SCHEMA}
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_cache(path: Path, ttl_seconds: int) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    age = time.time() - stat.st_mtime
    if age > ttl_seconds:
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != CACHE_SCHEMA:
        return None
    return payload


def read_cache_ignore_ttl(path: Path) -> dict[str, Any] | None:
    """Read a cache file ignoring TTL but enforcing schema check.

    Used by stale-fallback paths that need cached data regardless of age
    but must not return payloads written by old schema versions.  Returns
    None if the file is missing, corrupt, or has a schema mismatch.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != CACHE_SCHEMA:
        return None
    return payload


def is_stale(path: Path, max_age_seconds: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) > max_age_seconds
    except OSError:
        return True


def _display_width(text: str) -> int:
    """Return the display-column width of `text`.

    Uses Unicode East Asian Width: 'F' (Fullwidth) and 'W' (Wide) characters
    count as 2 columns; everything else (including ASCII) counts as 1.
    """
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in text)


def truncate_middle(text: str, max_len: int) -> str:
    """Truncate `text` to at most `max_len` *display columns*.

    Display columns counted per East Asian width; ASCII characters count as 1.
    'F' (Fullwidth) and 'W' (Wide) Unicode characters count as 2 columns each.

    Uses an ASCII ellipsis ("..") rather than the U+2026 character so
    that downstream renderers do not over-count: a single-codepoint "…" is
    3 UTF-8 bytes. Two ASCII dots is always 2 columns.
    """
    import unicodedata

    if max_len <= 0:
        return ""
    if _display_width(text) <= max_len:
        return text
    if max_len < 4:
        # Not enough room for ellipsis + content; return head slice by columns.
        cols = 0
        result = []
        for ch in text:
            w = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
            if cols + w > max_len:
                break
            result.append(ch)
            cols += w
        return "".join(result)

    ellipsis = ".."
    ellipsis_cols = 2  # len(ellipsis) == 2 and both are ASCII
    budget = max_len - ellipsis_cols
    head_budget = (budget + 1) // 2
    tail_budget = budget // 2

    # Build head: consume chars from left until head_budget columns filled.
    head_chars: list[str] = []
    head_cols = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        if head_cols + w > head_budget:
            break
        head_chars.append(ch)
        head_cols += w

    # Build tail: consume chars from right until tail_budget columns filled.
    tail_chars: list[str] = []
    tail_cols = 0
    if tail_budget > 0:
        for ch in reversed(text):
            w = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
            if tail_cols + w > tail_budget:
                break
            tail_chars.append(ch)
            tail_cols += w
        tail_chars.reverse()

    return f"{''.join(head_chars)}{ellipsis}{''.join(tail_chars)}"
