"""Skill/agent/script metadata registry for sage.

Scans three on-disk locations and builds a lightweight index:

- ``agents/*.md``          → kind=agent
- ``skills/*/SKILL.md``   → kind=skill
- ``scripts/*``            → kind=script  (skips binaries)

Each entry is a small descriptor dict:

.. code-block:: python

    {
        "name":     "session-lifecycle",
        "kind":     "skill",          # agent | skill | script
        "one_line": "Use at session start ...",  # ≤120-char descriptor
        "triggers": ["session", "start", "lifecycle"],
        "path":     "skills/session-lifecycle/SKILL.md",
    }

The registry MUST NOT store full artifact bodies — only descriptors that
point back to on-disk paths.  This matches PRD §6: "Index a 40-token
descriptor; load the 2,000-token artifact only when actually invoked."

Build is deterministic: scanning the same unchanged tree twice yields
identical output (entries sorted by path before returning).

Usage::

    from sage_mcp.extensions.skill_registry import build_registry, search_registry

    entries = build_registry("/path/to/sage-repo")
    results = search_registry(entries, "session lifecycle")
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Optional

# ── Secret-scrub (shared module) ──────────────────────────────────────────
# ADR-0042: registry one-liners use the high-confidence (write-boundary)
# scrub only. The registry feeds Tier-0, but its content is metadata-only
# (name, one_line, triggers) — no git SHAs or raw SHA-annotated content
# is expected. The aggressive hex≥40 pass is applied once to the FULL
# assembled Tier-0 block in layers.py::assemble_tier0, which covers the
# registry section too. Using aggressive scrub here would double-scrub
# and could incorrectly redact descriptor tokens.
from ..secret_scrub import scrub_secrets as _scrub_secrets

logger = logging.getLogger(__name__)

# Maximum length for the one_line descriptor (PRD §6: ~40 tokens ≈ 160 chars;
# we cap conservatively at 120 to keep the Tier-0 block lean).
_ONE_LINE_MAX = 120

# Module-level cache keyed by repo root.  Each value is a tuple of
# (entries, build_mtime) where build_mtime is the max mtime (float) of the
# three scanned directories at build time.  Used by the staleness check in
# build_registry to auto-rebuild when agents/, skills/, or scripts/ are edited.
_cache_lock = threading.Lock()
_cached_registry: dict[str, tuple[list[dict], float]] = {}  # root → (entries, build_mtime)


# ── Frontmatter parser ─────────────────────────────────────────────────────

# Regex matching the start of a top-level frontmatter key (e.g. "name:",
# "description:").  Used by _extract_fm_field to stop continuation capture.
_FM_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    """Return (frontmatter_lines, body_text).  Empty list if no fence."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body = "\n".join(lines[i + 1 :])
            return lines[1:i], body
    return [], text  # unterminated fence — treat as no frontmatter


# Matches YAML block scalar indicators on a line by themselves.
_BLOCK_SCALAR = re.compile(r"^[|>][+-]?$")


def _extract_fm_field(fm_lines: list[str], key: str) -> str:
    """Manual frontmatter field reader — robust to colons inside the value.

    Captures the value on the ``key:`` line plus any indented continuation
    lines, stopping at the next top-level ``key:`` line.  This mirrors
    ``measure_framework_surface.extract_field`` exactly and handles
    descriptions like ``PAUSE: need nook lookup for <query>`` that cause
    ``yaml.safe_load`` to return an empty dict silently.

    Block-scalar indicators (``|``, ``>``, ``>-``, ``|-``) on the value
    portion are dropped; the following continuation lines become the value
    (FIX E).

    Returns empty string when the key is absent.
    """
    prefix = key + ":"
    out: list[str] = []
    capturing = False
    for ln in fm_lines:
        if not capturing:
            if ln.startswith(prefix):
                capturing = True
                inline_value = ln[len(prefix) :].strip()
                # Drop bare block-scalar indicator — content is on following lines.
                if not _BLOCK_SCALAR.match(inline_value):
                    out.append(inline_value)
            continue
        # Stop at the next top-level key.
        if _FM_KEY.match(ln):
            break
        out.append(ln.strip())
    if not capturing:
        return ""
    return " ".join(p for p in out if p).strip()


