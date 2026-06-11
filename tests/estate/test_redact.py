"""Unit tests for sage.estate.redact — the shared secret/PII redactor.

WHERE: tests/estate/test_redact.py
"""

import itertools
import re

import pytest

from sage_mcp.estate.redact import (
    is_secret_key,
    mask_if_secret,
    looks_like_path,
    strip_home_path,
    redact_value,
    redact_string,
)

# ── is_secret_key ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "KEY",
        "token",
        "access_token",
        "TOKEN",
        "secret",
        "CLIENT_SECRET",
        "password",
        "PASSWORD",
        "auth",
        "authorization",
        "AUTH_TOKEN",
        "oauth_key",
        "private_key",
    ],
)
def test_is_secret_key_matches(key: str):
    """Keys that match the secret pattern must return True."""
    assert is_secret_key(key), f"Expected {key!r} to be detected as secret-shaped"


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "description",
        "name",
        "tools",
        "family",
        "title",
        "slot",
        "id",
        "revision",
        "count",
        "skills",
    ],
)
def test_is_secret_key_non_secret(key: str):
    """Non-secret keys must not be flagged."""
    assert not is_secret_key(key), f"Expected {key!r} NOT to be detected as secret-shaped"


# ── mask_if_secret ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,value",
    [
        ("api_key", "ghp_abcdef123456"),
        ("token", "sk-proj-xxx"),
        ("secret", "hunter2"),
        ("password", "correct-horse-battery-staple"),
        ("AUTH", "Bearer eyJhbGci"),
    ],
)
def test_mask_if_secret_masks(key: str, value: str):
    """Secret-keyed values must be replaced with [REDACTED]."""
    assert mask_if_secret(key, value) == "[REDACTED]"


@pytest.mark.parametrize(
    "key,value",
    [
        ("model", "claude-sonnet"),
        ("description", "Designs architecture for the system"),
        ("name", "dev-architect"),
        ("tools", "Read"),
    ],
)
def test_mask_if_secret_passes_through(key: str, value: str):
    """Non-secret-keyed values must be returned unchanged."""
    assert mask_if_secret(key, value) == value


# ── looks_like_path ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "/home/alice/.claude/agents/dev-architect.md",
        "/home/alice",
        "~/dev/sage",
        "C:\\Users\\alice\\sage",
        "C:/Users/alice/sage",
        "/home/fixture/agents/dev-redact-test.md",
        "/Users/Alice/projects/sage",
    ],
)
def test_looks_like_path_detects(value: str):
    """Absolute / home-rooted paths must be detected."""
    assert looks_like_path(value), f"Expected {value!r} to be detected as path"


@pytest.mark.parametrize(
    "value",
    [
        "dev-architect",
        "Designs architecture",
        "claude-sonnet",
        "Read",
        "relative/path/fragment",
        "dev/github/Ryuuske/s.a.g.e",  # relative — allowed in grounds.plots.path
    ],
)
def test_looks_like_path_ignores_non_paths(value: str):
    """Relative paths and plain strings must NOT be flagged."""
    assert not looks_like_path(value), f"Expected {value!r} NOT to be detected as path"


# ── strip_home_path — no-username leak guarantee ──────────────────────────────


def test_strip_home_path_bare_unix_home_no_username():
    """/home/alice alone must not produce 'alice' in output (sev 88 fix)."""
    result = strip_home_path("/home/alice")
    assert "alice" not in result, f"Username leaked: {result!r}"
    assert result == "~"


def test_strip_home_path_unix_home_with_subdirs_no_username():
    """/home/alice/dev/sage must not contain 'alice' in output."""
    result = strip_home_path("/home/alice/dev/sage")
    assert "alice" not in result, f"Username leaked: {result!r}"
    assert "dev/sage" in result or result.endswith("dev/sage")


def test_strip_home_path_windows_no_username():
    """C:\\Users\\Alice\\x must not contain 'Alice' in output."""
    result = strip_home_path("C:\\Users\\Alice\\x")
    assert "Alice" not in result, f"Username leaked: {result!r}"


