"""Tests for the Phase-10 clean-room framework export (src/sage_mcp/export.py).

The export is the terminal deliverable's safety boundary: it must ship ONLY the
allowlist (fail closed), strip private git ancestry (distinct root), and fail the
build on any operator PII / stale-vocab token.
"""

from __future__ import annotations

import subprocess

import pytest

from sage_mcp import export as exp

# Build forbidden literals from fragments so THIS test file — which ships in the
# export (tests/ is allowlisted) — carries no verbatim forbidden token in code,
# comments, OR identifier names. The gate scans every shipped file including this
# one with no exemption, so any verbatim token here would (correctly) fail the
# real sage export. Identifier names are also kept token-free (e.g. a name like
# "_OLD_FW" would itself match the case-insensitive prior-framework pattern).
_REALNAME = exp._tok("Ah", "ti")
_OLD_FW = exp._tok("solo", "-palace")
_OLD_TOOL = exp._tok("palace_", "search")
_OLD_AGENT = exp._tok("aidev-", "librarian")
_UPSTREAM = exp._tok("Mem", "Palace")
_PRIV_WING = exp._tok("HRES", "-OPS.V3")  # split so no fragment carries the verbatim prefix


def _make_repo(root):
    """Build a minimal fixture repo with both ship and do-not-ship content."""
    # Shipped dirs
    (root / "src" / "sage_mcp").mkdir(parents=True)
    (root / "src" / "sage_mcp" / "__init__.py").write_text("# sage\n")
    (root / "agents").mkdir()
    (root / "agents" / "aidev-keeper.md").write_text("# keeper\nThe keeper agent.\n")
    (root / "skills").mkdir()
    (root / "skills" / "s.md").write_text("# skill\n")
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "docs" / "specs" / "telemetry.md").write_text("# telemetry spec\n")
    # Shipped root files
    (root / "README.md").write_text("# sage\nA memory framework.\n")
    (root / "LICENSE").write_text("MIT License\nCopyright (c) sage\n")
    (root / "pyproject.toml").write_text('[project]\nname = "sage"\nversion = "1.0.0"\n')
    # do-not-ship dirs
    (root / "docs" / "decisions").mkdir(parents=True)
    (root / "docs" / "decisions" / "0001-x.md").write_text("# ADR private\n")
    (root / "docs" / "audits").mkdir()
    (root / "docs" / "audits" / "a.md").write_text("# audit private\n")
    (root / "docs" / "handoff").mkdir()
    (root / "docs" / "handoff" / "run-log.md").write_text("# run log private\n")
    # do-not-ship root file with historical vocab
    (root / "CHANGELOG.md").write_text(f"# Changelog\n- renamed from {_OLD_FW}\n")
    # build noise inside a shipped dir
    (root / "src" / "sage_mcp" / "__pycache__").mkdir()
    (root / "src" / "sage_mcp" / "__pycache__" / "x.pyc").write_text("bytecode")


