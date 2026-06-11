"""Tests for sage_mcp.telemetry — append-only JSONL turn log."""

from __future__ import annotations

import json

from sage_mcp.telemetry import (
    TurnRecord,
    log_from_verdict,
    log_turn,
    new_turn_id,
    read_recent,
)
from sage_mcp.verdict_parser import parse_verdict


def _redirect_log(tmp_path, monkeypatch):
    target = tmp_path / "turns.jsonl"
    monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(target))
    return target


class TestLogTurn:
    def test_appends_jsonl_line(self, tmp_path, monkeypatch):
        target = _redirect_log(tmp_path, monkeypatch)
        rec = TurnRecord(
            turn_id="abc",
            timestamp="2026-05-25T12:00:00Z",
            phase="audit",
            mode="aidev",
            agent="aidev-code-reviewer",
            verdict="APPROVE",
        )
        assert log_turn(rec) is True
        text = target.read_text()
        assert text.endswith("\n")
        row = json.loads(text.strip())
        assert row["turn_id"] == "abc"
        assert row["verdict"] == "APPROVE"

    def test_multiple_turns_append(self, tmp_path, monkeypatch):
        target = _redirect_log(tmp_path, monkeypatch)
        for i in range(3):
            log_turn(
                TurnRecord(
                    turn_id=f"t{i}",
                    timestamp="2026-05-25T12:00:00Z",
                    phase="audit",
                    mode="aidev",
                    agent="x",
                )
            )
        lines = target.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_io_failure_swallowed(self, tmp_path, monkeypatch):
        # Point at a path under a file (not a directory) — mkdir + write fail.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(blocker / "x.jsonl"))
        rec = TurnRecord(
            turn_id="z",
            timestamp="2026-05-25T12:00:00Z",
            phase="audit",
            mode="aidev",
            agent="x",
        )
        assert log_turn(rec) is False  # but no exception


class TestLogFromVerdict:
    def test_pulls_lane_and_severity_top(self, tmp_path, monkeypatch):
        target = _redirect_log(tmp_path, monkeypatch)
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: REQUEST_CHANGES\n"
            "lane: aidev-code-reviewer\n"
            "report: none\n"
            "findings: 2\n"
            "@@FINDING 1\n"
            "severity: 90\n"
            "file: x.py\n"
            "line: 1\n"
            "category: manifest\n"
            "summary: a\n"
            "@@FINDING 2\n"
            "severity: 40\n"
            "file: y.py\n"
            "line: 2\n"
            "category: lane\n"
            "summary: b\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert v.valid
        tid = log_from_verdict(v, phase="audit", mode="aidev")
        assert tid is not None
        row = json.loads(target.read_text().strip())
        assert row["agent"] == "aidev-code-reviewer"
        assert row["severity_top"] == 90
        assert row["findings_count"] == 2
        assert row["verdict"] == "REQUEST_CHANGES"

    def test_shared_turn_id_across_paired_auditors(self, tmp_path, monkeypatch):
        _redirect_log(tmp_path, monkeypatch)
        shared = new_turn_id()
        a = parse_verdict(
            "@@VERDICT BEGIN\nverdict: APPROVE\nlane: aidev-code-reviewer\n"
            "report: none\nfindings: 0\n@@VERDICT END\n"
        )
        b = parse_verdict(
            "@@VERDICT BEGIN\nverdict: APPROVE\nlane: aidev-adversarial-auditor\n"
            "report: none\nfindings: 0\n@@VERDICT END\n"
        )
        ta = log_from_verdict(a, phase="audit", mode="aidev", turn_id=shared)
        tb = log_from_verdict(b, phase="audit", mode="aidev", turn_id=shared)
        assert ta == tb == shared
        rows = read_recent()
        assert len({r["turn_id"] for r in rows}) == 1
        assert {r["agent"] for r in rows} == {
            "aidev-code-reviewer",
            "aidev-adversarial-auditor",
        }


class TestReadRecent:
    def test_missing_log_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(tmp_path / "nope.jsonl"))
        assert read_recent() == []

    def test_limit_tails(self, tmp_path, monkeypatch):
        _redirect_log(tmp_path, monkeypatch)
        for i in range(5):
            log_turn(
                TurnRecord(
                    turn_id=f"t{i}",
                    timestamp="2026-05-25T12:00:00Z",
                    phase="audit",
                    mode="aidev",
                    agent="x",
                )
            )
        rows = read_recent(limit=3)
        assert [r["turn_id"] for r in rows] == ["t2", "t3", "t4"]

    def test_corrupted_line_skipped(self, tmp_path, monkeypatch):
        target = _redirect_log(tmp_path, monkeypatch)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '{"turn_id":"good","timestamp":"x","phase":"audit","mode":"aidev","agent":"x"}\n'
            "not-json\n"
            '{"turn_id":"good2","timestamp":"x","phase":"audit","mode":"aidev","agent":"x"}\n'
        )
        rows = read_recent()
        assert [r["turn_id"] for r in rows] == ["good", "good2"]


class TestPermissions:
    """ADR-0077: telemetry rows carry verbatim verdict text — owner-only on disk."""

    def test_log_turn_creates_0600_file(self, tmp_path, monkeypatch):
        target = _redirect_log(tmp_path, monkeypatch)
        log_turn(
            TurnRecord(
                turn_id="perm1",
                timestamp="2026-05-31T12:00:00Z",
                phase="audit",
                mode="aidev",
                agent="aidev-code-reviewer",
            )
        )
        mode = target.stat().st_mode & 0o777
        assert mode == 0o600, f"telemetry log is {oct(mode)}, expected 0o600 (ADR-0077)"

    def test_log_turn_hardens_existing_world_readable_file(self, tmp_path, monkeypatch):
        target = _redirect_log(tmp_path, monkeypatch)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        target.chmod(0o644)  # simulate a pre-existing umask-default file
        log_turn(
            TurnRecord(
                turn_id="perm2",
                timestamp="2026-05-31T12:00:00Z",
                phase="audit",
                mode="aidev",
                agent="aidev-code-reviewer",
            )
        )
        assert (target.stat().st_mode & 0o777) == 0o600
