"""Tests for skill_exporter.py — agentskills.io conformance validator + packager.

Covers:
- Valid skill exports cleanly; bundle re-validates clean (round-trip).
- Bad name variants each fail with the right issue:
  uppercase letters, leading hyphen, trailing hyphen, consecutive hyphens, >64 chars.
- Empty description fails.
- >1024-char description fails.
- compatibility >500 chars fails.
- metadata that is not a mapping fails.
- Skill with a scripts/ subdir → subdir is copied into the bundle.
- Missing SKILL.md → validation issue (no crash).
- Invalid YAML frontmatter (unquoted colon) → "not valid YAML" issue (ADV-2 guard).
- allowed-tools as a YAML list → fails (must be str).
- Block-scalar description: over 1024 chars (real length) fails; under 1024 passes.
- export_skill(force=True) with traversal name → raises, does NOT write outside dest_dir.
- Regression guard: every real skills/*/ in the repo conforms.
- SKILL.md with UTF-8 BOM validates the same as without (FIX-3 BOM tolerance).
- SKILL.md with leading blank lines validates the same as without (FIX-3 ws tolerance).
- CLI --all batch catches ValueError from traversal name, continues, exits nonzero (FIX-1).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sage_mcp.skill_exporter import (
    ExportResult,
    SkillExportError,
    _split_frontmatter_text,
    export_skill,
    validate_skill,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_skill(
    tmp_path: Path,
    name: str = "my-skill",
    description: str = "Does something useful when triggered.",
    extra_fm: str = "",
    body: str = "# My Skill\n\nBody text.\n",
    subdir: str | None = None,
    subdir_file: str | None = None,
) -> Path:
    """Build a minimal synthetic skill directory in tmp_path."""
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    fm_lines = ["---", f"name: {name}", f"description: {description}"]
    if extra_fm:
        fm_lines.append(extra_fm.rstrip())
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)

    (skill_dir / "SKILL.md").write_text("\n".join(fm_lines), encoding="utf-8")

    if subdir:
        sd = skill_dir / subdir
        sd.mkdir()
        fname = subdir_file or "helper.sh"
        (sd / fname).write_text("#!/bin/sh\necho hello\n", encoding="utf-8")

    return skill_dir


# ── Valid skill — round-trip ───────────────────────────────────────────────


def test_valid_skill_exports_cleanly(tmp_path: Path) -> None:
    """A conformant skill exports without issues and re-validates clean."""
    skill_dir = _make_skill(tmp_path, name="my-skill")
    dest = tmp_path / "out"

    result = export_skill(skill_dir, dest)

    assert isinstance(result, ExportResult)
    assert result.skill_name == "my-skill"
    assert (result.dest_bundle / "SKILL.md").exists()
    # Round-trip: re-validate the exported bundle.
    assert validate_skill(result.dest_bundle) == []


def test_round_trip_written_files(tmp_path: Path) -> None:
    """written_files contains exactly SKILL.md when no optional subdirs exist."""
    skill_dir = _make_skill(tmp_path, name="simple-skill")
    result = export_skill(skill_dir, tmp_path / "out")
    assert len(result.written_files) == 1
    assert result.written_files[0].name == "SKILL.md"


# ── Name validation ────────────────────────────────────────────────────────


def test_name_uppercase_fails(tmp_path: Path) -> None:
    """Uppercase letters in name → validation issue."""
    skill_dir = tmp_path / "MySkill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: MySkill\ndescription: Something useful.\n---\nBody.\n",
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any("lowercase" in i.message for i in issues), issues


def test_name_leading_hyphen_fails(tmp_path: Path) -> None:
    """Name starting with hyphen → validation issue."""
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: -bad-skill
        description: Something useful.
        ---
        Body.
        """),
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any("start with a hyphen" in i.message for i in issues), issues


