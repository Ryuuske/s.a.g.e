#!/usr/bin/env python3
"""
layers.py — 4-Layer Memory Stack for sage
===================================================

Load only what you need, when you need it.

    Layer 0: Identity       (~100 tokens)   — Always loaded. "Who am I?"
    Layer 1: Essential Story (~500-800)      — Always loaded. Top moments from the nook.
    Layer 2: On-Demand      (~200-500 each)  — Loaded when a topic/wing comes up.
    Layer 3: Deep Search    (unlimited)      — Full ChromaDB semantic search.

Wake-up cost: ~600-900 tokens (L0+L1). Leaves 95%+ of context free.

Tier-0 cached-prefix block (WI-3):
    Extends the L0+L1 wake-up with a compact skill/agent registry section.
    Same wing + same nook state → byte-identical Tier-0 block (determinism
    contract for prompt-cache prefix stability).

Reads directly from ChromaDB (nook_drawers)
and ~/.sage/identity.txt.
"""

import hashlib
import os
import sys
from pathlib import Path
from collections import defaultdict

from .config import SageConfig
from .consolidation import CORE_HALL_NAME as _CORE_HALL_NAME
from .nook import get_collection as _get_collection
from .searcher import _first_or_empty, build_where_filter
from .secret_scrub import scrub_secrets_aggressive

# ---------------------------------------------------------------------------
# Tier-0 budget constant (WI-3 / PRD §7)
# ---------------------------------------------------------------------------
# Target range from the PRD is ~3–6k tokens.  This is a MEASUREMENT
# PARAMETER — not a pre-validated hard limit.  The default sits in the
# middle of the PRD range; WI-7a measures the actual assembled size and
# records it as the baseline.  Change this value only after measuring.
TIER0_TOKEN_BUDGET: int = 4500


# ---------------------------------------------------------------------------
# Tier-0 block (WI-3) — identity core + L1 wing halls + compact registry
# ---------------------------------------------------------------------------


class Tier0Block:
    """Assembled Tier-0 cached-prefix block.

    Attributes:
        text:         The full deterministic block text.
        token_count:  Estimated token count (len(text) // 4).
        budget:       The configured budget ceiling (TIER0_TOKEN_BUDGET).
        within_budget: True when token_count <= budget.
        registry_count: Number of registry entries included in the block.
    """

    def __init__(
        self,
        text: str,
        budget: int,
        registry_count: int,
    ) -> None:
        self.text = text
        self.token_count: int = len(text) // 4
        self.budget: int = budget
        self.within_budget: bool = self.token_count <= budget
        self.registry_count: int = registry_count

    def __str__(self) -> str:
        return self.text


def _build_registry_section(repo_root: str | None) -> tuple[str, int]:
    """Build the compact registry section for the Tier-0 block.

    Returns (section_text, entry_count).  Fails gracefully: returns
    an empty section if the registry cannot be built (e.g. no agents/
    or skills/ directory at repo_root).

    Only metadata fields are included (name + one_line + triggers[:3]).
    Full artifact bodies are never loaded — PRD §6 metadata-only rule.
    """
    if not repo_root:
        return "", 0

    try:
        from .extensions.skill_registry import build_registry

        entries = build_registry(repo_root)
    except Exception:
        return "", 0

    if not entries:
        return "", 0

    lines = [
        "## REGISTRY — skills / agents / scripts",
        "# name  |  kind  |  one_line  |  triggers[:3]",
    ]
    for e in entries:
        name = e.get("name", "")
        kind = e.get("kind", "")
        one_line = e.get("one_line", "")
        triggers = e.get("triggers", [])[:3]
        trigger_str = ", ".join(triggers)
        lines.append(f"  {name}  [{kind}]  {one_line}  ({trigger_str})")

    return "\n".join(lines), len(entries)


