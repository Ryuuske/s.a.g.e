"""sage_mcp.estate.redact — Shared redactor for the Estate adapter.

Single enforcement point (ADR-0003 §No-secrets) before any value enters the
Estate Model JSON.  Two rules, both mandatory:

1. **Secret masking** — Any string VALUE whose *key* matches
   ``key|token|secret|password|auth`` (case-insensitive) is replaced with
   ``"[REDACTED]"``.  Used when walking dict entries before emission.

2. **Home-path stripping** — Any string that contains an absolute home-rooted
   filesystem path (``/home/<user>/…``, ``/Users/<user>/…``,
   ``C:\\Users\\<user>\\…``, or ``~/…`` / ``~\\…``) has the home-root-plus-
   username prefix replaced by ``~``, leaving only the portion after the user
   directory.  This guarantees the username segment NEVER survives in any
   **decoded** path string.  Percent-encoded forms (``%2Fhome%2Falice``) and
   environment-variable references (``%USERPROFILE%``) are not decoded by this
   layer and therefore fall outside this guarantee.

3. **Secret-value masking** — Any string that contains a token matching known
   secret patterns (``ghp_``, ``gho_``, ``github_pat_``, ``sk-``, AWS AKIA
   keys, ``Bearer <token>``) is replaced with ``[REDACTED]`` for that token
   while keeping surrounding prose intact.  Applied before home-path stripping
   on free-text fields (``redact_string``).

All functions are PURE (no I/O, no side-effects) and re-entrant.
"""

from __future__ import annotations

import re

# ── Secret detection ──────────────────────────────────────────────────────────

_SECRET_KEY_RE = re.compile(r"key|token|secret|password|auth", re.IGNORECASE)

_REDACTED = "[REDACTED]"


def is_secret_key(key: str) -> bool:
    """Return True if *key* matches the secret-shaped key pattern.

    >>> is_secret_key("api_key")
    True
    >>> is_secret_key("description")
    False
    """
    return bool(_SECRET_KEY_RE.search(key))


def mask_if_secret(key: str, value: str) -> str:
    """Return ``[REDACTED]`` when *key* is secret-shaped; otherwise *value*.

    >>> mask_if_secret("token", "ghp_abc123")
    '[REDACTED]'
    >>> mask_if_secret("model", "claude-sonnet")
    'claude-sonnet'
    """
    if is_secret_key(key):
        return _REDACTED
    return value


# ── Secret-value patterns (for free-text masking) ────────────────────────────

_SECRET_VALUE_RE = re.compile(
    r"(?:"
    r"ghp_\w+"  # GitHub personal access token
    r"|gho_\w+"  # GitHub OAuth token
    r"|github_pat_\w+"  # GitHub fine-grained PAT
    r"|sk-[A-Za-z0-9_-]+"  # OpenAI / generic sk- key
    r"|AKIA[0-9A-Z]{16}"  # AWS access key ID
    r"|Bearer\s+\S+"  # HTTP Bearer token
    r"|xox[baprs]-\S+"  # Slack token (bot/app/personal/refresh/signing)
    r"|AIza[0-9A-Za-z\-_]{35}"  # Google API key
    r"|glpat-[0-9A-Za-z\-_]{20}"  # GitLab PAT
    r"|-----BEGIN [A-Z ]+PRIVATE KEY-----"  # PEM private key header
    r")",
    re.IGNORECASE,
)


def _mask_secret_values(value: str) -> str:
    """Replace secret-shaped token values in *value* with ``[REDACTED]``.

    Surrounding prose is preserved; only the matched token is replaced.

    >>> _mask_secret_values("token is ghp_abc123xyz and more words")
    'token is [REDACTED] and more words'
    >>> _mask_secret_values("no secrets here")
    'no secrets here'
    """
    return _SECRET_VALUE_RE.sub(_REDACTED, value)


# ── Home-path detection and stripping ─────────────────────────────────────────

