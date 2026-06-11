"""Phase 6 end-to-end bootstrap acceptance test.

Plan steps 4-7 run against the User's actual ``~/.sage/`` and
their actual destination repos, which would create real nook data
this test must not. Instead this exercises the same code paths in a
fully isolated tmp environment so the acceptance criteria are proven
green by CI without ever touching the User's machine state:

  4. ``sage init`` produces a clean ``~/.sage/``
     directory.       → assert files created
  5. Mine the first wing (the sage project itself).
     → mine a small fixture, assert drawers present
  6. ``recall`` returns sensible drawers.
     → recall a known token, assert it surfaces
  7. The agent-keyed filter works end-to-end.
     → write a drawer with agents=[X], assert recall --agent X
       finds only it

The fixture project lives entirely inside ``tmp_path`` so we never
walk the real sage tree; the registered wing is ``sage``
(present in the test wing_config.json) so the Phase 4 gate accepts
the writes.
"""

from __future__ import annotations

import importlib
import sys

import pytest


# Stop hook tests live in their own module; the bootstrap test only
# touches mining + recall + add_drawer / search.


@pytest.fixture
def isolated_nook(tmp_path, monkeypatch):
    """Tmp HOME + tmp config dir + tmp nook dir + cached config reset."""
    # Redirect HOME so SageConfig defaults to <tmp>/.sage.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("SAGE_NOOK_PATH", raising=False)
    monkeypatch.delenv("SAGE_NOOK_PATH", raising=False)

    # Reload modules so they pick up the new HOME.
    for modname in ["sage_mcp.config", "sage_mcp.mcp_server"]:
        if modname in sys.modules:
            importlib.reload(sys.modules[modname])

    # The wing_registry test config (set in conftest.py) registers all
    # the wings the test suite uses; nothing further to do for the gate.
    return {"home": tmp_path}


def test_init_creates_expected_dir_structure(isolated_nook, monkeypatch):
    """Plan step 4: sage init produces a clean ~/.sage/
    with all five files/dirs the plan documents."""
    from pathlib import Path
    from sage_mcp.config import SageConfig

    cfg = SageConfig()  # picks up new HOME → ~/.sage
    cfg.init()

    sp_dir = isolated_nook["home"] / ".sage"
    assert sp_dir.is_dir(), f"~/.sage not created at {sp_dir}"

    # All five plan-promised outputs must be present.
    assert (sp_dir / "config.json").is_file(), "config.json missing"
    assert (sp_dir / "wing_config.json").is_file(), (
        "wing_config.json was not copied from the repo template by init"
    )
    assert (sp_dir / "identity.txt").is_file(), "identity.txt stub missing after init"
    assert (sp_dir / "kg.sqlite").is_file(), "kg.sqlite was not created by init"
    # nook data dir — the path comes from config.json
    nook_dir = Path(cfg.nook_path).expanduser()
    assert nook_dir.is_dir(), f"nook data dir not created at {nook_dir}"

    # The copied wing config carries the wing-type TAXONOMY plus ONLY the
    # framework-generic wings (Personal for WI-5 user-facts, telemetry for the
    # telemetry stream — both under ~/.sage). It must NOT carry the maintainer's
    # project/dev wings (codex Phase-8 HIGH fix removed those from the tracked
    # template); a fresh user registers their own via `sage bootstrap`.
    import json

    cfg_data = json.loads((sp_dir / "wing_config.json").read_text())
    assert set(cfg_data["wings"]) == {"Personal", "telemetry"}, (
        "fresh init must ship only framework-generic wings, not maintainer project wings"
    )
    assert "Acme-Ops.V3" not in cfg_data["wings"], "no maintainer dev wing leaks"
    assert "dev" in cfg_data["wing_types"], "wing-type taxonomy must be copied"
    assert len(cfg_data["wing_types"]) >= 5


def test_mine_then_recall_round_trip(isolated_nook, monkeypatch):
    """Plan steps 5-6: mine a fixture project, recall a known marker."""
    from sage_mcp import mcp_server
    from sage_mcp.config import SageConfig
    from sage_mcp.miner import mine

    cfg = SageConfig()
    cfg.init()
    nook_path = cfg.nook_path

    # Tiny fixture project — single markdown file with a distinctive marker.
    fixture = isolated_nook["home"] / "fixture_project"
    fixture.mkdir()
    (fixture / "README.md").write_text(
        "# Fixture\n\n"
        "Unique marker WAYFINDING-ALPHA for the Phase 6 end-to-end test. "
        "This drawer should be discoverable via nook_search.\n",
        encoding="utf-8",
    )

    mine(
        project_dir=str(fixture),
        nook_path=nook_path,
        wing_override="sage",
        agent="phase6-e2e",
        respect_gitignore=False,
    )

    # Patch the MCP server's _config to point at the same nook.
    monkeypatch.setattr(mcp_server, "_config", cfg)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **k: None)

    result = mcp_server.tool_search(
        query="WAYFINDING-ALPHA fixture marker",
        wing="sage",
        max_distance=0.0,
        limit=10,
    )
    hits = result.get("results") or []
    assert any("WAYFINDING-ALPHA" in (h.get("text") or "") for h in hits), (
        f"expected the marker to surface; got: {hits}"
    )


def test_agent_keyed_filter_end_to_end(isolated_nook, monkeypatch):
    """Plan step 7: tag a drawer via add_drawer agents=[X]; recall --agent
    X finds it; recall --agent unknown returns nothing."""
    from sage_mcp import mcp_server
    from sage_mcp.config import SageConfig

    cfg = SageConfig()
    cfg.init()
    monkeypatch.setattr(mcp_server, "_config", cfg)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **k: None)

    # File a drawer with explicit agents tag.
    add_result = mcp_server.tool_add_drawer(
        wing="sage",
        room="facts",
        content=(
            "Phase 6 E2E marker NEPHELIBATA. Reviewing aidev-code-reviewer.md "
            "for the missing input-validation slot."
        ),
        agents=["aidev-code-reviewer"],
    )
    assert add_result["success"] is True, add_result

    # Recall with matching agent — should surface.
    matching = mcp_server.tool_search(
        query="phase 6 nephelibata input-validation",
        wing="sage",
        agents=["aidev-code-reviewer"],
        max_distance=0.0,
        limit=10,
    )
    hits = matching.get("results") or []
    assert any("NEPHELIBATA" in (h.get("text") or "") for h in hits), (
        f"agent-keyed recall missed the tagged drawer: {hits}"
    )

    # Recall with unknown agent — should NOT surface.
    other = mcp_server.tool_search(
        query="phase 6 nephelibata",
        wing="sage",
        agents=["aidev-no-such-agent"],
        max_distance=0.0,
        limit=10,
    )
    other_hits = other.get("results") or []
    assert all("NEPHELIBATA" not in (h.get("text") or "") for h in other_hits), (
        f"agent filter leaked: {other_hits}"
    )
