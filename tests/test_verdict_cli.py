"""Smoke tests for the `sage verdict log` CLI subcommand."""

from __future__ import annotations

import argparse
import io
import json

import pytest

from sage_mcp.cli import cmd_verdict


def _args(**kwargs):
    base = {
        "nook": None,
        "verdict_command": None,
        "file": None,
        "phase": "audit",
        "mode": "aidev",
        "wing": None,
        "turn_id": None,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_verdict_log_well_formed_block(tmp_path, monkeypatch, capsys):
    log = tmp_path / "turns.jsonl"
    monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log))
    text = (
        "@@VERDICT BEGIN\n"
        "verdict: APPROVE\n"
        "lane: code-reviewer\n"
        "report: none\n"
        "findings: 0\n"
        "@@VERDICT END\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    cmd_verdict(_args(verdict_command="log"))
    out = capsys.readouterr().out
    assert "valid:       True" in out
    assert "APPROVE" in out
    row = json.loads(log.read_text().strip())
    assert row["verdict"] == "APPROVE"
    assert row["agent"] == "code-reviewer"


def test_verdict_log_prose_only_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(tmp_path / "turns.jsonl"))
    monkeypatch.setattr("sys.stdin", io.StringIO("VERDICT: APPROVE. all clean"))
    with pytest.raises(SystemExit) as exc:
        cmd_verdict(_args(verdict_command="log"))
    assert exc.value.code == 1


def test_verdict_log_hold_exits_nonzero(tmp_path, monkeypatch):
    log = tmp_path / "turns.jsonl"
    monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(log))
    text = (
        "@@VERDICT BEGIN\n"
        "verdict: HOLD\n"
        "lane: aidev-state-reviewer\n"
        "report: none\n"
        "findings: 1\n"
        "@@FINDING 1\n"
        "severity: 50\n"
        "file: n/a\n"
        "line: 0\n"
        "category: other\n"
        "summary: prior audit report path missing\n"
        "@@VERDICT END\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    with pytest.raises(SystemExit) as exc:
        cmd_verdict(_args(verdict_command="log"))
    assert exc.value.code == 1


def test_verdict_log_missing_subcommand_exits(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_verdict(_args(verdict_command=None))
    assert exc.value.code == 2
    assert "subcommand required" in capsys.readouterr().err


def test_verdict_log_reads_from_file(tmp_path, monkeypatch, capsys):
    payload = tmp_path / "reply.txt"
    payload.write_text(
        "@@VERDICT BEGIN\n"
        "verdict: APPROVE\n"
        "lane: test-engineer\n"
        "report: none\n"
        "findings: 0\n"
        "@@VERDICT END\n"
    )
    monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(tmp_path / "turns.jsonl"))
    cmd_verdict(_args(verdict_command="log", file=str(payload)))
    out = capsys.readouterr().out
    assert "test-engineer" in out
    assert "APPROVE" in out


def test_verdict_log_file_read_oserror_exits(tmp_path, monkeypatch):
    """cmd_verdict exits nonzero when the named file cannot be read (OSError path).

    Covers cli.py:1267-1269. Finding [22]/[20] from pycore audit cluster.
    """
    nonexistent = str(tmp_path / "does_not_exist.txt")
    with pytest.raises(SystemExit) as exc:
        cmd_verdict(_args(verdict_command="log", file=nonexistent))
    assert exc.value.code == 1


def test_verdict_log_abort_verdict_exits_nonzero(tmp_path, monkeypatch, capsys):
    """A valid ABORT verdict block makes cmd_verdict exit nonzero.

    Covers cli.py:1310 ABORT branch (HOLD is already tested).
    Finding [20] from pycore audit cluster.
    """
    monkeypatch.setenv("SAGE_TELEMETRY_PATH", str(tmp_path / "turns.jsonl"))
    abort_text = (
        "@@VERDICT BEGIN\n"
        "verdict: ABORT\n"
        "lane: aidev-adversarial-auditor\n"
        "report: none\n"
        "findings: 1\n"
        "@@FINDING 1\n"
        "severity: 100\n"
        "file: n/a\n"
        "line: 0\n"
        "category: other\n"
        "summary: catastrophic defect requiring immediate abort\n"
        "@@VERDICT END\n"
    )
    payload = tmp_path / "abort.txt"
    payload.write_text(abort_text)
    with pytest.raises(SystemExit) as exc:
        cmd_verdict(_args(verdict_command="log", file=str(payload)))
    assert exc.value.code == 1