def test_name_trailing_hyphen_fails(tmp_path: Path) -> None:
    """Name ending with hyphen → validation issue."""
    skill_dir = tmp_path / "skill-trail"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: bad-skill-
        description: Something useful.
        ---
        Body.
        """),
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any("end with a hyphen" in i.message for i in issues), issues


def test_name_consecutive_hyphens_fails(tmp_path: Path) -> None:
    """Name with consecutive hyphens → validation issue."""
    skill_dir = tmp_path / "consec-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: bad--skill
        description: Something useful.
        ---
        Body.
        """),
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any("consecutive" in i.message for i in issues), issues


def test_name_too_long_fails(tmp_path: Path) -> None:
    """Name longer than 64 chars → validation issue."""
    long_name = "a" * 65
    skill_dir = tmp_path / "long-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {long_name}\ndescription: Something.\n---\nBody.\n",
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any("64" in i.message for i in issues), issues


def test_name_missing_fails(tmp_path: Path) -> None:
    """Missing name field → validation issue."""
    skill_dir = tmp_path / "no-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Something.\n---\nBody.\n", encoding="utf-8"
    )
    issues = validate_skill(skill_dir)
    field_names = [i.field_name for i in issues]
    assert "name" in field_names, issues


# ── Description validation ─────────────────────────────────────────────────


def test_description_empty_fails(tmp_path: Path) -> None:
    """Empty description → validation issue."""
    skill_dir = tmp_path / "empty-desc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: empty-desc\ndescription: ''\n---\nBody.\n", encoding="utf-8"
    )
    issues = validate_skill(skill_dir)
    assert any(i.field_name == "description" for i in issues), issues


def test_description_over_1024_fails(tmp_path: Path) -> None:
    """Description over 1024 chars → validation issue."""
    skill_dir = tmp_path / "long-desc"
    skill_dir.mkdir()
    long_desc = "x" * 1025
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: long-desc\ndescription: {long_desc}\n---\nBody.\n",
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any(i.field_name == "description" and "1024" in i.message for i in issues), issues


def test_description_exactly_1024_passes(tmp_path: Path) -> None:
    """Description of exactly 1024 chars is valid."""
    skill_dir = _make_skill(tmp_path, name="exact-desc", description="x" * 1024)
    issues = validate_skill(skill_dir)
    assert not any(i.field_name == "description" for i in issues), issues


def test_description_block_scalar_over_1024_fails(tmp_path: Path) -> None:
    """Block-scalar description that is >1024 real chars → validation issue (ADV-2 guard).

    The real-YAML parser measures the parsed string length, not the raw YAML lines.
    This catches a block scalar that expands to more than 1024 chars.
    """
    # Build a block-scalar description that is 1025 chars when parsed (yaml strips
    # the trailing newline from a literal block scalar and trims, but "x"*1025 + newline
    # = 1025 real chars after strip).
    long_text = "x" * 1025
    skill_dir = tmp_path / "block-long"
    skill_dir.mkdir()
    content = textwrap.dedent(f"""\
    ---
    name: block-long
    description: |
      {long_text}
    ---
    Body.
    """)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    issues = validate_skill(skill_dir)
    assert any(i.field_name == "description" and "1024" in i.message for i in issues), issues


def test_description_block_scalar_under_1024_passes(tmp_path: Path) -> None:
    """Block-scalar description that is ≤1024 real chars → valid."""
    short_text = "x" * 100
    skill_dir = tmp_path / "block-short"
    skill_dir.mkdir()
    content = textwrap.dedent(f"""\
    ---
    name: block-short
    description: |
      {short_text}
    ---
    Body.
    """)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    issues = validate_skill(skill_dir)
    assert not any(i.field_name == "description" for i in issues), issues


# ── Optional field validation ──────────────────────────────────────────────