def test_export_ships_allowlist_and_excludes_do_not_ship(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    dest = tmp_path / "out"
    shipped = exp.export(repo, dest, out=lambda m: None)

    # Allowlisted content present
    assert (dest / "src" / "sage_mcp" / "__init__.py").is_file()
    assert (dest / "agents" / "aidev-keeper.md").is_file()
    assert (dest / "docs" / "specs" / "telemetry.md").is_file()
    assert (dest / "README.md").is_file()
    assert (dest / "LICENSE").is_file()

    # do-not-ship absent
    assert not (dest / "docs" / "decisions").exists()
    assert not (dest / "docs" / "audits").exists()
    assert not (dest / "docs" / "handoff").exists()
    public_cl = (dest / "CHANGELOG.md").read_text(encoding="utf-8")
    from sage_mcp.version import __version__

    assert f"[{__version__}]" in public_cl  # authored public changelog, version-pinned
    assert public_cl.count("## [") == 1  # exactly one release entry, no dev history
    assert "solo" not in public_cl.lower()

    # build noise filtered
    assert not (dest / "src" / "sage_mcp" / "__pycache__").exists()

    # Nook scaffold present, README-only (no operator data)
    assert (dest / "nook-scaffold" / "README.md").is_file()
    assert "no memory data" in (dest / "nook-scaffold" / "README.md").read_text().lower()

    assert "README.md" in shipped


def test_export_ships_public_persona_not_raw_voice_source(tmp_path):
    """Only the derived public persona artifact ships; the raw sage-profile.*
    source (carrying the unbuilt voice/TTS spec + a .docx binary) stays private."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    (repo / "docs" / "concepts").mkdir(parents=True)
    (repo / "docs" / "persona-src").mkdir(parents=True)
    (repo / "docs" / "concepts" / "sage-persona.md").write_text("# S.A.G.E.\nText discipline.\n")
    (repo / "docs" / "persona-src" / "sage-profile.md").write_text(
        "# voice spec\nElevenLabs settings\n"
    )
    (repo / "docs" / "persona-src" / "sage-profile.docx").write_bytes(b"PK\x03\x04binary")
    dest = tmp_path / "out"
    exp.export(repo, dest, out=lambda m: None)

    assert (dest / "docs" / "concepts" / "sage-persona.md").is_file()
    assert not (dest / "docs" / "persona-src" / "sage-profile.md").exists()
    assert not (dest / "docs" / "persona-src" / "sage-profile.docx").exists()


def test_export_fails_closed_on_planted_unlisted_file(tmp_path):
    """A new private file at the repo root must NOT appear in the export — the
    allowlist excludes anything it does not name (fail closed)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    (repo / "SECRET_OPERATOR_NOTES.md").write_text("private operator notes\n")
    (repo / "docs" / "research").mkdir()
    (repo / "docs" / "research" / "leak.md").write_text("research\n")

    dest = tmp_path / "out"
    exp.export(repo, dest, out=lambda m: None)

    assert not (dest / "SECRET_OPERATOR_NOTES.md").exists()
    assert not (dest / "docs" / "research").exists()


def test_export_pii_gate_fails_on_planted_token(tmp_path):
    """A planted operator-name token in a SHIPPED file fails the build closed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    # plant the operator's real name in a shipped file
    (repo / "README.md").write_text(f"# sage\nBuilt by {_REALNAME} for everyone.\n")

    dest = tmp_path / "out"
    with pytest.raises(exp.ExportPIIError) as ei:
        exp.export(repo, dest, out=lambda m: None)
    assert any(h.label == "operator real name" for h in ei.value.hits)
    assert not dest.exists()  # rejected export torn down (no artifact survives)


def test_export_pii_gate_fails_on_stale_vocab(tmp_path):
    """Residual prior-framework / MCP-prefix / prior-agent tokens fail the build."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    (repo / "agents" / "aidev-keeper.md").write_text(
        f"# keeper\nRoutes to {_OLD_AGENT} via {_OLD_TOOL} over {_OLD_FW}.\n"
    )
    dest = tmp_path / "out"
    with pytest.raises(exp.ExportPIIError) as ei:
        exp.export(repo, dest, out=lambda m: None)
    labels = {h.label for h in ei.value.hits}
    assert "stale agent name (prior librarian)" in labels
    assert "stale MCP tool prefix" in labels
    assert "stale framework name (prior project)" in labels


def test_export_mempalace_allowed_in_notice_only(tmp_path):
    """The upstream store name is allowed in NOTICE (MIT attribution) but flagged
    elsewhere."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    # NOTICE attribution — allowed
    (repo / "NOTICE").write_text(f"sage derives from {_UPSTREAM} (MIT). Thanks.\n")
    dest = tmp_path / "out"
    shipped = exp.export(repo, dest, out=lambda m: None)
    assert (dest / "NOTICE").is_file()
    assert "NOTICE" in shipped

    # Same token in a shipped README — flagged
    (repo / "README.md").write_text(f"# sage\nUses {_UPSTREAM} as the store.\n")
    with pytest.raises(exp.ExportPIIError) as ei:
        exp.export(repo, tmp_path / "out2", out=lambda m: None)
    assert any("used as store" in h.label for h in ei.value.hits)


def test_export_creates_distinct_git_root(tmp_path):
    """The export is a fresh git repo with exactly one commit and no parent —
    zero commits reachable from any private working-repo history."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    dest = tmp_path / "out"
    exp.export(repo, dest, out=lambda m: None)

    assert (dest / ".git").is_dir()
    # exactly one commit
    log = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=str(dest),
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "1"
    # the root commit has no parent (distinct root)
    parents = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=str(dest),
        capture_output=True,
        text=True,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(dest), capture_output=True, text=True, check=True
    )
    assert parents.stdout.strip() == head.stdout.strip()


