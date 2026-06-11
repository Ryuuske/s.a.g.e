"""Parse Claude Code's statusline stdin JSON for rate-limit window data.

Claude Code injects a `rate_limits` block into the JSON it writes to the
statusline script's stdin after the first API response of a session.
Schema (verbatim from Claude Code rendering code):

    rate_limits: {
        five_hour:  { used_percentage: float,  resets_at: int },
        seven_day:  { used_percentage: float,  resets_at: int },
    }

`used_percentage` is `utilization * 100` (float in [0, 100]).
`resets_at` is a Unix epoch integer (seconds).

Both fields can be absent, null, or malformed — this module normalises them
defensively and returns None for any value that fails validation.
"""

from __future__ import annotations

import math
import time
from typing import Any


def _parse_used_percentage(raw: Any) -> float | None:
    """Coerce and validate a used_percentage value.

    Accepts numeric types and strings coercible to float.  Returns None
    for null, non-numeric, NaN, or infinite values.  Clamps valid results
    to [0.0, 100.0].
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return max(0.0, min(100.0, value))


def _parse_resets_at(raw: Any) -> int | None:
    """Coerce and validate a resets_at epoch-seconds value.

    Accepts ints and floats.  Rejects values outside the plausible range
    [now - 3600, now + 30*86400] to guard against:
    - zero / null-like sentinels
    - millisecond timestamps (would be ~53 years in the future)
    - stale data from cache entries surviving past their reset point

    The past-window guard is intentionally tight (1 hour, not 24 hours):
    a cached resets_at that is more than 1h in the past is considered stale
    and must not be rendered as a valid future reset time.
    """
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    now = time.time()
    lo = now - 3600  # 1 hour ago — tighter guard against stale cached resets
    hi = now + 30 * 86400  # 30 days out — no real window exceeds that
    if not (lo <= value <= hi):
        return None
    return value


def _parse_window(block: Any) -> dict[str, float | int | None]:
    """Parse one rate-limit window block into normalised dict.

    Returns ``{"used_percent": float|None, "resets_at": int|None}``.
    """
    if not isinstance(block, dict):
        return {"used_percent": None, "resets_at": None}
    return {
        "used_percent": _parse_used_percentage(block.get("used_percentage")),
        "resets_at": _parse_resets_at(block.get("resets_at")),
    }


def parse_rate_limits(payload: dict) -> dict[str, dict[str, float | int | None]]:
    """Return ``{primary: {...}, secondary: {...}}`` from Claude Code's stdin JSON.

    ``primary`` is the five-hour session window; ``secondary`` is the
    seven-day weekly window.  Each window dict is:

        {"used_percent": float|None, "resets_at": int|None}

    Defensive parsing — values that are missing, null, non-numeric, or
    out of range produce None for that field.  Never raises.
    """
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        rate_limits = {}
    return {
        "primary": _parse_window(rate_limits.get("five_hour")),
        "secondary": _parse_window(rate_limits.get("seven_day")),
    }
