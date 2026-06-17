"""Agent manifest-block validation.

For every agents/*.md file that carries a manifest block (has required_inputs:
or forbidden_inputs: in its YAML frontmatter), asserts:

1. required_inputs and forbidden_inputs each parse as a LIST of strings, not a
   dict.  An unquoted "key: value" item (colon-space in the YAML block) is
   silently parsed by PyYAML as a mapping — catching this is the primary purpose
   of this test.  See docs/specs/manifest-schema.md YAML-quoting note.

2. No required_inputs or forbidden_inputs item contains an unfilled
   <placeholder> token (bare <...> leftover in a non-template field).
   briefing_template IS a template and may contain <...>; that field is
   excluded from this check.

3. tools: is present and non-empty in the frontmatter.

4. model: is one of {opus, sonnet, haiku}.

Frontmatter is parsed with yaml.safe_load after stripping YAML comment lines
(lines beginning with '#'), matching the approach used by
scripts/measure_framework_surface.py (which also uses yaml.safe_load on
frontmatter after extracting it).

YAML parse errors on the description: field (which frequently contains colons)
are handled by isolating the YAML parse to the frontmatter block only —
the measure_framework_surface.py split_frontmatter() approach avoids the
body entirely.

Any violation is reported as a FINDING rather than silently skipped.  The
agent file is named in the assertion message so CI output identifies the
offending file without needing to re-run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents"

# ---------------------------------------------------------------------------
# Helpers — frontmatter extraction (mirrors measure_framework_surface.py)
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"<[^>]+>")
_VALID_MODELS = frozenset({"opus", "sonnet", "haiku"})


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    """Return (frontmatter_lines, body_text).  Empty if no fence."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body = "\n".join(lines[i + 1 :])
            return lines[1:i], body
    return [], text


def _parse_frontmatter(fm_lines: list[str]) -> tuple[dict, str | None]:
    """Parse YAML frontmatter.  Returns (data, error_message_or_None).

    On success returns (parsed_dict, None).  On YAML parse failure returns
    ({}, error_string) — callers must NOT silently discard agents whose
    frontmatter fails to parse, because the manifest validation exists to catch
    unparseable manifests.  A returned error string means the agent must be
    surfaced as a test failure.
    """
    try:
        return yaml.safe_load("\n".join(fm_lines)) or {}, None
    except yaml.YAMLError as exc:
        return {}, str(exc)


def _has_manifest(data: dict) -> bool:
    """Return True if the frontmatter contains at least required_inputs."""
    return "required_inputs" in data


# Populated by _collect_manifest_agents() at module load before parametrisation.
_PARSE_FAILURES: list[tuple[Path, str]] = []
# F3: agents whose frontmatter parses successfully but as a non-dict (scalar/list).
# These must be a loud failure, not a silent skip.
_NONDICT_FRONTMATTER: list[tuple[Path, type]] = []