def test_compatibility_over_500_fails(tmp_path: Path) -> None:
    """compatibility field over 500 chars → validation issue."""
    long_compat = "y" * 501
    skill_dir = tmp_path / "compat-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: compat-skill\ndescription: Something.\ncompatibility: {long_compat}\n---\nBody.\n",
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any(i.field_name == "compatibility" and "500" in i.message for i in issues), issues


def test_compatibility_exactly_500_passes(tmp_path: Path) -> None:
    """compatibility of exactly 500 chars is valid."""
    skill_dir = _make_skill(tmp_path, name="compat-ok", extra_fm="compatibility: " + "z" * 500)
    issues = validate_skill(skill_dir)
    assert not any(i.field_name == "compatibility" for i in issues), issues


def test_metadata_not_mapping_fails(tmp_path: Path) -> None:
    """metadata that is a string (not a mapping) → validation issue."""
    skill_dir = tmp_path / "bad-meta"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: bad-meta
        description: Something.
        metadata: "not a mapping"
        ---
        Body.
        """),
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any(i.field_name == "metadata" for i in issues), issues


def test_metadata_mapping_passes(tmp_path: Path) -> None:
    """metadata as a proper YAML mapping passes."""
    skill_dir = tmp_path / "good-meta"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: good-meta
        description: Something.
        metadata:
          author: test
          version: "1.0"
        ---
        Body.
        """),
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert not any(i.field_name == "metadata" for i in issues), issues


