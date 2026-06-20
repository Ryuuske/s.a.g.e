#!/usr/bin/env python3
"""sage statusline for Claude Code.

Reads the Claude statusLine JSON on stdin (model, workspace, cwd,
rate_limits, ...) and renders one line covering:

    folder  ⎇branch  model  C %·t  Cwk %·t  X %·t  Xwk %·t

where C/Cwk are Claude 5h/weekly windows (from stdin rate_limits) and
X/Xwk are Codex 5h/weekly windows (from codex app-server, cached).

Designed to run fast: heavy work is cached in ~/.cache/sage/.
Falls back gracefully when data is unavailable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from claude_stdin import parse_rate_limits  # noqa: E402
from claude_window import read_window, write_window  # noqa: E402
from codex_window import get_budget as get_codex_budget  # noqa: E402
from format import (  # noqa: E402
    BOLD,
    CYAN,
    DIM,
    MAGENTA,
    RESET,
    color_for_percent,
    reset_in,
    truncate_middle,
)


def read_stdin_json() -> dict:
    try:
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def folder_label(cwd: str) -> str:
    if not cwd:
        return "?"
    path = Path(cwd)
    parts = path.parts
    # Render "user/repo" if under ~/sage/github/<user>/<repo>/...
    try:
        idx = parts.index("github")
        if idx + 2 < len(parts):
            return f"{parts[idx + 1]}/{parts[idx + 2]}"
    except ValueError:
        pass
    return path.name or str(path)


def git_branch(cwd: str) -> str | None:
    if not cwd or not Path(cwd).exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    return branch or None


def render_pct(label: str, percent: float | None, resets_at: int | None) -> str:
    if percent is None:
        return f"{DIM}{label} —{RESET}"
    color = color_for_percent(percent)
    pct_str = f"{int(round(percent))}%"
    when = reset_in(resets_at)
    return f"{DIM}{label}{RESET} {color}{pct_str}{RESET}{DIM}·{when}{RESET}"


def main() -> int:
    payload = read_stdin_json()
    ws = payload.get("workspace")
    if not isinstance(ws, dict):
        ws = {}
    cwd = ws.get("current_dir") or payload.get("cwd") or os.getcwd()
    mdl = payload.get("model")
    if not isinstance(mdl, dict):
        mdl = {}
    model = mdl.get("display_name") or "?"

    folder = truncate_middle(folder_label(cwd), 28)
    branch = git_branch(cwd)
    if branch is not None:
        branch = truncate_middle(branch, 20)

    # --- Claude windows: read from stdin rate_limits ---
    stdin_windows = parse_rate_limits(payload)
    claude_primary = stdin_windows["primary"]
    claude_secondary = stdin_windows["secondary"]

    # Cache if we got usable data; merge with cache so partial stdin (e.g.
    # only five_hour) does not silently drop a previously-cached seven_day.
    primary_ok = claude_primary.get("used_percent") is not None
    secondary_ok = claude_secondary.get("used_percent") is not None
    if primary_ok or secondary_ok:
        try:
            write_window({"primary": claude_primary, "secondary": claude_secondary})
        except (OSError, RuntimeError) as exc:
            # Fail open: statusline must never block Claude Code's prompt rendering.
            print(f"sage-statusline: claude cache write failed: {exc}", file=sys.stderr)

    # Re-read merged cache: write_window merges incoming with existing, so the
    # file may contain data for windows that stdin omitted.  Update in-memory
    # windows from the merged result; prefer cache values that carry real data.
    merged = read_window()
    if merged is not None:
        cached_primary = merged.get("primary") or {}
        cached_secondary = merged.get("secondary") or {}
        if cached_primary.get("used_percent") is not None:
            claude_primary = cached_primary
        if cached_secondary.get("used_percent") is not None:
            claude_secondary = cached_secondary

    # --- Codex windows: fetched from codex app-server (cached) ---
    try:
        codex = get_codex_budget()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        # Fail open: statusline must never block Claude Code's prompt rendering.
        print(f"sage-statusline: codex budget error: {exc}", file=sys.stderr)
        codex = {
            "stale": True,
            "primary": {"used_percent": None, "resets_at": None},
            "secondary": {"used_percent": None, "resets_at": None},
        }

    codex_primary = codex.get("primary") or {}
    codex_secondary = codex.get("secondary") or {}

    parts: list[str] = []
    parts.append(f"{BOLD}{CYAN}{folder}{RESET}")
    if branch:
        parts.append(f"{DIM}⎇{RESET} {MAGENTA}{branch}{RESET}")
    parts.append(f"{DIM}{model}{RESET}")

    # Claude 5h window
    parts.append(
        render_pct("C", claude_primary.get("used_percent"), claude_primary.get("resets_at"))
    )
    # Claude weekly window
    parts.append(
        render_pct(
            "Cwk",
            claude_secondary.get("used_percent"),
            claude_secondary.get("resets_at"),
        )
    )
    # Codex windows: render X and Xwk using whatever data is available.
    # When stale is True but the cache carried real percentages, show those
    # percentages (not None) so the user retains the last-known-good budget
    # signal exactly when the live fetch degraded.  A dim "(stale)" token
    # is appended at the end so the user knows the values are not live.
    # Branch on whether used_percent is None — NOT on whether stale is set.
    # Codex 5h window
    parts.append(render_pct("X", codex_primary.get("used_percent"), codex_primary.get("resets_at")))
    # Codex weekly window
    parts.append(
        render_pct(
            "Xwk",
            codex_secondary.get("used_percent"),
            codex_secondary.get("resets_at"),
        )
    )
    # Append stale marker only when stale=True AND at least one cached
    # percentage is present (marker is redundant if there's nothing to mark).
    if codex.get("stale") and (
        codex_primary.get("used_percent") is not None
        or codex_secondary.get("used_percent") is not None
    ):
        parts.append(f"{DIM}(stale){RESET}")

    # Two-space inter-segment join: keeps a visible segment boundary even
    # when ANSI colors are stripped (logs, screenshots-to-text, accessibility
    # tooling). The within-segment "·" remains distinct.
    print("  ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