def _parse_frontmatter_fields(text: str) -> dict[str, str]:
    """Return a dict of extracted frontmatter fields using the robust manual parser.

    Extracts ``name`` and ``description`` (the two fields the registry uses).
    Falls back to empty strings when absent.  Never uses yaml.safe_load — that
    silently returns ``{}`` for unquoted colons in values (FIX 1).
    """
    fm_lines, _ = _split_frontmatter(text)
    if not fm_lines:
        return {}
    return {
        "name": _extract_fm_field(fm_lines, "name"),
        "description": _extract_fm_field(fm_lines, "description"),
    }


# ── Ignore matching (FIX 2a + FIX B) ────────────────────────────────────────


def _load_gitignore_matcher_class():
    """Import GitignoreMatcher from the lightweight ignore module.

    ``sage.ignore`` is a pure-stdlib module with no chromadb or
    vector dependencies, so this import succeeds even when optional heavy
    deps are absent.  The function signature is kept (vs. a direct import)
    so the monkeypatch surface in tests (patching ``_load_gitignore_matcher_class``
    to raise ImportError) stays intact for the FIX B warning path.
    """
    import importlib

    mod = importlib.import_module("sage_mcp.ignore")
    return mod.GitignoreMatcher


def _load_nookignore(repo_root: Path):
    """Return a GitignoreMatcher for the repo's ``.sageignore``, or None.

    Reuses the proven GitignoreMatcher from miner.py — the same class that
    handles the mine-ignore lane elsewhere in sage.

    If the class cannot be loaded (e.g. chromadb uninstalled), emits a visible
    warning and returns None.  The secret-scrub pass remains active as
    defense-in-depth; only the path-filter control degrades.
    """
    try:
        GitignoreMatcher = _load_gitignore_matcher_class()
        return GitignoreMatcher.from_dir(repo_root, filename=".sageignore")
    except Exception as exc:
        logger.warning(
            "skill_registry: .sageignore ignore-matcher unavailable "
            "(%s: %s); path filtering disabled — secret-scrub still active.",
            type(exc).__name__,
            exc,
        )
        return None


def _is_ignored(path: Path, ignore_matcher) -> bool:
    """Return True if ``path`` is matched by the ignore_matcher (or matcher is None)."""
    if ignore_matcher is None:
        return False
    decision = ignore_matcher.matches(path)
    return bool(decision)


# ── One-liner extraction ───────────────────────────────────────────────────


def _truncate_one_line(text: str) -> str:
    """Truncate ``text`` to ``_ONE_LINE_MAX`` chars; append ``…`` if cut."""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= _ONE_LINE_MAX:
        return text
    return text[: _ONE_LINE_MAX - 1].rstrip() + "…"


def _one_line_from_fields(fields: dict) -> str:
    """Extract a one-liner from parsed frontmatter fields.

    Priority: ``description`` field → first sentence of that field.
    Returns the FULL first sentence WITHOUT truncation so the caller can
    scrub secrets before truncating (FIX C: scrub-before-truncation).
    Returns empty string if no usable description is found.
    """
    raw = fields.get("description", "")
    if not raw or not isinstance(raw, str):
        return ""
    # First sentence: split on ". " or end-of-string.
    first_sentence = raw.split(". ")[0].rstrip(".")
    return first_sentence.strip()