# ---------------------------------------------------------------------------
# Personal/core identity facts — WI-5
# ---------------------------------------------------------------------------
# Drawers written to wing="Personal", hall="core" are durable identity facts
# that surface in the Tier-0 block alongside the static identity.txt content.
# They must be excluded from WI-6 decay (hall=core distinguishes them).
#
# Drawers in hall="detail" are retrieval-only (Tier-1 / L2) and must NOT
# appear in Tier-0 assembly — they accumulate over time and would bloat the
# always-on prefix.
#
# Budget discipline: the Personal/core section is positioned BETWEEN L0 and
# L1 in the assembled block and is subject to the same budget ceiling +
# aggressive-scrub as the rest of Tier-0 (ADR-0042).
#
# NOTE — l1-config in wing_config.json is ADVISORY / documentation only.
# The field (wing_types["personal"]["l1"] == ["core"]) documents the intent
# that only the "core" hall feeds Tier-0, but it is NOT consumed here at
# runtime.  The actual Tier-0 detail-leak guard is the hardcoded
# ``hall=_PERSONAL_CORE_HALL`` filter in ``_load_personal_core`` below.
# A future reader must NOT assume the config field enforces the guard —
# the code is the contract, the config field is informational.

_PERSONAL_WING = "Personal"
# RESERVED PROTECTED hall name — shared with consolidation.CORE_HALL_NAME.
# ``core`` is a RESERVED protected hall name across ALL wings: any drawer in
# a ``core`` hall is treated as durable and excluded from WI-6 decay entirely
# (see ADR-0043).  Tier-0 sourcing (this file) uses the same constant for the
# Personal-scoped identity-core query so a rename of the constant
# automatically updates both the decay-exclusion guard and the Tier-0 source.
_PERSONAL_CORE_HALL = _CORE_HALL_NAME
_PERSONAL_CORE_MAX_DRAWERS = 10  # hard cap: keeps identity core compact
_PERSONAL_CORE_MAX_CHARS = 1200  # ~300 tokens max for personal core section


def _load_personal_core(nook_path: str) -> str:
    """Fetch Personal/core drawers and format as a compact identity-core section.

    Returns an empty string when the Personal wing has no core drawers or the
    nook is unreachable. Fails gracefully — any exception returns "".

    Deterministic sort: importance desc, then filed_at asc (oldest first so
    the most established facts appear first), then md5 hash of text as
    tertiary tiebreaker (same discipline as Layer1 — WI-3 byte-stable
    contract for the Tier-0 cache prefix).
    """
    try:
        col = _get_collection(nook_path, create=False)
    except Exception:
        return ""

    try:
        results = col.get(
            where={"$and": [{"wing": _PERSONAL_WING}, {"hall": _PERSONAL_CORE_HALL}]},
            include=["documents", "metadatas"],
            limit=_PERSONAL_CORE_MAX_DRAWERS,
        )
    except Exception:
        return ""

    docs = results.get("documents") or []
    metas = results.get("metadatas") or []

    if not docs:
        return ""

    # Score and deterministically sort
    scored = []
    for doc, meta in zip(docs, metas):
        meta = meta or {}
        doc = doc or ""
        try:
            importance = float(meta.get("importance", 3))
        except (TypeError, ValueError):
            importance = 3.0
        filed_at = meta.get("filed_at", "") or ""
        scored.append((importance, filed_at, doc, meta))

    # Sort: importance desc, filed_at asc (oldest first = most established),
    # then md5 tertiary tiebreaker for byte-stability.
    scored.sort(
        key=lambda x: (
            -x[0],
            x[1],
            hashlib.md5((x[2] or "").encode("utf-8", errors="replace")).hexdigest(),
        )
    )

    lines = ["## PERSONAL IDENTITY — core facts"]
    total_len = 0
    for _imp, _filed, doc, _meta in scored:
        snippet = doc.strip().replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:297] + "..."
        entry = f"  - {snippet}"
        if total_len + len(entry) > _PERSONAL_CORE_MAX_CHARS:
            lines.append("  ... (more via nook_search Personal/core)")
            break
        lines.append(entry)
        total_len += len(entry)

    if len(lines) <= 1:
        # Only header, no entries fit
        return ""

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer 0 — Identity
# ---------------------------------------------------------------------------


