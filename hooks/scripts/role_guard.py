#!/usr/bin/env python3
"""PreToolUse role-guard hook for the sage Claude Code plugin.

Enforces the orchestrator write-block: denies file-write tool calls
(Write, Edit, MultiEdit, NotebookEdit) when there is no subagent signal
in the hook payload — indicating the call is from the main session
(orchestrator), not a dispatched subagent.

Also provides best-effort denial of Bash commands whose text matches
common file-write idioms (output redirection, tee, sed -i, etc.),
with carve-outs for ``git`` and ``gh`` (orchestrator admin lane).

Design decisions:
  - Subagent signal: presence of ``agent_id`` OR ``agentId`` (either
    camelCase or snake_case key → allow).  Both are checked because the
    exact field name emitted by Claude Code was not verified against a
    live captured payload at authoring time (ADR-0126 §keystone-gap).
    When absent → orchestrator call → deny write tools.
    When present → dispatched agent → allow.
  - Bash chaining: the command is split on ``&&``, ``||``, ``;``, ``|``
    and each segment is evaluated independently.  The git/gh carve-out
    applies only to a segment that is itself a pure git/gh command, never
    to a write segment chained after it.
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

# ── Subagent detection ────────────────────────────────────────────────────────
# Both camelCase and snake_case are checked because the exact field name emitted
# by Claude Code has not been verified against a live captured payload at
# authoring time (ADR-0126 §keystone-gap).  Sandbox proof (work item 7) must
# assert the dispatched-agent-write ALLOWED branch with a real payload.
_AGENT_ID_KEYS = ("agent_id", "agentId")


def _is_subagent(payload: dict) -> bool:
    """Return True if the payload carries a subagent signal (either key form)."""
    return any(payload.get(k) is not None for k in _AGENT_ID_KEYS)


# ── Bash write-pattern detection ──────────────────────────────────────────────
# Best-effort; not exhaustive.  Exotic Bash writes are a documented residual
# (ADR-0126).  The four edit tools above are the total guarantee.

# Shell command-chaining operators used to split a compound command into segments.
# Each segment is evaluated independently — the git/gh carve-out applies only to
# a segment that is itself a pure git/gh command.
_CHAIN_SPLIT = re.compile(r"&&|\|\||;|\|")

# Carve-out: these command prefixes are always allowed (orchestrator admin lane).
# Applied per-segment after splitting on chaining operators.
_BASH_ALLOW_PREFIXES = re.compile(r"^\s*(git|gh)\b")

# Patterns that indicate a file write in a Bash command segment.
# Excluded from the redirection check:
#   - /dev/null  (bit-bucket — not a real write)
#   - /tmp/*     (scratch space; acceptable residual per spec)
#   - fd-number redirects: >&N, N>&M, N>file  where N is a digit
#     (stderr/stdout-swap idioms like 2>&1, >&2, 2> err.txt are not
#      file-system writes the guard needs to block)
_BASH_WRITE_PATTERNS = [
    # Output redirection to a real path.
    # Exclusions:
    #   (?<!\d)  — negative lookbehind: N> forms (fd-number redirects such as
    #              2>err.txt, 1>&2) are not filesystem writes.
    #   (?!&)    — >&N forms (e.g. >&2) are fd-swap, not filesystem writes.
    #   /dev/null — bit-bucket.
    #   /tmp/     — scratch space; accepted residual per spec.
    re.compile(r"(?<!\d)>+\s*(?!&)(?!/dev/null)(?!/tmp/)(?:\S)"),
    # tee writing to a real path (including tee -a <file>).
    # Parses optional short flags (e.g. -a) then requires a path that is
    # not /dev/null.
    re.compile(r"\btee\b(?:\s+-\w+)*\s+(?!/dev/null\b)\S"),
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
    # python -c with open(path, 'w') or open(path, "w") pattern
    re.compile(r"""python[23]?\s+-c\s+.*open\s*\(\s*\S.*,\s*['"]w['"]\s*\)"""),
]

# Matches a segment that is a bare fd-redirect line (e.g. "2> err.txt",
# "1>&2") — these are shell redirections of file-descriptor numbers, not
# commands writing to named files.
_FD_REDIRECT_ONLY = re.compile(r"^\s*\d+>")


def _segment_is_write(segment: str) -> bool:
    """Return True if a single (already-split) command segment is a write idiom.

    Returns False when the segment is an fd-redirect-only fragment or starts
    with git/gh (admin carve-out).
    """
    segment = segment.strip()
    if not segment:
        return False
    # fd-redirect fragments like "2> err.txt" or "1>&2" — not a filesystem write.
    if _FD_REDIRECT_ONLY.match(segment):
        return False
    # git/gh carve-out: applies only to a segment that is itself a git/gh command.
    if _BASH_ALLOW_PREFIXES.match(segment):
        return False
    for pattern in _BASH_WRITE_PATTERNS:
        if pattern.search(segment):
            return True
    return False


def _bash_is_write(command: str) -> bool:
    """Return True if the command (possibly compound) matches a file-write idiom.

    The command is split on shell chaining operators (&&, ||, ;, |) and each
    resulting segment is evaluated independently.  The guard denies if ANY
    segment is a write idiom — a chained write after a safe git command is
    still caught.
    """
    if not command or not isinstance(command, str):
        return False
    segments = _CHAIN_SPLIT.split(command)
    return any(_segment_is_write(seg) for seg in segments)


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

    # Subagent signal present (agent_id or agentId) → dispatched agent → allow.
    if _is_subagent(payload):
        return _make_allow()

    # No subagent signal → orchestrator call.

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