def _one_line_from_content_body(text: str, skip_frontmatter: bool = True) -> str:
    """Extract a one-liner from file content when frontmatter yields nothing.

    For markdown files: first non-empty, non-heading line after frontmatter.
    For script files: first comment line (# ... or // ...).

    Returns the FULL candidate text WITHOUT truncation so the caller can
    scrub secrets before truncating (FIX C: scrub-before-truncation).
    """
    lines = text.splitlines()
    in_frontmatter = False
    frontmatter_done = False
    fm_fence_count = 0

    for line in lines:
        stripped = line.strip()

        # Track YAML frontmatter block only when skip_frontmatter=True.
        # When skip_frontmatter=False (script files), a leading '---' is
        # NOT stripped — the caller at line 541 explicitly passes False so
        # script files keep their leading separator.
        if skip_frontmatter and not frontmatter_done and stripped == "---":
            fm_fence_count += 1
            if fm_fence_count == 1:
                in_frontmatter = True
                continue
            if fm_fence_count == 2:
                in_frontmatter = False
                frontmatter_done = True
                continue
        if in_frontmatter:
            continue

        if not stripped:
            continue

        # Shebang line (e.g. #!/bin/bash, #!/usr/bin/env python3) — skip,
        # never use as the one_line descriptor.
        if stripped.startswith("#!"):
            continue

        # Script comment lines (# text or // text or /* text) — use the
        # first one as the one_line.  Check BEFORE the generic heading skip
        # so `# description text` in a script isn't dropped as a heading.
        if stripped.startswith(("# ", "// ", "/* ")):
            candidate = re.sub(r"^[#/!*]+\s*", "", stripped)
            return candidate.strip()

        # Skip markdown headings (bare # or ## etc. without following text
        # are section titles, not descriptors).
        if stripped.startswith("#"):
            continue

        # Python/JS triple-quote docstring opener — strip the quotes and
        # use the inline text (if any) or the next pass's first line.
        if stripped.startswith(('"""', "'''")):
            candidate = stripped[3:].strip().rstrip("\"'").strip()
            if candidate:
                return candidate
            # Docstring content is on the next line — continue to pick it up.
            continue

        # First substantive prose line.
        return stripped

    return ""


# ── Trigger extraction ─────────────────────────────────────────────────────


def _triggers_from_text(one_line: str, name: str, kind: str) -> list[str]:
    """Derive a deduplicated keyword list from the one_line + name + kind.

    Splits on whitespace and punctuation, lowercases, drops stop-words,
    keeps tokens of length ≥ 3.  The name parts (hyphen-split) and kind
    are always included.
    """
    _STOP = frozenset(
        {
            "a",
            "an",
            "the",
            "and",
            "or",
            "but",
            "for",
            "to",
            "in",
            "of",
            "on",
            "at",
            "is",
            "it",
            "its",
            "be",
            "use",
            "by",
            "from",
            "with",
            "not",
            "do",
            "as",
            "if",
            "any",
            "all",
            "when",
            "that",
            "this",
            "per",
            "via",
            "vs",
        }
    )
    tokens: list[str] = []

    # Always include name parts and kind.
    tokens.extend(p.lower() for p in re.split(r"[-_./]", name) if len(p) >= 2)
    tokens.append(kind)

    # Tokenise the one_line descriptor.
    for token in re.split(r"[\s,;:()\[\]`'\"]+", one_line):
        token = token.strip(".!?/\\").lower()
        if len(token) >= 3 and token not in _STOP:
            tokens.append(token)

    # Deduplicate preserving first-occurrence order.
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


# ── Mtime helper (FIX 3 + FIX A) ─────────────────────────────────────────


def _max_mtime_of_dirs(*dirs: Path) -> float:
    """Return the maximum mtime (float) across all files and dirs under each
    scanned directory (recursive).

    Recurses into subdirectories so that editing a nested file such as
    ``skills/<name>/SKILL.md`` is detected — a change to that file bumps
    the file's own mtime but may not bump the top-level ``skills/`` dir
    mtime on all filesystems (FIX A: the old top-dir-only stat missed this).

    Skips ``__pycache__`` directories and their contents — those dirs are
    touched by Python on import and would cause spurious cache misses if
    included in the staleness signal.
    """
    max_mt: float = 0.0
    for d in dirs:
        if not d.is_dir():
            continue
        # Walk all entries (files and dirs) recursively, skipping __pycache__.
        for entry in d.rglob("*"):
            # Skip __pycache__ trees — Python writes these on import and they
            # are irrelevant to registry content.
            if "__pycache__" in entry.parts:
                continue
            try:
                mt = entry.stat().st_mtime
                if mt > max_mt:
                    max_mt = mt
            except OSError:
                pass
        # Also stat the root dir itself.
        try:
            mt = d.stat().st_mtime
            if mt > max_mt:
                max_mt = mt
        except OSError:
            pass
    return max_mt


# ── Per-kind scanners ──────────────────────────────────────────────────────


