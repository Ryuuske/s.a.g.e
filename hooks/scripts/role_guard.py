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
  - Bash write-detection: parsed with ``shlex`` (posix=False,
    punctuation_chars=True — quote-aware, compound operators preserved).
    The raw command is pre-split on ``\\n`` (newline) first, then each
    line is tokenised by shlex.  The resulting token stream is further
    split on shell control-operator tokens (``&&``, ``||``, ``;``, ``|``).
    Each resulting statement is evaluated independently for write idioms.
    Fail-OPEN if shlex raises (malformed input etc.) — never brick a
    session on a guard bug.
  - Write idiom detection uses posix=False so that quoted ``>`` tokens
    retain their quote wrappers (e.g. ``'>'`` → token ``"'>'"``).  A
    bare unquoted ``>`` or ``>>`` token is a redirect; a quoted one
    (``"'>'"`` or ``'">'"``) is a shell argument, not a redirect.
  - Write-idiom check runs FIRST, then git/gh carve-out.  A statement
    that starts with ``git`` or ``gh`` but also contains a write idiom
    (e.g. ``git commit -m x > f.txt``) is DENIED.
  - Fail-OPEN on any internal error or malformed stdin.
    A guard bug must never brick a session.
  - ``SAGE_ROLE_GUARD=off`` → immediate no-op (allow-all).
    Mirrors the ``SAGE_HOOK_PROFILE`` opt-out pattern in hooks_cli.py.

Accepted residuals (NOT caught by this guard — documented in ADR-0126):
  - ``$(...)`` command substitution that writes files
  - Backtick command substitution (e.g. `` `cmd > f` ``)
  - ``eval`` with a dynamically constructed write command
  - Decode-pipe-shell (``base64 -d | bash``, ``python -c "exec(...)"`` etc.)
  - Explicit fd redirects to files (``cmd 1>file`` — the ``1`` prefix
    triggers the fd-number heuristic; accepted residual for best-effort)
  - ``&>file`` bash combined redirect (not a ``>`` or ``>>`` token)
  These are exotic or edge-case constructs.  The four edit tools
  (Write, Edit, MultiEdit, NotebookEdit) are the total hard guarantee.

See ADR-0126 for the full policy rationale and the Bash residual
documentation. See CLAUDE.md §12 (write-block inviolable).
"""

from __future__ import annotations

import json
import os
import re
import shlex
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


# ── Bash write-pattern detection (shlex-based, quote-aware) ──────────────────
# Best-effort; not exhaustive.  Exotic Bash writes are a documented residual
# (ADR-0126).  The four edit tools above are the total guarantee.

# Shell control operators that delimit independent statements within a command.
# After pre-splitting on newlines, the shlex token stream is further split on
# these tokens to isolate each logical statement.
_CONTROL_OPS = frozenset({"&&", "||", ";", "|"})

# Tokens that indicate in-place editing when following "sed" (bare, unquoted).
_SED_INPLACE_FLAGS = frozenset({"-i", "--in-place"})

# Bare redirect tokens (unquoted, as produced by posix=False shlex).
_REDIRECT_TOKENS = frozenset({">", ">>"})

# Fd-swap redirect tokens: start with > and followed by & (not a file write).
# e.g. ">&" produced by shlex from ">&2".
_FD_SWAP_RE = re.compile(r"^>+&")


def _is_quoted_token(token: str) -> bool:
    """Return True if the token was a quoted shell word (posix=False mode).

    In non-posix shlex mode, a token that originated from a quoted string
    retains its surrounding quote characters in the token value.  A bare
    unquoted ``>`` is just ``">"``, while a quoted ``'>'`` becomes ``"'>'"``
    and a double-quoted ``">"`` becomes ``'">"``.

    A token that starts AND ends with matching quote chars was fully quoted
    and is a shell argument, not a redirect operator.
    """
    if len(token) >= 2:
        if (token[0] == "'" and token[-1] == "'") or (token[0] == '"' and token[-1] == '"'):
            return True
    return False


def _is_bare_redirect(token: str) -> bool:
    """Return True if *token* is a bare (unquoted) ``>`` or ``>>`` token.

    Excludes:
      - Quoted forms (``'>'``, ``">"``).
      - Fd-swap forms (``>&``, ``>>&``).
    """
    if token not in _REDIRECT_TOKENS:
        return False
    if _is_quoted_token(token):
        return False
    if _FD_SWAP_RE.match(token):
        return False
    return True


def _tokenise_statements(raw_command: str) -> list[list[str]]:
    """Tokenise *raw_command* into a list of statements.

    Steps:
      1. Pre-split on newline (``\\n``) — newline is a statement separator in
         shell and is handled here rather than by shlex.
      2. For each line, run shlex (posix=False, punctuation_chars=True) to get
         quote-aware tokens with compound operators preserved (``&&``, ``>>``,
         etc. are emitted as single tokens; quoted strings retain their quote
         wrappers so ``'>'`` stays ``"'>'"`` and is distinguishable from bare
         ``">"``).
         If shlex raises, return an empty list so the caller can fail-OPEN.
      3. Split the resulting token stream on control-operator tokens
         (``&&``, ``||``, ``;``, ``|``) to isolate individual statements.

    Returns a list of statements, where each statement is a list of string
    tokens.  Returns an empty list on shlex error (caller should fail-OPEN).
    """
    lines = raw_command.split("\n")
    statements: list[list[str]] = []
    current_statement: list[str] = []

    for line in lines:
        # Each newline is a statement terminator — flush regardless of blank.
        if current_statement:
            statements.append(current_statement)
            current_statement = []

        line = line.strip()
        if not line:
            continue

        try:
            lex = shlex.shlex(line, posix=False, punctuation_chars=True)
            lex.whitespace = " \t\r"  # do NOT include \n — handled above
            tokens = list(lex)
        except ValueError:
            # shlex failed (e.g. unmatched quote) — fail-OPEN.
            return []

        for tok in tokens:
            if tok in _CONTROL_OPS:
                if current_statement:
                    statements.append(current_statement)
                current_statement = []
            else:
                current_statement.append(tok)

    if current_statement:
        statements.append(current_statement)

    return statements


def _statement_has_write_idiom(tokens: list[str]) -> bool:
    """Return True if *tokens* (a single statement) contain a write idiom.

    Checks (in order):

    1. A bare ``>`` or ``>>`` token that is:
         - Not preceded by an all-digits token (fd-number prefix like
           ``2``, ``1`` — indicates fd-number redirect, not a real write).
         - Not a fd-swap form (``>&``, ``>>&``).
         - Not followed by ``/dev/null``.

    2. A write-command head token (bare, unquoted):
         - ``tee``: deny unless the only non-flag argument is ``/dev/null``.
         - ``sed``: deny only when ``-i`` or ``--in-place`` is present.
         - ``dd``, ``cp``, ``mv``, ``install``, ``truncate``: always deny.

    3. ``python``/``python3`` ``-c`` with open-for-write in the inline code.

    Does NOT apply the git/gh carve-out — that is the caller's responsibility.
    """
    if not tokens:
        return False

    n = len(tokens)

    # ── 1. Bare redirect token scan ────────────────────────────────────────
    for i, tok in enumerate(tokens):
        if tok not in _REDIRECT_TOKENS:
            continue
        # Skip if the token is quoted (e.g. grep '>' file → token is "'>'" )
        if _is_quoted_token(tok):
            continue
        # Skip fd-swap forms like >&, >>&
        if _FD_SWAP_RE.match(tok):
            continue
        # Skip if preceded by an all-digits token (fd-number redirect)
        # e.g. ["pytest", "2", ">", "err.txt"] → "2" precedes ">"
        prev_tok = tokens[i - 1] if i > 0 else ""
        if prev_tok.isdigit() or (prev_tok and prev_tok.isdigit()):
            continue
        # Generalise: any token consisting entirely of digits is an fd number.
        if re.fullmatch(r"\d+", prev_tok):
            continue
        # Skip if target is /dev/null (bit-bucket, not a real write)
        # or /tmp/* (scratch space; accepted residual per spec).
        next_tok = tokens[i + 1] if i + 1 < n else ""
        if next_tok in ("/dev/null", "'/dev/null'", '"/dev/null"'):
            continue
        if next_tok.startswith("/tmp/"):
            continue
        # Passed all exclusions → real redirect → write idiom.
        return True

    # ── 2. Write-command head token ────────────────────────────────────────
    # The head token is the first token in the statement; in posix=False mode
    # a quoted head (unlikely but possible) retains quote chars.  Bare tokens
    # compare directly to the string values below.
    head = tokens[0] if tokens else ""

    if head == "tee":
        # tee /dev/null is a bit-bucket — not a real write.
        # tee with any real-path arg (or -a <file>) IS a write.
        non_flag_args = [t for t in tokens[1:] if not t.startswith("-")]
        if non_flag_args == ["/dev/null"]:
            return False
        # Any other target (real path, or no target — writes to file) → deny.
        # tee with zero non-flag args writes to stdout only → allow.
        return len(non_flag_args) > 0

    if head == "sed":
        # Deny only when -i / --in-place is present (bare, unquoted).
        return any(t in _SED_INPLACE_FLAGS for t in tokens[1:])

    if head in ("dd", "cp", "mv", "install", "truncate"):
        return True

    # ── 3. python -c open-for-write ───────────────────────────────────────
    if head in ("python", "python3", "python2"):
        try:
            c_idx = tokens.index("-c")
        except ValueError:
            return False
        # Join remaining tokens as code string (posix=False: may include quotes)
        code_str = " ".join(tokens[c_idx + 1 :])
        # Match open( with 'w' or "w" mode anywhere in the code tokens.
        if re.search(r"""open\s*\(.*,\s*['"]w['"]\s*\)""", code_str):
            return True

    return False


def _statement_is_git_gh(tokens: list[str]) -> bool:
    """Return True if the statement's head command is bare ``git`` or ``gh``."""
    return bool(tokens) and tokens[0] in ("git", "gh")


def _bash_is_write(command: str) -> bool:
    """Return True if *command* (possibly compound) matches a file-write idiom.

    Steps:
      1. Tokenise with shlex into statements (newline + control-operator split).
         Fail-OPEN (return False — allow) if shlex raises.
      2. For each statement:
           a. Check for a write idiom FIRST.
           b. If a write idiom is found → DENY (True), regardless of git/gh prefix.
           c. If no write idiom → apply the git/gh carve-out (allow that statement).
      3. If ANY statement has a write idiom, the whole command is denied.

    The git/gh carve-out is intentionally checked AFTER the write-idiom check so
    that ``git commit -m msg > f.txt`` is correctly denied.
    """
    if not command or not isinstance(command, str):
        return False

    statements = _tokenise_statements(command)
    if not statements and command.strip():
        # shlex raised — fail-OPEN (allow).
        return False

    for stmt in statements:
        if not stmt:
            continue
        if _statement_has_write_idiom(stmt):
            # Write idiom found — deny, regardless of git/gh prefix.
            return True
        # No write idiom: git/gh carve-out applies (allow).

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