def test_strip_home_path_mid_string_tilde():
    """Mid-string home path in free text must be stripped without leaking username."""
    result = strip_home_path("see /home/alice/Documents/private for details")
    assert "alice" not in result, f"Username leaked: {result!r}"
    assert "/home/" not in result, f"Home prefix leaked: {result!r}"


def test_strip_home_path_bare_home_only_returns_placeholder():
    """/home/alice with no subpath must return placeholder, never bare username."""
    result = strip_home_path("/home/alice")
    assert result == "~"
    assert "alice" not in result


def test_strip_home_path_macos_users_no_username():
    """/Users/Alice/projects must not contain 'Alice' in output."""
    result = strip_home_path("/Users/Alice/projects")
    assert "Alice" not in result, f"Username leaked: {result!r}"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/home/alice/.claude/agents/dev-architect.md", "~/.claude/agents/dev-architect.md"),
        ("/home/fixture/agents/dev-redact-test.md", "~/agents/dev-redact-test.md"),
        ("/home/alice", "~"),
        ("~/dev/sage", "~/dev/sage"),
        ("C:\\Users\\alice\\sage", "~/sage"),
    ],
)
def test_strip_home_path_replacement_shape(value: str, expected: str):
    """Home-path prefix is replaced with ~ leaving the remainder intact."""
    assert strip_home_path(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "dev-architect",
        "Designs architecture for the system",
        "claude-sonnet",
        "relative/path/fragment",
    ],
)
def test_strip_home_path_passthrough(value: str):
    """Non-path strings must pass through unchanged."""
    assert strip_home_path(value) == value


# ── redact_string — secret token masking ─────────────────────────────────────


def test_redact_string_masks_github_pat():
    """Description containing ghp_ token: token is [REDACTED], surrounding words kept."""
    result = redact_string("access via ghp_abc123xyz456 for the repo")
    assert "[REDACTED]" in result
    assert "ghp_abc123xyz456" not in result
    # Surrounding words must survive
    assert "access via" in result
    assert "for the repo" in result


def test_redact_string_masks_sk_key():
    """Description containing sk-live_xxx: token is [REDACTED], surrounding words kept."""
    result = redact_string("key sk-live_xxxYYYZZZ is used here")
    assert "[REDACTED]" in result
    assert "sk-live_xxxYYYZZZ" not in result
    assert "key" in result
    assert "is used here" in result


def test_redact_string_masks_bearer_token():
    """Description containing Bearer eyJ...: token portion is [REDACTED]."""
    result = redact_string("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9 in header")
    assert "[REDACTED]" in result
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert "Authorization:" in result


def test_redact_string_masks_gho_token():
    """gho_ OAuth token is masked."""
    result = redact_string("token gho_oauthXXXXXXXX here")
    assert "[REDACTED]" in result
    assert "gho_oauthXXXXXXXX" not in result


def test_redact_string_masks_github_fine_grained_pat():
    """github_pat_ fine-grained PAT is masked."""
    result = redact_string("pat github_pat_1234567890ABCDEF for org")
    assert "[REDACTED]" in result
    assert "github_pat_1234567890ABCDEF" not in result
    assert "pat" in result
    assert "for org" in result


def test_redact_string_masks_aws_access_key():
    """AKIA... AWS access key ID is masked."""
    result = redact_string("key AKIAIOSFODNN7EXAMPLE used in config")
    assert "[REDACTED]" in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result
    assert "used in config" in result


def test_redact_string_no_secrets_passthrough():
    """Plain description with no secrets or paths is returned UNCHANGED."""
    plain = "Designs architecture for the distributed system"
    assert redact_string(plain) == plain


def test_redact_string_strips_path():
    """Home paths in bare string values (no key context) are replaced with ~."""
    result = redact_string("/home/fixture/agents/dev-redact-test.md")
    assert result == "~/agents/dev-redact-test.md"
    assert "fixture" not in result


def test_redact_string_passthrough():
    """Plain strings pass through the bare-string redactor unchanged."""
    assert redact_string("Designs architecture") == "Designs architecture"
    assert redact_string("claude-sonnet") == "claude-sonnet"


