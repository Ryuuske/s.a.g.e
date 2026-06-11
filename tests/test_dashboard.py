"""Tests for the Phase-9 governance + store-health dashboard."""

from __future__ import annotations

from sage_mcp import dashboard as dash


def _audit_row(agent, verdict, turn_id, sev=0, findings=0):
    return {
        "phase": "audit",
        "mode": "aidev",
        "agent": agent,
        "verdict": verdict,
        "turn_id": turn_id,
        "severity_top": sev,
        "findings_count": findings,
    }


# ── collect_governance ───────────────────────────────────────────────────────


def test_collect_governance_counts_verdicts_and_lanes():
    rows = [
        _audit_row("aidev-code-reviewer", "APPROVE", "t1"),
        _audit_row("aidev-adversarial-auditor", "APPROVE", "t1"),
        _audit_row("aidev-code-reviewer", "REQUEST_CHANGES", "t2", sev=85, findings=2),
        {"phase": "implement", "agent": "orchestrator", "verdict": None},  # non-audit ignored
    ]
    g = dash.collect_governance(rows)
    assert g["total_rows"] == 4
    assert g["audit_rows"] == 3
    assert g["total_verdicts"] == 3
    assert g["by_verdict"] == {"APPROVE": 2, "REQUEST_CHANGES": 1}
    assert g["by_lane"]["aidev-code-reviewer"] == {"APPROVE": 1, "REQUEST_CHANGES": 1}
    assert g["blocking_findings"] == 1  # the sev=85 row
    assert abs(g["approve_rate"] - (2 / 3)) < 1e-9
    assert abs(g["blocking_rate"] - (1 / 3)) < 1e-9


def test_collect_governance_detects_disagreement():
    rows = [
        _audit_row("aidev-code-reviewer", "APPROVE", "tX"),
        _audit_row("aidev-adversarial-auditor", "REQUEST_CHANGES", "tX", sev=90),
    ]
    g = dash.collect_governance(rows)
    assert g["paired_turns"] == 1
    assert g["disagreements"] == 1  # same turn, differing verdicts


def test_collect_governance_empty():
    g = dash.collect_governance([])
    assert g["total_verdicts"] == 0
    assert g["approve_rate"] is None
    assert g["blocking_rate"] is None
    assert g["by_verdict"] == {}
    assert g["disagreements"] == 0


def test_collect_governance_same_lane_retry_is_not_a_pair():
    """A same-lane retry pinning the same turn_id must NOT count as a paired
    auditor or a disagreement (codex Phase-9 HIGH): only DISTINCT lanes pair."""
    rows = [
        _audit_row("aidev-code-reviewer", "APPROVE", "tDup"),
        _audit_row("aidev-code-reviewer", "REQUEST_CHANGES", "tDup", sev=85),  # same lane, retry
    ]
    g = dash.collect_governance(rows)
    assert g["paired_turns"] == 0, "one lane on one turn is not a pair"
    assert g["disagreements"] == 0, "a same-lane retry is not an auditor disagreement"


def test_collect_governance_handles_missing_fields():
    # A row with no agent / no severity must not crash.
    g = dash.collect_governance([{"phase": "audit", "verdict": "APPROVE", "turn_id": "t"}])
    assert g["total_verdicts"] == 1
    assert "unknown" in g["by_lane"]


def test_collect_governance_tolerates_malformed_severity():
    """A schema-drifted severity_top ('high', '85.5', null) degrades that row,
    not the whole dashboard (codex Phase-9)."""
    rows = [
        _audit_row("aidev-code-reviewer", "APPROVE", "t1", sev="high"),  # garbage
        _audit_row("aidev-adversarial-auditor", "REJECT", "t2", sev="85.5"),  # float-string → 85
        {
            "phase": "audit",
            "agent": "x",
            "verdict": "APPROVE",
            "turn_id": "t3",
            "severity_top": None,
        },
    ]
    g = dash.collect_governance(rows)
    assert g["total_verdicts"] == 3
    assert g["blocking_findings"] == 1  # only the "85.5" → 85 row counts as blocking


# ── collect_store_health ─────────────────────────────────────────────────────


def test_collect_store_health_missing_dir_degrades(tmp_path):
    # State A: nook dir absent → guarded helper returns None, dashboard degrades.
    out = dash.collect_store_health(str(tmp_path / "no_such_nook"), "nook_drawers")
    assert out["available"] is False
    assert "reason" in out


def test_collect_store_health_missing_sqlite_degrades(tmp_path):
    # State B: dir present but no chroma.sqlite3 → the guarded helper rejects it
    # BEFORE opening (so a read-only health check never mutates the dir).
    nook = tmp_path / "empty_nook"
    nook.mkdir()
    out = dash.collect_store_health(str(nook), "nook_drawers")
    assert out["available"] is False
    assert "chroma.sqlite3" in out["reason"]
    # The guarded path must NOT have created chroma.sqlite3 (no mutation).
    assert not (nook / "chroma.sqlite3").exists()


def test_collect_store_health_aggregates_from_store(monkeypatch):
    """The store-read SUCCESS path via the guarded helper: room/wing/strength."""

    rows = [
        {"room": "handoff", "wing": "sage", "strength": 0.9},
        {"room": "decisions", "wing": "sage", "strength": 0.2},
        {"room": "handoff", "wing": "sage", "strength": 0.1},  # AT the floor
        {"room": "handoff", "wing": "sage", "strength": 0.05},  # BELOW the floor
    ]

    class _FakeCol:
        def count(self):
            return len(rows)

        def get(self, limit, offset, include):  # paginated signature
            return {"metadatas": rows[offset : offset + limit]}

    # Mock the GUARDED open helper (not raw chromadb) — that is the path the
    # dashboard now uses, so the test exercises real wiring.
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda nook_path, collection_name=None, out=None: _FakeCol(),
    )
    out = dash.collect_store_health("/whatever", "nook_drawers")
    assert out["available"] is True
    assert out["count"] == 4
    assert out["by_room"] == {"handoff": 3, "decisions": 1}
    assert out["by_wing"] == {"sage": 4}
    assert out["strength"]["min"] == 0.05
    assert out["strength"]["max"] == 0.9
    # ADR-0043 floor=0.1: 0.1 and 0.05 are at-or-below; only 0.05 is strictly below.
    assert out["strength"]["at_or_below_floor"] == 2
    assert out["strength"]["below_floor"] == 1


