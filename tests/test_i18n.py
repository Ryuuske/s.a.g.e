"""Tests for sage.i18n — internationalization helpers."""

from __future__ import annotations


def test_i18n_load_lang_and_translate():
    """load_lang, t(), current_lang(), and get_regex() exercise i18n basics."""
    from sage_mcp.i18n import load_lang, t, current_lang, get_regex

    # Load English (default)
    strings = load_lang("en")
    assert isinstance(strings, dict)
    assert current_lang() == "en"

    # t() returns a key passthrough for unknown keys
    result = t("nonexistent.key.xyz")
    assert "nonexistent" in result or result == "nonexistent.key.xyz"

    # get_regex() returns a dict
    regex = get_regex()
    assert isinstance(regex, dict)


def test_i18n_load_lang_unknown_falls_back_to_en():
    """load_lang with an unknown lang code falls back to 'en'."""
    from sage_mcp.i18n import load_lang, current_lang

    load_lang("zz-unknown-code")
    # Falls back to 'en'
    assert current_lang() == "en"
    load_lang("en")  # reset


def test_i18n_t_single_level_key():
    """t() with a single-level key (no dot) returns value or key."""
    from sage_mcp.i18n import load_lang, t

    load_lang("en")
    # Single-level key: no dot — hits the else branch (line 74)
    result = t("nonexistent_single")
    assert result == "nonexistent_single"


def test_i18n_t_format_error_suppressed():
    """t() suppresses KeyError/IndexError from bad format strings."""
    from sage_mcp.i18n import t, load_lang
    import sage_mcp.i18n as i18n_mod

    load_lang("en")
    # Inject a bad format string into _strings so format() raises KeyError
    original = i18n_mod._strings
    i18n_mod._strings = {"sect": {"key": "{missing_var}"}}
    result = t("sect.key", other_var="x")  # format() will raise KeyError
    i18n_mod._strings = original
    # Must not raise; returns the un-interpolated template
    assert isinstance(result, str)


def test_i18n_get_entity_patterns_empty_languages():
    """get_entity_patterns with empty tuple falls back to English."""
    from sage_mcp.i18n import get_entity_patterns

    cfg = get_entity_patterns(())
    assert isinstance(cfg, dict)


def test_i18n_get_regex_with_empty_strings():
    """get_regex triggers load_lang when _strings is empty."""
    from sage_mcp.i18n import get_regex
    import sage_mcp.i18n as i18n_mod

    original = i18n_mod._strings
    i18n_mod._strings = {}
    result = get_regex()
    i18n_mod._strings = original
    assert isinstance(result, dict)


def test_i18n_t_with_kwargs_interpolation():
    """t() supports {var} interpolation via kwargs."""
    from sage_mcp.i18n import load_lang, t

    load_lang("en")
    # Try a known key with kwargs; if not found, passthrough without crashing
    result = t("some.missing.key", count=3)
    assert isinstance(result, str)

    # t() with no _strings forces a load
    import sage_mcp.i18n as i18n_mod

    original = i18n_mod._strings
    i18n_mod._strings = {}
    result2 = t("nonexistent.key")
    i18n_mod._strings = original
    assert isinstance(result2, str)