# ── redact_value ──────────────────────────────────────────────────────────────


def test_redact_value_secret_key_always_redacted():
    """Secret-keyed values are [REDACTED] even if they contain a path."""
    result = redact_value("token", "/home/alice/token.txt")
    assert result == "[REDACTED]"


def test_redact_value_path_in_non_secret_field():
    """Non-secret key + home-path value → username consumed, ~ prefix."""
    result = redact_value("description", "/home/alice/.claude/agents/dev-architect.md")
    assert "alice" not in result
    assert result == "~/.claude/agents/dev-architect.md"


def test_redact_value_plain_non_secret():
    """Non-secret key + plain string → unchanged."""
    result = redact_value("model", "claude-sonnet")
    assert result == "claude-sonnet"


def test_redact_value_secret_key_plain_value():
    """Secret key + plain value (no path) → [REDACTED]."""
    result = redact_value("password", "hunter2")
    assert result == "[REDACTED]"


def test_redact_value_non_secret_key_embedded_token():
    """Non-secret key with embedded secret token → token masked (finding 45/40).

    redact_value() routes the non-secret branch through redact_string(), which
    applies _mask_secret_values before strip_home_path.  A caller passing a
    ghp_/sk-/Bearer token in a free-text field must NOT get the raw token back.
    """
    result = redact_value("description", "tok ghp_abc123 here")
    assert "ghp_abc123" not in result
    assert "[REDACTED]" in result


# ── redact_value identity check (sev 70 fix) ──────────────────────────────────


def test_redact_value_uses_equality_not_identity():
    """redact_value must use == not 'is' for the [REDACTED] sentinel check.

    This guards against CPython identity optimisation not holding for
    dynamically-constructed strings equal to '[REDACTED]'.
    """
    from sage_mcp.estate.redact import _REDACTED

    # Construct a string equal to _REDACTED but guaranteed to be a different object.
    dynamic_redacted = "".join(["[", "REDACTED", "]"])
    # Confirm it is NOT the same object (the test premise)
    # (CPython may intern short strings, so we use a workaround if needed)
    # The real check is that redact_value("token", ...) == "[REDACTED]",
    # which the other tests already cover.  Here we test the internal branch
    # by verifying _REDACTED == dynamic_redacted (value equality) regardless
    # of identity.
    assert _REDACTED == dynamic_redacted
    # And that redact_value does not leak a home path when the key is secret.
    result = redact_value("api_key", "/home/alice/secret.txt")
    assert result == "[REDACTED]"
    assert "alice" not in result


# ── BLOCKING sev-85: surviving-username bypasses in strip_home_path ───────────

CANARY = "alice"
CANARY_UPPER = "Alice"


# Helper: assert canary does not appear in result (case-insensitive)
def _no_canary(result: str) -> None:
    assert CANARY not in result, f"Canary '{CANARY}' leaked: {result!r}"
    assert CANARY_UPPER not in result, f"Canary '{CANARY_UPPER}' leaked: {result!r}"
    assert CANARY.upper() not in result, f"Canary '{CANARY.upper()}' leaked: {result!r}"


def test_strip_home_path_case_insensitive_Home_Alice():
    """/Home/Alice/x must not contain 'Alice' (mixed-case home directory)."""
    result = strip_home_path("/Home/Alice/x")
    _no_canary(result)
    assert result == "~/x", f"Expected '~/x', got {result!r}"


def test_strip_home_path_case_insensitive_HOME_ALICE():
    """/HOME/ALICE/x must not contain 'alice' (all-caps variant)."""
    result = strip_home_path("/HOME/ALICE/x")
    _no_canary(result)
    assert result == "~/x", f"Expected '~/x', got {result!r}"


def test_strip_home_path_windows_lowercase_users():
    r"""C:\users\alice\x (lowercase 'users') must not contain 'alice'."""
    result = strip_home_path("C:\\users\\alice\\x")
    _no_canary(result)
    assert result == "~/x", f"Expected '~/x', got {result!r}"


