"""Tests for sage_mcp.secret_scrub — two-tier write-path architecture (ADR-0042)."""

from sage_mcp.secret_scrub import (
    _AGGRESSIVE_PATTERNS,
    _HIGH_CONFIDENCE_PATTERNS,
    scrub_secrets,
    scrub_secrets_aggressive,
)

# A realistic 40-char git SHA-1 (all hex, no credential prefix).
_GIT_SHA_40 = "a" * 40
# A longer SHA-256 (64 hex chars).
_GIT_SHA_64 = "b" * 64

# Fake credential strings that must always be redacted.
_ANTHROPIC_KEY = "sk-ant-api01-fakekey1234567890abcdefghij"
_OPENAI_KEY = "sk-abcdefghij1234567890abcde"
_GITHUB_PAT = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345"
_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
_GOOGLE_KEY = "AIzaSyBcdefghijklmnopqrstuvwxyz123456789"
_SLACK = "-".join(
    ["xoxb", "123456789012", "123456789012", "abcdefghijklmnop"]
)  # ADR-0106: runtime concat
_PEM = "-----BEGIN RSA PRIVATE KEY-----"
_PASSWORD_URL = "https://user:password@example.com/path"
# Stripe secret keys use an UNDERSCORE prefix (sk_live_ / sk_test_ / rk_live_),
# which the existing sk-<hyphen> pattern does not match. All fake.
# Built by concatenation so no committed blob contains a contiguous
# Stripe-shaped token — GitHub push protection scans raw blobs and blocks
# pushes containing sk_live_<24+ alnum> shapes even when fake (ADR-0094).
# Precision note (post-merge audit PMA-1): the ADR-0094 convention binds
# fixtures whose shape a commercial scanner ACTUALLY RECOGNIZES — push
# protection itself is the oracle. The contiguous ghp_/AKIA/xoxb fakes
# elsewhere in this file push clean (not real-shaped enough to match) and
# therefore do not require concatenation; if a future push is ever blocked
# on one of them, concatenate that fixture the same way.
_STRIPE_LIVE = "sk_live_" + "FAKE0123456789abcdefABCD"
_STRIPE_TEST = "sk_test_" + "FAKE0123456789abcdefABCD"
_STRIPE_RESTRICTED = "rk_live_" + "FAKE0123456789abcdefABCD"


# ── High-confidence write-boundary scrub ─────────────────────────────────────


def test_high_confidence_redacts_stripe_keys():
    """Stripe secret keys (underscore prefix) must be redacted by the high-confidence
    scrubber even when bare (no keyword anchor). The existing sk-<hyphen> pattern
    misses the sk_<underscore> Stripe family — this is the gap under test."""
    for key in (_STRIPE_LIVE, _STRIPE_TEST, _STRIPE_RESTRICTED):
        assert "[REDACTED]" in scrub_secrets(key), f"Stripe key not redacted: {key[:8]}..."


def test_high_confidence_redacts_anthropic_key():
    assert "[REDACTED]" in scrub_secrets(f"key={_ANTHROPIC_KEY}")


def test_high_confidence_redacts_openai_key():
    assert "[REDACTED]" in scrub_secrets(f"key={_OPENAI_KEY}")


def test_high_confidence_redacts_github_pat():
    assert "[REDACTED]" in scrub_secrets(f"token={_GITHUB_PAT}")


def test_high_confidence_redacts_aws_key():
    assert "[REDACTED]" in scrub_secrets(f"access_key={_AWS_KEY}")


def test_high_confidence_redacts_jwt():
    assert "[REDACTED]" in scrub_secrets(f"auth={_JWT}")


def test_high_confidence_redacts_google_key():
    assert "[REDACTED]" in scrub_secrets(f"key={_GOOGLE_KEY}")


def test_high_confidence_redacts_slack_token():
    assert "[REDACTED]" in scrub_secrets(f"token={_SLACK}")