def test_collect_store_health_tolerates_none_metadata(monkeypatch):
    """Chroma can return None metadata rows (partial-flush/mid-delete) — the
    collector must normalize them like sage status, not crash (codex Phase-9)."""

    class _FakeCol:
        def count(self):
            return 3

        def get(self, limit, offset, include):
            return {
                "metadatas": [{"room": "handoff", "wing": "sage"}, None, None][
                    offset : offset + limit
                ]
            }

    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda nook_path, collection_name=None, out=None: _FakeCol(),
    )
    out = dash.collect_store_health("/whatever", "nook_drawers")
    assert out["available"] is True
    assert out["count"] == 3
    # the two None rows tally under the "?" unknown bucket (matching sage status),
    # never a literal None key.
    assert out["by_room"].get("handoff") == 1
    assert out["by_room"].get("?") == 2
    assert None not in out["by_room"]
    # '?' must survive even with many populated buckets (no top-N truncation).
    assert "?" in out["by_room"]
    assert dash.render(
        {
            **dash.collect_governance([]),
        },
        out,
    )  # renders without "None=" crash
    assert "None=" not in dash.render(dash.collect_governance([]), out)


def test_collect_store_health_unknown_bucket_survives_many_rooms(monkeypatch):
    """With >12 populated rooms + one malformed row, the '?' bucket must still be
    present and rendered (codex Phase-9: no top-N truncation drops it)."""
    rows = [{"room": f"room{i}", "wing": "sage", "strength": 0.9} for i in range(20)]
    rows.append(None)  # one malformed → '?'

    class _FakeCol:
        def count(self):
            return len(rows)

        def get(self, limit, offset, include):
            return {"metadatas": rows[offset : offset + limit]}

    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda nook_path, collection_name=None, out=None: _FakeCol(),
    )
    out = dash.collect_store_health("/whatever", "nook_drawers")
    assert "?" in out["by_room"], "the '?' bucket must not be truncated away"
    assert len(out["by_room"]) == 21  # 20 rooms + '?'
    text = dash.render(dash.collect_governance([]), out)
    assert "?=1" in text


# ── render ───────────────────────────────────────────────────────────────────


def test_render_with_signals_and_store():
    governance = dash.collect_governance(
        [
            _audit_row("aidev-code-reviewer", "APPROVE", "t1"),
            _audit_row("aidev-adversarial-auditor", "REQUEST_CHANGES", "t1", sev=85),
        ]
    )
    store = {
        "available": True,
        "count": 100,
        "by_room": {"handoff": 60, "decisions": 40},
        "by_wing": {"sage": 100},
        "strength": {
            "min": 0.2,
            "max": 0.99,
            "mean": 0.8,
            "at_or_below_floor": 0,
            "below_floor": 0,
        },
    }
    text = dash.render(governance, store)
    assert "GOVERNANCE" in text and "STORE HEALTH" in text
    assert "APPROVE=1" in text and "REQUEST_CHANGES=1" in text
    assert "blocking findings (sev>=80): 1 (50% of verdicts)" in text  # blocking RATE rendered
    assert "drawers: 100" in text
    assert "handoff=60" in text
    assert "sage=100" in text
    assert "min=0.2" in text
    assert "at-or-below-floor(0.1)=0" in text


def test_render_empty_governance_and_unavailable_store():
    text = dash.render(dash.collect_governance([]), {"available": False, "reason": "no store"})
    assert "no audit verdicts logged yet" in text
    assert "store unavailable: no store" in text


# ── dashboard orchestrator ───────────────────────────────────────────────────


def test_dashboard_runs_and_returns_text(monkeypatch, capsys):
    # Mock the two data sources so the orchestrator is exercised without a live store.
    monkeypatch.setattr(
        "sage_mcp.telemetry.read_recent",
        lambda limit=1000: [_audit_row("aidev-code-reviewer", "APPROVE", "t1")],
    )
    monkeypatch.setattr(
        dash,
        "collect_store_health",
        lambda nook_path, collection_name: {"available": False, "reason": "mocked"},
    )
    text = dash.dashboard()
    out = capsys.readouterr().out
    assert "GOVERNANCE" in text
    assert text == out.rstrip("\n")
    assert "APPROVE=1" in text


def test_dashboard_cli_real_parser(monkeypatch):
    """Invoke `sage dashboard` through the REAL parser + dispatch (not a hand-built
    namespace) — proves the parsed namespace carries the global ``--nook``
    (default None) so cmd_dashboard does not raise AttributeError. Guards the
    codex Phase-9 false-positive that the subcommand lacks ``nook``."""
    import sys

    from sage_mcp import cli

    called = {}
    monkeypatch.setattr(
        "sage_mcp.dashboard.dashboard", lambda nook_path=None: called.setdefault("nook", nook_path)
    )
    monkeypatch.setattr(sys, "argv", ["sage", "dashboard"])
    cli.main()  # real parse_args + dispatch — raises AttributeError if args.nook is absent
    assert called.get("nook", "MISSING") is None