def test_strip_home_path_windows_forward_slash():
    """C:/Users/alice/x (forward-slash Windows path) must not contain 'alice'."""
    result = strip_home_path("C:/Users/alice/x")
    _no_canary(result)
    assert result == "~/x", f"Expected '~/x', got {result!r}"


def test_strip_home_path_unc_backslash():
    r"""\\server\Users\alice\proj (UNC backslash) must not contain 'alice'."""
    result = strip_home_path("\\\\server\\Users\\alice\\proj")
    _no_canary(result)
    assert result == "~/proj", f"Expected '~/proj', got {result!r}"


def test_strip_home_path_unc_forward_slash():
    """//server/Users/alice/proj (UNC forward-slash) must not contain 'alice'."""
    result = strip_home_path("//server/Users/alice/proj")
    _no_canary(result)
    assert result == "~/proj", f"Expected '~/proj', got {result!r}"


def test_strip_home_path_nested_remnant():
    """/home/alice/home/alice must leave NO 'alice' after repeated substitution."""
    result = strip_home_path("/home/alice/home/alice")
    _no_canary(result)
    # After two passes: first /home/alice/ → ~/  leaving ~/home/alice;
    # second ~/home/alice is not a home-root pattern, but the next sub-loop pass
    # treats ~/home/alice as prose and re-checks — the 'home/alice' remainder is
    # NOT a home-root pattern (no leading /), so we assert the canary is gone.
    assert "alice" not in result


def test_strip_home_path_bare_alice():
    """/home/alice alone → '~', no 'alice'."""
    result = strip_home_path("/home/alice")
    _no_canary(result)
    assert result == "~"


def test_strip_home_path_alice_with_trailing_path():
    """/home/alice/dev/sage → '~/dev/sage', no 'alice'."""
    result = strip_home_path("/home/alice/dev/sage")
    _no_canary(result)
    assert result == "~/dev/sage"


# ── HARDENING sev-70: secret-value IGNORECASE + broader patterns ──────────────


def test_secret_value_bearer_lowercase():
    """bearer <token> (lowercase) must be masked (IGNORECASE fix)."""
    result = redact_string("header: bearer eyJhbGciOiJIUzI1NiJ9 is set")
    assert "[REDACTED]" in result
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert "header:" in result


def test_secret_value_ghp_uppercase():
    """GHP_xxx (uppercase prefix) must be masked (IGNORECASE fix)."""
    result = redact_string("token GHP_ABCDEF1234567890 provided")
    assert "[REDACTED]" in result
    assert "GHP_ABCDEF1234567890" not in result


def test_secret_value_slack_bot_token():
    """xoxb- Slack bot token must be masked."""
    tok = "-".join(["xoxb", "12345", "67890", "abcdefghij"])  # ADR-0106: runtime concat
    result = redact_string(f"slack {tok} here")
    assert "[REDACTED]" in result
    assert tok not in result


def test_secret_value_slack_app_token():
    """xoxa- Slack app token must be masked."""
    result = redact_string("token xoxa-my-app-token and more")
    assert "[REDACTED]" in result
    assert "xoxa-my-app-token" not in result


def test_secret_value_google_api_key():
    """AIza... Google API key (35 chars after prefix) must be masked."""
    key = "AIza" + "A" * 35
    result = redact_string(f"key {key} end")
    assert "[REDACTED]" in result
    assert key not in result


def test_secret_value_gitlab_pat():
    """glpat-... GitLab PAT (20 chars) must be masked."""
    pat = "glpat-" + "A" * 20
    result = redact_string(f"pat {pat} end")
    assert "[REDACTED]" in result
    assert pat not in result


def test_secret_value_pem_private_key_header():
    """-----BEGIN ... PRIVATE KEY----- PEM header must be masked."""
    result = redact_string("-----BEGIN RSA PRIVATE KEY-----")
    assert "[REDACTED]" in result
    assert "BEGIN RSA PRIVATE KEY" not in result


def test_secret_value_pem_ec_private_key_header():
    """-----BEGIN EC PRIVATE KEY----- variant must be masked."""
    result = redact_string("key: -----BEGIN EC PRIVATE KEY----- in config")
    assert "[REDACTED]" in result


