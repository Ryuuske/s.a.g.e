"""skill_exporter.py — agentskills.io conformance validator + portable packager.

Validates a sage skill directory against the Agent Skills open standard
(https://agentskills.io) and exports it as a portable bundle.

Public API:

    validate_skill(skill_dir: Path) -> list[ValidationIssue]
        Returns a list of issues. Empty list means the skill is conformant.

    export_skill(skill_dir: Path, dest_dir: Path, *, force: bool = False) -> ExportResult
        Validates first, then copies SKILL.md + optional scripts/references/assets
        into dest_dir/<skill-name>/. Returns what was written.

agentskills.io spec enforced:

    name          REQUIRED — lowercase letters, digits, hyphens ONLY; max 64 chars;
                             must not start or end with a hyphen; no consecutive hyphens.
    description   REQUIRED — non-empty; max 1024 chars.
    license       OPTIONAL — must be a string if present.
    compatibility OPTIONAL — must be a string if present; max 500 chars.
    metadata      OPTIONAL — must be a mapping if present.
    allowed-tools OPTIONAL — must be a string if present (space-separated per spec;
                             a YAML list value is a FAILURE).

Unknown frontmatter keys are ignored (pass-through per spec).

Frontmatter parsing strategy (real-YAML-authoritative, per ADR-0046):

    ``validate_skill`` parses frontmatter with ``yaml.safe_load``.  If it raises
    ``yaml.YAMLError``, that is a conformance FAILURE — a real agentskills.io
    consumer would reject the skill.  All field checks run off the real-parsed
    dict, giving correct length for block scalars, correct types for
    ``allowed-tools``/``metadata``, and free quote handling.

    The bespoke colon-safe hand parser (``_extract_string_field``) is NOT used
    in the conformance path.  Its continued use is confined to
    ``extensions/skill_registry.py`` for search/display (its original purpose).

    ``_split_frontmatter_text`` is retained only to isolate the raw frontmatter
    text for ``yaml.safe_load``; it is not used for field extraction.

    ``_resolve_skill_name`` also uses ``yaml.safe_load``; if YAML is invalid or
    the name field is absent, it falls back to the directory name.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ── Validation types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationIssue:
    """A single conformance issue found in a skill directory."""

    field_name: str
    message: str

    def __str__(self) -> str:
        return f"{self.field_name}: {self.message}"


# ── Export result ──────────────────────────────────────────────────────────


@dataclass
class ExportResult:
    """Result returned by export_skill() on success."""

    skill_name: str
    dest_bundle: Path
    written_files: list[Path] = field(default_factory=list)


# ── Name validation ────────────────────────────────────────────────────────

# Matches a valid agentskills.io name: lowercase letters, digits, hyphens.
_VALID_NAME_CHARS = re.compile(r"^[a-z0-9-]+$")
_NAME_MAX = 64


def _validate_name(name: object) -> list[ValidationIssue]:
    """Return issues for the ``name`` field.  Empty list = valid."""
    if name is None:
        return [ValidationIssue("name", "required field is missing")]
    if not isinstance(name, str):
        return [
            ValidationIssue(
                "name",
                f"must be a string, got {type(name).__name__}",
            )
        ]
    if not name:
        return [ValidationIssue("name", "must be non-empty")]
    issues: list[ValidationIssue] = []
    if len(name) > _NAME_MAX:
        issues.append(
            ValidationIssue(
                "name",
                f"exceeds {_NAME_MAX} character limit (got {len(name)})",
            )
        )
    if not _VALID_NAME_CHARS.match(name):
        issues.append(
            ValidationIssue(
                "name",
                "must contain only lowercase letters, digits, and hyphens",
            )
        )
    if name.startswith("-"):
        issues.append(ValidationIssue("name", "must not start with a hyphen"))
    if name.endswith("-"):
        issues.append(ValidationIssue("name", "must not end with a hyphen"))
    if "--" in name:
        issues.append(ValidationIssue("name", "must not contain consecutive hyphens"))
    return issues


# ── Description validation ─────────────────────────────────────────────────

_DESC_MAX = 1024


def _validate_description(desc: object) -> list[ValidationIssue]:
    """Return issues for the ``description`` field."""
    if desc is None:
        return [ValidationIssue("description", "required field is missing")]
    if not isinstance(desc, str):
        return [
            ValidationIssue(
                "description",
                f"must be a string, got {type(desc).__name__}",
            )
        ]
    if not desc.strip():
        return [ValidationIssue("description", "must be non-empty")]
    if len(desc) > _DESC_MAX:
        return [
            ValidationIssue(
                "description",
                f"exceeds {_DESC_MAX} character limit (got {len(desc)})",
            )
        ]
    return []


# ── Optional field validators ──────────────────────────────────────────────

_COMPAT_MAX = 500


def _validate_optional_fields(fm: dict) -> list[ValidationIssue]:
    """Validate optional agentskills.io frontmatter fields when present.

    All checks run off the real-parsed dict from ``yaml.safe_load``.
    """
    issues: list[ValidationIssue] = []

    # license — optional, must be str if present.
    license_val = fm.get("license")
    if license_val is not None and not isinstance(license_val, str):
        issues.append(
            ValidationIssue(
                "license",
                f"must be a string if present, got {type(license_val).__name__}",
            )
        )

    # compatibility — optional, must be str, max 500 chars.
    compat_val = fm.get("compatibility")
    if compat_val is not None:
        if not isinstance(compat_val, str):
            issues.append(
                ValidationIssue(
                    "compatibility",
                    f"must be a string if present, got {type(compat_val).__name__}",
                )
            )
        elif len(compat_val) > _COMPAT_MAX:
            issues.append(
                ValidationIssue(
                    "compatibility",
                    f"exceeds {_COMPAT_MAX} character limit (got {len(compat_val)})",
                )
            )

    # metadata — optional, must be a mapping (dict) if present.
    meta_val = fm.get("metadata")
    if meta_val is not None and not isinstance(meta_val, dict):
        actual = type(meta_val).__name__ if meta_val is not None else "null"
        issues.append(
            ValidationIssue(
                "metadata",
                f"must be a mapping if present, got {actual}",
            )
        )

    # allowed-tools — optional, must be str (space-separated per spec).
    # A YAML list is a conformance FAILURE.
    allowed_tools = fm.get("allowed-tools")
    if allowed_tools is not None and not isinstance(allowed_tools, str):
        issues.append(
            ValidationIssue(
                "allowed-tools",
                f"must be a space-separated string if present, got {type(allowed_tools).__name__}",
            )
        )

    return issues


# ── Frontmatter fence helper ───────────────────────────────────────────────


def _split_frontmatter_text(text: str) -> str | None:
    """Return the raw text between the ``---`` fences, or None if absent/unterminated.

    Used only to isolate the frontmatter block for ``yaml.safe_load`` — NOT
    for field extraction.

    Tolerances applied before fence detection:
    - A leading UTF-8 BOM (U+FEFF) is stripped.
    - Leading blank / whitespace-only lines before the opening ``---`` are
      skipped.  The closing fence and body handling are unchanged.
    """
    # Strip leading BOM if present.
    if text.startswith("﻿"):
        text = text[1:]

    lines = text.splitlines()

    # Skip leading blank/whitespace-only lines to find the opening fence.
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1

    if start >= len(lines) or lines[start].strip() != "---":
        return None

    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[start + 1 : i])
    return None  # unterminated fence


# ── Public API ─────────────────────────────────────────────────────────────


def validate_skill(skill_dir: Path) -> list[ValidationIssue]:
    """Validate a skill directory against the agentskills.io standard.

    Uses ``yaml.safe_load`` as the authoritative frontmatter parser (ADR-0046).
    If frontmatter fails YAML parsing, that is a conformance FAILURE — a real
    agentskills.io consumer would reject the skill.

    Returns a list of :class:`ValidationIssue` objects.  An empty list
    means the skill is conformant.  Never raises on malformed or missing
    SKILL.md — parse failures are returned as issues.

    Args:
        skill_dir: Path to the skill directory (must contain ``SKILL.md``).
    """
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        return [ValidationIssue("SKILL.md", "file not found in skill directory")]

    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return [ValidationIssue("SKILL.md", f"could not read file: {exc}")]

    fm_text = _split_frontmatter_text(text)
    if fm_text is None:
        return [ValidationIssue("SKILL.md", "no YAML frontmatter found (missing --- fence)")]

    # Parse the frontmatter block with the real YAML parser.
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        first_line = str(exc).splitlines()[0]
        return [ValidationIssue("SKILL.md", f"frontmatter is not valid YAML: {first_line}")]

    if not isinstance(fm, dict):
        actual = type(fm).__name__ if fm is not None else "null"
        return [ValidationIssue("SKILL.md", f"frontmatter must be a YAML mapping, got {actual}")]

    issues: list[ValidationIssue] = []
    issues.extend(_validate_name(fm.get("name")))
    issues.extend(_validate_description(fm.get("description")))
    issues.extend(_validate_optional_fields(fm))
    return issues


class SkillExportError(Exception):
    """Raised by export_skill() when validation fails and force=False."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(
            "Skill failed agentskills.io conformance validation:\n"
            + "\n".join(f"  - {i}" for i in issues)
        )


