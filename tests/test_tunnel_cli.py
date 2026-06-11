"""Smoke tests for the `sage tunnel` CLI subcommand.

Covers the parse paths + the dispatch into mcp_server. Hermetic — uses
mocked MCP tool responses so we don't depend on a real nook.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from sage_mcp.cli import cmd_tunnel


def _args(**kwargs):
    base = {"nook": None, "tunnel_command": None, "wing": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _both(capsys):
    """Return out+err combined; pytest's capture sometimes routes
    print() into err when other modules touched stderr first."""
    cap = capsys.readouterr()
    return cap.out + cap.err


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_list_empty(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with patch("sage_mcp.mcp_server.tool_list_tunnels", return_value=[]):
        cmd_tunnel(_args(tunnel_command="list"))
    assert "No tunnels registered" in _both(capsys)


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_list_renders_entries(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    fake_tunnels = [
        {
            "id": "tun_abc",
            "source": {"wing": "sage", "room": "decisions"},
            "target": {"wing": "Acme-Ops.V3", "room": "audits"},
            "label": "auditor pattern shared",
        }
    ]
    with patch("sage_mcp.mcp_server.tool_list_tunnels", return_value=fake_tunnels):
        cmd_tunnel(_args(tunnel_command="list"))
    text = _both(capsys)
    assert "tun_abc" in text
    assert "sage/decisions" in text
    assert "Acme-Ops.V3/audits" in text
    assert "auditor pattern shared" in text


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_list_wing_filter_passed_through(mock_cfg):
    mock_cfg.return_value.nook_path = "/fake"
    with patch("sage_mcp.mcp_server.tool_list_tunnels") as mock_list:
        mock_list.return_value = []
        cmd_tunnel(_args(tunnel_command="list", wing="sage"))
        mock_list.assert_called_once_with(wing="sage")


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_create_success(mock_cfg, capsys):
    """tool_create_tunnel returns the stored tunnel dict (no "success" key) on
    success — see nook_graph.create_tunnel. The CLI must render the tunnel
    id from "id" (with "tunnel_id" as legacy fallback), not gate on "success".
    Pre-fix this test mocked the wrong shape and masked the live bug."""
    mock_cfg.return_value.nook_path = "/fake"
    with patch("sage_mcp.mcp_server.tool_create_tunnel") as mock_create:
        mock_create.return_value = {
            "id": "tun_xyz",
            "source": {"wing": "sage", "room": "decisions"},
            "target": {"wing": "Acme-Ops.V3", "room": "audits"},
            "label": "",
            "kind": "manual",
            "created_at": "2026-05-26T10:00:00Z",
        }
        cmd_tunnel(
            _args(
                tunnel_command="create",
                source_wing="sage",
                source_room="decisions",
                target_wing="Acme-Ops.V3",
                target_room="audits",
                label=None,
            )
        )
    mock_create.assert_called_once_with(
        source_wing="sage",
        source_room="decisions",
        target_wing="Acme-Ops.V3",
        target_room="audits",
        label="",
    )
    out = capsys.readouterr().out
    assert "tun_xyz" in out
    assert "sage/decisions" in out


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_create_failure_exits_nonzero(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with patch("sage_mcp.mcp_server.tool_create_tunnel") as mock_create:
        mock_create.return_value = {"error": "wing not registered"}
        with pytest.raises(SystemExit) as exc:
            cmd_tunnel(
                _args(
                    tunnel_command="create",
                    source_wing="not-a-wing",
                    source_room="x",
                    target_wing="sage",
                    target_room="y",
                    label=None,
                )
            )
    assert exc.value.code == 1
    assert "wing not registered" in capsys.readouterr().err


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_delete_success(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with patch("sage_mcp.mcp_server.tool_delete_tunnel", return_value={"deleted": "tun_x"}):
        cmd_tunnel(_args(tunnel_command="delete", tunnel_id="tun_x"))
    assert "Deleted tunnel tun_x" in capsys.readouterr().out


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_missing_subcommand_exits(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with pytest.raises(SystemExit) as exc:
        cmd_tunnel(_args(tunnel_command=None))
    assert exc.value.code == 2
    assert "subcommand required" in capsys.readouterr().err


# ── follow / find subcommands (Pass 3 Cat 15 F2/F3 regression coverage) ──


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_follow_renders_list(mock_cfg, capsys):
    """tool_follow_tunnels returns a list of connection dicts. Pre-fix the CLI
    treated it as a dict and AttributeError'd on any non-empty result."""
    mock_cfg.return_value.nook_path = "/fake"
    fake_followed = [
        {"wing": "Acme-Ops.V3", "room": "audits", "via": "tun_abc"},
        {"wing": "Team-Wiki", "room": "decisions", "via": "tun_def"},
    ]
    with patch("sage_mcp.mcp_server.tool_follow_tunnels", return_value=fake_followed):
        cmd_tunnel(_args(tunnel_command="follow", wing="sage", room="decisions"))
    out = _both(capsys)
    assert "Connected rooms from sage/decisions" in out
    assert "Acme-Ops.V3" in out
    assert "Team-Wiki" in out


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_follow_empty_list_no_crash(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with patch("sage_mcp.mcp_server.tool_follow_tunnels", return_value=[]):
        cmd_tunnel(_args(tunnel_command="follow", wing="sage", room="decisions"))
    assert "No connected rooms" in _both(capsys)


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_follow_error_dict_exits_1(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with patch(
        "sage_mcp.mcp_server.tool_follow_tunnels",
        return_value={"error": "wing must be a non-empty string"},
    ):
        with pytest.raises(SystemExit) as exc:
            cmd_tunnel(_args(tunnel_command="follow", wing="", room="x"))
    assert exc.value.code == 1
    assert "wing must be a non-empty string" in capsys.readouterr().err


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_find_renders_list(mock_cfg, capsys):
    """tool_find_tunnels returns a list of bridge dicts on success."""
    mock_cfg.return_value.nook_path = "/fake"
    fake = [
        {"room": "shared-pattern", "wings": ["sage", "Acme-Ops.V3"]},
    ]
    with patch("sage_mcp.mcp_server.tool_find_tunnels", return_value=fake):
        cmd_tunnel(_args(tunnel_command="find", wing_a="sage", wing_b="Acme-Ops.V3"))
    out = _both(capsys)
    assert "candidate bridge" in out
    assert "shared-pattern" in out


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_find_empty_list_no_crash(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with patch("sage_mcp.mcp_server.tool_find_tunnels", return_value=[]):
        cmd_tunnel(_args(tunnel_command="find", wing_a="a", wing_b="b"))
    assert "No candidate tunnel-worthy bridges" in _both(capsys)


@patch("sage_mcp.cli.SageConfig")
def test_tunnel_find_no_nook_dict_exits_1(mock_cfg, capsys):
    mock_cfg.return_value.nook_path = "/fake"
    with patch(
        "sage_mcp.mcp_server.tool_find_tunnels",
        return_value={"error": "No nook found", "hint": "Run: sage init <dir>"},
    ):
        with pytest.raises(SystemExit) as exc:
            cmd_tunnel(_args(tunnel_command="find", wing_a="a", wing_b="b"))
    assert exc.value.code == 1
    assert "No nook found" in capsys.readouterr().err