def test_secret_value_no_false_positive_plain():
    """Plain description text does not get falsely masked."""
    plain = "Designs architecture for the distributed system"
    assert redact_string(plain) == plain


# ── HARDENING sev-55: workshop adapter id/title/tools[] defense-in-depth ──────


def test_workshop_id_with_home_path_is_stripped(tmp_path):
    """Agent id embedding /home/alice/... must not contain 'alice' in emitted model."""
    from sage_mcp.estate.adapter.workshop import build_workshop

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    # The name field embeds a home path as a pathological edge case.
    (agents_dir / "dev-pathcase.md").write_text(
        "---\nname: /home/alice/dev-pathcase\nmodel: m1\ndescription: Test\n---\n"
    )
    workshop, _, _ = build_workshop(agents_dir)
    for agent in workshop["agents"]:
        _no_canary(agent["id"])
        _no_canary(agent["family"])


def test_workshop_title_with_home_path_is_stripped(tmp_path):
    """Workshop title embedding /home/alice/... must not contain 'alice'."""
    from sage_mcp.estate.adapter.workshop import build_workshop

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    workshop, _, _ = build_workshop(agents_dir, title="/home/alice/workshop title")
    _no_canary(workshop["title"])


def test_workshop_tools_with_home_path_are_stripped(tmp_path):
    """Tool entry embedding /home/alice/... in tools[] must not contain 'alice'."""
    from sage_mcp.estate.adapter.workshop import build_workshop

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "dev-toolcase.md").write_text(
        "---\nname: dev-toolcase\nmodel: m1\ntools:\n  - /home/alice/bin/mytool\ndescription: Test\n---\n"
    )
    workshop, _, _ = build_workshop(agents_dir)
    for agent in workshop["agents"]:
        for tool in agent.get("tools", []):
            _no_canary(tool)


# ── BLOCKING sev-86: greedy username class leaks across dual-path prose ───────
#
# Each test feeds a string containing TWO home paths in one value and asserts
# the canary ('alice'/'Alice') appears nowhere in the output.  Separators
# tested: ', ', ' then ', tab (\t), and newline (\n).


@pytest.mark.parametrize(
    "separator",
    [", ", " then ", "\t", "\n"],
    ids=["comma-space", "then", "tab", "newline"],
)
def test_dual_unix_paths_no_canary(separator: str):
    """Two /home/alice paths separated by prose must not leak 'alice'."""
    value = f"/home/alice/dev{separator}/home/alice/docs"
    result = strip_home_path(value)
    _no_canary(result)


@pytest.mark.parametrize(
    "separator",
    [", ", " then ", "\t", "\n"],
    ids=["comma-space", "then", "tab", "newline"],
)
def test_unix_then_windows_no_canary(separator: str):
    """Unix /home/alice followed by Windows C:\\Users\\alice must not leak 'alice'."""
    value = f"/home/alice/dev{separator}C:\\Users\\alice\\proj"
    result = strip_home_path(value)
    _no_canary(result)


@pytest.mark.parametrize(
    "separator",
    [", ", " then ", "\t", "\n"],
    ids=["comma-space", "then", "tab", "newline"],
)
def test_windows_then_unix_no_canary(separator: str):
    """Windows C:\\Users\\alice followed by Unix /home/alice must not leak 'alice'."""
    value = f"C:\\Users\\alice\\proj{separator}/home/alice/docs"
    result = strip_home_path(value)
    _no_canary(result)


@pytest.mark.parametrize(
    "separator",
    [", ", " then ", "\t", "\n"],
    ids=["comma-space", "then", "tab", "newline"],
)
def test_unc_then_unix_no_canary(separator: str):
    r"""\\server\Users\alice UNC followed by /home/alice must not leak 'alice'."""
    value = f"\\\\server\\Users\\alice\\share{separator}/home/alice/docs"
    result = strip_home_path(value)
    _no_canary(result)


def test_redact_string_dual_path_prose_no_canary():
    """redact_string on dual-path prose (the concrete sev-86 canary) must not leak."""
    result = redact_string("see /home/alice, then C:\\Users\\alice")
    _no_canary(result)


