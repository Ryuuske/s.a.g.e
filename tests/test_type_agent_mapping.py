"""Drift guard: type-slug → agent-name mapping table in docs/specs/agent-registry-protocol.md.

Parses the markdown table in the "Type→agent mapping" subsection (protocol section 3) and asserts:
1. The table is non-empty (≥10 rows).
2. Every agent name referenced in the table has a real agents/<name>.md file on disk.

This test is intentionally written BEFORE the table is added to the protocol doc
(TDD red phase) and goes green once the table lands.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents"
PROTOCOL_PATH = REPO_ROOT / "docs" / "specs" / "agent-registry-protocol.md"

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|(.+)\|$")
_AGENT_NAME_RE = re.compile(r"`([a-z][a-z0-9-]+)`")

# The subsection heading we look for (case-insensitive substring match).
_SUBSECTION_MARKER = "type→agent mapping"


def _parse_type_agent_mapping() -> list[tuple[str, list[str]]]:
    """Return list of (type_slug, [agent_names]) from the mapping table.

    Looks for a subsection whose heading contains "Type→agent mapping" (or
    "Type->agent mapping") in docs/specs/agent-registry-protocol.md, then parses
    every markdown table row in that subsection until a blank line or new
    heading ends the table.

    Returns an empty list if the subsection or table is absent — which will
    trigger the non-empty assertion as a red test.
    """
    text = PROTOCOL_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find the subsection heading
    in_subsection = False
    in_table = False
    rows: list[tuple[str, list[str]]] = []

    for line in lines:
        stripped = line.strip()

        # Detect the subsection start
        if not in_subsection:
            heading_lower = stripped.lower().replace("->", "→")
            if _SUBSECTION_MARKER in heading_lower and stripped.startswith("#"):
                in_subsection = True
                continue
            continue

        # Inside the subsection — look for table rows
        if stripped.startswith("|"):
            # Skip header and separator rows (header has "Type" / "Agent", separator has ---)
            if "---" in stripped:
                continue
            # Skip header row containing column names
            if re.search(r"\|\s*(type|agent|slug)", stripped, re.IGNORECASE):
                continue
            # Parse table row: | `type-slug` | `agent-a`, `agent-b` |
            # Extract type slug from first column
            cols = [c.strip() for c in stripped.split("|")]
            # cols[0] is '' (before leading |), cols[1] is type, cols[2] is agents
            if len(cols) < 3:
                continue
            # Extract type slug
            type_match = re.search(r"`([^`]+)`", cols[1])
            if not type_match:
                continue
            type_slug = type_match.group(1)
            # Extract agent names from second column (may be comma-separated backtick names)
            agent_names = _AGENT_NAME_RE.findall(cols[2])
            if agent_names:
                rows.append((type_slug, agent_names))
            in_table = True
        else:
            # End of table — a new heading or blank line after table rows exits
            if in_table and stripped:
                # New heading: stop
                if stripped.startswith("#"):
                    break
                # Non-pipe non-empty line after table rows: stop
                break

    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTypeAgentMappingTable:
    """The Type→agent mapping table in docs/specs/agent-registry-protocol.md is present
    and every referenced agent exists on disk."""

    def test_table_is_non_empty(self):
        rows = _parse_type_agent_mapping()
        assert len(rows) >= 10, (
            f"Type→agent mapping table in {PROTOCOL_PATH.relative_to(REPO_ROOT)} "
            f"has {len(rows)} row(s); expected ≥10. "
            "Add the mapping table subsection under protocol section 3 per ADR-0096."
        )

    def test_all_mapped_agents_exist_on_disk(self):
        rows = _parse_type_agent_mapping()
        real = frozenset(f.stem for f in AGENTS_DIR.glob("*.md"))

        missing: list[str] = []
        for type_slug, agent_names in rows:
            for name in agent_names:
                if name not in real:
                    missing.append(f"{type_slug} → {name}")

        assert not missing, (
            "Agent names in the Type→agent mapping table that have no "
            "agents/<name>.md file on disk:\n" + "\n".join(f"  {m}" for m in sorted(missing))
        )