def _scan_agents(repo_root: Path, ignore_matcher) -> list[dict]:
    """Scan ``agents/*.md`` and return descriptor entries."""
    agents_dir = repo_root / "agents"
    if not agents_dir.is_dir():
        return []

    entries: list[dict] = []
    for md_path in sorted(agents_dir.glob("*.md")):
        if md_path.name.startswith("."):
            continue
        if _is_ignored(md_path, ignore_matcher):
            continue
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fields = _parse_frontmatter_fields(text)
        name = fields.get("name") or md_path.stem
        # FIX C: scrub full text BEFORE truncating so boundary-straddling
        # secrets are caught in full before the cut discards trailing chars.
        full_text = _one_line_from_fields(fields)
        if not full_text:
            full_text = _one_line_from_content_body(text)
        full_text = _scrub_secrets(full_text)
        one_line = _truncate_one_line(full_text)

        rel_path = str(md_path.relative_to(repo_root))
        # Derive triggers from the full scrubbed text (pre-truncation) so no
        # token from a boundary-cut partial secret leaks into the trigger list.
        triggers = _triggers_from_text(full_text, name, "agent")
        entries.append(
            {
                "name": name,
                "kind": "agent",
                "one_line": one_line,
                "triggers": triggers,
                "path": rel_path,
            }
        )
    return entries


def _scan_skills(repo_root: Path, ignore_matcher) -> list[dict]:
    """Scan ``skills/*/SKILL.md`` and return descriptor entries."""
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return []

    entries: list[dict] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        if _is_ignored(skill_md, ignore_matcher):
            continue
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fields = _parse_frontmatter_fields(text)
        name = fields.get("name") or skill_md.parent.name
        # FIX C: scrub full text BEFORE truncating.
        full_text = _one_line_from_fields(fields)
        if not full_text:
            full_text = _one_line_from_content_body(text)
        full_text = _scrub_secrets(full_text)
        one_line = _truncate_one_line(full_text)

        rel_path = str(skill_md.relative_to(repo_root))
        triggers = _triggers_from_text(full_text, name, "skill")
        entries.append(
            {
                "name": name,
                "kind": "skill",
                "one_line": one_line,
                "triggers": triggers,
                "path": rel_path,
            }
        )
    return entries


_BINARY_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".zip",
        ".gz",
        ".tar",
        ".bz2",
        ".xz",
        ".whl",
        ".db",
        ".sqlite3",
        ".sqlite",
    }
)


def _is_binary_path(path: Path) -> bool:
    """Return True when ``path``'s suffix is a known binary type."""
    return path.suffix.lower() in _BINARY_SUFFIXES


def _scan_scripts(repo_root: Path, ignore_matcher) -> list[dict]:
    """Scan ``scripts/*`` (non-binary files only) and return descriptor entries.

    Only scans direct children of the scripts directory (no recursion) to
    match the plan's "scripts/*" specification.  Binaries are skipped.
    """
    scripts_dir = repo_root / "scripts"
    if not scripts_dir.is_dir():
        return []

    entries: list[dict] = []
    for script_path in sorted(scripts_dir.iterdir()):
        if not script_path.is_file():
            continue
        if script_path.name.startswith("."):
            continue
        if _is_binary_path(script_path):
            continue
        if _is_ignored(script_path, ignore_matcher):
            continue
        try:
            # Read only the first 4 KB — enough for the first docstring/comment.
            text = script_path.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            continue

        name = script_path.stem or script_path.name
        # FIX C: scrub full text BEFORE truncating.
        full_text = _one_line_from_content_body(text, skip_frontmatter=False)
        full_text = _scrub_secrets(full_text)
        one_line = _truncate_one_line(full_text)

        rel_path = str(script_path.relative_to(repo_root))
        triggers = _triggers_from_text(full_text, name, "script")
        entries.append(
            {
                "name": name,
                "kind": "script",
                "one_line": one_line,
                "triggers": triggers,
                "path": rel_path,
            }
        )
    return entries


# ── Public API ─────────────────────────────────────────────────────────────


