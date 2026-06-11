"""Shared secret-scrubbing utilities for sage.

Implements the two-tier write-path architecture from ADR-0042.

**Tier discipline (ADR-0042):**

- HIGH_CONFIDENCE patterns (14) — applied at EVERY nook write entry point
  (add_drawer, update_drawer, diary_write, kg_add). These have low
  false-positive rate and cover common credential shapes. Use
  ``scrub_secrets()`` or ``scrub_secrets(text, aggressive=False)`` at
  write boundaries (default).

- AGGRESSIVE pattern (hex≥40) — applied ONLY when assembling the always-on
  Tier-0 cached-prefix block (``layers.py::assemble_tier0``), where
  over-redaction is accepted and a leak is most costly. Use
  ``scrub_secrets_aggressive()`` or ``scrub_secrets(text, aggressive=True)``
  for Tier-0 assembly only.

**Classification rule for future patterns:**
Every new pattern must be classified into the write-boundary set
(high-confidence, low false-positive — add to ``_HIGH_CONFIDENCE_PATTERNS``)
or the Tier-0-only set (accepted over-redaction — add to
``_AGGRESSIVE_PATTERNS``). Reviewers must enforce this split on every
pattern addition.

**Recognised credential shapes are scrubbed on every write path** (PRD §13):
the 14 high-confidence patterns cover the common shapes — API keys (OpenAI
incl. sk-proj-/sk-test-, Anthropic, Google, Slack), GitHub tokens, AWS access
key IDs and (contextual) secret access keys, PEM private-key blocks (full
body, not just the header), JWTs, bearer tokens, password-in-URL, and
keyword-anchored `password=`/`secret:`-style assignments. Legitimate git SHAs
survive verbatim in stored drawers; only the always-on Tier-0 view redacts them.

**Known residual (not a guarantee gap to ignore):** a bare high-entropy secret
with no recognizable prefix or adjacent keyword (e.g. a naked 40-char base64
token on its own line) is indistinguishable from data and is NOT caught at the
write boundary — only the Tier-0 aggressive hex pass catches the hex subset.
Do not claim "no credential can ever persist"; claim "recognised shapes are
scrubbed". Callers handling raw key material should not rely on the scrubber as
the sole control.
"""

from __future__ import annotations

import re

# ── HIGH-CONFIDENCE patterns (write-boundary default) ─────────────────────
# 14 patterns covering the most common credential shapes. Low false-positive
# rate — these are the patterns applied at EVERY nook write entry point.
# A 40-char git SHA-1 does NOT match any of these; verbatim storage is
# preserved for legitimate dev content.
_HIGH_CONFIDENCE_PATTERNS: list[re.Pattern] = [
    re.compile(r"sk-ant-[A-Za-z0-9-]+"),  # Anthropic API key
    # OpenAI-style key — allow `-`/`_` so sk-proj-/sk-test-/sk-svcacct- keys
    # match (the old [A-Za-z0-9]{20,} could not span the hyphen). E2E F1.
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub token (PAT/OAuth/Actions)
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key ID
    # PEM private key — redact the WHOLE block (header+body+footer), not just
    # the header line. The header-only pattern left the base64 key material
    # verbatim (cosmetic redaction). E2E C-SCRUB-2 (blocking).
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),  # PEM header fallback (truncated block)
    re.compile(r"://[^:@/\s]+:[^@/\s]+@"),  # password-in-URL
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),  # Google API key
    # Authorization: Bearer <token> — opaque bearer tokens. E2E C-SCRUB-1.
    re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/-]{10,}={0,2}"),
    # AWS *secret* access key (contextual — anchored on the key name, since the
    # 40-char base64 value alone is indistinguishable from data). E2E H1.
    re.compile(
        r"aws_secret_access_key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{20,}",
        re.IGNORECASE,
    ),
    # Generic secret assignment — keyword-anchored so false-positive stays low;
    # catches bare `password: x` / `token=y` the prefix patterns miss. The
    # keyword + separator + value anchoring keeps prose ("my password") safe.
    # E2E F2. (Moderate over-redaction accepted at the write boundary, per the
    # classification rule below — a credential-shaped assignment outweighs the
    # rare over-redaction of a non-secret value.)
    # No leading \b so prefixed identifiers match too (db_password, API_KEY,
    # MY_SECRET); the trailing \b keeps prose ("passwordless", "secretary") safe.
    # The value is captured to END OF LINE (`[^\n\r]{4,}`), NOT just the first
    # whitespace-delimited token: a token-bounded capture latched onto a
    # following keyword (`password:\npassword: <secret>` / `password: password:
    # <secret>`) and left the real secret on the next token. Rest-of-line capture
    # redacts the whole assignment value (over-redacting trailing same-line text
    # is the accepted tradeoff). E2E round-B residual.
    re.compile(
        r"(?i)(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token"
        r"|auth[_-]?token|client[_-]?secret|private[_-]?key)\b"
        r"\s*[:=]\s*['\"]?[^\n\r]{4,}"
    ),
]

# ── AGGRESSIVE pattern (Tier-0 only) ──────────────────────────────────────
# Applied ONLY in assemble_tier0() — the always-on Tier-0 surface where
# over-redaction is accepted (ADR-0042 / WI-3 ADV-13). This pattern
# intentionally matches legitimate git SHA-1/SHA-256 values, which is why
# it MUST NOT appear on the general write path (I#5).
_AGGRESSIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b[0-9a-fA-F]{40,}\b"),  # generic high-entropy hex (≥40 chars)
]

# Backwards-compatible alias: the combined set that was the original
# _SECRET_PATTERNS.  Kept so external code that imported _SECRET_PATTERNS
# for testing still works.  New code should prefer the named tier constants.
_SECRET_PATTERNS: list[re.Pattern] = _HIGH_CONFIDENCE_PATTERNS + _AGGRESSIVE_PATTERNS


def scrub_secrets(text: str, *, aggressive: bool = False) -> str:
    """Replace recognised secret patterns in ``text`` with ``[REDACTED]``.

    Default (``aggressive=False``): applies only the 14 high-confidence
    patterns from ``_HIGH_CONFIDENCE_PATTERNS``.  Safe for every nook
    write boundary — no real credential shape passes through, and legitimate
    git SHA-1 values are preserved verbatim (ADR-0042).

    With ``aggressive=True``: additionally applies the hex≥40 pattern from
    ``_AGGRESSIVE_PATTERNS``.  Use ONLY for the always-on Tier-0 surface
    (``layers.py::assemble_tier0``) where over-redaction is accepted.

    Safe to call on any string; returns the original value unchanged when
    no patterns match.
    """
    patterns = _HIGH_CONFIDENCE_PATTERNS
    if aggressive:
        patterns = _HIGH_CONFIDENCE_PATTERNS + _AGGRESSIVE_PATTERNS
    for pat in patterns:
        text = pat.sub("[REDACTED]", text)
    return text


def scrub_secrets_aggressive(text: str) -> str:
    """Apply high-confidence + aggressive (hex≥40) scrub — Tier-0 only.

    Convenience wrapper for ``scrub_secrets(text, aggressive=True)``.
    Use ONLY in ``layers.py::assemble_tier0`` (the always-on Tier-0 surface).
    Do NOT use at general write boundaries.
    """
    return scrub_secrets(text, aggressive=True)