# Matches home-root-plus-username prefix (plus optional trailing separator)
# anywhere in a string.  re.IGNORECASE covers /Home/Alice, /HOME/ALICE, etc.
#
# Variants handled:
#   /home/<user>/  or  /home/<user>  (Unix — any case)
#   /Users/<user>/  or  /Users/<user>  (macOS — any case)
#   C:\Users\<user>\  or  C:\Users\<user>  (Windows backslash — any case)
#   C:/Users/<user>/  or  C:/Users/<user>  (Windows forward-slash — any case)
#   \\<host>\Users\<user>\  or  //<host>/Users/<user>/  (UNC — any case)
#   ~/home/<user>/  or  ~/Users/<user>/  (nested remnant after prior substitution)
#   ~/  or  ~\  (already-safe tilde-home — pass through)
#
# SEPARATOR RUNS (sev-86 fix — primary defense):
#   The separator between the root marker and the username is now [/\\\s]+
#   (one-or-more of: forward-slash, backslash, or whitespace) rather than a
#   single literal character.  This closes the doubled-separator and
#   space-after-root bypass: /home//alice, /home/ alice, /home\alice,
#   /Users//alice, C:\Users\\alice all now match and have the username consumed.
#
# The username segment ([^/\\\s]+) MUST be consumed by the match so it cannot
# survive in the output.  The trailing separator (if any) is also consumed so
# the replacement "~/" does not produce a doubled slash.
#
# Ordering note: the nested-remnant alternatives (~/home/... ~/Users/...) are
# listed BEFORE the bare tilde alternatives so they take priority when the
# combined root+username pattern appears immediately after ~/.
_HOME_ROOT_RE = re.compile(
    r"(?:"
    r"(?:~/home(?=[/\\\s])[/\\\s]+[^/\\\s]+[/\\]?)"  # ~/home/<sep-run><user>  (nested remnant)
    r"|(?:~/Users(?=[/\\\s])[/\\\s]+[^/\\\s]+[/\\]?)"  # ~/Users/<sep-run><user>  (nested remnant macOS)
    r"|(?:/home(?=[/\\\s])[/\\\s]+[^/\\\s]+[/\\]?)"  # /home/<sep-run><user> (Unix)
    r"|(?:/Users(?=[/\\\s])[/\\\s]+[^/\\\s]+[/\\]?)"  # /Users/<sep-run><user> (macOS)
    r"|(?:[A-Za-z]:[/\\]+Users[/\\\s]+[^/\\\s]+[/\\]?)"  # C:\Users\<sep-run><user>
    r"|(?:\\\\[^\\]+\\Users[/\\\s]+[^/\\\s]+[/\\]?)"  # \\host\Users\<sep-run><user>
    r"|(?://[^/]+/Users[/\\\s]+[^/\\\s]+[/\\]?)"  # //host/Users/<sep-run><user>
    r"|(?:~[/\\])"  # ~/  or  ~\  (already safe, keep as-is)
    r"|(?:~$)"  # bare ~ at end of string
    r")",
    re.IGNORECASE,
)

# Detection regex — same separator-run broadening so looks_like_path catches
# the same extended shapes before strip_home_path is invoked.
_HOME_PATH_DETECT_RE = re.compile(
    r"(?:"
    r"~/home(?=[/\\\s])[/\\\s]+[^/\\\s]+"  # ~/home/<sep-run><user>  (nested remnant)
    r"|~/Users(?=[/\\\s])[/\\\s]+[^/\\\s]+"  # ~/Users/<sep-run><user>  (nested remnant macOS)
    r"|/home(?=[/\\\s])[/\\\s]+[^/\\\s]+"  # /home/<sep-run><user> (Unix)
    r"|/Users(?=[/\\\s])[/\\\s]+[^/\\\s]+"  # /Users/<sep-run><user> (macOS)
    r"|[A-Za-z]:[/\\]+Users[/\\\s]+[^/\\\s]+"  # C:\Users\<sep-run><user>
    r"|\\\\[^\\]+\\Users[/\\\s]+[^/\\\s]+"  # \\host\Users\<sep-run><user>
    r"|//[^/]+/Users[/\\\s]+[^/\\\s]+"  # //host/Users/<sep-run><user>
    r"|~[/\\]"  # ~/  or  ~\
    r"|^~$"  # bare ~
    r")",
    re.IGNORECASE,
)