def test_export_is_rerunnable(tmp_path):
    """A second export over the same dest rebuilds cleanly (idempotent)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    dest = tmp_path / "out"
    exp.export(repo, dest, out=lambda m: None)
    # plant a stray file in the old export; the rerun must remove it
    (dest / "stray.md").write_text("stale\n")
    exp.export(repo, dest, out=lambda m: None)
    assert not (dest / "stray.md").exists()
    assert (dest / "README.md").is_file()


def test_export_dest_cannot_equal_repo_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    with pytest.raises(ValueError):
        exp.export(repo, repo, out=lambda m: None)


def test_scan_pii_sibling_branding_is_case_insensitive(tmp_path):
    """The sibling-tool gate must catch title-case / all-caps branding (e.g. in a
    heading or copied README) — parity with the prior-framework check. The
    'solo-tier' repo scale-tier term must NOT be flagged."""
    root = tmp_path / "art"
    (root / "agents").mkdir(parents=True)
    # Build the title-case / all-caps planted tokens from fragments (.title()/.upper())
    # so THIS shipped test file carries no verbatim sibling-tool literal.
    title = exp._tok("Solo", "-Ops")
    caps = exp._tok("SOLO-", "STATUSLINE")
    (root / "agents" / "a.md").write_text(f"# {title} Setup\nRun the {caps} script.\n")
    (root / "agents" / "ok.md").write_text("Branch protection optional for solo-tier repos.\n")
    hits = exp.scan_pii(root)
    flagged = {h.path for h in hits}
    assert "agents/a.md" in flagged  # title-case + all-caps caught
    assert "agents/ok.md" not in flagged  # solo-tier is legitimate, not flagged


def test_scan_pii_has_no_shipped_file_exemption(tmp_path):
    """The gate must NOT exempt any shipped file. A planted token in a file that
    *looks like* the gate's own source still fails — an exemption on a shipped
    file would let the planted literals ride along while the scan reported clean."""
    root = tmp_path / "art"
    (root / "src" / "sage_mcp").mkdir(parents=True)
    (root / "src" / "sage_mcp" / "export.py").write_text(f"# {_REALNAME} {_OLD_FW}\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_export.py").write_text(f"planted {_REALNAME}\n")
    hits = exp.scan_pii(root)
    paths = {h.path for h in hits}
    assert "src/sage_mcp/export.py" in paths  # no self-exemption
    assert "tests/test_export.py" in paths


def test_scan_pii_scans_extensionless_and_binary_files(tmp_path):
    """No suffix whitelist: an extensionless script or a binary blob carrying a
    token is scanned, not skipped (a suffix gate let .docx/.Identifier through)."""
    root = tmp_path / "art"
    root.mkdir()
    (root / "claude-wakeup").write_text(
        f"#!/usr/bin/env python\nHOME = '{_REALNAME}'\n"
    )  # no suffix
    (root / "blob.docx").write_bytes(b"PK\x03\x04 ... " + _REALNAME.encode() + b" ... binary")
    hits = exp.scan_pii(root)
    paths = {h.path for h in hits}
    assert "claude-wakeup" in paths
    assert "blob.docx" in paths


def test_export_does_not_follow_symlinked_file(tmp_path):
    """A symlinked file in a ship dir must NOT be followed — copy2 would write the
    (possibly private, unlisted) target's contents into the export."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    secret = tmp_path / "outside_secret.md"
    secret.write_text(f"private {_REALNAME}\n")
    (repo / "agents" / "linky.md").symlink_to(secret)
    dest = tmp_path / "out"
    # If the symlink were followed, the gate would catch the planted token and
    # raise; instead the symlink is skipped, so the export is clean.
    exp.export(repo, dest, out=lambda m: None)
    assert not (dest / "agents" / "linky.md").exists()