class Layer0:
    """
    ~100 tokens. Always loaded.
    Reads from ~/.sage/identity.txt — a plain-text file the user writes.

    Example identity.txt:
        I am Atlas, a personal AI assistant for Alice.
        Traits: warm, direct, remembers everything.
        People: Alice (creator), Bob (Alice's partner).
        Project: A journaling app that helps people process emotions.
    """

    def __init__(self, identity_path: str = None):
        if identity_path is None:
            identity_path = os.path.expanduser("~/.sage/identity.txt")
        self.path = identity_path
        self._text = None

    def render(self) -> str:
        """Return the identity text, or a sensible default."""
        if self._text is not None:
            return self._text

        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                self._text = f.read().strip()
        else:
            self._text = "## L0 — IDENTITY\nNo identity configured. Create ~/.sage/identity.txt"

        return self._text

    def token_estimate(self) -> int:
        return len(self.render()) // 4


# ---------------------------------------------------------------------------
# Layer 1 — Essential Story (auto-generated from nook)
# ---------------------------------------------------------------------------


class Layer1:
    """
    ~500-800 tokens. Always loaded.
    Auto-generated from the highest-weight / most-recent drawers in the nook.
    Groups by room, picks the top N moments, compresses to a compact summary.
    """

    MAX_DRAWERS = 15  # at most 15 moments in wake-up
    MAX_CHARS = 3200  # hard cap on total L1 text (~800 tokens)
    MAX_SCAN = 2000  # don't scan more than this for L1 generation

    def __init__(self, nook_path: str = None, wing: str = None):
        cfg = SageConfig()
        self.nook_path = nook_path or cfg.nook_path
        self.wing = wing

    def generate(self) -> str:
        """Pull top drawers from ChromaDB and format as compact L1 text."""
        try:
            col = _get_collection(self.nook_path, create=False)
        except Exception:
            return "## L1 — No nook found. Run: sage mine <dir>"

        # Fetch all drawers in batches to avoid SQLite variable limit (~999)
        _BATCH = 500
        docs, metas = [], []
        offset = 0
        while True:
            kwargs = {"include": ["documents", "metadatas"], "limit": _BATCH, "offset": offset}
            if self.wing:
                kwargs["where"] = {"wing": self.wing}
            try:
                batch = col.get(**kwargs)
            except Exception:
                break
            batch_docs = batch.get("documents", [])
            batch_metas = batch.get("metadatas", [])
            if not batch_docs:
                break
            docs.extend(batch_docs)
            metas.extend(batch_metas)
            offset += len(batch_docs)
            if len(batch_docs) < _BATCH or len(docs) >= self.MAX_SCAN:
                break

        if not docs:
            return "## L1 — No memories yet."

        # Score each drawer: prefer high importance, recent filing
        scored = []
        for doc, meta in zip(docs, metas):
            meta = meta or {}
            doc = doc or ""
            importance = 3
            # Try multiple metadata keys that might carry weight info
            for key in ("importance", "emotional_weight", "weight"):
                val = meta.get(key)
                if val is not None:
                    try:
                        importance = float(val)
                    except (ValueError, TypeError):
                        pass
                    break
            scored.append((importance, meta, doc))

        # Sort by importance descending; use source_file as the secondary
        # tiebreaker and a short hash of the drawer text as the TERTIARY
        # tiebreaker so the sort is fully deterministic even when multiple
        # drawers share the same source_file (or all source_files are empty)
        # (F#1: byte-stable Tier-0 block / tier0_block_stable proxy).
        scored.sort(
            key=lambda x: (
                -x[0],
                x[1].get("source_file", "") or "",
                hashlib.md5((x[2] or "").encode("utf-8", errors="replace")).hexdigest(),
            ),
        )
        top = scored[: self.MAX_DRAWERS]

        # Group by room for readability
        by_room = defaultdict(list)
        for imp, meta, doc in top:
            room = meta.get("room", "general")
            by_room[room].append((imp, meta, doc))

        # Build compact text
        lines = ["## L1 — ESSENTIAL STORY"]

        total_len = 0
        for room, entries in sorted(by_room.items()):
            room_line = f"\n[{room}]"
            lines.append(room_line)
            total_len += len(room_line)

            for _imp, meta, doc in entries:
                source = Path(meta.get("source_file", "")).name if meta.get("source_file") else ""

                # Truncate doc to keep L1 compact
                snippet = doc.strip().replace("\n", " ")
                if len(snippet) > 200:
                    snippet = snippet[:197] + "..."

                entry_line = f"  - {snippet}"
                if source:
                    entry_line += f"  ({source})"

                if total_len + len(entry_line) > self.MAX_CHARS:
                    lines.append("  ... (more in L3 search)")
                    return "\n".join(lines)

                lines.append(entry_line)
                total_len += len(entry_line)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer 2 — On-Demand (wing/room filtered retrieval)
