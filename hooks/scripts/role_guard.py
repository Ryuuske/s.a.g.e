#!/usr/bin/env python3
"""PreToolUse role-guard hook for the sage Claude Code plugin.

Enforces the orchestrator write-block: denies file-write tool calls
(Write, Edit, MultiEdit, NotebookEdit) when there is no ``agent_id``
in the hook payload — indicating the call is from the main session
(orchestrator), not a dispatched subagent.

Also provides best-effort denial of Bash commands whose text matches
common file-write idioms (output redirection, tee, sed -i, etc.),
with carve-outs for ``git`` and ``gh`` (orchestrator admin lane).

Design decisions:
  - ``agent_id`` PRESENCE (not ``agent_type``) is the key signal.
    When absent → orchestrator call → deny write tools.
    When present → dispatched agent → allow.
  - Fail-OPEN on any internal error or malformed stdin.
    A guard bug must never brick a session.
  - ``SAGE_ROLE_GUARD=off`` → immediate no-op (allow-all).
    Mirrors the ``SAGE_HOOK_PROFILE`` opt-out pattern in hooks_cli.py.

See ADR-0126 for the full policy rationale and the Bash residual
documentation. See CLAUDE.md §12 (write-block inviolable).
"""

from __future__ import annotations

import json
import os
import re
import sys

# ── Dial ─────────────────────────────────────────────────────────────────────
_ROLE_GUARD_ENV = "SAGE_ROLE_GUARD"


def _guard_enabled() -> bool:
    """Return False when SAGE_ROLE_GUARD=off; True for any other value.

    Mirrors the SAGE_HOOK_PROFILE dial pattern in hooks_cli.py.
    """
    raw = os.environ.get(_ROLE_GUARD_ENV, "").strip().lower()
    return raw != "off"


# ── Tool sets ─────────────────────────────────────────────────────────────────
# These four tools are the total write guarantee.  Bash is best-effort.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# ── Deny message ──────────────────────────────────────────────────────────────
_DENY_REASON = (
    "S.A.G.E. orchestrator is write-blocked — dispatch an implementer agent "
    "(aidev-code-implementer / dev-code-implementer) to write. "
    "See CLAUDE.md role-discipline inviolable."
)

# ── Bash write-pattern detection ──────────────────────────────────────────────
# Best-effort; not exhaustive.  Exotic Bash writes are a documented residual
# (ADR-0126).  The four edit tools above are the total guarantee.

# Carve-out: these command prefixes are always allowed (orchestrator admin lane).
_BASH_ALLOW_PREFIXES = re.compile(r"^\s*(git|gh)\b")

# Patterns that indicate a file write in a Bash command.
# Excludes /dev/null and /tmp/* from the output-redirection check.
_BASH_WRITE_PATTERNS = [
    # Output redirection to a real path (excludes /dev/null and /tmp/)
    re.compile(r">+\s*(?!/dev/null)(?!/tmp/)(?:\S)"),
    # tee to a path
    re.compile(r"\btee\s+(?!-\b)"),
    # sed in-place
    re.compile(r"\bsed\s+(-[a-zA-Z]*i|--in-place)\b"),
    # dd
    re.compile(r"\bdd\b"),
    # cp and mv (writing to a destination)
    re.compile(r"\b(?:cp|mv)\s"),
    # install
    re.compile(r"\binstall\b"),
    # truncate
    re.compile(r"\btruncate\s"),
    # Here-doc to file (cat <<EOF > file)
    re.compile(r"<<\s*['\"]?\w+['\"]?\s*>"),
    # python -c with open(..., 'w') pattern
    re.compile(r"""python[23]?\s+-c\s+.*open\s*\(.*['"]['"]w['"]"""),
]


def _bash_is_write(command: str) -> bool:
    """Return True if command matches a known file-write idiom.

    Returns False (allow) when command starts with git or gh (admin carve-out).
    """
    if not command or not isinstance(command, str):
        return False
    if _BASH_ALLOW_PREFIXES.match(command):
        return False
    for pattern in _BASH_WRITE_PATTERNS:
        if pattern.search(command):
            return True
    return False


# ── Decision logic ────────────────────────────────────────────────────────────


def _make_deny() -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": _DENY_REASON,
        }
    }


def _make_allow() -> dict:
    # Empty dict = allow (Claude Code treats absent decision as allow)
    return {}


def decide(payload: dict) -> dict:
    """Core decision function — deterministic, testable without I/O.

    Args:
        payload: Parsed stdin JSON from Claude Code's PreToolUse hook.

    Returns:
        dict to emit as JSON on stdout.
    """
    # Dial: off → allow-all
    if not _guard_enabled():
        return _make_allow()

    tool_name = payload.get("tool_name", "")
    agent_id = payload.get("agent_id")  # absent (None) → orchestrator

    # agent_id present → dispatched subagent → allow
    if agent_id is not None:
        return _make_allow()

    # agent_id absent → orchestrator call

    # Hard-deny the four write tools
    if tool_name in _WRITE_TOOLS:
        return _make_deny()

    # Best-effort Bash write-pattern check
    if tool_name == "Bash":
        tool_input = payload.get("tool_input", {})
        command = ""
        if isinstance(tool_input, dict):
            command = tool_input.get("command", "")
        elif isinstance(tool_input, str):
            command = tool_input
        if _bash_is_write(command):
            return _make_deny()

    return _make_allow()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    """Read stdin JSON, emit permission decision JSON to stdout."""
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        print(f"role_guard: stdin read failed (fail-open): {exc}", file=sys.stderr)
        print("{}", flush=True)
        return 0

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError(f"expected dict, got {type(payload).__name__}")
    except Exception as exc:
        print(f"role_guard: malformed stdin (fail-open): {exc}", file=sys.stderr)
        print("{}", flush=True)
        return 0

    try:
        result = decide(payload)
    except Exception as exc:
        print(f"role_guard: decision error (fail-open): {exc}", file=sys.stderr)
        print("{}", flush=True)
        return 0

    try:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(f"role_guard: output error (fail-open): {exc}", file=sys.stderr)
        print("{}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