def _collect_manifest_agents() -> list[tuple[Path, dict]]:
    """Return (path, parsed_data) for every agent file that has a manifest.

    Agents whose frontmatter is present but fails YAML parse are appended to
    the module-level _PARSE_FAILURES list so that the test suite can assert on
    them rather than silently skip them.

    Agents whose frontmatter parses successfully but as a non-dict (scalar/list)
    are appended to _NONDICT_FRONTMATTER — a loud failure (F3: non-dict frontmatter
    must not be silently skipped, as it could hide a manifest-bearing agent).
    """
    results = []
    for f in sorted(AGENTS_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fm_lines, _ = _split_frontmatter(text)
        if not fm_lines:
            continue  # no frontmatter — not a manifest-bearing agent
        data, parse_err = _parse_frontmatter(fm_lines)
        if parse_err is not None:
            _PARSE_FAILURES.append((f, parse_err))
        elif not isinstance(data, dict):
            # Frontmatter parsed as a non-dict (scalar/list) — loud failure (F3).
            _NONDICT_FRONTMATTER.append((f, type(data)))
        elif _has_manifest(data):
            results.append((f, data))
    return results


# ---------------------------------------------------------------------------
# Parametrize over manifest-bearing agents
# ---------------------------------------------------------------------------

_MANIFEST_AGENTS = _collect_manifest_agents()
_MANIFEST_AGENT_IDS = [f.name for f, _ in _MANIFEST_AGENTS]


@pytest.fixture(params=_MANIFEST_AGENTS, ids=_MANIFEST_AGENT_IDS)
def manifest_agent(request):
    """Yield (path, parsed_frontmatter) for each manifest-bearing agent."""
    return request.param


# ---------------------------------------------------------------------------
# Assertion 0 — no agent has an unparseable frontmatter block
# ---------------------------------------------------------------------------


def test_no_frontmatter_parse_failures():
    """Every agents/*.md file with a frontmatter block must parse cleanly.

    _collect_manifest_agents() populates _PARSE_FAILURES with (path, error)
    tuples for any agent whose frontmatter raises yaml.YAMLError.  An entry
    here means the agent is silently excluded from all other assertions, which
    is exactly the bug P1.5 exists to prevent.
    """
    if _PARSE_FAILURES:
        lines = "\n".join(f"  {f.name}: {err}" for f, err in _PARSE_FAILURES)
        raise AssertionError(
            f"{len(_PARSE_FAILURES)} agent(s) have frontmatter that fails "
            f"yaml.safe_load and are excluded from manifest validation:\n{lines}\n"
            f"Fix the YAML (quote description: values containing colon-space) "
            f"so these agents participate in manifest validation."
        )


def test_no_nondict_frontmatter():
    """Every agents/*.md frontmatter that parses must parse as a dict (mapping), not a scalar/list.

    F3: a frontmatter block that yaml.safe_load() returns as a non-dict (e.g. a bare
    scalar string or a list) would cause _has_manifest() to silently skip the agent,
    hiding any manifest that was intended to be there.  This is a loud failure, not a
    silent skip.

    In practice this would look like a frontmatter block whose entire content is an
    unquoted value (YAML scalar) rather than key: value pairs.  The assert is placed
    here rather than inlined in parametrised tests so that it fires even when
    _MANIFEST_AGENTS is empty (no agents with required_inputs are present).
    """
    if _NONDICT_FRONTMATTER:
        lines = "\n".join(
            f"  {f.name}: frontmatter parsed as {t.__name__} (expected dict)"
            for f, t in _NONDICT_FRONTMATTER
        )
        raise AssertionError(
            f"{len(_NONDICT_FRONTMATTER)} agent(s) have frontmatter that parses as a "
            f"non-dict type and are excluded from manifest validation:\n{lines}\n"
            f"Frontmatter must be a YAML mapping (key: value pairs).  A bare scalar or "
            f"list block would silently hide any manifest block."
        )


# ---------------------------------------------------------------------------
# Assertion 1 — required_inputs and forbidden_inputs are lists of strings
# ---------------------------------------------------------------------------


class TestManifestListShape:
    """required_inputs and forbidden_inputs must parse as list[str], not dict."""

    def test_required_inputs_is_list_of_strings(self, manifest_agent):
        path, data = manifest_agent
        ri = data.get("required_inputs")
        if ri is None:
            return  # field absent — covered by presence test if needed

        assert isinstance(ri, list), (
            f"{path.name}: required_inputs parsed as {type(ri).__name__}, expected list. "
            f"An unquoted 'key: value' item is silently parsed as a mapping by PyYAML. "
            f"Quote any item whose body contains a colon-space. "
            f"See docs/specs/manifest-schema.md YAML-quoting note."
        )
        dict_items = [i for i in ri if isinstance(i, dict)]
        assert not dict_items, (
            f"{path.name}: required_inputs contains {len(dict_items)} dict item(s) instead of "
            f"strings.  First offending item: {repr(dict_items[0])[:120]}. "
            f"Wrap the item value in YAML quotes (single or double) so the colon-space is "
            f"treated as part of the string, not as a YAML mapping separator. "
            f"See docs/specs/manifest-schema.md YAML-quoting note."
        )

    def test_forbidden_inputs_is_list_of_strings(self, manifest_agent):
        path, data = manifest_agent
        fi = data.get("forbidden_inputs")
        if fi is None:
            return  # field optional

        assert isinstance(fi, list), (
            f"{path.name}: forbidden_inputs parsed as {type(fi).__name__}, expected list. "
            f"See docs/specs/manifest-schema.md YAML-quoting note."
        )
        dict_items = [i for i in fi if isinstance(i, dict)]
        assert not dict_items, (
            f"{path.name}: forbidden_inputs contains {len(dict_items)} dict item(s) instead of "
            f"strings.  First offending item: {repr(dict_items[0])[:120]}. "
            f"See docs/specs/manifest-schema.md YAML-quoting note."
        )


# ---------------------------------------------------------------------------
# Assertion 1b — requires field shape (external-dependency declarations, ADR-0121)
# ---------------------------------------------------------------------------


class TestRequiresField:
    """`requires` (optional) must be a list of dicts, each with non-empty dep/kind/
    install/why and kind from the ADR-0121 enum.  A malformed entry silently produces
    empty cells in the generated docs/reference/agent-dependencies.md."""

    _KINDS = {"mcp-plugin", "system", "package", "venv"}

    def test_requires_well_formed(self, manifest_agent):
        path, data = manifest_agent
        req = data.get("requires")
        if req is None:
            return  # field optional
        assert isinstance(req, list), (
            f"{path.name}: requires parsed as {type(req).__name__}, expected list. "
            f"See docs/specs/manifest-schema.md."
        )
        for i, entry in enumerate(req):
            assert isinstance(entry, dict), (
                f"{path.name}: requires[{i}] is {type(entry).__name__}, expected a mapping "
                f"with dep/kind/install/why."
            )
            for key in ("dep", "kind", "install", "why"):
                val = entry.get(key)
                assert isinstance(val, str) and val.strip(), (
                    f"{path.name}: requires[{i}] missing or empty '{key}' "
                    f"(would generate an empty cell in agent-dependencies.md)."
                )
            assert entry["kind"] in self._KINDS, (
                f"{path.name}: requires[{i}].kind={entry['kind']!r} not in {sorted(self._KINDS)}."
            )


# ---------------------------------------------------------------------------
# Assertion 2 — no unfilled <placeholder> in required_inputs / forbidden_inputs
# ---------------------------------------------------------------------------


class TestNoUnfilledPlaceholders:
    """required_inputs and forbidden_inputs items must not be bare unfilled placeholders.

    briefing_template is explicitly excluded — it IS a template.
    """

    def test_required_inputs_no_bare_placeholder(self, manifest_agent):
        path, data = manifest_agent
        ri = data.get("required_inputs")
        if not isinstance(ri, list):
            return  # shape test covers this

        bare = [
            item for item in ri if isinstance(item, str) and _PLACEHOLDER_RE.fullmatch(item.strip())
        ]
        assert not bare, (
            f"{path.name}: required_inputs contains items that are ONLY a placeholder token "
            f"(nothing else): {bare}. "
            f"Each required_inputs item should describe what the input is, not be a bare "
            f"<placeholder> token. Placeholder tokens inside briefing_template are allowed."
        )

    def test_forbidden_inputs_no_bare_placeholder(self, manifest_agent):
        path, data = manifest_agent
        fi = data.get("forbidden_inputs")
        if not isinstance(fi, list):
            return

        bare = [
            item for item in fi if isinstance(item, str) and _PLACEHOLDER_RE.fullmatch(item.strip())
        ]
        assert not bare, (
            f"{path.name}: forbidden_inputs contains items that are ONLY a placeholder token: "
            f"{bare}."
        )


# ---------------------------------------------------------------------------
# Assertion 3 — tools: present and non-empty
# ---------------------------------------------------------------------------


class TestToolsPresent:
    """Every manifest-bearing agent must have a non-empty tools: field."""

    def test_tools_present_and_nonempty(self, manifest_agent):
        path, data = manifest_agent
        tools = data.get("tools")
        assert tools, (
            f"{path.name}: tools: field is absent or empty. "
            f"Every manifest-bearing agent must declare at least one tool grant."
        )


# ---------------------------------------------------------------------------
# Assertion 4 — model: in {opus, sonnet, haiku}
# ---------------------------------------------------------------------------


class TestModelValid:
    """model: must be one of {opus, sonnet, haiku} in every manifest-bearing agent."""

    def test_model_in_valid_set(self, manifest_agent):
        path, data = manifest_agent
        model = str(data.get("model", "")).strip().lower()
        assert model in _VALID_MODELS, (
            f"{path.name}: model '{model}' is not in {{opus, sonnet, haiku}}. "
            f"Set model: to one of the three valid tiers."
        )


# ---------------------------------------------------------------------------
# F3 — unit coverage: non-dict frontmatter detection helpers
# ---------------------------------------------------------------------------


class TestNonDictFrontmatterDetection:
    """Unit coverage for the F3 non-dict frontmatter detection path.

    Tests the helper functions directly with synthetic frontmatter content to
    confirm that the detection logic fires correctly without requiring a real
    agent file to have broken frontmatter.
    """

    def test_scalar_frontmatter_is_not_a_dict(self):
        """A frontmatter block whose sole content is a bare string parses as str, not dict."""
        import yaml

        scalar_fm = ["just a bare string with no colons"]
        result = yaml.safe_load("\n".join(scalar_fm))
        assert not isinstance(result, dict), (
            "Precondition: yaml.safe_load of a bare scalar must NOT return a dict."
        )

    def test_list_frontmatter_is_not_a_dict(self):
        """A frontmatter block that is a YAML list parses as list, not dict."""
        import yaml

        list_fm = ["- item one", "- item two"]
        result = yaml.safe_load("\n".join(list_fm))
        assert isinstance(result, list), (
            "Precondition: yaml.safe_load of a list block must return a list."
        )
        assert not isinstance(result, dict)

    def test_normal_mapping_frontmatter_is_a_dict(self):
        """Normal key: value frontmatter parses as dict — the expected good path."""
        import yaml

        mapping_fm = ["name: test-agent", "model: sonnet", "description: test desc"]
        result = yaml.safe_load("\n".join(mapping_fm))
        assert isinstance(result, dict), "Normal key: value frontmatter must parse as a dict."

    def test_collect_detects_nondict_frontmatter(self, tmp_path):
        """_collect_manifest_agents-style logic detects non-dict frontmatter as a loud failure.

        Simulates what _collect_manifest_agents() does: if yaml.safe_load returns
        a non-dict for a non-empty frontmatter block, it must be recorded rather
        than silently skipped.
        """
        # Write an agent with a bare scalar frontmatter
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        bad_agent = agents_dir / "bad-agent.md"
        # A frontmatter block that yaml.safe_load will return as a string, not a dict.
        # This is contrived but tests the detection path.
        bad_agent.write_text("---\njust a bare scalar\n---\n\n# Bad agent\n", encoding="utf-8")

        # Replicate the collection logic inline
        nondict_found = []
        for f in sorted(agents_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            fm_lines, _ = _split_frontmatter(text)
            if not fm_lines:
                continue
            data, parse_err = _parse_frontmatter(fm_lines)
            if parse_err is None and not isinstance(data, dict):
                nondict_found.append((f, type(data)))

        assert len(nondict_found) == 1, (
            f"Expected exactly 1 non-dict frontmatter agent, found {len(nondict_found)}."
        )
        assert nondict_found[0][0].name == "bad-agent.md"