# ---------------------------------------------------------------------------


class Layer2:
    """
    ~200-500 tokens per retrieval.
    Loaded when a specific topic or wing comes up in conversation.
    Queries ChromaDB with a wing/room filter.
    """

    def __init__(self, nook_path: str = None):
        cfg = SageConfig()
        self.nook_path = nook_path or cfg.nook_path

    def retrieve(self, wing: str = None, room: str = None, n_results: int = 10) -> str:
        """Retrieve drawers filtered by wing and/or room."""
        try:
            col = _get_collection(self.nook_path, create=False)
        except Exception:
            return "No nook found."

        where = build_where_filter(wing, room)

        kwargs = {"include": ["documents", "metadatas"], "limit": n_results}
        if where:
            kwargs["where"] = where

        try:
            results = col.get(**kwargs)
        except Exception as e:
            return f"Retrieval error: {e}"

        docs = results.get("documents", [])
        metas = results.get("metadatas", [])

        if not docs:
            label = f"wing={wing}" if wing else ""
            if room:
                label += f" room={room}" if label else f"room={room}"
            return f"No drawers found for {label}."

        lines = [f"## L2 — ON-DEMAND ({len(docs)} drawers)"]
        for doc, meta in zip(docs[:n_results], metas[:n_results]):
            meta = meta or {}
            doc = doc or ""
            room_name = meta.get("room", "?")
            source = Path(meta.get("source_file", "")).name if meta.get("source_file") else ""
            snippet = doc.strip().replace("\n", " ")
            if len(snippet) > 300:
                snippet = snippet[:297] + "..."
            entry = f"  [{room_name}] {snippet}"
            if source:
                entry += f"  ({source})"
            lines.append(entry)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer 3 — Deep Search (full semantic search via ChromaDB)
# ---------------------------------------------------------------------------


class Layer3:
    """
    Unlimited depth. Semantic search against the full nook.
    Reuses searcher.py logic against nook_drawers.
    """

    def __init__(self, nook_path: str = None):
        cfg = SageConfig()
        self.nook_path = nook_path or cfg.nook_path

    def search(self, query: str, wing: str = None, room: str = None, n_results: int = 5) -> str:
        """Semantic search, returns compact result text."""
        try:
            col = _get_collection(self.nook_path, create=False)
        except Exception:
            return "No nook found."

        where = build_where_filter(wing, room)

        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = col.query(**kwargs)
        except Exception as e:
            return f"Search error: {e}"

        docs = _first_or_empty(results, "documents")
        metas = _first_or_empty(results, "metadatas")
        dists = _first_or_empty(results, "distances")

        if not docs:
            return "No results found."

        lines = [f'## L3 — SEARCH RESULTS for "{query}"']
        for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
            meta = meta or {}
            doc = doc or ""
            similarity = round(max(0.0, 1 - dist), 3)
            wing_name = meta.get("wing", "?")
            room_name = meta.get("room", "?")
            source = Path(meta.get("source_file", "")).name if meta.get("source_file") else ""

            snippet = doc.strip().replace("\n", " ")
            if len(snippet) > 300:
                snippet = snippet[:297] + "..."

            lines.append(f"  [{i}] {wing_name}/{room_name} (sim={similarity})")
            lines.append(f"      {snippet}")
            if source:
                lines.append(f"      src: {source}")

        return "\n".join(lines)

    def search_raw(
        self, query: str, wing: str = None, room: str = None, n_results: int = 5
    ) -> list:
        """Return raw dicts instead of formatted text."""
        try:
            col = _get_collection(self.nook_path, create=False)
        except Exception:
            return []

        where = build_where_filter(wing, room)

        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = col.query(**kwargs)
        except Exception:
            return []

        hits = []
        for doc, meta, dist in zip(
            _first_or_empty(results, "documents"),
            _first_or_empty(results, "metadatas"),
            _first_or_empty(results, "distances"),
        ):
            # ChromaDB may return None for doc/meta when a drawer's HNSW entry
            # exists but its metadata/document rows haven't been materialized
            # (partial-flush states, mid-delete, schema upgrade boundaries).
            # Degrade gracefully — the hit still appears with real distance;
            # storage fields show their fallback where content is missing.
            meta = meta or {}
            doc = doc or ""
            hits.append(
                {
                    "text": doc,
                    "wing": meta.get("wing", "unknown"),
                    "room": meta.get("room", "unknown"),
                    "source_file": Path(meta.get("source_file", "?")).name,
                    "similarity": round(1 - dist, 3),
                    "metadata": meta,
                }
            )
        return hits


