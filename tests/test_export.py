"""Tests for the Phase-10 clean-room framework export (src/sage_mcp/export.py).

The export is the terminal deliverable's safety boundary: it must ship ONLY the
allowlist (fail closed), strip private git ancestry (distinct root), and fail the
build on any operator PII / stale-vocab token.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

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


# ─────────────────────────────────────────────────────────────────────────────
# Script-reference integrity guard (ADR-0071 blocker fix).
# Agents and skills may reference scripts/... paths; every such referenced
# path must resolve within the shipped surface.  A future agent added that
# points at an unshipped script becomes a red test here, not a broken release.
# ─────────────────────────────────────────────────────────────────────────────

_SCRIPT_REF_RE = re.compile(r"scripts/[A-Za-z0-9_./-]+")


def _dangling_script_refs(dest: Path, shipped: list[str]) -> list[tuple[str, str]]:
    """Return (agent/skill relative path, referenced script path) for every
    scripts/... reference in shipped agents/ and skills/ files that does NOT
    resolve to a shipped path (exact file match or a directory prefix match).

    A reference like ``scripts/media/run.py`` must appear verbatim in
    *shipped*.  A reference like ``scripts/media`` (a dir) passes if any
    shipped path begins with ``scripts/media/``.

    Three categories are excluded from the check because they produce
    false positives against legitimate documentation content:

    1. **Embedded-path references** — when the character immediately before
       the ``scripts/`` token is ``/``, the match is part of a longer path
       (e.g. ``hooks/scripts/stop.py``, ``$ROOT/scripts/foo.mjs``).  These
       point into a different directory tree, not the framework root.

    2. **WHERE-clause examples** — lines containing ``WHERE:`` are example
       output fragments, not operational invocations.  A ``scripts/X.py``
       that appears only in a WHERE example is documentation, not a ship
       dependency.

    3. **Bare-word category nouns** — a match of the form ``scripts/X``
       (exactly one component after ``scripts/``, no file extension) with no
       shipped descendants is treated as a prose category noun
       (e.g. ``scripts/tools``) rather than a file reference.
    """
    shipped_set = set(shipped)
    dangling = []
    for subdir in ("agents", "skills"):
        base = dest / subdir
        if not base.is_dir():
            continue
        for fpath in sorted(base.rglob("*")):
            if not fpath.is_file():
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel_file = str(fpath.relative_to(dest))
            for line in text.splitlines():
                # Exclude WHERE-clause example lines (rule 2).
                if "WHERE:" in line:
                    continue
                for m in _SCRIPT_REF_RE.finditer(line):
                    # Exclude embedded-path references (rule 1).
                    start = m.start()
                    if start > 0 and line[start - 1] == "/":
                        continue
                    ref = m.group().rstrip("/.,)`'\"")
                    # Exclude bare-word category nouns (rule 3): ``scripts/X``
                    # with no file extension and no shipped descendants.
                    parts_after_scripts = ref[len("scripts/") :].split("/")
                    is_single_bare_word = (
                        len(parts_after_scripts) == 1 and "." not in parts_after_scripts[0]
                    )
                    if is_single_bare_word:
                        prefix = ref + "/"
                        if not any(s.startswith(prefix) for s in shipped_set):
                            continue
                    # Exact file match in shipped.
                    if ref in shipped_set:
                        continue
                    # Directory prefix: any shipped path descends from ref/.
                    prefix = ref.rstrip("/") + "/"
                    if any(s.startswith(prefix) for s in shipped_set):
                        continue
                    dangling.append((rel_file, ref))
    return dangling


def test_agent_script_refs_resolve_to_shipped_fixture(tmp_path):
    """Guard: an agent referencing a scripts/... path that is NOT in the
    shipped surface is detected.  When the referenced dir IS shipped the
    check passes.  This makes a future agent pointing at an unshipped script
    a red test instead of a broken release."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    # Add an agent that references a scripts/pipeline/ path.
    (repo / "agents" / "pipeline-runner.md").write_text(
        "Run via scripts/pipeline/run.py end-to-end.\n"
    )

    # ── Case 1: scripts/pipeline/ NOT shipped → dangling ref detected. ──
    dest1 = tmp_path / "out1"
    shipped1 = exp.export(repo, dest1, run_pii_gate=False, init_git=False, out=lambda m: None)
    dangling1 = _dangling_script_refs(dest1, shipped1)
    assert any(ref == "scripts/pipeline/run.py" for _, ref in dangling1), (
        "expected dangling ref to scripts/pipeline/run.py when the dir is not shipped"
    )

    # ── Case 2: scripts/pipeline/ shipped → no dangling refs. ──
    (repo / "scripts" / "pipeline").mkdir(parents=True)
    (repo / "scripts" / "pipeline" / "run.py").write_text("# runner\n")
    dest2 = tmp_path / "out2"
    # Export with run_pii_gate=False so the fixture stays minimal; in
    # production SHIP_DIRS controls what ships.  For this fixture test we
    # check that the helper recognises the shipped file as satisfying the ref.
    # We manually add the path to the shipped list to simulate it being in
    # SHIP_DIRS (the real SHIP_DIRS is validated by the real-repo test below).
    shipped2 = list(shipped1) + ["scripts/pipeline/run.py"]
    # Also materialise the file in dest2 so rglob can find agents/.
    shutil.copytree(dest1, dest2, dirs_exist_ok=True)
    dangling2 = _dangling_script_refs(dest2, shipped2)
    assert not any(ref == "scripts/pipeline/run.py" for _, ref in dangling2), (
        "expected no dangling ref when scripts/pipeline/run.py is in the shipped surface"
    )


