"""Roster/matrix drift guard.

Three assertions:
1. Every agent name referenced in docs/specs/audit-pairing-matrix.md resolves to a
   real agents/<name>.md file.
2. Every KEY in _AGENT_SKILL_MAP inside
   scripts/measure_framework_surface.py (Contract B agent↔skill map) resolves
   to a real agents/<name>.md file.  (The VALUES are skill names, not agent
   names, and are validated separately by the skill-map tests.)
3. The agent count stated in docs/reference/agent-roster.md equals the actual
   agents/*.md file count.  If family-breakdown subtotals exist in the table,
   they are also asserted to sum to the real count.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents"
MATRIX_PATH = REPO_ROOT / "docs" / "specs" / "audit-pairing-matrix.md"
ROSTER_PATH = REPO_ROOT / "docs" / "reference" / "agent-roster.md"
SURFACE_SCRIPT = REPO_ROOT / "scripts" / "measure_framework_surface.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _real_agent_stems() -> frozenset[str]:
    """Return the set of agent file stems (without .md) on disk."""
    return frozenset(f.stem for f in AGENTS_DIR.glob("*.md"))


def _load_surface_module():
    """Import measure_framework_surface.py by path without adding it to sys.path."""
    spec = importlib.util.spec_from_file_location("measure_framework_surface", SURFACE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extract_matrix_agent_names() -> list[str]:
    """Parse the Pairing rows table and return every agent name mentioned.

    Agent cells in the matrix look like:
      `agent-name`
      `agent-name` *(self-audit, then →)*
      `agent-name` *(self-pass)*

    The Trigger column and Protocol column are intentionally excluded because
    they contain change_type slugs and protocol keywords, not agent names.
    The columns are (0-indexed):
      0: change_type slug  — skip
      1: Trigger           — skip
      2: auditor_primary   — include
      3: auditor_secondary — include
      4: auditor_tertiary  — include (may be "—")
      5: Protocol          — skip

    We collect only the backtick-quoted identifiers from columns 2–4 that
    look like valid agent names (lowercase, hyphens, ASCII letters/digits).
    """
    text = MATRIX_PATH.read_text(encoding="utf-8")
    agent_names: list[str] = []

    _AGENT_RE = re.compile(r"`([a-z][a-z0-9-]+)`")
    # Recognise the "x (if y)" parenthetical that some tertiary cells have
    # e.g. `sec-auditor` (if security-touching)

    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        # Split on '|', strip whitespace; columns[0] is "" (before leading |)
        columns = [c.strip() for c in line.split("|")]
        # Need at least 6 pipe-delimited segments (col 0 blank + 5 data cols)
        if len(columns) < 6:
            continue
        # Skip header and separator rows
        if columns[1].startswith("change_type") or columns[1].startswith("---"):
            continue
        # Columns 2, 3, 4 are auditor_primary, auditor_secondary, auditor_tertiary
        for col_idx in (2, 3, 4):
            cell = columns[col_idx]
            for match in _AGENT_RE.finditer(cell):
                name = match.group(1)
                # Exclude protocol keywords that appear in backticks in the cell
                # (none currently, but guard anyway)
                if name not in ("parallel", "sequential", "solo", "self-pass"):
                    agent_names.append(name)

    return agent_names


def _parse_roster_documented_count() -> tuple[int, list[int]]:
    """Return (documented_total, family_subtotals) from docs/reference/agent-roster.md.

    Looks for a Markdown table row containing a "Total" or bold total cell and
    a count.  Returns (total, [subtotals_from_family_table]).

    The subtotals come from the "Count" column in the Family directory table;
    the grand total is the cell in the "Total" row.
    """
    text = ROSTER_PATH.read_text(encoding="utf-8")

    # --- grand total: look for a row like: | **Total** | — | — | **75** | ... ---
    # Generated 3-col shape: | **Total** | — | **83** |
    total_re = re.compile(
        r"\|\s*\*{0,2}Total\*{0,2}\s*\|(?:[^|]*\|)+?\s*\*{0,2}(\d+)\*{0,2}\s*\|",
        re.IGNORECASE,
    )
    match = total_re.search(text)
    if not match:
        raise ValueError(
            f"Could not find a 'Total' row with a numeric count in {ROSTER_PATH}. "
            "Ensure the family-directory table has a Total row."
        )
    documented_total = int(match.group(1))

    # --- family subtotals: rows like: | AI Development | ... | 14 | ... ---
    # The Count column is the 4th pipe-delimited cell (0-index 3 after splitting).
    subtotals: list[int] = []
    in_family_table = False
    for line in text.splitlines():
        if not line.startswith("|"):
            in_family_table = False
            continue
        cols = [c.strip() for c in line.split("|")]
        # Detect the family-directory table header
        if "Family" in cols and "Count" in cols:
            in_family_table = True
            continue
        if in_family_table:
            if cols[1].startswith("---"):
                continue
            # Grand-total row may be bolded: **Total** or plain Total
            if re.sub(r"\*", "", cols[1]).strip().lower().startswith("total"):
                # Grand-total row — stop collecting subtotals
                break
            # Generated table: | Family | Prefix | Count |
            # cols: ['', Family, Prefix, Count, ''] — Count is the last non-empty cell
            cells = [c.replace("*", "").strip() for c in cols if c.strip()]
            if cells and cells[-1].isdigit():
                subtotals.append(int(cells[-1]))

    return documented_total, subtotals


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMatrixAgentResolution:
    """Every agent named in the audit-pairing matrix has a real agents/*.md file."""

    def test_all_matrix_agents_exist(self):
        real = _real_agent_stems()
        matrix_agents = _extract_matrix_agent_names()
        assert matrix_agents, "No agent names extracted from the matrix — check the parser."

        missing = [name for name in matrix_agents if name not in real]
        assert not missing, (
            "Agents referenced in docs/specs/audit-pairing-matrix.md but missing as "
            "agents/<name>.md files:\n" + "\n".join(f"  {n}" for n in sorted(missing))
        )


class TestSkillMapAgentResolution:
    """Every agent key in _AGENT_SKILL_MAP resolves to a real agents/*.md file."""

    def test_all_skill_map_agents_exist(self):
        real = _real_agent_stems()
        mod = _load_surface_module()
        skill_map: dict[str, str] = mod._AGENT_SKILL_MAP

        assert skill_map, "_AGENT_SKILL_MAP is empty — check the import."

        missing = [stem for stem in skill_map if stem not in real]
        assert not missing, (
            "Agent keys in _AGENT_SKILL_MAP (scripts/measure_framework_surface.py) "
            "that have no agents/<name>.md file:\n" + "\n".join(f"  {n}" for n in sorted(missing))
        )


class TestRosterCountAccuracy:
    """The count documented in docs/reference/agent-roster.md equals the real agents/*.md count."""

    def test_documented_total_equals_real_count(self):
        real_count = len(list(AGENTS_DIR.glob("*.md")))
        documented_total, _ = _parse_roster_documented_count()

        assert documented_total == real_count, (
            f"docs/reference/agent-roster.md documents {documented_total} agents, "
            f"but agents/*.md has {real_count} files. "
            f"Update the Total row in the family-directory table."
        )

    def test_family_subtotals_sum_to_real_count(self):
        """Assert subtotals sum to the real count when the table is fully filled.

        The per-family count table in docs/reference/agent-roster.md may intentionally lag
        behind the total when new families are added without updating per-row cells.
        The roster-integrity acceptance criteria state:
          'if the table's listed rows don't account for all agents, correct the
          total to the real number and leave a one-line note that the per-family
          table may lag; this test enforces the total going forward.'

        This test is therefore advisory: it issues a warning if subtotals lag so
        maintainers know to fill in per-family cells, but it does not fail the
        suite — the hard guard is test_documented_total_equals_real_count above.
        """
        import warnings

        real_count = len(list(AGENTS_DIR.glob("*.md")))
        _, subtotals = _parse_roster_documented_count()

        if not subtotals:
            # No parseable subtotals — total check covers correctness
            return

        subtotal_sum = sum(subtotals)
        if subtotal_sum != real_count:
            warnings.warn(
                f"Family-breakdown subtotals in docs/reference/agent-roster.md sum to "
                f"{subtotal_sum} but agents/*.md has {real_count} files "
                f"(subtotals: {subtotals}). "
                f"Update per-family Count cells to match the real count. "
                f"This is advisory — test_documented_total_equals_real_count is the hard guard.",
                stacklevel=2,
            )
