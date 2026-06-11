"""Fetch Codex rate-limit window data via the codex app-server JSON-RPC."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from typing import Any

from format import CACHE_DIR, atomic_write_json, read_cache, read_cache_ignore_ttl

CACHE_PATH = CACHE_DIR / "codex.json"
DEFAULT_TTL_SECONDS = 30
DEFAULT_STALE_SECONDS = 180


def _coerce_used_percent(raw: Any) -> float | None:
    """Coerce and validate a usedPercent value from Codex RPC.

    Accepts numeric types and strings coercible to float.  Returns None
    for null, non-numeric, NaN, or infinite values.  Clamps valid results
    to [0.0, 100.0].  Mirrors claude_stdin._parse_used_percentage.
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


def _coerce_resets_at(raw: Any) -> int | None:
    """Coerce and validate a resetsAt epoch-seconds value from Codex RPC.

    Accepts ints and floats.  Rejects values outside the plausible range
    [now - 3600, now + 30*86400] to guard against zero/null sentinels,
    millisecond timestamps, and stale past data.  Mirrors
    claude_stdin._parse_resets_at with a tighter 1h past-window guard.
    """
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    now = time.time()
    lo = now - 3600  # 1 hour ago — tighter guard than claude_stdin
    hi = now + 30 * 86400  # 30 days out — no real window exceeds that
    if not (lo <= value <= hi):
        return None
    return value


def _spawn_app_server() -> subprocess.Popen[bytes]:
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _send(proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("codex app-server stdin unavailable")
    proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _recv_response(
    proc: subprocess.Popen[bytes], want_id: int, timeout: float
) -> dict[str, Any] | None:
    if proc.stdout is None:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            return None
        try:
            msg = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if msg.get("id") == want_id:
            return msg
    return None


def fetch_live(timeout: float = 8.0) -> dict[str, Any]:
    """Spawn codex app-server, ask for rate limits, exit. Returns raw RPC result."""
    proc = _spawn_app_server()
    try:
        _send(
            proc,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "sage-statusline",
                        "title": "sage",
                        # MCP clientInfo protocol version — independent of sage package version.
                        "version": "0.1.0",
                    },
                    "capabilities": {},
                },
            },
        )
        if not _recv_response(proc, 1, timeout):
            raise RuntimeError("codex app-server initialize timeout")

        _send(proc, {"method": "initialized", "params": {}})

        _send(proc, {"id": 2, "method": "account/rateLimits/read", "params": {}})
        msg = _recv_response(proc, 2, timeout)
        if not msg:
            raise RuntimeError("codex app-server rateLimits/read timeout")
        if "error" in msg:
            raise RuntimeError(f"codex error: {msg['error'].get('message', msg['error'])}")
        return msg.get("result", {})
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _summarize(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate Codex app-server JSON-RPC (camelCase) into our cache schema (snake_case).

    This is the camelCase→snake_case boundary. Field renames here are
    deliberate and should not be "fixed" toward consistency with the
    Codex protocol.
    """
    snapshot = raw.get("rateLimits") or {}
    primary = snapshot.get("primary") or {}
    secondary = snapshot.get("secondary") or {}
    by_id = raw.get("rateLimitsByLimitId") or {}

    return {
        "updated_at": int(time.time()),
        "plan_type": snapshot.get("planType"),
        "limit_id": snapshot.get("limitId"),
        "limit_name": snapshot.get("limitName"),
        "rate_limit_reached_type": snapshot.get("rateLimitReachedType"),
        "credits": snapshot.get("credits"),
        "primary": {
            "used_percent": _coerce_used_percent(primary.get("usedPercent")),
            "window_minutes": primary.get("windowDurationMins"),
            "resets_at": _coerce_resets_at(primary.get("resetsAt")),
        },
        "secondary": {
            "used_percent": _coerce_used_percent(secondary.get("usedPercent")),
            "window_minutes": secondary.get("windowDurationMins"),
            "resets_at": _coerce_resets_at(secondary.get("resetsAt")),
        },
        "by_limit_id": {
            key: {
                "limit_name": val.get("limitName"),
                "primary_used_percent": _coerce_used_percent(
                    (val.get("primary") or {}).get("usedPercent")
                ),
                "secondary_used_percent": _coerce_used_percent(
                    (val.get("secondary") or {}).get("usedPercent")
                ),
            }
            for key, val in by_id.items()
        },
    }


def get_budget(
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return budget dict, refreshing from codex app-server if cache is cold."""
    if not force_refresh:
        cached = read_cache(CACHE_PATH, ttl_seconds)
        if cached is not None:
            cached["stale"] = False
            return cached

    try:
        raw = fetch_live()
        summary = _summarize(raw)
        summary["stale"] = False
        atomic_write_json(CACHE_PATH, summary)
        return summary
    except (OSError, RuntimeError) as exc:
        stale_payload = read_cache_ignore_ttl(CACHE_PATH)
        if stale_payload is not None:
            stale_payload["stale"] = True
            stale_payload["error"] = str(exc)
            return stale_payload
        return {
            "stale": True,
            "error": str(exc),
            "primary": {"used_percent": None, "resets_at": None},
            "secondary": {"used_percent": None, "resets_at": None},
        }


if __name__ == "__main__":
    force = "--refresh" in sys.argv
    print(json.dumps(get_budget(force_refresh=force), indent=2))