def test_high_confidence_redacts_pem_header():
    assert "[REDACTED]" in scrub_secrets(_PEM)


def test_high_confidence_redacts_password_in_url():
    assert "[REDACTED]" in scrub_secrets(_PASSWORD_URL)


def test_high_confidence_git_sha_40_survives():
    """A 40-char git SHA-1 MUST survive verbatim on the write path (ADR-0042 I#5).

    The high-confidence write-boundary scrub does NOT redact hex-only strings —
    that would erase commit SHAs from decision/episodic drawers.
    """
    text = f"Commit: {_GIT_SHA_40} resolves the race condition."
    result = scrub_secrets(text)
    assert _GIT_SHA_40 in result, (
        f"Git SHA-1 was incorrectly redacted on the write path — ADR-0042 violation.\n"
        f"Input:  {text!r}\n"
        f"Output: {result!r}"
    )


def test_high_confidence_git_sha_64_survives():
    """A 64-char git SHA-256 also survives on the write path."""
    text = f"Hash: {_GIT_SHA_64}"
    result = scrub_secrets(text)
    assert _GIT_SHA_64 in result


def test_high_confidence_passthrough_clean_text():
    text = "No secrets here — just plain English about the nook."
    assert scrub_secrets(text) == text


def test_high_confidence_aggressive_false_is_default():
    """scrub_secrets(text) and scrub_secrets(text, aggressive=False) are identical."""
    text = f"SHA: {_GIT_SHA_40}, key: {_ANTHROPIC_KEY}"
    assert scrub_secrets(text) == scrub_secrets(text, aggressive=False)


# ── Regression: credential shapes that previously leaked (E2E validation) ────
# Each of these was found stored VERBATIM by the sage E2E validation run.

_OPENAI_PROJECT_KEY = "sk-proj-abc123DEF456ghi789jkl012MNO345pqr678"
_OPENAI_TEST_KEY = "sk-test-ABC123DEF456GHI789JKL"
_BEARER = "Authorization: Bearer abc123DEF456ghi789JKL012mno345PQR"
_AWS_SECRET_CTX = "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
_BARE_PASSWORD = "password: hunter2plaintext"
_BARE_PASSWORD_EQ = "db_password=SuperSecret123"
_PEM_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA0xQAdef456GHIjkl789MNOpqr012STUvwx345YZabc678def90\n"
    "GHIjkl789MNOpqr012STUvwx345YZabc678def90GHIjkl789MNOpqr012STUvwx==\n"
    "-----END RSA PRIVATE KEY-----"
)
_PEM_BODY = "MIIEowIBAAKCAQEA0xQAdef456GHIjkl789MNOpqr012STUvwx345YZabc678def90"


def test_redacts_openai_project_key_with_hyphens():
    """sk-proj-/sk-test- keys (hyphens after sk-) must be redacted (E2E F1)."""
    assert _OPENAI_PROJECT_KEY not in scrub_secrets(f"key={_OPENAI_PROJECT_KEY}")
    assert _OPENAI_TEST_KEY not in scrub_secrets(f'api_key = "{_OPENAI_TEST_KEY}"')


def test_redacts_bearer_token():
    """Authorization: Bearer <token> must be redacted (E2E C-SCRUB-1)."""
    out = scrub_secrets(_BEARER)
    assert "abc123DEF456ghi789JKL012mno345PQR" not in out


def test_redacts_aws_secret_access_key():
    """AWS *secret* access key (contextual) must be redacted (E2E H1)."""
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in scrub_secrets(_AWS_SECRET_CTX)


def test_redacts_bare_password_assignment():
    """Bare password:/= assignments must be redacted (E2E F2)."""
    assert "hunter2plaintext" not in scrub_secrets(_BARE_PASSWORD)
    assert "SuperSecret123" not in scrub_secrets(_BARE_PASSWORD_EQ)