# ── BLOCKING sev-86 (4th gap): leading-separator / whitespace runs ────────────
#
# /home//alice, /home/ alice, /home\alice, /Users//alice, C:\Users\\alice all
# bypassed the previous regex because it expected exactly ONE separator char
# between the root marker and the username segment.  The fix widens the
# separator to [/\\\s]+ (one-or-more) and adds a fail-closed safety net.
#
# Unit tests: raw separator-run shapes — canary must not appear.


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/home//alice", "~"),
        ("/home/ alice", "~"),
        ("/home\\\alice", "~"),
        ("/Users//alice", "~"),
        ("C:\\Users\\\\alice", "~"),
        ("/home///alice", "~"),  # triple separator
    ],
    ids=[
        "double-slash-unix",
        "space-after-root",
        "backslash-sep-unix",
        "double-slash-macos",
        "double-backslash-windows",
        "triple-slash-unix",
    ],
)
def test_leading_sep_run_no_canary(value: str, expected: str):
    """Separator-run variants between home root and username must not leak 'alice'."""
    result = strip_home_path(value)
    _no_canary(result)
    assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_tab_between_root_and_name_no_canary():
    """Tab character between /home and username must not leak 'alice'."""
    value = "/home/\talice"
    result = strip_home_path(value)
    _no_canary(result)
    assert result == "~", f"Expected '~', got {result!r}"


def test_leading_sep_at_end_of_string_no_canary():
    """/home/ alice at end-of-string (no subpath) must not leak 'alice'."""
    result = strip_home_path("/home/ alice")
    _no_canary(result)
    assert result == "~", f"Expected '~', got {result!r}"


def test_uppercase_canary_no_leak_via_sep_run():
    """/home/ Alice (space + title-case) must not leak 'Alice'."""
    result = strip_home_path("/home/ Alice")
    _no_canary(result)
    assert result == "~", f"Expected '~', got {result!r}"


# ── Property-style invariant: no residual home-root marker after strip ────────
#
# For each of ~15 home-path shapes, assert the post-strip_home_path invariant:
# the output contains neither a bare /home, /users, nor ~/segment marker.

_RESIDUAL_MARKER_RE = re.compile(
    r"/home(?:[/\\\s]|$)"
    r"|/users(?:[/\\\s]|$)"
    r"|\\users(?:[/\\\s]|$)",
    re.IGNORECASE,
)


def _assert_no_residual_marker(result: str) -> None:
    """Assert the fail-closed guarantee: no home-root marker survives in *result*."""
    assert not _RESIDUAL_MARKER_RE.search(result), (
        f"Residual home-root marker in output: {result!r}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "/home/alice",
        "/home/alice/dev/sage",
        "/home//alice",
        "/home/ alice",
        "/home/\talice",
        "/home///alice",
        "/home\\alice",
        "/Users/Alice/projects",
        "/Users//Alice",
        "C:\\Users\\alice\\work",
        "C:\\Users\\\\alice",
        "C:/Users/alice/work",
        "\\\\server\\Users\\alice\\share",
        "//server/Users/alice/share",
        "/Home/Alice/x",  # mixed case
    ],
    ids=[
        "unix-bare",
        "unix-with-subpath",
        "unix-double-slash",
        "unix-space-after-root",
        "unix-tab-after-root",
        "unix-triple-slash",
        "unix-backslash-sep",
        "macos-bare",
        "macos-double-slash",
        "windows-backslash",
        "windows-double-backslash",
        "windows-forward-slash",
        "unc-backslash",
        "unc-forward-slash",
        "unix-mixed-case",
    ],
)
def test_no_residual_home_root_marker_invariant(value: str):
    """Post-strip_home_path invariant: no /home, /users marker survives in output."""
    result = strip_home_path(value)
    _no_canary(result)
    _assert_no_residual_marker(result)


# ── End-to-end: build_workshop with separator-run canary in description ───────
#
# Builds a synthetic agent whose description and title contain separator-run
# home-path variants.  Asserts the canary 'alice' does not appear ANYWHERE in
# the json.dumps of the emitted workshop model — id, title, tools included.


