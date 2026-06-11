"""Tests for sage_mcp.cli_format — the CLI search renderers (ADR-0073).

These cover the None-safety and formatting obligations inherited from the
retired print-path search() suite: render_full / render_terse must tolerate
missing keys and None text without crashing, and emit the expected shape.
"""

from sage_mcp.cli_format import render_full, render_terse


def _hit(**over):
    base = {
        "drawer_id": "drw_abc",
        "text": "line one\nline two",
        "wing": "proj",
        "room": "backend",
        "source_file": "auth.py",
        "similarity": 0.91,
        "strength": 0.5,
        "matched_via": "drawer+closet",
        "agents": ["aidev-keeper"],
    }
    base.update(over)
    return base


# ── render_full ─────────────────────────────────────────────────────────


def test_render_full_emits_hit_block(capsys):
    render_full({"results": [_hit()]}, query="auth", wing="proj", room="backend")
    out = capsys.readouterr().out
    assert 'Results for: "auth"' in out
    assert "Wing: proj" in out and "Room: backend" in out
    assert "[1] proj / backend" in out
    assert "Source: auth.py" in out
    assert "cosine=0.91" in out and "strength=0.5" in out and "via=drawer+closet" in out
    assert "line one" in out and "line two" in out


def test_render_full_empty_results(capsys):
    render_full({"results": []}, query="nothing")
    out = capsys.readouterr().out
    assert 'No results found for: "nothing"' in out


def test_render_full_shows_vector_disabled_banner(capsys):
    render_full(
        {"results": [], "vector_disabled": True, "vector_disabled_reason": "hnsw diverged"},
        query="q",
    )
    out = capsys.readouterr().out
    assert "BM25 fallback" in out and "hnsw diverged" in out


def test_render_full_tolerates_none_text_and_missing_keys(capsys):
    # A hit missing every optional key and with None text must not crash.
    render_full({"results": [{"text": None}]}, query="q")
    out = capsys.readouterr().out
    assert "[1] ? / ?" in out
    assert "Source: ?" in out
    assert "via=drawer" in out  # default matched_via


def test_render_full_missing_results_key(capsys):
    # No "results" key at all → treated as empty, no crash.
    render_full({}, query="q")
    out = capsys.readouterr().out
    assert 'No results found for: "q"' in out


# ── render_terse ────────────────────────────────────────────────────────


def test_render_terse_emits_drawer_line(capsys):
    render_terse({"results": [_hit()]}, query="auth", agent="aidev-keeper", wing="proj")
    out = capsys.readouterr().out
    assert "1 drawer(s) for 'auth'" in out
    assert "agent=aidev-keeper" in out and "wing=proj" in out
    assert "[drw_abc]" in out
    assert "agents=['aidev-keeper']" in out
    assert "line one" in out


def test_render_terse_empty(capsys):
    render_terse({"results": []}, query="zzz", agent="x")
    out = capsys.readouterr().out
    assert "No drawers matched 'zzz' tagged with agent='x'." in out


def test_render_terse_tolerates_none_text_and_missing_keys(capsys):
    render_terse({"results": [{}]}, query="q")
    out = capsys.readouterr().out
    assert "(unknown id)" in out
    assert "agents=[]" in out


def test_render_terse_truncates_preview_to_200(capsys):
    long = "x" * 500
    render_terse({"results": [_hit(text=long, drawer_id="d1", agents=[])]}, query="q")
    out = capsys.readouterr().out
    assert "x" * 200 in out
    assert "x" * 201 not in out


def test_render_full_bm25_hit_shows_bm25_only_not_cosine_none(capsys):
    # BM25-fallback hits carry similarity=None / strength absent; must not print
    # the literal 'cosine=None' (regression for the vector-disabled path).
    hit = {
        "text": "t",
        "wing": "w",
        "room": "r",
        "source_file": "f.py",
        "similarity": None,
        "matched_via": "bm25_sqlite",
    }
    render_full(
        {"results": [hit], "vector_disabled": True, "vector_disabled_reason": "diverged"},
        query="q",
    )
    out = capsys.readouterr().out
    assert "cosine=None" not in out
    assert "bm25-only" in out
    assert "via=bm25_sqlite" in out
    assert "strength=?" in out


def test_render_full_shows_metric_warning_banner(capsys):
    render_full(
        {"results": [], "metric_warning": "nook created without cosine distance (hnsw:space='l2')"},
        query="q",
    )
    out = capsys.readouterr().out
    assert "NOTICE:" in out
    assert "cosine distance" in out