# ── Fail-closed safety net (convergence guarantee — sev-86 defense in depth) ──
#
# LOAD-BEARING GUARANTEE: after strip_home_path() returns, the output contains
# no substring matching a bare home-root marker (/home, /users, \users, :/users,
# or UNC \\host\users) followed by a path segment (username).  The safe output
# form "~/" is NOT targeted by this net — ~/rest is the correct post-strip shape.
# This net fires AFTER the primary regex substitution loop; if the primary regex
# ever misses a shape, this net guarantees no username segment survives.
#
# Implementation: scan for any residual home-root marker in the already-stripped
# string, then for each match consume from the marker through the following
# separator run and path token.  Replace the whole span with ~.
#
# NOTE: ~/  (tilde-slash) is intentionally excluded — it is the correct safe
# output and must never be replaced.  Only raw /home, /users, \users, :\users,
# and UNC paths are targeted.
_FAILCLOSED_RE = re.compile(
    r"(?:"
    # /home followed by separator run (may be empty) and optional token.
    # Right boundary (?=[/\\\s]|$) ensures /homes, /home2, /homestead don't match.
    r"(?:/home(?=[/\\\s]|$)[/\\\s]*[^/\\\s,]*)"
    # /users followed by separator run and optional token.
    # Right boundary (?=[/\\\s]|$) ensures /users-list, /username don't match.
    r"|(?:/users(?=[/\\\s]|$)[/\\\s]*[^/\\\s,]*)"
    # \users (Windows backslash remnant, not tilde-prefixed)
    r"|(?:(?<![~])[/\\]users(?=[/\\\s]|$)[/\\\s]*[^/\\\s,]*)"
    # :\users (Windows drive-relative remnant like :\users\alice)
    r"|(?::[/\\]+users(?=[/\\\s]|$)[/\\\s]*[^/\\\s,]*)"
    # UNC \\host\users\ followed by token
    r"|(?:\\\\[^\\]+\\users[/\\\s]*[^/\\\s,]*)"
    r"|(?://[^/]+/users[/\\\s]*[^/\\\s,]*)"
    r")",
    re.IGNORECASE,
)


def looks_like_path(value: str) -> bool:
    """Return True if *value* appears to contain an absolute or home-rooted path.

    Also matches separator-run variants (doubled slashes, spaces after root
    marker) that the broadened _HOME_PATH_DETECT_RE now covers.

    >>> looks_like_path("/home/alice/.claude/agents/dev-architect.md")
    True
    >>> looks_like_path("C:\\\\Users\\\\alice\\\\sage")
    True
    >>> looks_like_path("dev-architect")
    False
    >>> looks_like_path("~/Documents/notes.md")
    True
    >>> looks_like_path("/home//alice")
    True
    >>> looks_like_path("/home/ alice")
    True
    """
    return bool(_HOME_PATH_DETECT_RE.search(value) or _FAILCLOSED_RE.search(value))


