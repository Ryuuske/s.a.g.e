"""test_agent_catalog_generator.py — Tests for installer-assets/gen-agent-catalog.py.

C6 acceptance criteria:
  - Running the generator against repo agents/ yields exactly the roster count
    with all names present.
  - Each entry has name, family, one_line fields.
  - install.sh --dry-run says it would generate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GENERATOR = _REPO_ROOT / "installer-assets" / "gen-agent-catalog.py"
_AGENTS_DIR = _REPO_ROOT / "agents"


@pytest.fixture(scope="module")
def catalog_output(tmp_path_factory) -> dict:
    """Run the generator against the real agents dir and parse the output."""
    tmp = tmp_path_factory.mktemp("catalog")
    out_file = tmp / "agent-catalog.json"
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), str(_AGENTS_DIR), str(out_file)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Generator exited {result.returncode}; stderr={result.stderr!r}"
    assert out_file.exists(), "Generator did not create the output file"
    return json.loads(out_file.read_text(encoding="utf-8"))


def _count_agents() -> int:
    """Count the number of agent .md files in agents/."""
    return len(list(_AGENTS_DIR.glob("*.md")))


def test_catalog_has_correct_agent_count(catalog_output):
    """Catalog must contain exactly the same number of agents as agents/*.md."""
    expected = _count_agents()
    actual = len(catalog_output["agents"])
    assert actual == expected, (
        f"Catalog has {actual} agents but agents/ contains {expected} .md files"
    )


def test_catalog_contains_all_agent_names(catalog_output):
    """Every agents/*.md name (without .md) must appear in the catalog."""
    catalog_names = {a["name"] for a in catalog_output["agents"]}
    for md_file in _AGENTS_DIR.glob("*.md"):
        expected_name = md_file.stem
        assert expected_name in catalog_names, (
            f"Agent '{expected_name}' from agents/{md_file.name} is missing from catalog"
        )


def test_catalog_schema_fields(catalog_output):
    """Every entry must have name, family, and one_line fields."""
    for entry in catalog_output["agents"]:
        assert "name" in entry, f"Missing 'name' in entry: {entry}"
        assert "family" in entry, f"Missing 'family' in entry: {entry}"
        assert "one_line" in entry, f"Missing 'one_line' in entry: {entry}"
        assert isinstance(entry["name"], str) and entry["name"], "name must be non-empty string"
        assert isinstance(entry["family"], str) and entry["family"], (
            "family must be non-empty string"
        )
        assert isinstance(entry["one_line"], str) and entry["one_line"], (
            "one_line must be non-empty string"
        )


def test_catalog_top_level_schema(catalog_output):
    """Top-level schema: version, generated_at, agents list."""
    assert catalog_output.get("version") == "1.0.0"
    assert "generated_at" in catalog_output
    assert isinstance(catalog_output["agents"], list)


def test_catalog_sorted_by_name(catalog_output):
    """Agents list must be sorted by name."""
    names = [a["name"] for a in catalog_output["agents"]]
    assert names == sorted(names), "Catalog agents must be sorted by name"


def test_non_agent_md_is_excluded(tmp_path):
    """A .md file without frontmatter `name:` must NOT become a catalog entry (AA-1).

    Kills the count-test tautology: a stray README.md or notes file dropped into
    agents/ would otherwise inflate the catalog via the filename-stem fallback.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: A real agent. Does things.\n---\n# Real\n",
        encoding="utf-8",
    )
    (agents_dir / "README.md").write_text(
        "# Notes about this directory\nNot an agent.\n", encoding="utf-8"
    )
    out_file = tmp_path / "catalog.json"
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), str(agents_dir), str(out_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    catalog = json.loads(out_file.read_text(encoding="utf-8"))
    names = [a["name"] for a in catalog["agents"]]
    assert names == ["real-agent"], f"non-agent md leaked into catalog: {names}"
    assert "README" in result.stderr, "expected a WARNING naming the skipped file"


def test_unclosed_frontmatter_is_skipped(tmp_path):
    """A file whose frontmatter never closes must be skipped, not body-scanned (AA-2)."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: A real agent.\n---\n# Real\n",
        encoding="utf-8",
    )
    (agents_dir / "broken.md").write_text(
        "---\nname: broken-agent\n# no closing fence; body follows\n"
        "description: this body line must never overwrite frontmatter\n",
        encoding="utf-8",
    )
    out_file = tmp_path / "catalog.json"
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), str(agents_dir), str(out_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    catalog = json.loads(out_file.read_text(encoding="utf-8"))
    names = [a["name"] for a in catalog["agents"]]
    assert names == ["real-agent"], f"unclosed-frontmatter file leaked: {names}"


def test_symlinked_output_path_refused(tmp_path):
    """A symlinked output path must be refused, not followed (post-merge sec finding).

    Writing through a symlink would let a pre-planted link redirect the catalog
    write to an arbitrary file; the generator must exit nonzero and leave the
    link target untouched.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: A real agent.\n---\n", encoding="utf-8"
    )
    target = tmp_path / "innocent-file.json"
    target.write_text('{"do": "not clobber"}', encoding="utf-8")
    link = tmp_path / "catalog.json"
    link.symlink_to(target)
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), str(agents_dir), str(link)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0, "generator must refuse a symlinked output path"
    assert "symlink" in result.stderr.lower()
    assert target.read_text(encoding="utf-8") == '{"do": "not clobber"}', (
        "symlink target was clobbered"
    )


def test_preplanted_tmp_symlink_cannot_redirect_write(tmp_path):
    """A pre-planted symlink at a predictable temp name must not be followed (PR #45 review).

    The output-path symlink refusal alone is bypassable: with catalog.json.tmp
    pre-planted as a symlink, the temp write follows it (clobbering its target)
    and os.replace then installs the symlink as the catalog. The temp file must
    be unpredictable + O_EXCL so a planted name can never be hit.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: A real agent.\n---\n", encoding="utf-8"
    )
    target = tmp_path / "innocent.json"
    target.write_text('{"do": "not clobber"}', encoding="utf-8")
    out = tmp_path / "catalog.json"
    (tmp_path / "catalog.json.tmp").symlink_to(target)
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), str(agents_dir), str(out)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert target.read_text(encoding="utf-8") == '{"do": "not clobber"}', (
        "pre-planted tmp symlink target was clobbered"
    )
    if result.returncode == 0:
        assert not out.is_symlink(), "catalog landed as a symlink"
        json.loads(out.read_text(encoding="utf-8"))  # real, valid catalog


def test_generator_fails_on_missing_agents_dir(tmp_path):
    """Generator must exit nonzero when agents dir is absent."""
    missing = tmp_path / "no_such_dir"
    out_file = tmp_path / "catalog.json"
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), str(missing), str(out_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0, "Generator should exit nonzero for missing agents dir"


def test_install_sh_dry_run_mentions_catalog(tmp_path):
    """install.sh --dry-run must mention it would generate the catalog."""
    install_sh = _REPO_ROOT / "install.sh"
    if not install_sh.exists():
        pytest.skip("install.sh not found")
    env_overrides = {
        "HOME": str(tmp_path),
        "CLAUDE_DIR": str(tmp_path / ".claude"),
    }
    import os

    env = os.environ.copy()
    env.update(env_overrides)
    result = subprocess.run(
        ["bash", str(install_sh), "--dry-run", "--no-per-repo-claude-md"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert "would generate" in output.lower() or "agent-catalog" in output.lower(), (
        f"install.sh --dry-run did not mention catalog generation; output={output!r}"
    )