# ---------------------------------------------------------------------------
# Budget-trim helper (FIX 2)
# ---------------------------------------------------------------------------

_TRIM_MARKER = "[Tier-0 trimmed to budget]"


def _trim_tier0_to_budget(
    text: str,
    char_limit: int,
) -> str:
    """Trim the Tier-0 block to ``char_limit`` characters.

    Trim order (least important first):
      1. Registry trigger lists — strip the ``(trigger1, trigger2, ...)`` suffix
         from each registry entry line.
      2. Registry one-liner section entirely — drop the REGISTRY block.
      3. Oldest / lowest-priority L1 entries — keep L1 header + first entry.

    Identity core (L0) and the most recent L1 entry are always preserved.
    A one-line trim marker is appended when any trimming occurs.
    """
    if len(text) <= char_limit:
        return text

    # ── Pass 1: strip trigger lists from registry entry lines ───────────────
    import re as _re

    _TRIGGER_SUFFIX = _re.compile(r"  \([^)]*\)\s*$", _re.MULTILINE)
    candidate = _TRIGGER_SUFFIX.sub("", text)
    if len(candidate) <= char_limit:
        return candidate.rstrip() + "\n" + _TRIM_MARKER

    text = candidate  # carry forward; continue trimming

    # ── Pass 2: drop the REGISTRY section entirely ───────────────────────────
    # The registry section starts with a blank line + "## REGISTRY" and runs
    # to end-of-string.
    registry_start = text.find("\n## REGISTRY")
    if registry_start != -1:
        candidate = text[:registry_start]
        if len(candidate) <= char_limit:
            return candidate.rstrip() + "\n" + _TRIM_MARKER
        text = candidate

    # ── Pass 3: trim L1 to header + first entry ──────────────────────────────
    # Split into L0 block and L1 block. L1 starts with "## L1".
    l1_start = text.find("\n## L1")
    if l1_start == -1:
        # No L1 block found; return L0 + marker, capped at char_limit.
        return text[:char_limit].rstrip() + "\n" + _TRIM_MARKER

    l0_part = text[:l1_start]
    l1_part = text[l1_start:]

    # Keep only the L1 header line and the first entry line.
    l1_lines = l1_part.splitlines()
    kept_l1_lines: list[str] = []
    entry_count = 0
    for ln in l1_lines:
        kept_l1_lines.append(ln)
        # Count non-header, non-room-label content lines as entries.
        stripped = ln.strip()
        if stripped.startswith("- ") or stripped.startswith("  - "):
            entry_count += 1
            if entry_count >= 1:
                break
    kept_l1_lines.append("  ... (more in L3 search)")

    candidate = l0_part + "\n".join(kept_l1_lines)
    if len(candidate) <= char_limit:
        return candidate.rstrip() + "\n" + _TRIM_MARKER

    # Last resort: hard-cap at char_limit (preserves identity core).
    return candidate[:char_limit].rstrip() + "\n" + _TRIM_MARKER