def _resolve_skill_name(skill_dir: Path) -> str:
    """Return the skill name from frontmatter, or the directory name as fallback.

    Uses ``yaml.safe_load`` for parsing; falls back to the directory name if
    YAML is invalid or the name field is absent.
    """
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        try:
            text = skill_md.read_text(encoding="utf-8")
            fm_text = _split_frontmatter_text(text)
            if fm_text:
                fm = yaml.safe_load(fm_text)
                if isinstance(fm, dict):
                    name = fm.get("name")
                    if isinstance(name, str) and name:
                        return name
        except (OSError, yaml.YAMLError):
            pass
    return skill_dir.name


def export_skill(
    skill_dir: Path,
    dest_dir: Path,
    *,
    force: bool = False,
) -> ExportResult:
    """Export a skill directory as a portable agentskills.io-conformant bundle.

    Validates first.  If issues are found and ``force`` is False, raises
    :class:`SkillExportError`.  If ``force`` is True, exports anyway
    (useful for debugging non-conformant skills).

    Path-traversal safety: the resolved skill name must be a simple path
    component (no ``/``, ``\\``, ``..``, or empty).  This check applies even
    when ``force=True`` — a traversal attempt is always rejected.

    On success, copies ``SKILL.md`` plus any of ``scripts/``,
    ``references/``, ``assets/`` that exist into
    ``dest_dir/<skill-name>/``, preserving directory structure.

    Args:
        skill_dir:  Source skill directory.
        dest_dir:   Destination parent directory.  The bundle lands at
                    ``dest_dir/<skill-name>/``.
        force:      If True, skip the conformance gate and export anyway.

    Returns:
        :class:`ExportResult` with the skill name, bundle path, and list
        of written files.

    Raises:
        SkillExportError: If validation fails and ``force`` is False.
        ValueError: If the resolved skill name is unsafe (path traversal).
        OSError: If the source skill or destination cannot be accessed.
    """
    issues = validate_skill(skill_dir)
    if issues and not force:
        raise SkillExportError(issues)

    skill_name = _resolve_skill_name(skill_dir)

    # Path-traversal guard — applies even under force=True.
    if not skill_name or skill_name != Path(skill_name).name or ".." in skill_name:
        raise ValueError(
            f"Unsafe skill name {skill_name!r}: must be a simple path component "
            "(no path separators, '..', or empty string)"
        )

    skill_md = skill_dir / "SKILL.md"

    bundle_dir = dest_dir / skill_name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    # Copy SKILL.md.
    dest_skill_md = bundle_dir / "SKILL.md"
    shutil.copy2(skill_md, dest_skill_md)
    written.append(dest_skill_md)

    # Copy optional sibling directories.
    for subdir_name in ("scripts", "references", "assets"):
        src_subdir = skill_dir / subdir_name
        if src_subdir.is_dir():
            dest_subdir = bundle_dir / subdir_name
            if dest_subdir.exists():
                shutil.rmtree(dest_subdir)
            shutil.copytree(src_subdir, dest_subdir)
            for f in dest_subdir.rglob("*"):
                if f.is_file():
                    written.append(f)

    return ExportResult(
        skill_name=skill_name,
        dest_bundle=bundle_dir,
        written_files=written,
    )
