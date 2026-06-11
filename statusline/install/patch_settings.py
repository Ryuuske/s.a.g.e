#!/usr/bin/env python3
"""Idempotently patch a Claude Code settings.json with the sage
statusLine entry and one or two SessionStart hooks.

Usage:
    patch_settings.py SETTINGS_PATH STATUSLINE_CMD HOOK_CMD [--hook-wakeup CMD] [--dry-run]

HOOK_CMD registers the codex-budget SessionStart hook (required).
--hook-wakeup CMD optionally registers the claude-wakeup SessionStart hook.

Writes a one-time backup at SETTINGS_PATH + ".sage.bak" before the
first patch. Re-running with the same arguments is a no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HOOK_MARKERS = (
    "inject-codex-budget.py",
    "claude-wakeup-sessionstart.py",
    "autonomy-continuation-sessionstart.py",
)

_PATH_BOUNDARY = frozenset("/\\ \"'\t")


def _hook_marker_for(cmd: str) -> str | None:
    """Return which of our markers appears as the final path component of `cmd`,
    or None.

    The marker must be the final path-component of the command being invoked.
    install.sh and install.ps1 both wrap the hook path in quotes for
    space-resilience (e.g. `python3 '/path/inject-codex-budget.py'` or
    `python3 "C:\\path\\inject-codex-budget.py"`), so the marker check must
    look past trailing quotes / whitespace. Trailing arguments or redirects
    (which would change the command's identity) are still rejected by the
    right-boundary requirement.

    The left boundary requires a PATH SEPARATOR (`/` or `\\`) immediately
    before the marker, or the marker must appear at position 0. A quote or
    whitespace preceding the marker indicates the marker is appearing as a
    quoted argument to a foreign command (not as the script being invoked) —
    those cases are rejected. This prevents false-positive ownership claims on
    foreign hooks like `/opt/foo.py 'inject-codex-budget.py'`.
    """
    if not cmd:
        return None
    for marker in HOOK_MARKERS:
        idx = cmd.rfind(marker)
        if idx < 0:
            continue
        # Left boundary: marker is start-of-string OR preceded by a path
        # separator. A quote/whitespace preceding the marker indicates the
        # marker is appearing as a quoted argument to a foreign command,
        # not as the script being invoked — reject those cases.
        if idx > 0 and cmd[idx - 1] not in ("/", "\\"):
            continue
        # Right boundary: marker terminates the command except for trailing
        # whitespace and quote characters (no path-significant chars allowed
        # after the marker).
        tail = cmd[idx + len(marker) :]
        if all(c in _PATH_BOUNDARY for c in tail):
            return marker
    return None


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        print(f"ERROR: {path} is not valid JSON ({exc}); refusing to patch", file=sys.stderr)
        sys.exit(2)


def patch(
    data: dict,
    statusline_cmd: str,
    hook_cmd: str,
    wakeup_hook_cmd: str | None = None,
    continuation_hook_cmd: str | None = None,
) -> bool:
    """Idempotently apply the sage statusLine and up to three SessionStart hooks.

    Hook entries are matched by HOOK_MARKERS substring (path-bounded) rather
    than by exact command string. This survives Python launcher / path
    changes between installs. Foreign hooks are preserved untouched.
    """
    changed = False

    desired_statusline = {"type": "command", "command": statusline_cmd}
    if data.get("statusLine") != desired_statusline:
        data["statusLine"] = desired_statusline
        changed = True

    # Build the map of desired hooks keyed by marker.
    desired_by_marker: dict[str, dict] = {
        "inject-codex-budget.py": {"type": "command", "command": hook_cmd, "timeout": 10},
    }
    if wakeup_hook_cmd is not None:
        desired_by_marker["claude-wakeup-sessionstart.py"] = {
            "type": "command",
            "command": wakeup_hook_cmd,
            "timeout": 10,
        }
    if continuation_hook_cmd is not None:
        desired_by_marker["autonomy-continuation-sessionstart.py"] = {
            "type": "command",
            "command": continuation_hook_cmd,
            "timeout": 10,
        }

    hooks = data.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])

    placed: set[str] = set()
    for entry in session_start:
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            continue
        # Collect indices of duplicate entries (second+ occurrence of a marker already
        # placed by an earlier inner entry). Collected after the scan to avoid the
        # iterate-and-mutate footgun.
        to_remove: list[int] = []
        for i, h in enumerate(list(inner)):
            if not isinstance(h, dict):
                continue
            cmd = h.get("command") or ""
            marker = _hook_marker_for(cmd)
            if marker is None or marker not in desired_by_marker:
                continue
            if marker in placed:
                # Duplicate from a prior install run (e.g. quoted-path bug in W.5 round-2).
                # Mark for removal; first occurrence was already kept (and possibly updated).
                to_remove.append(i)
                continue
            desired = desired_by_marker[marker]
            if h != desired:
                inner[i] = desired
                changed = True
            placed.add(marker)
        if to_remove:
            # Remove in reverse-index order to preserve earlier indices.
            for idx in sorted(to_remove, reverse=True):
                del inner[idx]
            changed = True

    for marker, desired in desired_by_marker.items():
        if marker not in placed:
            session_start.append({"hooks": [desired]})
            changed = True

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings_path")
    parser.add_argument("statusline_cmd")
    parser.add_argument("hook_cmd", help="codex-budget SessionStart hook command")
    parser.add_argument(
        "--hook-wakeup",
        dest="wakeup_hook_cmd",
        default=None,
        help="optional claude-wakeup SessionStart hook command",
    )
    parser.add_argument(
        "--hook-continuation",
        dest="continuation_hook_cmd",
        default=None,
        help="optional autonomy-continuation SessionStart hook command (ADR-0066)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings_path = Path(args.settings_path)
    data = load_settings(settings_path)
    changed = patch(
        data,
        args.statusline_cmd,
        args.hook_cmd,
        args.wakeup_hook_cmd,
        args.continuation_hook_cmd,
    )

    if not changed:
        print(f"settings.json already patched: {settings_path}")
        return 0

    if args.dry_run:
        print(f"would patch: {settings_path}")
        print("--- after patch ---")
        print(json.dumps(data, indent=2))
        return 0

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        backup = settings_path.with_suffix(settings_path.suffix + ".sage.bak")
        if not backup.exists():
            backup.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"backup written: {backup}")

    # Atomic write via tempfile.mkstemp for symlink-race parity with
    # statusline/lib/format.py:atomic_write_json. A predictable .tmp
    # path could be pre-created as a symlink by an attacker with write
    # access to ~/.claude/.
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{settings_path.name}.",
        suffix=".sage.tmp",
        dir=str(settings_path.parent),
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, settings_path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    print(f"patched: {settings_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