def test_redacts_pem_private_key_body_not_just_header():
    """The PEM key BODY must be redacted, not only the header (E2E C-SCRUB-2, blocking).

    The pre-fix scrubber matched only the -----BEGIN----- line, leaving the
    base64 key material verbatim — cosmetic redaction.
    """
    out = scrub_secrets(_PEM_BLOCK)
    assert _PEM_BODY not in out, "PEM private-key body survived scrub — cosmetic redaction"
    assert "-----END RSA PRIVATE KEY-----" not in out


def test_password_value_not_left_on_next_token():
    """A trailing secret must not survive when a label/keyword precedes it.

    Token-bounded value capture latched onto the FOLLOWING keyword and left the
    real secret on the next token (E2E round-B residual). Rest-of-line capture
    fixes both the doubled-keyword and label-then-kv arrangements.
    """
    for text in (
        "Database password:\npassword: hunter2plaintext",
        "password: password: hunter2plaintext",
    ):
        out = scrub_secrets(text)
        assert "hunter2plaintext" not in out, f"secret survived scrub: {out!r}"


def test_password_keyword_without_value_survives():
    """Over-redaction guard: the word 'password' in prose (no assignment) is kept."""
    text = "I forgot my password yesterday and had to reset it."
    assert scrub_secrets(text) == text


# ── Aggressive scrub (Tier-0 only) ───────────────────────────────────────────


def test_aggressive_redacts_git_sha_40():
    """Tier-0 scrub MUST redact 40-char hex strings (ADR-0042 Tier-0 surface)."""
    text = f"Commit: {_GIT_SHA_40} resolves the race condition."
    result = scrub_secrets(text, aggressive=True)
    assert _GIT_SHA_40 not in result
    assert "[REDACTED]" in result


def test_aggressive_redacts_git_sha_64():
    """Tier-0 scrub redacts 64-char hex strings too."""
    text = f"Hash: {_GIT_SHA_64}"
    result = scrub_secrets(text, aggressive=True)
    assert _GIT_SHA_64 not in result


def test_aggressive_still_redacts_credentials():
    """The aggressive path also covers all high-confidence patterns."""
    assert "[REDACTED]" in scrub_secrets(_ANTHROPIC_KEY, aggressive=True)
    assert "[REDACTED]" in scrub_secrets(_GITHUB_PAT, aggressive=True)


def test_scrub_secrets_aggressive_helper_matches():
    """scrub_secrets_aggressive(text) == scrub_secrets(text, aggressive=True)."""
    text = f"SHA: {_GIT_SHA_40}, key: {_ANTHROPIC_KEY}"
    assert scrub_secrets_aggressive(text) == scrub_secrets(text, aggressive=True)


# ── Pattern-tier invariants ───────────────────────────────────────────────────


def test_high_confidence_pattern_count():
    """Tripwire: high-confidence pattern count is intentional. Update the count
    deliberately when adding/removing a pattern (ADR-0042 + E2E credential-shape
    hardening; +1 for the Stripe sk_/rk_ family, ADR-0093)."""
    assert len(_HIGH_CONFIDENCE_PATTERNS) == 14


def test_aggressive_has_1_pattern():
    """1 aggressive pattern: hex≥40 (Tier-0 only)."""
    assert len(_AGGRESSIVE_PATTERNS) == 1


def test_hex_pattern_only_in_aggressive_not_high_confidence():
    """The hex≥40 pattern must NOT appear in the high-confidence set (ADR-0042)."""
    hex_pat_str = r"\b[0-9a-fA-F]{40,}\b"
    for pat in _HIGH_CONFIDENCE_PATTERNS:
        assert pat.pattern != hex_pat_str, (
            "hex≥40 pattern found in _HIGH_CONFIDENCE_PATTERNS — "
            "this would over-redact git SHAs on the general write path (ADR-0042 I#5)"
        )
    # Confirm it IS in aggressive
    assert any(p.pattern == hex_pat_str for p in _AGGRESSIVE_PATTERNS)