def test_export_skips_symlinked_top_level_ship_dir(tmp_path):
    """A top-level SHIP_DIR that is itself a symlink to an outside dir must NOT be
    walked — os.walk follows the symlinked root even though child symlinks are
    pruned. A non-PII secret under the target must not ship (codex high: the gate
    is not a complete secret scanner, so allowlist escape ≠ caught)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    # remove the real agents dir and replace it with a symlink to an outside dir
    import shutil as _sh

    _sh.rmtree(repo / "agents")
    outside = tmp_path / "outside_private"
    outside.mkdir()
    (outside / "credentials.md").write_text("api_key=DEADBEEF not-a-pii-pattern\n")
    (repo / "agents").symlink_to(outside, target_is_directory=True)

    dest = tmp_path / "out"
    exp.export(repo, dest, out=lambda m: None)
    assert not (dest / "agents").exists()  # symlinked ship-dir root not walked
    assert not (dest / "agents" / "credentials.md").exists()


def test_export_rejects_ancestor_and_inside_dest(tmp_path):
    """dest must not be the repo, an ancestor of it (rmtree would wipe source), or
    inside it. All three raise before any deletion."""
    repo = tmp_path / "a" / "repo"
    repo.mkdir(parents=True)
    _make_repo(repo)
    with pytest.raises(ValueError):
        exp.export(repo, tmp_path / "a", out=lambda m: None)  # ancestor
    with pytest.raises(ValueError):
        exp.export(repo, repo / "sub" / "out", out=lambda m: None)  # inside
    assert (repo / "README.md").is_file()  # source untouched


def test_export_rejects_symlinked_dest_and_spares_target(tmp_path):
    """A symlinked dest must be rejected — resolve()+rmtree would otherwise delete
    the symlink's TARGET directory (codex critical: irreversible data loss)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keepme.txt").write_text("must survive\n")
    link = tmp_path / "out_link"
    link.symlink_to(precious, target_is_directory=True)
    with pytest.raises(ValueError):
        exp.export(repo, link, out=lambda m: None)
    assert (precious / "keepme.txt").is_file()  # target untouched


def test_export_pii_error_does_not_echo_matched_content(tmp_path):
    """The gate's failure message must NOT contain the matched forbidden literal —
    echoing it re-discloses the caught PII into stderr / CI logs (codex high)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    (repo / "README.md").write_text(f"# sage\nsecret line with {_REALNAME} embedded\n")
    with pytest.raises(exp.ExportPIIError) as ei:
        exp.export(repo, tmp_path / "out", out=lambda m: None)
    msg = str(ei.value)
    assert _REALNAME not in msg  # redacted
    assert "secret line" not in msg  # no surrounding content either
    assert "README.md" in msg and "operator real name" in msg  # path + label still reported


def test_export_fails_closed_when_git_unavailable(tmp_path, monkeypatch):
    """With init_git=True, a git failure RAISES — the distinct-root AC2 guarantee
    is not silently skipped (no fail-open history-less artifact)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    real_run = subprocess.run

    def _fake_run(cmd, *a, **k):
        if cmd and cmd[0] == "git":
            raise FileNotFoundError("git not found")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(FileNotFoundError):
        exp.export(repo, tmp_path / "out", out=lambda m: None)


def test_do_not_ship_disjoint_from_allowlist(tmp_path):
    """Drift guard: no DO_NOT_SHIP path may also appear in the ship allowlist."""
    ship = set(exp.SHIP_DIRS) | set(exp.SHIP_FILES)
    for path in exp.DO_NOT_SHIP:
        assert path not in ship, f"{path} is both do-not-ship and allowlisted"


def test_export_excludes_zone_identifier(tmp_path):
    """Windows mark-of-the-web sidecars (CLAUDE.md:Zone.Identifier) never ship."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    (repo / "agents" / "x.md:Zone.Identifier").write_text("[ZoneTransfer]\n")
    dest = tmp_path / "out"
    exp.export(repo, dest, out=lambda m: None)
    assert not (dest / "agents" / "x.md:Zone.Identifier").exists()
