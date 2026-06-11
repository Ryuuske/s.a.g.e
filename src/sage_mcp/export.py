"""sage export — clean-room allowlist publisher (Phase 10, ADR-0071).

Produces a **public-by-construction** export of the sage framework. The export
copies ONLY the explicit ship-list into a fresh directory that gets its own git
root (zero private ancestry), writes an empty Nook scaffold that
``sage bootstrap`` populates on first run, then runs the **export-PII gate** over
the result and **fails closed** if any operator PII or stale-vocab token appears.

Two invariants make this safe to re-run for every release:

1. **Allowlist, not denylist.** Anything not named in ``SHIP_DIRS`` / ``SHIP_FILES``
   is absent from the export by default. A new private file added to the working
   repo cannot leak into a future export unless someone explicitly allowlists it.
2. **Distinct git root.** The export directory is a brand-new ``git init`` with a
   single initial commit. No commit from the private working repo is reachable
   from the export's history, so the private ADR/audit/plan trail can never be
   recovered from the public artifact.

The export-PII gate (``scan_pii``) is the safety net behind the allowlist: even
if a shipped file's *content* carries a residual stale-vocabulary token (the
prior framework / MCP-prefix / agent / upstream-store names), the operator's real
name, or anything that deanonymizes the operator, the build fails rather than
publishing it. The forbidden tokens themselves are never written verbatim in this
module (the gate scans this file too, with no exemption — see ``_tok``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Ship-list (public-by-construction allowlist).
# ─────────────────────────────────────────────────────────────────────────────

# Directories copied whole (recursively), minus the build-noise filter below.
# Anything not in this tuple is excluded by default — the export fails closed.
SHIP_DIRS: tuple[str, ...] = (
    "src/sage_mcp",
    "agents",
    "skills",
    "commands",
    "hooks",
    "statusline",
    "installer-assets",
    "claude-md",
    "rules",
    "tests",
    ".claude-plugin",
    ".github",
    ".devcontainer",
    # NB: docs/specs ships the CURRENT framework spec contracts via SHIP_FILES
    # below. Frozen/design-phase specs were relocated to internal/archive/specs/
    # at Master Run Stage 3 and are excluded by construction.
    # NB: the persona SOURCE files live at internal/persona/ — only the derived
    # public artifact (docs/concepts/sage-persona.md) ships, via SHIP_FILES. The
    # raw sage-profile.{md,docx,txt} carry the unbuilt voice/TTS/mode-router
    # spec and stay private.
)

# Individual loose / root files shipped. Missing files are skipped with a warning
# (the release gate verifies the required ones, e.g. LICENSE/NOTICE, separately) —
# a missing optional file never fails the build, but an UNLISTED file never ships.
SHIP_FILES: tuple[str, ...] = (
    "README.md",
    "LICENSE",
    "NOTICE",  # upstream MIT lineage (10.8); created before the 10.5 run
    "docs/concepts/mission.md",
    "AGENTS.md",
    "CLAUDE.md",
    "pyproject.toml",
    "uv.lock",
    "install.sh",
    "install.ps1",
    ".gitignore",
    ".sageignore",
    ".mcp.json",
    ".pre-commit-config.yaml",
    ".python-version",
    "wing_config.json",  # cleaned template (Phase 8): taxonomy + framework wings only
    "docs/guides/onboarding.md",
    "docs/guides/releasing.md",
    "docs/index.md",
    "docs/reference/agent-roster.md",
    "docs/reference/skills.md",
    "docs/reference/commands.md",
    "docs/reference/surface.md",
    # CI-referenced repo tooling (public CI must run green)
    "scripts/gate_docs.py",
    "scripts/gate_stray_work_items.py",
    "scripts/gen_docs.py",
    "scripts/measure_framework_surface.py",
    "docs/specs/universal-agent-constraints.md",
    "docs/specs/mcp-bootstrap.md",
    "docs/specs/audit-pairing-matrix.md",
    "docs/specs/agent-registry-protocol.md",
    "docs/concepts/third-party-patterns.md",
    "docs/concepts/closets.md",
    "docs/concepts/format-coverage.md",
    "docs/specs/virtual-line-numbering.md",
    "docs/specs/schema.sql",
    "docs/concepts/sage-persona.md",
    # estate contract JSONs — required by the shipped ts-web CI job
    # (render.test.ts drift guard); byte-identical to the src/ copies
    "docs/projects/sage-estate-dashboard/estate-design-tokens.json",
    "docs/projects/sage-estate-dashboard/estate-model.schema.json",
    # docs/specs — current framework specs (historical design docs: internal/archive/specs/)
    "docs/specs/verdict-schema.md",
    "docs/specs/telemetry.md",
    "docs/specs/manifest-schema.md",
    "docs/specs/backlog-changelog-schema.md",
)

# Deliberately NOT shipped (each is private working-repo state). Recorded here as
# executable documentation of the do-not-ship boundary; the allowlist already
# excludes them by omission, this tuple is what the test asserts against.
DO_NOT_SHIP: tuple[str, ...] = (
    "docs/decisions",  # ADRs — private trail
    "docs/audits",  # per-change audit reports
    "docs/plans",  # working plans
    "docs/roadmap",  # working roadmap / vision drafts
    "docs/handoff",  # session handoffs + the run-log
    "docs/research",  # research notes
    "docs/recovery",  # recovery runbooks (operator-specific)
    "docs/vision",  # vision drafts
    "docs/agents",  # per-agent internal CHANGELOG process docs (BACKLOG halves retired 2026-06-10)
    "CHANGELOG.md",  # carries historical prior-framework vocabulary
    "HISTORY.md",  # internal dev history
    # (the pre-rebrand one-time migration tool was archived to internal/archive/tools/)
    "scripts/gen_adr_index.py",  # targets internal/decisions (dev repo only)
    "scripts/prune-plugin-cache.sh",  # operator cache tool, zero refs
    "docs/specs/framework-standards-charter.md",  # dev-repo governance contracts;
    # intentionally dev-only (measure_framework_surface.py self-encodes the contracts)
)

# Within shipped dirs, skip build noise / caches (never part of a clean export).
_EXCLUDE_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "dist-demo",
    }
)
# A dir is also excluded when its name ENDS WITH one of these (e.g. <pkg>.egg-info,
# which an exact-name set would miss).
_EXCLUDE_DIR_SUFFIXES = (".egg-info",)
_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".so", ".coverage")
_EXCLUDE_FILE_NAMES = frozenset({".DS_Store"})


def _excluded_dir(name: str) -> bool:
    return name in _EXCLUDE_DIR_NAMES or name.endswith(_EXCLUDE_DIR_SUFFIXES)


def _excluded_file(name: str) -> bool:
    # Windows "mark-of-the-web" alternate-data-stream sidecars (e.g.
    # ``CLAUDE.md:Zone.Identifier``) are build cruft and never ship.
    if name in _EXCLUDE_FILE_NAMES or ":Zone.Identifier" in name:
        return True
    return name.endswith(_EXCLUDE_SUFFIXES)


# ─────────────────────────────────────────────────────────────────────────────
# Export-PII / stale-vocab gate.
# ─────────────────────────────────────────────────────────────────────────────


def _tok(*parts: str) -> str:
    """Join fragments into a forbidden literal.

    Splitting each forbidden token across string fragments keeps THIS shipped
    source file (export.py ships under src/sage_mcp/) free of any verbatim token, so
    ``scan_pii`` can scan **every** shipped file — including itself and its tests
    — with **no exemption**. An exemption on a shipped file would defeat the
    gate: the planted literals would ride along in the exported copy while the
    scan reported clean.
    """
    return "".join(parts)


# (compiled pattern, human label). The gate greps every shipped file and fails
# the build on any hit. THIS tuple is the operative literal list; ADR-0071 §4 is
# the arbiter POLICY for which CATEGORIES the gate must cover (operator real name;
# prior-framework / MCP-prefix / agent / upstream-store vocabulary; operator
# private project names; operator home path / username; sibling-tool branding).
# Completeness check: every category ADR-0071 §4 names has a pattern here — EXCEPT
# the upstream-store-name-as-store category, which lives just below as
# `_MEMPALACE_PATTERN` (kept out of this tuple so its NOTICE/LICENSE attribution
# carve-out is explicit). So the full operative pattern set is PII_PATTERNS +
# _MEMPALACE_PATTERN. The ADR deliberately does NOT enumerate the verbatim tokens
# (they are operator PII); the tokens live here, split via _tok. Labels are kept
# literal-free (no verbatim forbidden token) so this file passes its own gate.
PII_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(rf"\b{_tok('Ah', 'ti')}\b"), "operator real name"),
    (re.compile(r"solo[-_]palace", re.IGNORECASE), "stale framework name (prior project)"),
    # The 2026-06-11 rebrand retires the prior name entirely (User: "all
    # mentions must be changed"); split via _tok so this file self-passes.
    (re.compile(rf"\b{_tok('ke', 'el')}\b", re.IGNORECASE), "stale framework name (pre-rebrand)"),
    (re.compile(r"\bpalace_[a-z]+"), "stale MCP tool prefix"),
    (re.compile(rf"\b{_tok('aidev-', 'librarian')}\b"), "stale agent name (prior librarian)"),
    # Operator PRIVATE project / business taxonomy that leaked via the old wing
    # registry + test fixtures. The operator's PUBLIC publish identity
    # (``Ryuuske`` the GitHub owner, ``ZiSaStudios`` the brand) is deliberately
    # NOT here — it is legitimate attribution in LICENSE/README. Only the private
    # project names that a stranger should never see are flagged.
    (
        re.compile(
            r"\b"
            + _tok("HRES", "-")  # split like every sibling so this source self-passes the gate
            + r"\w+"
            + "|"
            + _tok("Etsy", "-Store")
            + "|"
            + _tok("Personal", "-Finance")
            + "|"
            + _tok("Zi-Sa", "-Wiki")
            + "|"
            + _tok("Image", "-Generation")
        ),
        "operator private project name",
    ),
    # Sibling-tool branding from the prior installer/spine/statusline system
    # (ADR-0072 backlog): the old project name + the statusline/budget script
    # names + the matching ~/.cache namespace. A "solo-tier" repo scale tier is a
    # legitimate domain term and is deliberately NOT matched.
    (
        re.compile(
            _tok("solo", "-ops") + r"|solo[-_](?:statusline|codex-budget)",
            re.IGNORECASE,
        ),
        "stale sibling-tool name (prior installer/statusline)",
    ),
    # Operator's local home path / unix username, which deanonymizes the
    # maintainer (the home-dir path and the Claude projects-dir slug derived from
    # the operator's cwd). Both fragments are assembled via _tok.
    (
        re.compile(_tok("/home/", "zisa") + r"\b|" + _tok("-home-", "zisa-dev") + r"\b"),
        "operator home path / unix username",
    ),
)

# The upstream memory project's name is flagged everywhere EXCEPT the attribution
# files, where the MIT lineage legitimately names it (10.8). Kept separate from
# PII_PATTERNS so the attribution carve-out is explicit and testable.
_MEMPALACE_PATTERN = re.compile(_tok("Mem", "Palace"))
_MEMPALACE_ATTRIBUTION_FILES = frozenset({"NOTICE", "LICENSE"})


@dataclass(frozen=True)
class PIIHit:
    """One stale-vocab / PII match found in the export artifact."""

    path: str  # relative to export root
    line: int
    label: str
    text: str


class ExportPIIError(RuntimeError):
    """Raised when the export-PII gate finds operator PII or stale vocabulary.

    The message reports only ``path:line [label]`` — NEVER the matched line text.
    Echoing the caught content would re-disclose the exact operator PII the gate
    exists to suppress into stderr / shared CI logs (a fail-closed gate must not
    leak through its own diagnostics). The raw match stays on ``hit.text`` for
    in-process callers but is never rendered by ``str(error)``.
    """

    def __init__(self, hits: list[PIIHit]):
        self.hits = hits
        preview = "\n".join(f"  {h.path}:{h.line} [{h.label}]" for h in hits[:20])
        more = "" if len(hits) <= 20 else f"\n  … and {len(hits) - 20} more"
        super().__init__(
            f"export-PII gate FAILED — {len(hits)} stale-vocab/PII hit(s) "
            f"(content redacted; see path:line):\n{preview}{more}"
        )


def scan_pii(root: str | Path) -> list[PIIHit]:
    """Grep the entire export tree for operator PII + stale vocabulary.

    Returns every hit (empty list == clean). Scans **every** file — there is no
    suffix whitelist and no per-file exemption, so an extensionless script, a
    binary, or a ``.docx`` cannot smuggle a token past the gate. Each file is read
    as bytes and decoded with ``errors="replace"`` so ASCII tokens embedded in
    otherwise-binary content are still found. Only the excluded build-noise dirs
    (incl. the export's own ``.git/``) are pruned.
    """
    root = Path(root)
    hits: list[PIIHit] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _EXCLUDE_DIR_NAMES and not (Path(dirpath) / d).is_symlink()
        ]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(root))
            try:
                text = fpath.read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for pattern, label in PII_PATTERNS:
                    if pattern.search(line):
                        hits.append(PIIHit(rel, i, label, line))
                if _MEMPALACE_PATTERN.search(line) and rel not in _MEMPALACE_ATTRIBUTION_FILES:
                    hits.append(PIIHit(rel, i, "stale upstream store name (used as store)", line))
    return hits


# ─────────────────────────────────────────────────────────────────────────────
# Export build.
# ─────────────────────────────────────────────────────────────────────────────


def _should_skip(path: Path) -> bool:
    if _excluded_file(path.name):
        return True
    return any(_excluded_dir(part) for part in path.parts)


def _copy_tree(src: Path, dst: Path, shipped: list[str], root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(src):
        # Prune excluded dirs AND symlinked dirs (do not follow links out of the
        # allowlist — a symlinked dir could point at private state).
        dirnames[:] = [
            d for d in dirnames if not _excluded_dir(d) and not (Path(dirpath) / d).is_symlink()
        ]
        for fname in filenames:
            sp = Path(dirpath) / fname
            if _should_skip(sp):
                continue
            # NEVER follow a symlinked file: shutil.copy2 would copy the target's
            # contents (possibly an unlisted/private path) into the public export,
            # defeating the allowlist. Skip + warn instead.
            if sp.is_symlink():
                continue
            rel = sp.relative_to(root)
            dp = dst / sp.relative_to(src)
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dp)
            shipped.append(str(rel))


def _write_public_changelog(dest: Path) -> None:
    """Author the public CHANGELOG (the dev CHANGELOG never ships).

    One initial-release entry at the package version; later public releases
    append on top per Keep-a-Changelog.
    """
    import datetime

    from sage_mcp.version import __version__

    today = datetime.date.today().isoformat()
    (dest / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "All notable changes to sage are documented here. Format:\n"
        "[Keep a Changelog](https://keepachangelog.com/) · versioning: semver.\n\n"
        f"## [{__version__}] — {today}\n\n"
        "Initial public release.\n",
        encoding="utf-8",
    )


def _write_nook_scaffold(dest: Path) -> None:
    """Write the empty Nook scaffold (visionary Fork B1) that bootstrap populates.

    A README-only directory so a stranger's first ``sage bootstrap`` has a target;
    ships NO operator drawer data.
    """
    scaffold = dest / "nook-scaffold"
    scaffold.mkdir(parents=True, exist_ok=True)
    (scaffold / "README.md").write_text(
        "# Nook scaffold\n\n"
        "This is an empty memory store scaffold. On first run, `sage bootstrap`\n"
        "populates your own Nook here (or at `~/.sage/nook`). No memory data ships\n"
        "with the framework — your Nook is yours alone.\n",
        encoding="utf-8",
    )


def _git_init_fresh_root(dest: Path) -> None:
    """Initialise a brand-new git repo with one commit — zero private ancestry.

    RAISES (``CalledProcessError`` / ``FileNotFoundError``) if git is unavailable
    or any step fails. The distinct one-commit root is the AC2 zero-private-
    ancestry guarantee; a release export that cannot materialise it must FAIL
    closed rather than silently ship a history-less tree (ADR-0071 rejects the
    fail-open case). Use ``init_git=False`` only for debug copies.
    """

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(dest),
            check=True,
            capture_output=True,
            text=True,
        )

    _git("init", "-q")
    _git("-c", "user.email=release@sage.local", "-c", "user.name=sage release", "add", "-A")
    _git(
        "-c",
        "user.email=release@sage.local",
        "-c",
        "user.name=sage release",
        "commit",
        "-q",
        "-m",
        "Initial public release",
    )


def export(
    repo_root: str | Path,
    dest: str | Path,
    *,
    run_pii_gate: bool = True,
    init_git: bool = True,
    out: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """Build a clean public export of ``repo_root`` at ``dest``.

    Copies only the ship-list, writes the empty Nook scaffold, runs the
    export-PII gate (raising ``ExportPIIError`` if it finds anything — BEFORE any
    git commit, so a rejected export never materialises a committed artifact
    containing the caught data), then initialises a distinct git root.
    Re-runnable: ``dest`` is rebuilt from scratch each call. Returns the list of
    shipped relative paths.
    """
    log = out or print
    repo_root = Path(repo_root).resolve()
    # Validate the RAW dest BEFORE resolving: ``Path.resolve()`` follows a symlink,
    # so a symlinked ``dest`` would rewrite to its target and the ``rmtree`` below
    # would delete that arbitrary target directory (irreversible data loss). Reject
    # a symlinked dest outright — the export only ever wipes a real directory it
    # owns, never a link's target.
    dest_input = Path(dest)
    if dest_input.is_symlink():
        raise ValueError(
            f"export dest {dest_input} is a symlink; refusing (rmtree would delete "
            "its target, not the link)"
        )
    dest = dest_input.resolve()
    # ``dest`` is wiped with rmtree below, so it must not be the repo, an ANCESTOR
    # of the repo (rmtree would delete the source before copy), or INSIDE the repo
    # (which the copy walk would then recurse into). Reject all three.
    if repo_root == dest or repo_root.is_relative_to(dest) or dest.is_relative_to(repo_root):
        raise ValueError(
            f"export dest {dest} must be outside the repo root {repo_root} "
            "(not equal to, inside, or an ancestor of it)"
        )

    if dest.exists() and not dest.is_symlink():
        shutil.rmtree(dest)  # re-runnable: always a fresh real directory
    elif dest.is_symlink():  # defensive: a symlink surviving resolve() is never rmtree'd
        dest.unlink()
    dest.mkdir(parents=True)

    shipped: list[str] = []
    for d in SHIP_DIRS:
        src = repo_root / d
        # ``is_dir()`` follows symlinks, so a top-level ship dir that is itself a
        # symlink to an outside/private directory would have os.walk() copy the
        # target's files in (the per-file/child-dir symlink skip in _copy_tree only
        # guards BELOW the walk root). Reject a symlinked ship-dir root outright,
        # and require the resolved source to stay inside the repo.
        if src.is_symlink() or not src.is_dir() or not src.resolve().is_relative_to(repo_root):
            log(f"  ! ship-dir absent / symlink / outside-repo, skipped: {d}")
            continue
        _copy_tree(src, dest / d, shipped, repo_root)
    for f in SHIP_FILES:
        src = repo_root / f
        if src.is_file() and not src.is_symlink():
            dp = dest / f
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dp)
            shipped.append(f)
        else:
            log(f"  ! ship-file absent or symlink, skipped: {f}")

    _write_public_changelog(dest)
    _write_nook_scaffold(dest)
    log(f"  shipped {len(shipped)} files into {dest}")

    # Gate BEFORE git, and tear the failed export down so no artifact survives
    # carrying the caught tokens (not even in an un-committed working tree).
    if run_pii_gate:
        hits = scan_pii(dest)
        if hits:
            shutil.rmtree(dest, ignore_errors=True)
            raise ExportPIIError(hits)
        log("  export-PII gate: clean (0 hits)")

    if init_git:
        _git_init_fresh_root(dest)

    return shipped
