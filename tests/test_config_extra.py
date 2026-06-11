"""Extra tests for sage.config to cover remaining gaps."""

import json
import os

from sage_mcp.config import SageConfig


def test_config_bad_json(tmp_path):
    """Bad JSON in config file falls back to empty."""
    (tmp_path / "config.json").write_text("not json", encoding="utf-8")
    cfg = SageConfig(config_dir=str(tmp_path))
    assert cfg.nook_path  # still returns default


def test_people_map_from_file(tmp_path):
    (tmp_path / "people_map.json").write_text(json.dumps({"bob": "Robert"}), encoding="utf-8")
    cfg = SageConfig(config_dir=str(tmp_path))
    assert cfg.people_map == {"bob": "Robert"}


def test_people_map_bad_json(tmp_path):
    (tmp_path / "people_map.json").write_text("bad", encoding="utf-8")
    cfg = SageConfig(config_dir=str(tmp_path))
    assert cfg.people_map == {}


def test_people_map_missing(tmp_path):
    cfg = SageConfig(config_dir=str(tmp_path))
    assert cfg.people_map == {}


def test_topic_wings_default(tmp_path):
    cfg = SageConfig(config_dir=str(tmp_path))
    assert isinstance(cfg.topic_wings, list)
    assert "emotions" in cfg.topic_wings


def test_hall_keywords_default(tmp_path):
    cfg = SageConfig(config_dir=str(tmp_path))
    assert isinstance(cfg.hall_keywords, dict)
    assert "technical" in cfg.hall_keywords


def test_decisions_hall_keywords_are_high_signal_token_safe():
    """ADR-0068 governance hall must not misroute non-governance content.

    detect_hall scores with raw `kw in content_lower` substring checks, so the
    decisions-hall markers must be (a) governance-specific (no generic tokens
    like 'decision'/'finding'/'status' that appear in handoffs/bugs/specs) and
    (b) substring-safe for short markers ('adr-' not bare 'adr', which would
    match 'quadratic'/'cadre'/'adrenaline'). Locks codex MED1/MED2.
    """
    from sage_mcp.config import DEFAULT_HALL_KEYWORDS

    decisions = DEFAULT_HALL_KEYWORDS["decisions"]
    # No bare "adr" (substring-unsafe); the citation form "adr-" is allowed.
    assert "adr" not in decisions
    # No generic tokens that collide with ordinary engineering prose.
    for generic in ("decision", "verdict", "finding", "accepted", "consequences", "status"):
        assert generic not in decisions, (
            f"generic token {generic!r} would misroute non-governance content"
        )
    # Substring false-positive corpus must match NO decisions marker.
    false_positive = (
        "solving quadratic equations boosted adrenaline; the cadre reached a "
        "decision with status accepted and a finding."
    ).lower()
    assert not any(kw in false_positive for kw in decisions), (
        "decisions markers false-match non-governance text"
    )
    # A real ADR citation must match at least one marker.
    governance = "adr-0076 supersedes adr-0033; deciders: arbiter."
    assert any(kw in governance for kw in decisions)


def test_init_idempotent(tmp_path):
    cfg = SageConfig(config_dir=str(tmp_path))
    cfg.init()
    cfg.init()  # second call should not overwrite
    with open(tmp_path / "config.json") as f:
        data = json.load(f)
    assert "nook_path" in data


def test_save_people_map(tmp_path):
    cfg = SageConfig(config_dir=str(tmp_path))
    result = cfg.save_people_map({"alice": "Alice Smith"})
    assert result.exists()
    with open(result) as f:
        data = json.load(f)
    assert data["alice"] == "Alice Smith"


def test_env_sage_path(tmp_path):
    """SAGE_NOOK_PATH (env-var path) should also work."""
    os.environ.pop("SAGE_NOOK_PATH", None)
    raw = "/path/from/env"
    os.environ["SAGE_NOOK_PATH"] = raw
    try:
        cfg = SageConfig(config_dir=str(tmp_path))
        # nook_path is normalized via abspath + expanduser — compare
        # against the normalized form so the test is portable between
        # POSIX (no-op) and Windows (prepends current drive letter).
        assert cfg.nook_path == os.path.abspath(os.path.expanduser(raw))
    finally:
        del os.environ["SAGE_NOOK_PATH"]


def test_collection_name_from_config(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"collection_name": "custom_col"}), encoding="utf-8"
    )
    cfg = SageConfig(config_dir=str(tmp_path))
    assert cfg.collection_name == "custom_col"