# ---------------------------------------------------------------------------
# MemoryStack — unified interface
# ---------------------------------------------------------------------------


class MemoryStack:
    """
    The full 4-layer stack. One class, one nook, everything works.

        stack = MemoryStack()
        print(stack.wake_up())                # L0 + L1 (~600-900 tokens)
        print(stack.recall(wing="my_app"))     # L2 on-demand
        print(stack.search("pricing change"))  # L3 deep search
    """

    def __init__(self, nook_path: str = None, identity_path: str = None):
        cfg = SageConfig()
        self.nook_path = nook_path or cfg.nook_path
        self.identity_path = identity_path or os.path.expanduser("~/.sage/identity.txt")

        self.l0 = Layer0(self.identity_path)
        self.l1 = Layer1(self.nook_path)
        self.l2 = Layer2(self.nook_path)
        self.l3 = Layer3(self.nook_path)

    def wake_up(self, wing: str = None) -> str:
        """
        Generate wake-up text: L0 (identity) + L1 (essential story).
        Typically ~600-900 tokens. Inject into system prompt or first message.

        Args:
            wing: Optional wing filter for L1 (project-specific wake-up).
        """
        parts = []

        # L0: Identity
        parts.append(self.l0.render())
        parts.append("")

        # L1: Essential Story
        if wing:
            self.l1.wing = wing
        parts.append(self.l1.generate())

        return "\n".join(parts)

    def assemble_tier0(
        self,
        wing: str = None,
        repo_root: str = None,
        budget: int = TIER0_TOKEN_BUDGET,
    ) -> Tier0Block:
        """
        Assemble the Tier-0 cached-prefix block (WI-3).

        Combines:
          - L0 identity core
          - L1 essential story (wing-filtered when wing is given)
          - Compact skill/agent/script registry section (metadata only)

        The block is deterministic: same wing + same nook state produces a
        byte-identical result (required for prompt-cache prefix stability).

        Secret scrub is applied to the FULL assembled block once post-assembly
        so credentials in any section (identity, L1 drawers, registry) never
        reach the always-on context (FIX 1c).

        The assembled block is trimmed to ``budget`` tokens when oversized.
        Trim order (least important first): registry trigger lists, registry
        one-liners, oldest/lowest-importance L1 drawers.  Identity core and
        most-recent L1 content are always preserved (FIX 2).

        Args:
            wing:      Optional wing slug for L1 filtering.
            repo_root: Path to the repo whose agents/skills/scripts are scanned.
                       Auto-detected from this file's location when None.
            budget:    Token ceiling for the assembled block.  Default is
                       TIER0_TOKEN_BUDGET; WI-7a measures the actual size.

        Returns:
            Tier0Block with .text, .token_count, .within_budget, .registry_count.
        """
        # Resolve repo_root: try to auto-detect from the package location.
        if repo_root is None:
            here = Path(__file__).resolve().parent  # src/sage_mcp/
            for candidate in [here.parent.parent, here.parent, here]:
                if (candidate / "agents").is_dir() or (candidate / "skills").is_dir():
                    repo_root = str(candidate)
                    break

        # FIX 6: use a local variable for wing so self.l1.wing is not mutated
        # by this call; repeated calls with different wings don't leak state.
        saved_wing = self.l1.wing
        try:
            if wing:
                self.l1.wing = wing
            l1_text = self.l1.generate()
        finally:
            self.l1.wing = saved_wing

        parts: list[str] = []

        # L0: identity core (static identity.txt)
        l0_text = self.l0.render()
        parts.append(l0_text)

        # WI-5: Personal/core durable identity facts — injected between L0 and L1.
        # These are drawer-level facts in wing="Personal", hall="core".
        # hall="detail" drawers are retrieval-only and never appear here.
        personal_core_text = _load_personal_core(self.nook_path)
        if personal_core_text:
            parts.append("")
            parts.append(personal_core_text)

        parts.append("")

        # L1: wing-scoped essential story
        parts.append(l1_text)

        # Registry section (metadata-only, deterministic sort via build_registry)
        registry_text, registry_count = _build_registry_section(repo_root)
        if registry_text:
            parts.append("")
            parts.append(registry_text)

        # ADR-0042: apply AGGRESSIVE scrub (high-confidence + hex≥40) to the
        # FULL assembled block post-assembly. The Tier-0 surface is always-on
        # and always public; over-redaction is accepted here (WI-3 / ADV-13).
        # The hex≥40 pattern is intentionally confined to this path — it MUST
        # NOT appear on the general write path where git SHAs must survive.
        text = scrub_secrets_aggressive("\n".join(parts))

        # FIX 2: enforce budget — trim the block when it exceeds the token ceiling.
        # budget is in tokens (1 token ≈ 4 chars); char limit = budget * 4.
        char_limit = budget * 4
        if len(text) > char_limit:
            text = _trim_tier0_to_budget(text, char_limit)

        return Tier0Block(text=text, budget=budget, registry_count=registry_count)

    def recall(self, wing: str = None, room: str = None, n_results: int = 10) -> str:
        """On-demand L2 retrieval filtered by wing/room."""
        return self.l2.retrieve(wing=wing, room=room, n_results=n_results)

    def search(self, query: str, wing: str = None, room: str = None, n_results: int = 5) -> str:
        """Deep L3 semantic search."""
        return self.l3.search(query, wing=wing, room=room, n_results=n_results)

    def status(self) -> dict:
        """Status of all layers."""
        result = {
            "nook_path": self.nook_path,
            "L0_identity": {
                "path": self.identity_path,
                "exists": os.path.exists(self.identity_path),
                "tokens": self.l0.token_estimate(),
            },
            "L1_essential": {
                "description": "Auto-generated from top nook drawers",
            },
            "L2_on_demand": {
                "description": "Wing/room filtered retrieval",
            },
            "L3_deep_search": {
                "description": "Full semantic search via ChromaDB",
            },
        }

        # Count drawers
        try:
            col = _get_collection(self.nook_path, create=False)
            count = col.count()
            result["total_drawers"] = count
        except Exception:
            result["total_drawers"] = 0

        return result