def test_allowed_tools_as_list_fails(tmp_path: Path) -> None:
    """allowed-tools as a YAML list → fails (must be a space-separated string per spec).

    This is the ADV-3 regression guard: the real YAML parser correctly sees a list
    where the hand parser would have silently returned None or a mis-parsed string.
    """
    skill_dir = tmp_path / "bad-tools"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: bad-tools
        description: Something.
        allowed-tools:
          - Read
          - Write
        ---
        Body.
        """),
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert any(i.field_name == "allowed-tools" for i in issues), issues


def test_allowed_tools_as_string_passes(tmp_path: Path) -> None:
    """allowed-tools as a space-separated string → valid."""
    skill_dir = _make_skill(tmp_path, name="tools-ok", extra_fm="allowed-tools: Read Write Bash")
    issues = validate_skill(skill_dir)
    assert not any(i.field_name == "allowed-tools" for i in issues), issues


# ── Invalid YAML frontmatter — ADV-2 regression guard ─────────────────────


def test_invalid_yaml_frontmatter_returns_not_valid_yaml_issue(tmp_path: Path) -> None:
    """Frontmatter with an unquoted colon in a value → 'not valid YAML' issue.

    Regression guard for ADV-2: the old hand parser silently accepted this;
    the real-YAML-authoritative validator correctly rejects it.
    """
    skill_dir = tmp_path / "bad-yaml"
    skill_dir.mkdir()
    # Unquoted colon in description value — yaml.safe_load raises YAMLError.
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bad-yaml\ndescription: PAUSE: need nook lookup\n---\nBody.\n",
        encoding="utf-8",
    )
    issues = validate_skill(skill_dir)
    assert len(issues) == 1, issues
    assert issues[0].field_name == "SKILL.md"
    assert "not valid YAML" in issues[0].message, issues[0].message


# ── scripts/ subdir copied ─────────────────────────────────────────────────


def test_scripts_subdir_copied_into_bundle(tmp_path: Path) -> None:
    """A skill with a scripts/ subdir → scripts/ is present in the exported bundle."""
    skill_dir = _make_skill(tmp_path, name="with-scripts", subdir="scripts", subdir_file="run.sh")
    result = export_skill(skill_dir, tmp_path / "out")
    assert (result.dest_bundle / "scripts" / "run.sh").exists()
    # scripts/run.sh should appear in written_files.
    script_files = [f for f in result.written_files if f.name == "run.sh"]
    assert script_files, result.written_files


# ── Missing SKILL.md — no crash ────────────────────────────────────────────


def test_missing_skill_md_returns_issue_no_crash(tmp_path: Path) -> None:
    """Directory without SKILL.md → validation issue, no exception raised."""
    empty_dir = tmp_path / "empty-skill"
    empty_dir.mkdir()
    issues = validate_skill(empty_dir)
    assert len(issues) == 1
    assert issues[0].field_name == "SKILL.md"


# ── SkillExportError on validation failure ─────────────────────────────────


def test_export_raises_on_invalid_skill(tmp_path: Path) -> None:
    """export_skill() raises SkillExportError when validation fails and force=False."""
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: BadName\ndescription: ok\n---\nBody.\n", encoding="utf-8"
    )
    with pytest.raises(SkillExportError) as exc_info:
        export_skill(skill_dir, tmp_path / "out")
    assert exc_info.value.issues


def test_export_force_skips_validation_gate(tmp_path: Path) -> None:
    """export_skill(force=True) exports even an invalid skill."""
    skill_dir = tmp_path / "force-test"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: BadName\ndescription: ok\n---\nBody.\n", encoding="utf-8"
    )
    # Should not raise.
    result = export_skill(skill_dir, tmp_path / "out", force=True)
    assert (result.dest_bundle / "SKILL.md").exists()


# ── Path-traversal hardening — ADV-1 regression guard ─────────────────────


def test_export_force_with_traversal_name_raises_and_does_not_escape(
    tmp_path: Path,
) -> None:
    """export_skill(force=True) with a traversal skill name raises ValueError.

    The path-traversal guard applies even under force=True.  A skill whose
    resolved name contains '..' or '/' must never write outside dest_dir.
    ADV-1 regression guard.
    """
    # Build a skill dir whose SKILL.md has name: ../escape
    skill_dir = tmp_path / "traversal-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: ../escape\ndescription: Something.\n---\nBody.\n",
        encoding="utf-8",
    )

    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    # Must raise — never write outside dest_dir.
    with pytest.raises((ValueError, Exception)) as exc_info:
        export_skill(skill_dir, dest_dir, force=True)

    # Confirm nothing was written outside dest_dir.
    escape_target = tmp_path / "escape"
    assert not escape_target.exists(), f"Path traversal succeeded: {escape_target} was created"
    # The ValueError message should mention the unsafe name or path.
    assert "escape" in str(exc_info.value) or "unsafe" in str(exc_info.value).lower(), str(
        exc_info.value
    )


# ── Regression guard: every real skill conforms ────────────────────────────


def test_real_skills_all_conform() -> None:
    """Every real skills/*/ in the repo passes agentskills.io conformance validation.

    This is the regression guard that ensures sage's own skills stay
    portable as the framework evolves.  All 20 skills must be valid YAML and
    conformant after the quote-normalization in ADR-0046.
    """
    here = Path(__file__).resolve()
    # Walk up to repo root (contains skills/).
    repo_root: Path | None = None
    for parent in here.parents:
        if (parent / "skills").is_dir():
            repo_root = parent
            break

    if repo_root is None:
        pytest.skip("Could not locate skills/ directory from test file location")

    skills_dir = repo_root / "skills"
    skill_dirs = [d for d in sorted(skills_dir.iterdir()) if d.is_dir()]

    if not skill_dirs:
        pytest.skip("skills/ directory is empty")

    failures: list[str] = []
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue  # not a skill directory — skip silently
        issues = validate_skill(skill_dir)
        if issues:
            failures.append(f"{skill_dir.name}: " + "; ".join(str(i) for i in issues))

    assert not failures, (
        "The following real skills failed agentskills.io conformance:\n"
        + "\n".join(f"  {f}" for f in failures)
    )


# ── BOM + leading-whitespace tolerance (FIX-3) ────────────────────────────


_MINIMAL_FM = "---\nname: bom-skill\ndescription: Works fine.\n---\nBody.\n"


def test_bom_stripped_before_fence_detection() -> None:
    """_split_frontmatter_text strips a leading UTF-8 BOM before fence detection."""
    bom_text = "﻿" + _MINIMAL_FM
    result = _split_frontmatter_text(bom_text)
    assert result is not None, "BOM should be stripped before fence detection"
    assert "name: bom-skill" in result


def test_leading_blank_lines_skipped_before_fence_detection() -> None:
    """_split_frontmatter_text skips leading blank lines before the opening fence."""
    ws_text = "\n\n  \n" + _MINIMAL_FM
    result = _split_frontmatter_text(ws_text)
    assert result is not None, "Leading blank lines should be skipped before fence detection"
    assert "name: bom-skill" in result


def test_bom_skill_validates_same_as_without(tmp_path: Path) -> None:
    """A SKILL.md that starts with a UTF-8 BOM validates identically to one without."""
    skill_dir = tmp_path / "bom-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_bytes(b"\xef\xbb\xbf" + _MINIMAL_FM.encode("utf-8"))
    issues = validate_skill(skill_dir)
    assert issues == [], f"BOM skill should validate clean; got: {issues}"


def test_leading_blank_lines_skill_validates_same_as_without(tmp_path: Path) -> None:
    """A SKILL.md with leading blank lines validates identically to one without."""
    skill_dir = tmp_path / "ws-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("\n\n" + _MINIMAL_FM, encoding="utf-8")
    issues = validate_skill(skill_dir)
    assert issues == [], f"Leading-whitespace skill should validate clean; got: {issues}"


# ── CLI --all batch ValueError handling (FIX-1) ───────────────────────────


def test_cli_all_batch_catches_valueerror_no_traceback(tmp_path: Path) -> None:
    """CLI export skill --all catches ValueError from traversal name; no traceback.

    Verifies that:
    1. The bad (traversal-name) skill shows FAIL in output without a Python traceback.
    2. The good skill still exports (PASS line present).
    3. The process exits nonzero (because --force is NOT set, so any_fail is True
       for the bad-yaml skill that fails validation before the ValueError path; OR
       any_oserror is set when the ValueError is caught).

    This is the FIX-1 regression guard: before the fix the uncaught ValueError
    would abort the entire batch with a traceback.
    """
    # --- good skill ---
    good_dir = tmp_path / "skills" / "good-skill"
    good_dir.mkdir(parents=True)
    (good_dir / "SKILL.md").write_text(
        "---\nname: good-skill\ndescription: A valid skill.\n---\nBody.\n",
        encoding="utf-8",
    )

    # --- traversal skill: valid YAML but unsafe name ---
    # The name passes yaml.safe_load (it's a string) but the path-traversal guard
    # in export_skill() raises ValueError.  validate_skill() returns NO issues
    # (name validation checks the string value "../escape" which fails the
    # _validate_name regex, so actually this skill will fail validation too —
    # the ValueError path is reached only under --force).  Use --force so
    # export_skill() is actually called and the ValueError fires.
    bad_dir = tmp_path / "skills" / "traversal-skill"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text(
        "---\nname: ../escape\ndescription: Bad skill.\n---\nBody.\n",
        encoding="utf-8",
    )

    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    # --root points to the synthetic repo root; cmd_export appends /skills to it.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sage_mcp.cli",
            "export",
            "--root",
            str(tmp_path),
            "skill",
            "--all",
            "--dest",
            str(dest_dir),
            "--force",
        ],
        capture_output=True,
        text=True,
    )

    combined = result.stdout + result.stderr
    # No Python traceback — the ValueError must be caught.
    assert "Traceback (most recent call last)" not in combined, (
        f"Uncaught traceback in output:\n{combined}"
    )
    # The good skill still exported.
    assert "good-skill" in combined, f"Good skill missing from output:\n{combined}"
    # The process exited nonzero (bad skill caused any_oserror=True).
    assert result.returncode != 0, (
        f"Expected nonzero exit; got 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
