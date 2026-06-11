"""sage_mcp.estate.adapter.workshop — Workshop building adapter (Phase 1, file-only).

Reads the agents directory (``*.md`` files with YAML frontmatter) plus the
skills, rules, and hooks directories (passed as explicit path arguments — never
hard-coded to ``~/.claude``).  Emits the ``workshop`` building dict conforming
to the ``workshop_building`` $def in estate-model.schema.json.

**Slot-ledger semantics (ADR-0005):**
- Each agent id is assigned a slot at first-sight.  Assignment is
  deterministic: agents are processed in lexicographic order by id within each
  family, so a fresh ledger always produces the same slots.
- The ledger is an id→slot mapping carried by the caller (and persisted across
  runs).  Passing the same ledger back on subsequent calls gives stable slots
  even after agents are added or removed.
- A deleted agent's slot is never reclaimed.  The next new agent takes the
  highest current slot + 1.
- Buckets: each distinct family is a bucket.  Bucket slot = the slot of the
  family's first agent (first-sight, lexicographic within family).

**Security invariants (ADR-0003 / sec F2, F3, F4):**
- Every scanned path is ``realpath``-confined to its declared root directory.
  Paths that escape the root (e.g. via symlinks or ``../`` traversal) raise
  ``ValueError``.
- ``followlinks=False`` on all ``os.walk`` calls.
- The ``sage.estate.redact`` module is the sole enforcement point; description
  and other string fields are run through it before emission.
- No ``shell=True``, no subprocess, no network.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from sage_mcp.estate.redact import redact_string, strip_home_path

# ── YAML frontmatter extraction ───────────────────────────────────────────────

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract and parse the YAML frontmatter block from *text*.

    Returns an empty dict if no frontmatter is found or if parsing fails.
    Failures are silently swallowed so a single malformed agent file does not
    abort a full scan; the caller fills in defaults.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}
    try:
        result = yaml.safe_load(m.group(1))
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


# ── Family derivation ─────────────────────────────────────────────────────────


def _family_from_id(agent_id: str) -> str:
    """Derive the agent family from its id (the prefix before the first ``-``).

    >>> _family_from_id("dev-architect")
    'dev'
    >>> _family_from_id("aidev-code-implementer")
    'aidev'
    >>> _family_from_id("solo")
    'solo'
    """
    return agent_id.split("-", 1)[0]


# ── Path confinement ──────────────────────────────────────────────────────────


def _confined_realpath(path: Path, root: Path) -> Path:
    """Resolve *path* to its real absolute path and assert it is under *root*.

    Raises ``ValueError`` if the resolved path escapes *root* (e.g. via a
    symlink pointing outside the root).  *root* must already be resolved.
    """
    resolved = Path(os.path.realpath(path))
    # resolved must start with root (use os.fspath for reliable prefix check)
    root_str = str(root)
    resolved_str = str(resolved)
    if not (resolved_str == root_str or resolved_str.startswith(root_str + os.sep)):
        raise ValueError(
            f"Path escape detected: {path!r} resolves to {resolved!r}, outside root {root!r}"
        )
    return resolved


# ── Armory counts ─────────────────────────────────────────────────────────────


def _count_files(directory: Path, glob: str, *, followlinks: bool = False) -> int:
    """Count files matching *glob* under *directory* (non-recursive glob).

    Uses ``os.walk`` with ``followlinks=False`` and realpath-confinement for
    recursive scans.  For a flat glob (no ``**``), only the top-level dir is
    examined to keep it simple and safe.
    """
    if not directory.is_dir():
        return 0
    root = Path(os.path.realpath(directory))
    count = 0
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=followlinks):
        dp = Path(os.path.realpath(dirpath))
        # Confine: every walked subdir must stay under root.
        root_str = str(root)
        dp_str = str(dp)
        if not (dp_str == root_str or dp_str.startswith(root_str + os.sep)):
            continue
        for fname in filenames:
            fpath = dp / fname
            if fpath.match(glob):
                count += 1
    return count


def _count_skills(skills_dir: Path) -> int:
    """Count ``SKILL.md`` files anywhere under *skills_dir*."""
    return _count_files(skills_dir, "SKILL.md")


def _count_rules(rules_dir: Path) -> int:
    """Count ``*.md`` files in the top level of *rules_dir*."""
    if not rules_dir.is_dir():
        return 0
    root = Path(os.path.realpath(rules_dir))
    return sum(1 for f in root.iterdir() if f.is_file() and f.suffix == ".md")


def _count_hooks(hooks_dir: Path) -> int:
    """Count all files anywhere under *hooks_dir* (scripts + support files)."""
    return _count_files(hooks_dir, "*")


# ── Slot ledger helpers ───────────────────────────────────────────────────────


def _assign_slots(
    agent_ids: list[str],
    ledger: dict[str, int],
) -> dict[str, int]:
    """Assign slots to *agent_ids* using (and updating) *ledger* in place.

    Algorithm:
    1. Agents already in *ledger* keep their existing slot (stable).
    2. New agents (not yet in *ledger*) are processed in the order they appear
       in *agent_ids* (which the caller ensures is deterministic — lexicographic
       by id within family).
    3. Each new agent takes ``max(current_slots) + 1``, or 0 if the ledger is
       empty.  This means added agents always append; they never reflow
       survivors.

    Returns the updated *ledger* (same object, mutated).
    """
    for agent_id in agent_ids:
        if agent_id not in ledger:
            next_slot = max(ledger.values()) + 1 if ledger else 0
            ledger[agent_id] = next_slot
    return ledger


# ── Bucket builder ────────────────────────────────────────────────────────────


def _build_buckets(
    agents_by_family: dict[str, list[dict[str, Any]]],
    ledger: dict[str, int],
    bucket_ledger: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """Build the bucket map keyed by family using a persistent bucket-slot ledger.

    A family's bucket slot is assigned at first-sight (when the family key first
    appears in *bucket_ledger*) and NEVER changes afterwards, even if the
    family's first-seen agent is later removed (ADR-0005 never-reflow).

    Assignment at first-sight:
    - If the family is already in *bucket_ledger*, reuse its recorded slot.
    - Otherwise, assign ``min(family_slots)`` as the bucket slot and record it.

    *bucket_ledger* is mutated in place.

    Returns a dict: family → bucket dict (``{key, slot}``).
    """
    buckets: dict[str, dict[str, Any]] = {}
    for family, agents in agents_by_family.items():
        family_slots = [ledger[a["id"]] for a in agents if a["id"] in ledger]
        if not family_slots:
            continue
        if family not in bucket_ledger:
            bucket_ledger[family] = min(family_slots)
        buckets[family] = {"key": family, "slot": bucket_ledger[family]}
    return buckets


# ── Agent file parser ─────────────────────────────────────────────────────────


def _parse_agent_file(path: Path, root: Path) -> dict[str, Any] | None:
    """Parse one agent ``.md`` file and return a partial agent dict.

    Returns ``None`` if the file cannot be read or doesn't look like an agent
    manifest (no frontmatter, id derivable from filename only).

    The returned dict always has ``id`` and ``family``; other fields are present
    only when found in frontmatter.  Slot is NOT set here (assigned by ledger).
    """
    try:
        _confined_realpath(path, root)
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None

    fm = _parse_frontmatter(text)

    # id: prefer frontmatter ``name`` field, fallback to stem.
    # strip_home_path is applied as defense-in-depth: id/family are not expected
    # to carry home paths, but if one slips through it must not survive (sev 55).
    agent_id: str = fm.get("name", "") or path.stem
    agent_id = str(agent_id).strip()
    if not agent_id:
        agent_id = path.stem
    agent_id = strip_home_path(agent_id)

    family = _family_from_id(agent_id)
    family = strip_home_path(family)

    agent: dict[str, Any] = {
        "id": agent_id,
        "family": family,
    }

    # model — optional
    model = fm.get("model", "")
    if model and isinstance(model, str):
        agent["model"] = redact_string(model)

    # tools — optional list.  strip_home_path applied to each entry (sev 55).
    tools = fm.get("tools", [])
    if isinstance(tools, list) and tools:
        agent["tools"] = [strip_home_path(str(t)) for t in tools if t]

    # description — optional, redacted
    desc = fm.get("description", "")
    if desc and isinstance(desc, str):
        agent["description"] = redact_string(desc.strip())

    return agent


# ── Public API ────────────────────────────────────────────────────────────────


def build_workshop(
    agents_dir: Path,
    *,
    skills_dir: Path | None = None,
    rules_dir: Path | None = None,
    hooks_dir: Path | None = None,
    ledger: dict[str, int] | None = None,
    bucket_ledger: dict[str, int] | None = None,
    title: str = "The Workshop",
) -> tuple[dict[str, Any], dict[str, int], dict[str, int]]:
    """Read the workshop sources and emit the ``workshop`` building dict.

    Parameters
    ----------
    agents_dir:
        Directory containing agent ``*.md`` files.  Must exist; malformed or
        unreadable files are skipped.
    skills_dir:
        Directory tree whose ``SKILL.md`` files are counted for the armory.
        ``None`` → 0.
    rules_dir:
        Directory whose top-level ``*.md`` files are counted as rules.
        ``None`` → 0.
    hooks_dir:
        Directory tree whose files are counted as hooks (scripts).
        ``None`` → 0.
    ledger:
        Existing id→slot mapping from a prior run.  Pass ``{}`` or ``None`` for
        a fresh run.  The ledger is updated in-place and returned alongside the
        building dict so the caller can persist it.
    bucket_ledger:
        Existing family→bucket-slot mapping from a prior run.  Pass ``{}`` or
        ``None`` for a fresh run.  A bucket's slot is assigned at first-sight
        of the family and NEVER changes afterwards (ADR-0005 never-reflow).
        The ledger is updated in-place and returned as the third tuple element.
    title:
        Display title for the building.

    Returns
    -------
    (workshop_dict, updated_ledger, updated_bucket_ledger)
        *workshop_dict* conforms to the ``workshop_building`` $def.
        *updated_ledger* is the (mutated) agent slot ledger — persist for
        stable slots on the next call.
        *updated_bucket_ledger* is the (mutated) bucket-slot ledger — persist
        for stable bucket slots on the next call.

    Security notes (ADR-0003, sec F2/F3/F4):
    - All paths are ``realpath``-confined to their declared root.
    - ``followlinks=False`` everywhere.
    - The redactor is applied to all string values from frontmatter.
    - No ``shell=True``, no subprocess, no network.
    """
    if ledger is None:
        ledger = {}
    if bucket_ledger is None:
        bucket_ledger = {}

    agents_dir = Path(agents_dir)
    agents_root = Path(os.path.realpath(agents_dir))

    # ── 1. Collect agent files ────────────────────────────────────────────────
    raw_agents: list[dict[str, Any]] = []

    if agents_root.is_dir():
        # Gather all .md files at the top level of agents_dir (not recursive —
        # agents are flat files, not nested).
        md_files = sorted(f for f in agents_root.iterdir() if f.is_file() and f.suffix == ".md")
        for md_path in md_files:
            agent = _parse_agent_file(md_path, agents_root)
            if agent is not None:
                raw_agents.append(agent)

    # ── 2. Group by family, sort lex within family ────────────────────────────
    # Deterministic order within each family: lexicographic by id.
    by_family: dict[str, list[dict[str, Any]]] = {}
    for agent in sorted(raw_agents, key=lambda a: (a["family"], a["id"])):
        fam = agent["family"]
        if fam not in by_family:
            by_family[fam] = []
        by_family[fam].append(agent)

    # Flat list in (family lex, id lex) order for slot assignment.
    ordered_ids = [a["id"] for fam in sorted(by_family) for a in by_family[fam]]

    # ── 3. Assign slots via the ledger ────────────────────────────────────────
    _assign_slots(ordered_ids, ledger)

    # ── 4. Build bucket map (with persistent bucket-slot ledger) ──────────────
    buckets = _build_buckets(by_family, ledger, bucket_ledger)

    # ── 5. Assemble final agent dicts (with slot + bucket) ────────────────────
    final_agents: list[dict[str, Any]] = []
    for fam in sorted(by_family):
        for agent in by_family[fam]:
            a = dict(agent)
            a["slot"] = ledger[a["id"]]
            bucket = buckets.get(fam)
            if bucket is not None:
                a["bucket"] = bucket
            final_agents.append(a)

    # Sort by slot for stable JSON output (slot order = first-sight order)
    final_agents.sort(key=lambda a: a["slot"])

    # ── 6. Armory counts ──────────────────────────────────────────────────────
    armory: dict[str, Any] = {}

    skills_count = _count_skills(skills_dir) if skills_dir else 0
    armory["skills"] = skills_count

    rules_count = _count_rules(rules_dir) if rules_dir else 0
    armory["rules"] = rules_count

    hooks_count = _count_hooks(hooks_dir) if hooks_dir else 0
    armory["hooks"] = hooks_count

    # tools: count of distinct tool names across all scanned agents.
    all_tools: set[str] = set()
    for agent in final_agents:
        for t in agent.get("tools", []):
            all_tools.add(t)
    armory["tools"] = len(all_tools)

    # ── 7. Assemble building dict ─────────────────────────────────────────────
    # strip_home_path on title as defense-in-depth (sev 55).
    workshop: dict[str, Any] = {
        "id": "workshop",
        "kind": "workshop",
        "title": strip_home_path(title),
        "agents": final_agents,
        "armory": armory,
    }

    return workshop, ledger, bucket_ledger