def strip_home_path(value: str) -> str:
    """Strip the home-root-plus-username prefix from *value*, replacing with ``~``.

    The username segment is consumed by the regex and never appears in the
    output for any **decoded** path string.  Percent-encoded forms
    (e.g. ``%2Fhome%2Falice``) and environment-variable references
    (e.g. ``%USERPROFILE%``) are not decoded by this layer and fall outside
    this guarantee.  Operates as a substring replacement so mid-string paths
    in free text are handled.

    The substitution is applied **repeatedly until the result is stable** so
    that nested occurrences like ``/home/user/home/user/rest`` do not leave a
    residual ``home/user`` fragment after the first pass.

    **Defense in depth — two layers:**

    1. *Primary* (``_HOME_ROOT_RE``): matches the home-root marker followed by
       a separator run (``[/\\\\ \\t]+``) then the username token.  Handles
       canonical paths, doubled separators, spaces after the root marker, and
       backslash variants (``/home//alice``, ``/home/ alice``, ``/home\\alice``,
       ``/Users//alice``, ``C:\\\\Users\\\\\\\\alice``).

    2. *Fail-closed safety net* (``_FAILCLOSED_RE``): after the primary loop
       converges, scans the result for any residual home-root marker
       (``/home``, ``/users``, ``\\users``, ``:/users``, ``~/segment``,
       ``UNC \\\\..\\users``).  Any match is replaced with ``~``.

    **Load-bearing guarantee**: after ``strip_home_path`` returns, the output
    contains no ``/home``, ``/users``, or ``~/segment`` home-root-plus-segment
    substring.  If the primary regex ever misses a shape, the fail-closed net
    ensures no username segment survives.

    Handles:
    - ``/home/<user>/rest``  →  ``~/rest``
    - ``/home/<user>`` (bare, no trailing path)  →  ``~``
    - ``/home//<user>/rest``  →  ``~/rest``  (doubled separator)
    - ``/home/ <user>/rest``  →  ``~/rest``  (space after root)
    - ``/home\\<user>/rest``  →  ``~/rest``  (backslash separator)
    - ``/Users/<user>/rest``  →  ``~/rest``
    - ``/Home/<user>/rest``  →  ``~/rest``  (case-insensitive)
    - ``C:\\\\Users\\\\<user>\\\\rest``  →  ``~/rest``
    - ``C:/Users/<user>/rest``  →  ``~/rest``  (forward-slash Windows)
    - ``\\\\\\\\server\\\\Users\\\\<user>\\\\rest``  →  ``~/rest``  (UNC backslash)
    - ``//server/Users/<user>/rest``  →  ``~/rest``  (UNC forward-slash)
    - ``~/rest``  →  ``~/rest``  (already safe, tilde/slash kept as-is)
    - plain strings with no home path  →  unchanged

    >>> strip_home_path("/home/alice/.claude/agents/dev-architect.md")
    '~/.claude/agents/dev-architect.md'
    >>> strip_home_path("/home/alice")
    '~'
    >>> strip_home_path("dev-architect")
    'dev-architect'
    >>> strip_home_path("C:\\\\Users\\\\alice\\\\sage")
    '~/sage'
    >>> strip_home_path("see /home/alice/Documents/private for details")
    'see ~/Documents/private for details'
    >>> strip_home_path("/home//alice")
    '~'
    >>> strip_home_path("/home/ alice")
    '~'
    """
    if not looks_like_path(value):
        return value

    # ── Primary pass: loop until stable ──────────────────────────────────────
    # _replace must close over ``current`` (the string being processed in this
    # pass) so that ``len(current)`` reflects the CURRENT string, not the original.
    result = value
    while True:
        current = result  # snapshot for this pass's closure

        def _replace(m: re.Match, _cur: str = current) -> str:
            matched = m.group(0)
            # For ~/  and  ~\  — already-safe tilde forms; normalise to ~/
            if matched == "~/" or matched == "~\\":
                return "~/"
            # For bare ~  at end of string — already safe; keep as-is
            if matched == "~":
                return "~"
            # For all other variants (home-root + separator-run + username +
            # optional trailing separator):
            # If the match ended with a separator OR there is still content in
            # the CURRENT string after the match, the trailing path follows —
            # emit ~/  so the remainder attaches cleanly.
            # If there is NO remaining path, emit bare ~.
            if matched[-1] in "/\\" or m.end() < len(_cur):
                return "~/"
            return "~"

        next_result = _HOME_ROOT_RE.sub(_replace, current)
        if next_result == current:
            break
        result = next_result

    # ── Fail-closed safety net (fixed-point loop) ────────────────────────────
    # If any residual home-root marker survived the primary loop (e.g. a shape
    # the primary regex did not cover), replace it with ~ now.  Looped to a
    # fixed point so that multiple residual markers are all scrubbed even if a
    # single pass leaves new ones exposed.  This is the load-bearing convergence
    # guarantee: no home-root-plus-segment substring survives strip_home_path
    # regardless of input shape.
    while True:
        next_result = _FAILCLOSED_RE.sub("~", result)
        if next_result == result:
            break
        result = next_result

    return result


# ── Combined redactor ─────────────────────────────────────────────────────────


def redact_value(key: str, value: str) -> str:
    """Apply both redaction rules to *value* for the given *key*.

    Order: secret masking first (if secret, always ``[REDACTED]``), then
    home-path stripping on the result (for non-secret keys that happen to
    carry a path).

    >>> redact_value("token", "/home/alice/token.txt")
    '[REDACTED]'
    >>> redact_value("description", "/home/alice/.claude/agents/dev-architect.md")
    '~/.claude/agents/dev-architect.md'
    >>> redact_value("model", "claude-sonnet")
    'claude-sonnet'
    """
    result = mask_if_secret(key, value)
    if result == _REDACTED:
        return result
    return redact_string(result)


def redact_string(value: str) -> str:
    """Strip home-path PII and secret token values from *value* (no key context).

    Used for fields where the key is known-safe but the value may carry a path
    or embedded secret token (e.g. ``description``, ``title``).

    Applies secret-value masking first (tokens like ``ghp_``, ``sk-``, etc.),
    then home-path stripping.  Normal prose without secrets or home paths is
    returned UNCHANGED.

    >>> redact_string("/home/alice/.claude/agents/dev-architect.md")
    '~/.claude/agents/dev-architect.md'
    >>> redact_string("Designs architecture")
    'Designs architecture'
    >>> redact_string("token is ghp_abc123 for access")
    'token is [REDACTED] for access'
    """
    result = _mask_secret_values(value)
    return strip_home_path(result)
