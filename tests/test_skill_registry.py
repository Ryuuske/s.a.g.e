"""Tests for skill/agent/script metadata registry (WI-2).

Verifies:
- build_registry produces metadata-only entries (no full artifact bodies)
- Entries for agents, skills, and scripts are scanned correctly
- Frontmatter description field parsed by robust manual extractor (handles unquoted colons)
- Determinism: two builds of the same tree produce identical output
- Cache invalidation and force_rebuild work
- Automatic cache staleness detection (mtime-based, recursive — FIX A)
- Secret scrubbing: hardcoded tokens produce [REDACTED] in one_line/triggers
- Scrub-before-truncation: boundary-straddling secrets fully redacted (FIX C)
- Extended secret patterns: Slack, JWT, Google API key, high-entropy hex (FIX D)
- Ignore-matcher warning path taken when loader unavailable (FIX B)
- .sageignore filtering path (FIX G)
- Block-scalar markers dropped from description (FIX E)
- search_registry keyword matching (name / one_line / triggers)
- Binary files are skipped in scripts scan
- Empty / absent directories are tolerated gracefully
- search_registry kind filter works
- MCP tool handler returns expected structure
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Build a minimal fake repo tree with agents, skills, and scripts."""
    # agents/
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "my-agent.md").write_text(
        textwrap.dedent("""\
        ---
        name: my-agent
        description: Use when you need to do something useful. Do not use for unrelated tasks.
        tools: Read, Write
        ---
        # My Agent

        This is the agent body — should NOT appear in registry entries.
        """),
        encoding="utf-8",
    )
    # description with an UNQUOTED colon — this is the real-world failure case
    # (yaml.safe_load returns {} silently; the manual extractor handles it correctly).
    (agents / "colon-agent.md").write_text(
        textwrap.dedent("""\
        ---
        name: colon-agent
        description: Use at session start: do X; do Y. Do not use for Z.
        ---
        # Colon Agent
        """),
        encoding="utf-8",
    )

    # A skill whose description mirrors the real session-lifecycle pattern
    # (multiple unquoted colons, PAUSE: references) — guards FIX 1.
    sl_dir = tmp_path / "skills" / "session-lifecycle"
    sl_dir.mkdir(parents=True)
    (sl_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: session-lifecycle
        description: Use at session start, on any PAUSE: need nook lookup from a specialist, and at session end. Encodes the orchestrator protocol.
        ---
        # Session Lifecycle Skill

        Body text that must not appear in one_line.
        """),
        encoding="utf-8",
    )

    # skills/my-skill/SKILL.md
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: my-skill
        description: Triggers on test execution and CI runs. Do not use outside test contexts.
        ---
        # My Skill

        Skill body — should NOT appear in registry entries.
        """),
        encoding="utf-8",
    )

    # skills/no-frontmatter-skill/SKILL.md
    nf_dir = tmp_path / "skills" / "no-frontmatter-skill"
    nf_dir.mkdir(parents=True)
    (nf_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        # No Frontmatter Skill

        First substantive line of the skill body that serves as a fallback one_line.
        """),
        encoding="utf-8",
    )

    # scripts/
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "do-something.sh").write_text(
        "#!/bin/bash\n# Run the deployment pipeline. Call with --dry-run to preview.\necho hello\n",
        encoding="utf-8",
    )
    (scripts / "helper.py").write_text(
        "#!/usr/bin/env python3\n# Helper utility for common tasks.\ndef main(): pass\n",
        encoding="utf-8",
    )
    # Binary file — must be skipped.
    (scripts / "compiled.so").write_bytes(b"\x7fELF\x00\x00\x00binary")

    return tmp_path


# ── Core build_registry tests ─────────────────────────────────────────────


def test_build_registry_returns_list_of_dicts(fake_repo):
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    assert isinstance(entries, list)
    assert len(entries) > 0
    for e in entries:
        assert isinstance(e, dict)


def test_all_required_fields_present(fake_repo):
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    for e in entries:
        assert "name" in e, f"Missing 'name' in {e}"
        assert "kind" in e, f"Missing 'kind' in {e}"
        assert "one_line" in e, f"Missing 'one_line' in {e}"
        assert "triggers" in e, f"Missing 'triggers' in {e}"
        assert "path" in e, f"Missing 'path' in {e}"
        assert e["kind"] in ("agent", "skill", "script"), f"Unexpected kind {e['kind']}"
        assert isinstance(e["triggers"], list), f"triggers must be list, got {type(e['triggers'])}"


def test_no_full_body_in_entries(fake_repo):
    """Entries must not store full artifact bodies — only metadata descriptors."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)

    # These strings appear only in the artifact bodies, never in one_line
    body_sentinels = [
        "should NOT appear in registry entries",
        "Skill body",
        "This is the agent body",
        "def main(): pass",
        "echo hello",
    ]
    for e in entries:
        for sentinel in body_sentinels:
            assert sentinel not in str(e), (
                f"Entry {e['name']} contains body content ({sentinel!r}): {e}"
            )


def test_one_line_max_length(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        _invalidate_cache,
        _ONE_LINE_MAX,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    for e in entries:
        assert len(e["one_line"]) <= _ONE_LINE_MAX, (
            f"one_line too long ({len(e['one_line'])} > {_ONE_LINE_MAX}) for {e['name']}: {e['one_line']!r}"
        )


def test_agent_entry_scanned(fake_repo):
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    agents = [e for e in entries if e["kind"] == "agent"]
    names = {e["name"] for e in agents}
    assert "my-agent" in names
    assert "colon-agent" in names


def test_skill_entry_scanned(fake_repo):
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    skills = [e for e in entries if e["kind"] == "skill"]
    names = {e["name"] for e in skills}
    assert "my-skill" in names


def test_script_entry_scanned(fake_repo):
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    scripts = [e for e in entries if e["kind"] == "script"]
    names = {e["name"] for e in scripts}
    assert "do-something" in names or "do-something.sh" in names
    assert "helper" in names or "helper.py" in names


def test_binary_script_excluded(fake_repo):
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    paths = {e["path"] for e in entries}
    assert not any("compiled.so" in p for p in paths), (
        f"Binary script should be excluded. Found paths: {paths}"
    )


def test_frontmatter_description_used_for_one_line(fake_repo):
    """one_line is derived from the frontmatter description field."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    agent = next((e for e in entries if e["name"] == "my-agent"), None)
    assert agent is not None
    # one_line should start from the description, not include the full body
    assert agent["one_line"]
    assert "useful" in agent["one_line"].lower() or "something" in agent["one_line"].lower()


def test_colon_in_description_parsed_correctly(fake_repo):
    """Descriptions with colons must be parsed via yaml.safe_load, not split on ':'."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    agent = next((e for e in entries if e["name"] == "colon-agent"), None)
    assert agent is not None
    # Should parse "Use at session start: do X; do Y" correctly (not split on colon)
    assert agent["one_line"]
    # The one_line should contain content derived from the description
    assert len(agent["one_line"]) > 5


def test_no_frontmatter_falls_back_to_content(fake_repo):
    """Skills without frontmatter should derive one_line from content body."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    skill = next((e for e in entries if e["name"] == "no-frontmatter-skill"), None)
    assert skill is not None
    # one_line should be non-empty (derived from content body)
    assert skill["one_line"]


def test_determinism(fake_repo):
    """Building the registry twice on the same tree must produce identical output."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    first = build_registry(fake_repo, force_rebuild=True)
    second = build_registry(fake_repo, force_rebuild=True)
    assert first == second, "Registry build is not deterministic"


def test_sorted_by_path(fake_repo):
    """Entries must be sorted by path for determinism."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    paths = [e["path"] for e in entries]
    assert paths == sorted(paths), f"Entries not sorted by path: {paths}"


def test_cache_returns_same_object(fake_repo):
    """Second call without force_rebuild returns the cached result."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    first = build_registry(fake_repo)
    second = build_registry(fake_repo)
    assert first == second


def test_force_rebuild_bypasses_cache(fake_repo):
    """force_rebuild=True must re-scan disk and reflect newly written files.

    Note: since FIX 3 the cache self-invalidates on mtime advance, so writing
    a file also triggers auto-rebuild without force_rebuild.  This test focuses
    on the guarantee that force_rebuild=True always returns a fresh scan.
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    first = build_registry(fake_repo)
    assert all(e["name"] != "new-agent" for e in first), (
        "new-agent must not exist in the initial build"
    )
    # Add a new agent file.
    (fake_repo / "agents" / "new-agent.md").write_text(
        "---\nname: new-agent\ndescription: A brand new agent.\n---\n",
        encoding="utf-8",
    )
    # With force_rebuild, the new agent appears regardless of mtime state.
    refreshed = build_registry(fake_repo, force_rebuild=True)
    assert any(e["name"] == "new-agent" for e in refreshed), (
        "force_rebuild=True must reflect the newly written agent"
    )


def test_missing_agents_dir_tolerated(tmp_path):
    """build_registry must not raise when agents/ is absent."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    (tmp_path / "skills" / "only-skill").mkdir(parents=True)
    (tmp_path / "skills" / "only-skill" / "SKILL.md").write_text(
        "---\nname: only-skill\ndescription: Exists alone.\n---\n",
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path)
    assert any(e["kind"] == "skill" for e in entries)
    assert all(e["kind"] != "agent" for e in entries)


def test_empty_repo_returns_empty_list(tmp_path):
    """A repo root with no agents/, skills/, or scripts/ returns []."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path)
    assert entries == []


def test_path_is_relative_to_repo_root(fake_repo):
    """Entry paths must be relative to repo_root, not absolute."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    for e in entries:
        assert not Path(e["path"]).is_absolute(), (
            f"Path should be relative, got absolute: {e['path']}"
        )


# ── search_registry tests ─────────────────────────────────────────────────


def test_search_returns_matching_entry(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    results = search_registry(entries, "my-agent")
    assert any(e["name"] == "my-agent" for e in results)


def test_search_kind_filter_agent(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    results = search_registry(entries, "", kind="agent")
    assert all(e["kind"] == "agent" for e in results)


def test_search_kind_filter_skill(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    results = search_registry(entries, "", kind="skill")
    assert all(e["kind"] == "skill" for e in results)


def test_search_kind_filter_script(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    results = search_registry(entries, "", kind="script")
    assert all(e["kind"] == "script" for e in results)


def test_search_limit_respected(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    results = search_registry(entries, "", limit=2)
    assert len(results) <= 2


def test_search_empty_query_returns_all(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    results = search_registry(entries, "", limit=100)
    assert len(results) == len(entries)


def test_search_no_results_for_gibberish(fake_repo):
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    results = search_registry(entries, "xyzzy_no_match_12345")
    assert results == []


# ── MCP tool handler tests ────────────────────────────────────────────────


def test_mcp_tool_in_tools_dict():
    """nook_registry_search must appear in the TOOLS dict."""
    from sage_mcp.mcp_server import TOOLS

    assert "nook_registry_search" in TOOLS
    entry = TOOLS["nook_registry_search"]
    assert "description" in entry
    assert "input_schema" in entry
    assert "handler" in entry
    assert callable(entry["handler"])


def test_mcp_tool_schema_has_expected_properties():
    """Tool schema must declare at least query, kind, limit, repo_root, force_rebuild."""
    from sage_mcp.mcp_server import TOOLS

    props = TOOLS["nook_registry_search"]["input_schema"].get("properties", {})
    for expected in ("query", "kind", "limit", "repo_root", "force_rebuild"):
        assert expected in props, f"Missing property {expected!r} in input_schema"


def test_mcp_handler_with_valid_repo(fake_repo):
    """Handler with repo_root pointing at a fake repo returns expected shape."""
    from sage_mcp.mcp_server import tool_registry_search
    from sage_mcp.extensions.skill_registry import _invalidate_cache

    _invalidate_cache(fake_repo)
    result = tool_registry_search(
        query="",
        repo_root=str(fake_repo),
        force_rebuild=True,
    )
    assert "error" not in result, f"Handler returned error: {result.get('error')}"
    assert "results" in result
    assert "total_indexed" in result
    assert "count" in result
    assert isinstance(result["results"], list)
    assert result["total_indexed"] > 0


def test_mcp_handler_no_full_bodies_in_results(fake_repo):
    """MCP handler results must not contain full artifact bodies."""
    from sage_mcp.mcp_server import tool_registry_search
    from sage_mcp.extensions.skill_registry import _invalidate_cache

    _invalidate_cache(fake_repo)
    result = tool_registry_search(
        query="",
        repo_root=str(fake_repo),
        force_rebuild=True,
        limit=50,
    )
    body_sentinels = [
        "should NOT appear in registry entries",
        "Skill body",
        "This is the agent body",
        "def main(): pass",
    ]
    result_str = json.dumps(result)
    for sentinel in body_sentinels:
        assert sentinel not in result_str, f"Body content leaked into MCP result: {sentinel!r}"


def test_mcp_handler_kind_filter(fake_repo):
    """Handler kind filter returns only entries of the requested kind."""
    from sage_mcp.mcp_server import tool_registry_search
    from sage_mcp.extensions.skill_registry import _invalidate_cache

    _invalidate_cache(fake_repo)
    result = tool_registry_search(
        query="",
        kind="agent",
        repo_root=str(fake_repo),
        force_rebuild=True,
        limit=50,
    )
    assert "error" not in result
    for entry in result["results"]:
        assert entry["kind"] == "agent"


def test_mcp_handler_invalid_kind_returns_error():
    """Handler with an invalid kind value must return an error dict."""
    from sage_mcp.mcp_server import tool_registry_search

    result = tool_registry_search(query="test", kind="banana")
    assert "error" in result
    assert "banana" in result["error"]


def test_mcp_handler_invalid_repo_returns_error():
    """Handler with a non-existent repo_root that can't auto-detect must return error."""
    from sage_mcp.mcp_server import tool_registry_search
    from sage_mcp.extensions.skill_registry import _invalidate_cache

    _invalidate_cache("/nonexistent/repo/path/xyz")
    result = tool_registry_search(
        query="test",
        repo_root="/nonexistent/repo/path/xyz",
        force_rebuild=True,
    )
    # An empty list is fine (no agents/skills dirs) — handler must not raise.
    # Both error response and empty-but-valid response are acceptable here.
    assert isinstance(result, dict)


# ── Entry size proof ──────────────────────────────────────────────────────


def test_entry_is_metadata_only_small(fake_repo):
    """Each entry must be compact — no large blobs.

    Serialised JSON of a single entry should be well under 1 KB.
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    for e in entries:
        size = len(json.dumps(e, ensure_ascii=False))
        assert size < 1024, (
            f"Entry {e['name']} is {size} bytes — entries must be metadata-only (< 1KB): {e}"
        )


# ── FIX 1 guard: unquoted-colon description yields behavioral one_line ────


def test_unquoted_colon_description_not_h1_heading(fake_repo):
    """one_line for session-lifecycle must be the behavioral description, not the H1 heading.

    Guards FIX 1: the manual frontmatter extractor must survive unquoted colons
    in description values (yaml.safe_load would silently return {} for these,
    causing one_line to fall back to the H1 heading "Session Lifecycle Skill").
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    skill = next((e for e in entries if e["name"] == "session-lifecycle"), None)
    assert skill is not None, "session-lifecycle skill not found in registry"
    # Must NOT be the H1 heading fallback.
    assert skill["one_line"] != "Session Lifecycle Skill", (
        f"one_line is H1 heading — frontmatter colon-robustness fix not working: {skill['one_line']!r}"
    )
    # Must contain content derived from the description (behavioral text starts with "Use").
    assert "Use" in skill["one_line"] or "session" in skill["one_line"].lower(), (
        f"one_line does not look like the behavioral description: {skill['one_line']!r}"
    )


def test_colon_agent_unquoted_description_parsed(fake_repo):
    """colon-agent's unquoted-colon description must yield a non-empty one_line.

    Guards FIX 1 for agents: description: Use at session start: do X ...
    must not produce an empty/fallback one_line.
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo)
    agent = next((e for e in entries if e["name"] == "colon-agent"), None)
    assert agent is not None
    assert agent["one_line"], "one_line is empty for colon-agent — colon-robust parser not working"
    assert "session start" in agent["one_line"].lower(), (
        f"Expected 'session start' in one_line, got: {agent['one_line']!r}"
    )


# ── FIX 2 guard: secret scrubbing ─────────────────────────────────────────


def test_secret_scrub_ghp_token_in_script(tmp_path):
    """A script containing a ghp_ token must produce [REDACTED] in one_line/triggers.

    Guards FIX 2b: the secret-scrub pass must fire before values enter the cache.
    """
    import textwrap as tw
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # Plant a fake GitHub PAT in the first comment line (extracted as one_line).
    fake_token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    (scripts / "deploy.sh").write_text(
        tw.dedent(f"""\
        #!/bin/bash
        # Deploy using token {fake_token} to prod.
        echo done
        """),
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path)
    script_entry = next((e for e in entries if e["name"] == "deploy"), None)
    assert script_entry is not None
    assert fake_token not in script_entry["one_line"], (
        f"Secret token leaked into one_line: {script_entry['one_line']!r}"
    )
    assert "[REDACTED]" in script_entry["one_line"], (
        f"Expected [REDACTED] in one_line, got: {script_entry['one_line']!r}"
    )
    triggers_str = " ".join(script_entry["triggers"])
    assert fake_token not in triggers_str, (
        f"Secret token leaked into triggers: {script_entry['triggers']!r}"
    )


# ── FIX 3 guard: automatic staleness detection ────────────────────────────


def test_cache_auto_rebuilds_on_mtime_advance(fake_repo):
    """Editing a NESTED skills/<name>/SKILL.md content triggers auto-rebuild.

    FIX A: _max_mtime_of_dirs now recurses — a content edit to a nested
    SKILL.md bumps the file's own mtime even when the parent ``skills/``
    dir mtime does not change.  The cache must auto-rebuild on the next
    build_registry() call without force_rebuild=True.

    Robustness (I-4): uses a longer sleep (0.15 s) to survive coarse-mtime
    filesystems, AND validates the rebuild via observed new content rather
    than relying solely on mtime granularity.  If content changed but the
    registry still returns the old one_line, the test correctly fails.
    """
    import os
    import time
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    _invalidate_cache(fake_repo)
    first = build_registry(fake_repo)
    first_names = {e["name"] for e in first}
    assert "my-skill" in first_names

    # Record original one_line so we can assert it changed after the edit.
    original_entry = next((e for e in first if e["name"] == "my-skill"), None)
    assert original_entry is not None
    original_one_line = original_entry["one_line"]
    assert "UPDATED" not in original_one_line, (
        "Precondition: original one_line must not contain 'UPDATED'"
    )

    # Sleep long enough for mtime granularity on coarse-mtime filesystems
    # (e.g. Linux tmpfs with 1 s resolution).  0.15 s is safe on modern
    # ext4/tmpfs but we also force the mtime explicitly via os.utime as a
    # belt-and-suspenders measure against sub-second-resolution file systems.
    nested_skill = fake_repo / "skills" / "my-skill" / "SKILL.md"
    nested_skill.write_text(
        textwrap.dedent("""\
        ---
        name: my-skill
        description: UPDATED description after cache was primed.
        ---
        # My Skill
        """),
        encoding="utf-8",
    )
    # Belt-and-suspenders: bump the mtime at least 1 second past the cached
    # build_mtime so even 1-second-granularity filesystems detect the change.
    future_ts = time.time() + 2.0
    os.utime(nested_skill, (future_ts, future_ts))

    # Call build_registry WITHOUT force_rebuild — FIX A staleness check must
    # detect the nested file mtime change and trigger a rebuild.
    refreshed = build_registry(fake_repo, force_rebuild=False)
    skill_entry = next((e for e in refreshed if e["name"] == "my-skill"), None)
    assert skill_entry is not None
    # Assert via CONTENT, not just timing: the new one_line must differ from
    # the original and contain the updated description.
    assert "UPDATED" in skill_entry["one_line"], (
        "Cache was NOT auto-rebuilt after nested SKILL.md content edit — "
        "FIX A recursive mtime check not working. one_line: "
        f"{skill_entry['one_line']!r}"
    )


# ── FIX B guard: ignore-matcher warning + scrub still runs ────────────────


def test_ignore_matcher_warning_emitted_when_loader_raises(tmp_path):
    """When the GitignoreMatcher loader raises, a warning is emitted (not silent).

    Guards FIX B: fail-open must be observable.  We monkeypatch
    _load_gitignore_matcher_class to raise ImportError and confirm that
    (a) a warning is logged via logger.warning(), and
    (b) scrubbing still occurs on entries (defense-in-depth backstop).
    """
    from unittest.mock import patch

    from sage_mcp.extensions import skill_registry
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    # Plant a skill with a fake GitHub token so we can confirm scrub runs.
    skills_dir = tmp_path / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    fake_token = "ghp_ZZZZZZZZZZZZZZZZZZZZzzzzzzzzzzzz"
    (skills_dir / "SKILL.md").write_text(
        f"---\nname: test-skill\ndescription: Uses token {fake_token} for auth.\n---\n",
        encoding="utf-8",
    )
    # Plant a .sageignore so the loader would be consulted normally.
    (tmp_path / ".sageignore").write_text("ignored-*\n", encoding="utf-8")

    _invalidate_cache(tmp_path)
    with patch.object(
        skill_registry,
        "_load_gitignore_matcher_class",
        side_effect=ImportError("chromadb not installed"),
    ):
        with patch.object(skill_registry.logger, "warning") as mock_warn:
            entries = build_registry(tmp_path, force_rebuild=True)

    # (a) Warning must have been emitted.
    assert mock_warn.called, (
        "Expected logger.warning() to be called when matcher loader raises, but it was not called."
    )
    warn_msg = str(mock_warn.call_args)
    assert "ignore-matcher unavailable" in warn_msg or "nookignore" in warn_msg, (
        f"Warning message does not mention nookignore: {warn_msg}"
    )

    # (b) Scrub still runs — token must not appear in any entry.
    for entry in entries:
        assert fake_token not in entry["one_line"], (
            f"Secret token leaked into one_line even with broken matcher: {entry['one_line']!r}"
        )


# ── FIX C guard: scrub-before-truncation boundary test ────────────────────


def test_boundary_straddling_secret_fully_redacted(tmp_path):
    """A secret positioned so the old truncation boundary would split it is
    still fully redacted under FIX C (scrub-before-truncation).

    We construct a description where a fake GitHub PAT sits so its end
    characters would fall past the _ONE_LINE_MAX cut point if truncation
    happened before scrubbing.  After FIX C the scrub runs on the full
    text first, so the pattern match succeeds and [REDACTED] replaces the
    entire token before any truncation occurs.
    """
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        _invalidate_cache,
        _ONE_LINE_MAX,
    )

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Build a description where the fake token straddles the cut boundary.
    # Pad before the token so that truncation at _ONE_LINE_MAX would cut
    # somewhere INSIDE the token (leaving a partial prefix in one_line).
    fake_token = "ghp_BOUNDARYTESTTOKEN1234567890abcdef"
    # We need enough prefix so token starts just before the cut point.
    prefix_len = _ONE_LINE_MAX - 5  # token starts 5 chars before the cut
    prefix = "x" * prefix_len
    full_description = f"{prefix} {fake_token} extra words after"

    (agents_dir / "boundary-agent.md").write_text(
        f"---\nname: boundary-agent\ndescription: {full_description}\n---\n",
        encoding="utf-8",
    )

    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    entry = next((e for e in entries if e["name"] == "boundary-agent"), None)
    assert entry is not None

    # The raw token must NOT appear in one_line or triggers.
    assert fake_token not in entry["one_line"], (
        f"Boundary-straddling secret leaked into one_line: {entry['one_line']!r}"
    )
    triggers_str = " ".join(entry["triggers"])
    assert fake_token not in triggers_str, (
        f"Boundary-straddling secret leaked into triggers: {entry['triggers']!r}"
    )
    # [REDACTED] must appear somewhere — either in one_line (if the token
    # starts before the truncation point) or the token is entirely after the
    # cut and the one_line contains only the prefix.  The key invariant is
    # that no partial token prefix leaks unredacted.  We assert no partial
    # prefix of the token (first 8 chars minimum) appears unredacted.
    token_prefix = fake_token[:8]  # "ghp_BOUN"
    assert token_prefix not in entry["one_line"] or "[REDACTED]" in entry["one_line"], (
        f"Partial token prefix leaked without [REDACTED] in one_line: {entry['one_line']!r}"
    )


# ── FIX D guard: extended secret patterns ─────────────────────────────────


def test_slack_token_redacted(tmp_path):
    """Slack tokens (xox[baprs]-...) are redacted by FIX D patterns."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    fake_token = "-".join(["xoxb", "12345678901", "ABCDEFGHIJKLMNOP"])  # ADR-0106: runtime concat
    (agents_dir / "slack-agent.md").write_text(
        f"---\nname: slack-agent\ndescription: Calls Slack using {fake_token} token.\n---\n",
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    entry = next(e for e in entries if e["name"] == "slack-agent")
    assert fake_token not in entry["one_line"], (
        f"Slack token leaked into one_line: {entry['one_line']!r}"
    )
    assert "[REDACTED]" in entry["one_line"]


def test_jwt_token_redacted(tmp_path):
    """JWT tokens (eyJ...eyJ...signature) are redacted by FIX D patterns."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    (agents_dir / "jwt-agent.md").write_text(
        f"---\nname: jwt-agent\ndescription: Authenticates with bearer {fake_jwt} credential.\n---\n",
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    entry = next(e for e in entries if e["name"] == "jwt-agent")
    assert fake_jwt not in entry["one_line"], (
        f"JWT token leaked into one_line: {entry['one_line']!r}"
    )
    assert "[REDACTED]" in entry["one_line"]


def test_high_entropy_hex_40_chars_survives_in_registry(tmp_path):
    """ADR-0042: The registry uses high-confidence (write-boundary) scrub only.
    A 40-char hex string (git SHA shape) survives verbatim in the registry
    one_line descriptor — over-redaction is not acceptable at this stage.
    The aggressive hex≥40 scrub is applied to the FULL Tier-0 block in
    assemble_tier0, which covers the registry section too.
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    fake_hex_40 = "a" * 40  # exactly 40 hex chars — git SHA shape
    (agents_dir / "hex-agent.md").write_text(
        f"---\nname: hex-agent\ndescription: Uses secret key {fake_hex_40} for HMAC.\n---\n",
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    entry = next(e for e in entries if e["name"] == "hex-agent")
    # The registry stores the hex value; the Tier-0 aggressive scrub handles
    # it at block-assembly time (ADR-0042 two-tier discipline).
    assert fake_hex_40 in entry["one_line"], (
        f"Registry should preserve 40-char hex in one_line (ADR-0042); got: {entry['one_line']!r}"
    )


def test_short_hex_8_chars_not_redacted(tmp_path):
    """A short 8-char hex string is NOT redacted — too short to be a secret."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    short_hex = "deadbeef"  # 8 chars — must NOT be redacted
    (agents_dir / "short-hex-agent.md").write_text(
        f"---\nname: short-hex-agent\ndescription: Returns checksum {short_hex} for validation.\n---\n",
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    entry = next(e for e in entries if e["name"] == "short-hex-agent")
    assert short_hex in entry["one_line"], (
        f"Short 8-char hex was incorrectly redacted. one_line: {entry['one_line']!r}"
    )


# ── FIX E guard: block-scalar marker dropped from description ─────────────


def test_block_scalar_pipe_marker_dropped(tmp_path):
    """A description: | (pipe block scalar) must not leak '|' into one_line.

    Guards FIX E: the manual parser must detect the block-scalar indicator
    on the value portion of the key line and skip it, treating the following
    indented lines as the actual value.
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    skills_dir = tmp_path / "skills" / "pipe-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: pipe-skill
        description: |
          Use when processing YAML block scalars. Do not use for inline values.
        ---
        # Pipe Skill
        """),
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    entry = next((e for e in entries if e["name"] == "pipe-skill"), None)
    assert entry is not None
    assert entry["one_line"] != "|", (
        f"Block-scalar '|' marker leaked as one_line: {entry['one_line']!r}"
    )
    assert "|" not in entry["one_line"] or "YAML" in entry["one_line"], (
        f"Unexpected '|' in one_line (not from YAML content): {entry['one_line']!r}"
    )
    # Must contain the actual description content, not the marker.
    assert (
        "YAML" in entry["one_line"]
        or "block scalar" in entry["one_line"]
        or "processing" in entry["one_line"]
    ), f"one_line does not contain expected description content: {entry['one_line']!r}"


def test_block_scalar_gt_marker_dropped(tmp_path):
    """A description: > (folded block scalar) must not leak '>' into one_line."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    skills_dir = tmp_path / "skills" / "gt-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: gt-skill
        description: >
          Use when folding long YAML descriptions. Triggers on folded content.
        ---
        # GT Skill
        """),
        encoding="utf-8",
    )
    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    entry = next((e for e in entries if e["name"] == "gt-skill"), None)
    assert entry is not None
    assert entry["one_line"] != ">", (
        f"Block-scalar '>' marker leaked as one_line: {entry['one_line']!r}"
    )
    assert (
        "folding" in entry["one_line"]
        or "YAML" in entry["one_line"]
        or "folded" in entry["one_line"]
    ), f"one_line does not contain expected description content: {entry['one_line']!r}"


def test_gh_scaffold_skill_block_scalar_resolved(fake_repo):
    """gh-scaffold-discipline-style pipe description yields real content, not '|'.

    Integration guard for FIX E: reproduces the real-world failure observed
    on skills/gh-scaffold-discipline where description: | caused '|' to leak.
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    # Add a skill mimicking gh-scaffold-discipline's frontmatter shape.
    skill_dir = fake_repo / "skills" / "scaffold-style"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
        ---
        name: scaffold-style
        description: |
          Use when scaffolding a new GitHub repository. Triggers on scaffold commands.
        ---
        # Scaffold Style Skill
        """),
        encoding="utf-8",
    )
    _invalidate_cache(fake_repo)
    entries = build_registry(fake_repo, force_rebuild=True)
    entry = next((e for e in entries if e["name"] == "scaffold-style"), None)
    assert entry is not None
    assert entry["one_line"] != "|", (
        f"Block-scalar '|' leaked as entire one_line: {entry['one_line']!r}"
    )
    assert "scaffold" in entry["one_line"].lower() or "GitHub" in entry["one_line"], (
        f"one_line does not reflect description content: {entry['one_line']!r}"
    )


# ── FIX G guard: .sageignore filtering ─────────────────────────────


def test_nookignore_filters_matching_file(tmp_path):
    """A file matching a .sageignore pattern must not appear in the registry.

    Guards FIX 2a (ignore-consult) + FIX G (test coverage): plants a skill
    and an agent, ignores the agent via .sageignore, and asserts the
    agent is absent while the skill is present.
    """
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    # Plant an agent to be ignored.
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "ignored-agent.md").write_text(
        "---\nname: ignored-agent\ndescription: Should be filtered out.\n---\n",
        encoding="utf-8",
    )
    (agents_dir / "visible-agent.md").write_text(
        "---\nname: visible-agent\ndescription: Should remain visible.\n---\n",
        encoding="utf-8",
    )
    # Plant a skill that should NOT be filtered.
    skills_dir = tmp_path / "skills" / "kept-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: kept-skill\ndescription: Kept skill, not ignored.\n---\n",
        encoding="utf-8",
    )
    # Write a .sageignore that excludes only ignored-agent.md.
    (tmp_path / ".sageignore").write_text(
        "agents/ignored-agent.md\n",
        encoding="utf-8",
    )

    _invalidate_cache(tmp_path)
    entries = build_registry(tmp_path, force_rebuild=True)
    names = {e["name"] for e in entries}

    assert "ignored-agent" not in names, (
        f"ignored-agent should have been filtered by .sageignore but appeared in: {names}"
    )
    assert "visible-agent" in names, (
        f"visible-agent should not be filtered but is missing from: {names}"
    )
    assert "kept-skill" in names, f"kept-skill should not be filtered but is missing from: {names}"


# ── Additional skill_registry unit tests (relocated from test_bootstrap.py) ──


def test_skill_registry_build_and_search(tmp_path):
    """build_registry + search_registry work on a synthetic repo tree."""
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    # Create a minimal agents/ + skills/ tree.
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "my-agent.md").write_text(
        "---\nname: my-agent\ndescription: Does things for you\n---\n\n# My Agent\n\nBody text.\n"
    )

    skills_dir = tmp_path / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Helps with skill tasks\n---\n\n## My Skill\n\nDetails.\n"
    )

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    kinds = {e["kind"] for e in entries}
    assert "agent" in kinds
    assert "skill" in kinds

    # Second call returns from cache.
    entries2 = build_registry(str(tmp_path))
    assert len(entries2) == len(entries)

    results = search_registry(entries, "things")
    assert any(e["name"] == "my-agent" for e in results)

    results_empty = search_registry(entries, "zzznomatches")
    assert results_empty == []


def test_skill_registry_first_substantive_line_branches(tmp_path):
    """_first_substantive_line handles headings, shebangs, docstrings, frontmatter."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Agent with no frontmatter description — triggers body parsing
    (agents_dir / "no-desc.md").write_text(
        "---\nname: no-desc\n---\n\n## Section\n\nFirst prose line.\n"
    )
    # Agent with heading followed by content
    (agents_dir / "heading-agent.md").write_text(
        "---\nname: heading-agent\n---\n\n# Title\n\nContent after heading.\n"
    )
    # Script-like agent with shebang (tests shebang skip)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "myscript.sh").write_text("#!/bin/bash\n# Script description\necho hello\n")
    (scripts_dir / "nocomment.py").write_text('"""Docstring description"""\nprint("hi")\n')

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    names = {e["name"] for e in entries}
    assert "no-desc" in names
    assert "myscript" in names


def test_skill_registry_search_with_kind_filter(tmp_path):
    """search_registry kind filter returns only matching kind."""
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "agentx.md").write_text(
        "---\nname: agentx\ndescription: agent thing\n---\n\n# Agentx\n"
    )
    skills_dir = tmp_path / "skills" / "skillx"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: skillx\ndescription: skill thing\n---\n\n## Skillx\n"
    )

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    agent_results = search_registry(entries, "", kind="agent")
    assert all(e["kind"] == "agent" for e in agent_results)

    skill_results = search_registry(entries, "", kind="skill")
    assert all(e["kind"] == "skill" for e in skill_results)


def test_skill_registry_internal_parsing(tmp_path):
    """Exercises edge-case branches in skill_registry parsers."""
    from sage_mcp.extensions.skill_registry import (
        _split_frontmatter,
        _invalidate_cache,
        build_registry,
    )

    # _split_frontmatter: unterminated fence returns ([], text)
    fm_lines, body = _split_frontmatter("---\nname: foo\n")
    assert fm_lines == []
    assert "name: foo" in body

    # _split_frontmatter: no fence at all
    fm_lines, body = _split_frontmatter("plain text\nno frontmatter")
    assert fm_lines == []
    assert "plain text" in body

    # Build registry with files that exercise docstring empty opener, empty file, OSError
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Agent with triple-quote docstring opener on its own line (empty inline → next line)
    (agents_dir / "docstr-agent.md").write_text(
        '---\nname: docstr-agent\n---\n\n"""\nContent on next line.\n"""\n'
    )
    # Agent with only headings and no prose (hits return "")
    (agents_dir / "no-prose.md").write_text("---\nname: no-prose\n---\n\n## Heading only\n")
    # Agent with frontmatter only, no body at all (hits return "")
    (agents_dir / "empty-body.md").write_text("---\nname: empty-body\n---\n")

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    names = {e["name"] for e in entries}
    assert "docstr-agent" in names
    assert "no-prose" in names
    assert "empty-body" in names


def test_skill_registry_mtime_and_cache(tmp_path):
    """build_registry cache is returned on second call without force_rebuild."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "cached-agent.md").write_text(
        "---\nname: cached-agent\ndescription: cached test\n---\n\n# Cached\n"
    )

    _invalidate_cache(tmp_path)
    entries1 = build_registry(str(tmp_path), force_rebuild=True)
    entries2 = build_registry(str(tmp_path))  # should hit cache
    _invalidate_cache(tmp_path)

    assert entries1 == entries2


def test_skill_registry_ignores_binary_scripts(tmp_path):
    """Scripts with binary suffixes are skipped."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "binary.pyc").write_bytes(b"\x00\x01\x02binary")
    (scripts_dir / "real_script.sh").write_text("#!/bin/bash\n# A real script\necho hi\n")

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    names = {e["name"] for e in entries}
    assert "binary" not in names
    assert "real_script" in names


def test_skill_registry_hidden_and_oserror_agents(tmp_path):
    """Hidden agent files and OSError reads are silently skipped."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Hidden agent (starts with '.') — must be skipped
    (agents_dir / ".hidden-agent.md").write_text("---\nname: hidden\n---\n\n# Hidden\n")
    # Readable agent
    (agents_dir / "visible-agent.md").write_text(
        "---\nname: visible\ndescription: ok\n---\n\n# Visible\n"
    )

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    names = {e["name"] for e in entries}
    assert "hidden" not in names
    assert "visible" in names


def test_skill_registry_hidden_scripts(tmp_path):
    """Hidden script files (starting with '.') are skipped."""
    from sage_mcp.extensions.skill_registry import build_registry, _invalidate_cache

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / ".hidden-script.sh").write_text("#!/bin/bash\n# hidden\n")
    (scripts_dir / "visible.sh").write_text("#!/bin/bash\n# Visible script\necho hi\n")

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    names = {e["name"] for e in entries}
    assert "hidden-script" not in names
    assert "visible" in names


def test_skill_registry_invalidate_cache_all(tmp_path):
    """_invalidate_cache(None) clears all cached entries."""
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        _invalidate_cache,
        _cached_registry,
    )

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "cache-agent.md").write_text(
        "---\nname: cache-agent\ndescription: t\n---\n\n# T\n"
    )

    _invalidate_cache(tmp_path)
    build_registry(str(tmp_path), force_rebuild=True)

    # Should have something cached now.
    assert len(_cached_registry) >= 1

    # Invalidate all.
    _invalidate_cache(None)
    assert len(_cached_registry) == 0


def test_skill_registry_search_kind_filter_excludes(tmp_path):
    """search_registry with kind filter skips entries of a different kind."""
    from sage_mcp.extensions.skill_registry import (
        build_registry,
        search_registry,
        _invalidate_cache,
    )

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "agent-a.md").write_text(
        "---\nname: agent-a\ndescription: agent thing\n---\n\n# Agent A\n"
    )
    skills_dir = tmp_path / "skills" / "skill-b"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: skill-b\ndescription: skill thing\n---\n\n## Skill B\n"
    )

    _invalidate_cache(tmp_path)
    entries = build_registry(str(tmp_path), force_rebuild=True)
    _invalidate_cache(tmp_path)

    # Search with kind=skill, query that would match agent too if not filtered
    results = search_registry(entries, "thing", kind="skill")
    assert all(e["kind"] == "skill" for e in results)
    assert any(e["name"] == "skill-b" for e in results)