def build_registry(
    repo_root: str | Path,
    force_rebuild: bool = False,
) -> list[dict]:
    """Scan agents, skills, and scripts under ``repo_root`` and return a
    sorted list of metadata descriptors.

    The result is cached in-process by ``repo_root`` so repeated calls
    within a session are cheap.  The cache is automatically invalidated
    when any scanned directory's mtime advances past the build-time snapshot
    (FIX 3: no restart needed after editing/adding artifacts).  Pass
    ``force_rebuild=True`` to force a rebuild regardless.

    Lock discipline (ADV-11): the mtime staleness check runs OUTSIDE the
    lock so the O(n) filesystem I/O walk does not hold ``_cache_lock`` and
    serialize callers.  The lock is only held for the brief cache-dict read
    (hit check) and the final cache-dict write.  Scan + mtime sampling also
    run outside the lock; on obtaining the lock for the write the cache is
    re-checked and only overwritten if still stale, so a concurrent scanner
    doesn't duplicate work.

    Output is sorted by ``path`` to guarantee determinism: building the
    registry twice on an unchanged tree produces identical output.

    No full artifact body is stored in any entry — only the metadata
    fields listed in the module docstring.
    """
    root = Path(repo_root).expanduser().resolve()
    key = str(root)

    scanned_dirs = (root / "agents", root / "skills", root / "scripts")

    # Fast path: staleness check runs OUTSIDE the lock (ADV-11).
    # The mtime walk is O(n) I/O — holding the lock across it would serialize
    # every concurrent caller and stat irrelevant trees under the lock.
    if not force_rebuild:
        # Brief lock-hold: read the cache dict only.
        with _cache_lock:
            cached = _cached_registry.get(key)

        if cached is not None:
            entries, build_mtime = cached
            # Staleness check: O(n) mtime walk runs outside the lock.
            current_mtime = _max_mtime_of_dirs(*scanned_dirs)
            if current_mtime <= build_mtime:
                return list(entries)
            # Mtime advanced — fall through to rebuild below.

    # Load the ignore matcher once per build (repo root only).
    ignore_matcher = _load_nookignore(root)

    entries: list[dict] = []
    entries.extend(_scan_agents(root, ignore_matcher))
    entries.extend(_scan_skills(root, ignore_matcher))
    entries.extend(_scan_scripts(root, ignore_matcher))

    # Sort by path for determinism.
    entries.sort(key=lambda e: e["path"])

    # Record the mtime snapshot at the moment we finish scanning.
    # This also runs outside the lock.
    build_mtime = _max_mtime_of_dirs(*scanned_dirs)

    # Re-check under write lock: another thread may have built and stored
    # a fresh entry while we were scanning outside the lock.
    with _cache_lock:
        if force_rebuild or key not in _cached_registry:
            _cached_registry[key] = (entries, build_mtime)
        else:
            # Another thread built the cache while we were scanning — prefer
            # the freshest build_mtime between the two.
            existing_entries, existing_mtime = _cached_registry[key]
            if build_mtime >= existing_mtime:
                _cached_registry[key] = (entries, build_mtime)
            else:
                entries = existing_entries

    return list(entries)


def _invalidate_cache(repo_root: Optional[str | Path] = None) -> None:
    """Drop the cached registry for ``repo_root`` (or all roots if None)."""
    with _cache_lock:
        if repo_root is None:
            _cached_registry.clear()
        else:
            key = str(Path(repo_root).expanduser().resolve())
            _cached_registry.pop(key, None)


def search_registry(
    entries: list[dict],
    query: str,
    kind: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Lightweight keyword search over a pre-built registry entry list.

    Matches against ``name``, ``one_line``, and ``triggers``.  Returns at
    most ``limit`` results ordered by match score (descending), with ties
    broken by ``path`` alphabetically.

    ``kind`` filters to ``"agent"``, ``"skill"``, or ``"script"`` when set.

    This is a pure in-process search — no nook, no ChromaDB, no
    embeddings.  The caller is responsible for loading and calling
    ``build_registry()`` first.  For semantic search, use ``nook_search``
    with a Tier-1 retrieval query after using this to discover the pointer.
    """
    if not query or not query.strip():
        subset = [e for e in entries if kind is None or e["kind"] == kind]
        return subset[:limit]

    query_tokens = set(t.lower() for t in re.split(r"\W+", query) if len(t) >= 2)

    scored: list[tuple[int, str, dict]] = []
    for entry in entries:
        if kind is not None and entry["kind"] != kind:
            continue
        score = 0
        name_lower = entry["name"].lower()
        one_line_lower = entry["one_line"].lower()
        triggers_set = set(t.lower() for t in entry["triggers"])

        for token in query_tokens:
            if token in name_lower:
                score += 3
            if token in one_line_lower:
                score += 2
            if token in triggers_set:
                score += 1

        if score > 0:
            scored.append((score, entry["path"], entry))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, _, e in scored[:limit]]