# ---------------------------------------------------------------------------
# CLI (standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    def usage():
        print("layers.py — 4-Layer Memory Stack")
        print()
        print("Usage:")
        print("  python layers.py wake-up              Show L0 + L1")
        print("  python layers.py wake-up --wing=NAME  Wake-up for a specific project")
        print("  python layers.py recall --wing=NAME   On-demand L2 retrieval")
        print("  python layers.py search <query>       Deep L3 search")
        print("  python layers.py status               Show layer status")
        sys.exit(0)

    if len(sys.argv) < 2:
        usage()

    cmd = sys.argv[1]

    # Parse flags
    flags = {}
    positional = []
    for arg in sys.argv[2:]:
        if arg.startswith("--") and "=" in arg:
            key, val = arg.split("=", 1)
            flags[key.lstrip("-")] = val
        elif not arg.startswith("--"):
            positional.append(arg)

    nook_path = flags.get("nook")
    stack = MemoryStack(nook_path=nook_path)

    if cmd in ("wake-up", "wakeup"):
        wing = flags.get("wing")
        text = stack.wake_up(wing=wing)
        tokens = len(text) // 4
        print(f"Wake-up text (~{tokens} tokens):")
        print("=" * 50)
        print(text)

    elif cmd == "recall":
        wing = flags.get("wing")
        room = flags.get("room")
        text = stack.recall(wing=wing, room=room)
        print(text)

    elif cmd == "search":
        query = " ".join(positional) if positional else ""
        if not query:
            print("Usage: python layers.py search <query>")
            sys.exit(1)
        wing = flags.get("wing")
        room = flags.get("room")
        text = stack.search(query, wing=wing, room=room)
        print(text)

    elif cmd == "status":
        s = stack.status()
        print(json.dumps(s, indent=2))

    else:
        usage()