def test_dangling_script_refs_excludes_where_lines(tmp_path):
    """Rule 2 (WHERE: exclusion): a scripts/ reference that appears ONLY on a
    WHERE: line must not be flagged — WHERE: lines are example output fragments,
    not operational invocations."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    # The ONLY reference to scripts/nonexistent.py is on a WHERE: line.
    (repo / "agents" / "example-agent.md").write_text(
        "# example-agent\n"
        "Run the tool and note the output:\n"
        "WHERE: scripts/nonexistent.py :: main()\n"
    )
    dest = tmp_path / "out"
    shipped = exp.export(repo, dest, run_pii_gate=False, init_git=False, out=lambda m: None)
    dangling = _dangling_script_refs(dest, shipped)
    assert not any(ref == "scripts/nonexistent.py" for _, ref in dangling), (
        "WHERE: example line must not be flagged as a dangling script ref"
    )


def test_dangling_script_refs_excludes_embedded_paths(tmp_path):
    """Rule 1 (embedded-path exclusion): a scripts/ token preceded by '/' is
    part of a longer path (e.g. hooks/scripts/stop.py) and must not be flagged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    # The reference is hooks/scripts/stop.py — char before 'scripts/' is '/'.
    (repo / "skills" / "example-skill.md").write_text(
        "# example-skill\nThe hook lives at hooks/scripts/stop.py on the host machine.\n"
    )
    dest = tmp_path / "out"
    shipped = exp.export(repo, dest, run_pii_gate=False, init_git=False, out=lambda m: None)
    dangling = _dangling_script_refs(dest, shipped)
    assert not any("scripts/stop.py" in ref for _, ref in dangling), (
        "embedded path hooks/scripts/stop.py must not be flagged as a dangling ref"
    )


def test_dangling_script_refs_suppresses_bare_word_category_nouns(tmp_path):
    """Rule 3 (bare-word suppression — known intentional blind spot): a reference
    of the form ``scripts/X`` with a single bare component and no file extension
    is treated as a prose category noun (e.g. ``scripts/tools``) rather than a
    file reference, and is NOT reported even when no shipped descendant exists.

    Intentional blind spot: a future author who adds a script at
    ``scripts/setup`` (no extension) and documents it as ``scripts/setup`` in an
    agent will NOT get a guard failure.  Use an extension (``scripts/setup.py``)
    or a nested path (``scripts/setup/run.py``) to be protected by the guard.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    # Bare single-component reference with no extension and no shipped descendant.
    (repo / "agents" / "setup-agent.md").write_text(
        "# setup-agent\nAll bootstrap utilities live under scripts/setup.\n"
    )
    dest = tmp_path / "out"
    shipped = exp.export(repo, dest, run_pii_gate=False, init_git=False, out=lambda m: None)
    dangling = _dangling_script_refs(dest, shipped)
    assert not any(ref == "scripts/setup" for _, ref in dangling), (
        "bare-word category noun scripts/setup must not be flagged (known blind spot)"
    )


def test_dangling_script_refs_mixed_line_only_non_where_flagged(tmp_path):
    """Mixed-line test: one genuine dangling ``scripts/foo/bar.py`` ref on a
    normal line AND a separate WHERE: line with a dangling ref → only the
    non-WHERE line is reported."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)
    (repo / "agents" / "mixed-agent.md").write_text(
        "# mixed-agent\n"
        "Invoke scripts/foo/bar.py to run the job.\n"
        "WHERE: scripts/only/in/where.py :: do_job()\n"
    )
    dest = tmp_path / "out"
    shipped = exp.export(repo, dest, run_pii_gate=False, init_git=False, out=lambda m: None)
    dangling = _dangling_script_refs(dest, shipped)
    refs = [ref for _, ref in dangling if "mixed-agent" in _]
    assert "scripts/foo/bar.py" in refs, "genuine dangling ref scripts/foo/bar.py must be reported"
    assert "scripts/only/in/where.py" not in refs, (
        "WHERE: example scripts/only/in/where.py must not be reported"
    )


def test_agent_script_refs_resolve_to_shipped_real_repo(tmp_path):
    """Release gate: every scripts/... reference in the real repo's shipped
    agents/ and skills/ resolves to a path in the shipped surface.

    This test would have been RED when scripts/media/ was absent from
    SHIP_DIRS (agents/media-*.md reference scripts/media/run.py).  Keeping
    it green requires SHIP_DIRS / SHIP_FILES to cover every script the agents
    actually invoke.
    """
    repo_root = Path(__file__).resolve().parents[1]
    dest = tmp_path / "real-export"
    shipped = exp.export(repo_root, dest, run_pii_gate=True, init_git=False, out=lambda m: None)
    dangling = _dangling_script_refs(dest, shipped)
    assert dangling == [], (
        "shipped agents/skills reference scripts not in the ship surface:\n"
        + "\n".join(f"  {f}: {r}" for f, r in dangling)
    )