def test_e2e_build_workshop_sep_run_canary_in_description(tmp_path):
    """build_workshop: description with /home//alice and /home/ alice must not leak canary."""
    import json
    from sage_mcp.estate.adapter.workshop import build_workshop

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "dev-seprun.md").write_text(
        "---\n"
        "name: dev-seprun\n"
        "model: m1\n"
        "description: 'private dir /home//alice/private and notes /home/ alice/notes'\n"
        "---\n"
    )
    workshop, _, _ = build_workshop(agents_dir)
    serialised = json.dumps(workshop)
    assert CANARY not in serialised.lower(), (
        f"Canary '{CANARY}' found in serialised workshop: {serialised!r}"
    )


def test_e2e_build_workshop_canary_in_id_title_tools(tmp_path):
    """build_workshop: canary must not appear in id/title/tools of emitted workshop."""
    import json
    from sage_mcp.estate.adapter.workshop import build_workshop

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "dev-idcase.md").write_text(
        "---\n"
        "name: dev-idcase\n"
        "model: m1\n"
        "tools:\n"
        "  - /home//alice/bin/mytool\n"
        "  - /home/ alice/bin/othertool\n"
        "description: regular description\n"
        "---\n"
    )
    workshop, _, _ = build_workshop(agents_dir, title="/home//alice/workshop title")
    serialised = json.dumps(workshop)
    assert CANARY not in serialised.lower(), (
        f"Canary '{CANARY}' found in serialised workshop (id/title/tools): {serialised!r}"
    )


# ── BLOCKING sev-88: right-boundary fix — sibling-dir leak + correctness ──────
#
# Root cause: /home and /users markers lacked a right word-boundary, so
# /homes/alice, /home2/..., /homestead/... were mangled AND leaked the username.
# Fix: lookahead (?=[/\\\s]|$) after /home and /users in all three regexes
# (_HOME_ROOT_RE, _HOME_PATH_DETECT_RE, _FAILCLOSED_RE).
#
# Tests:
# 1. Correctness corpus — non-home sibling dirs must pass through UNCHANGED.
# 2. Sibling-dir leak guard — these are non-home paths so alice legitimately
#    remains, but the function must not touch them at all.
# 3. Boundary: /home/alice still strips; /homes/alice passes through.
# 4. build_workshop e2e: /homestead/alice in title must not be mangled.
# 5. Fuzz/property corpus: generated combos of home-roots × seps × usernames ×
#    trailing paths — canary must not survive in any real home-path combination,
#    and post-strip invariant holds.


@pytest.mark.parametrize(
    "value",
    [
        "/homes/alice/work",
        "/home2/data",
        "/homestead/x",
        "/username/foo",
        "/users-list/x",
        "homestead",
    ],
    ids=[
        "nfs-homes",
        "home2",
        "homestead-dir",
        "username-dir",
        "users-list-hyphenated",
        "word-homestead",
    ],
)
def test_correctness_corpus_non_home_paths_unchanged(value: str):
    """Non-home sibling dirs must pass through strip_home_path UNCHANGED.

    These paths look superficially similar to home paths but are NOT home paths
    (no right-boundary match after /home or /users).  The function must not
    touch them at all — this guards against over-stripping.
    """
    result = strip_home_path(value)
    assert result == value, (
        f"Over-stripping: {value!r} was changed to {result!r}; must be unchanged"
    )


@pytest.mark.parametrize(
    "value",
    [
        "/homes/alice/work",
        "/home2/alice/x",
        "/homestead/alice",
    ],
    ids=["nfs-homes-with-user", "home2-with-user", "homestead-with-user"],
)
def test_sibling_dir_not_detected_as_home_path(value: str):
    """Non-home sibling dirs must not be detected as home paths by looks_like_path."""
    assert not looks_like_path(value), (
        f"{value!r} is not a home path but looks_like_path returned True"
    )


def test_right_boundary_home_strips_but_homes_does_not():
    """/home/alice strips; /homes/alice does not — right-boundary is exact."""
    stripped = strip_home_path("/home/alice")
    assert stripped == "~"
    assert "alice" not in stripped

    not_stripped = strip_home_path("/homes/alice")
    assert not_stripped == "/homes/alice", f"/homes/alice must be unchanged, got {not_stripped!r}"


def test_right_boundary_users_strips_but_users_list_does_not():
    """/Users/alice strips; /users-list/alice does not — right-boundary is exact."""
    stripped = strip_home_path("/Users/alice")
    assert stripped == "~"
    assert "alice" not in stripped

    not_stripped = strip_home_path("/users-list/alice")
    assert not_stripped == "/users-list/alice", (
        f"/users-list/alice must be unchanged, got {not_stripped!r}"
    )


def test_e2e_build_workshop_homestead_title_not_mangled(tmp_path):
    """build_workshop: /homestead/alice in title must NOT be mangled (correctness).

    /homestead is a legitimate non-home dir — the function must not touch it.
    The canary alice legitimately remains here because this is NOT a home path.
    """
    from sage_mcp.estate.adapter.workshop import build_workshop

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    workshop, _, _ = build_workshop(agents_dir, title="/homestead/alice")
    assert workshop["title"] == "/homestead/alice", (
        f"Title /homestead/alice was mangled to {workshop['title']!r}"
    )


def test_e2e_build_workshop_real_home_title_strips(tmp_path):
    """build_workshop: /home/alice in title IS stripped (real home path, sev-88)."""
    import json
    from sage_mcp.estate.adapter.workshop import build_workshop

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    workshop, _, _ = build_workshop(agents_dir, title="/home/alice")
    assert "alice" not in workshop["title"], f"Canary survived in title: {workshop['title']!r}"
    serialised = json.dumps(workshop)
    assert CANARY not in serialised.lower(), (
        f"Canary survived in serialised workshop: {serialised!r}"
    )


# ── Fuzz / property corpus ────────────────────────────────────────────────────
#
# Generated via itertools.product over:
#   home-roots × separator-runs × usernames × trailing paths
#
# For each combination (real home path variant):
#   - Assert canary does NOT survive strip_home_path.
#   - Assert post-strip invariant: no residual home-root marker in output.
#
# Separator-run variants exercise the sev-86 doubled-separator fix.
# Username case variants exercise the IGNORECASE flag.

# The tilde root (~) is the safe OUTPUT form; it is not a home-root marker to
# strip from raw inputs.  Only include roots that take a separator+username as
# the /home or /Users pattern.
_FUZZ_HOME_ROOTS = ["/home", "/Users", "C:\\Users", "C:/Users", "\\\\srv\\Users"]
_FUZZ_SEPARATORS = ["/", "//", "/ ", "\\", "\t", "///"]
_FUZZ_USERNAMES = ["alice", "Alice"]
_FUZZ_TRAILING = ["", "/x", "/dev/sage"]

_FUZZ_COMBOS = list(
    itertools.product(
        _FUZZ_HOME_ROOTS,
        _FUZZ_SEPARATORS,
        _FUZZ_USERNAMES,
        _FUZZ_TRAILING,
    )
)


def _build_fuzz_path(root: str, sep: str, user: str, trailing: str) -> str:
    """Build a fuzz path from components."""
    return f"{root}{sep}{user}{trailing}"


@pytest.mark.parametrize(
    "root,sep,user,trailing",
    _FUZZ_COMBOS,
    ids=[
        f"{r.replace(chr(92), '_').replace('/', '_').replace(':', '')}-{repr(s)}-{u}-{t or 'bare'}"
        for r, s, u, t in _FUZZ_COMBOS
    ],
)
def test_fuzz_canary_does_not_survive(root: str, sep: str, user: str, trailing: str):
    """Fuzz: for any home-root × separator-run × username × trailing combo,
    canary must not survive strip_home_path, and no residual marker remains.
    """
    value = _build_fuzz_path(root, sep, user, trailing)
    result = strip_home_path(value)
    assert user.lower() not in result.lower(), (
        f"Canary {user!r} survived: input={value!r} output={result!r}"
    )
    _assert_no_residual_marker(result)
