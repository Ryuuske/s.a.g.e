"""Tests for sage bootstrap — discover_repos + cmd_bootstrap."""

from __future__ import annotations

import argparse
import builtins
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from sage_mcp.cli import (
    DiscoveredRepo,
    _bootstrap_build_registry,
    _bootstrap_count_already_registered,
    cmd_bootstrap,
    discover_repos,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_dev_tree(tmp_path: Path) -> Path:
    """Create a synthetic ~/dev/github/owner/ tree with two repo dirs."""
    owner = tmp_path / "dev" / "github" / "owner"
    owner.mkdir(parents=True)
    (owner / "repo_a").mkdir()
    (owner / "repo_b").mkdir()
    (owner / ".hidden").mkdir()
    (owner / "not_a_dir.txt").write_text("file")
    return owner


def _make_projects_tree(tmp_path: Path) -> Path:
    """Create a synthetic ~/dev/projects/ tree with one project dir."""
    projects = tmp_path / "dev" / "projects"
    projects.mkdir(parents=True)
    (projects / "proj_alpha").mkdir()
    return projects


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        bootstrap_roots=[],
        dry_run=False,
        yes=False,
        no_mine=False,
        nook=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ── discover_repos ────────────────────────────────────────────────────────────


def test_discover_repos_finds_dev_repos(tmp_path, monkeypatch):
    """discover_repos finds two repos from a github/owner/ root."""
    owner = _make_dev_tree(tmp_path)

    # None are registered.
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    roots = [(str(owner), "dev")]
    found = discover_repos(roots)
    slugs = {r.slug for r in found}

    assert slugs == {"repo_a", "repo_b"}, f"expected repo_a + repo_b, got {slugs}"
    for r in found:
        assert r.wing_type == "dev"
        assert Path(r.path).is_dir()


def test_discover_repos_finds_projects(tmp_path, monkeypatch):
    """discover_repos assigns wing_type='project' for a projects root."""
    projects = _make_projects_tree(tmp_path)

    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    roots = [(str(projects), "project")]
    found = discover_repos(roots)

    assert len(found) == 1
    assert found[0].slug == "proj_alpha"
    assert found[0].wing_type == "project"


def test_discover_repos_skips_hidden_dirs(tmp_path, monkeypatch):
    """Directories starting with '.' must be ignored."""
    owner = _make_dev_tree(tmp_path)

    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    found = discover_repos([(str(owner), "dev")])
    slugs = {r.slug for r in found}
    assert ".hidden" not in slugs


def test_discover_repos_warns_on_framework_wing_collision(tmp_path, monkeypatch, capsys):
    """A user repo named like a shipped framework wing (Personal/telemetry) must
    NOT be silently skipped — bootstrap warns with an actionable message.

    Regression for the codex Phase-8 collision finding: the cleaned template ships
    Personal + telemetry (under ~/.sage); a fresh user's ~/dev/projects/Personal
    collides on slug and was silently dropped. Now it warns.
    """
    projects = tmp_path / "projects"
    (projects / "Personal").mkdir(parents=True)
    (projects / "real_app").mkdir()

    # Personal is registered as a framework-internal wing (~/.sage path).
    monkeypatch.setattr(
        "sage_mcp.extensions.wing_registry.is_registered",
        lambda slug: slug == "Personal",
    )
    monkeypatch.setattr(
        "sage_mcp.extensions.wing_registry.registered_wings",
        lambda: {"Personal": {"type": "personal", "path": str(Path.home() / ".sage" / "personal")}},
    )

    found = discover_repos([(str(projects), "project")])
    slugs = {r.slug for r in found}
    assert "real_app" in slugs
    assert "Personal" not in slugs, "framework-wing collision repo is not auto-registered"
    out = capsys.readouterr().out
    assert "Personal" in out and "framework-internal wing" in out, (
        "collision with a framework wing must warn, not silently skip"
    )
    # The recovery command must include the colliding repo's --path so following
    # it produces a usable wing (not a path-less dead-end).
    assert "--path" in out and str(projects / "Personal") in out, (
        "recovery command must carry the repo --path"
    )


def test_collision_warning_shell_quotes_spaced_path(tmp_path, monkeypatch, capsys):
    """The recovery command's --path must be shell-quoted so a path with spaces
    stays copy-pasteable (codex Phase-8 pass-5)."""
    import shlex

    projects = tmp_path / "my projects"  # space in the parent path
    (projects / "Personal").mkdir(parents=True)

    monkeypatch.setattr(
        "sage_mcp.extensions.wing_registry.is_registered",
        lambda slug: slug == "Personal",
    )
    monkeypatch.setattr(
        "sage_mcp.extensions.wing_registry.registered_wings",
        lambda: {"Personal": {"type": "personal", "path": str(Path.home() / ".sage" / "personal")}},
    )

    discover_repos([(str(projects), "project")])
    out = capsys.readouterr().out
    quoted = shlex.quote(str(projects / "Personal"))
    assert f"--path {quoted}" in out, f"path must be shell-quoted; got: {out}"


def test_discover_repos_skips_non_dirs(tmp_path, monkeypatch):
    """Regular files inside a root must not appear as candidates."""
    owner = _make_dev_tree(tmp_path)

    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    found = discover_repos([(str(owner), "dev")])
    slugs = {r.slug for r in found}
    assert "not_a_dir.txt" not in slugs


def test_discover_repos_skips_already_registered(tmp_path, monkeypatch):
    """Slugs that is_registered() returns True for must be skipped."""
    owner = _make_dev_tree(tmp_path)

    # Only repo_a is already registered.
    monkeypatch.setattr(
        "sage_mcp.extensions.wing_registry.is_registered",
        lambda slug: slug == "repo_a",
    )

    found = discover_repos([(str(owner), "dev")])
    slugs = {r.slug for r in found}
    assert "repo_a" not in slugs
    assert "repo_b" in slugs


def test_discover_repos_slug_collision_first_wins(tmp_path, monkeypatch, capsys):
    """When two roots produce the same slug, the first wins and a warning is printed."""
    owner1 = tmp_path / "owner1"
    owner1.mkdir()
    (owner1 / "shared_slug").mkdir()
    owner2 = tmp_path / "owner2"
    owner2.mkdir()
    (owner2 / "shared_slug").mkdir()

    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    roots = [(str(owner1), "dev"), (str(owner2), "dev")]
    found = discover_repos(roots)

    slugs = [r.slug for r in found]
    assert slugs.count("shared_slug") == 1
    assert found[0].path == str((owner1 / "shared_slug").resolve())

    out = capsys.readouterr().out
    assert "collision" in out or "WARNING" in out


# ── FIX 3: symlink skipping ───────────────────────────────────────────────────


def test_discover_repos_skips_symlinked_candidates(tmp_path, monkeypatch):
    """Symlinked child dirs under a root must not be discovered."""
    owner = tmp_path / "owner"
    owner.mkdir()
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    link = owner / "sym_repo"
    link.symlink_to(real_dir)

    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    found = discover_repos([(str(owner), "dev")])
    slugs = {r.slug for r in found}
    assert "sym_repo" not in slugs, f"symlinked dir must be skipped; got slugs={slugs}"


# ── FIX 5: root-is-itself-a-repo guard ───────────────────────────────────────


def test_discover_repos_skips_root_that_is_git_repo(tmp_path, monkeypatch, capsys):
    """A root that is itself a git repo (has .git/) must be skipped with a warning."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / ".git").mkdir()
    (owner / "src").mkdir()  # subdir that would be a bogus wing

    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    found = discover_repos([(str(owner), "dev")])
    assert found == [], f"root-is-git-repo must yield no candidates; got {found}"

    out = capsys.readouterr().out
    assert "git repo" in out or "WARNING" in out


# ── cmd_bootstrap --dry-run ───────────────────────────────────────────────────


def test_cmd_bootstrap_dry_run_registers_nothing(tmp_path, monkeypatch, capsys):
    """--dry-run must not call add_wing, mine, or build_registry."""
    owner = _make_dev_tree(tmp_path)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    add_wing_mock = MagicMock()
    mine_mock = MagicMock()
    build_registry_mock = MagicMock()

    monkeypatch.setattr(
        "sage_mcp.cli.discover_repos",
        lambda roots: [
            DiscoveredRepo(slug="repo_a", wing_type="dev", path=str(owner / "repo_a")),
            DiscoveredRepo(slug="repo_b", wing_type="dev", path=str(owner / "repo_b")),
        ],
    )
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)

    # add_wing is locally imported inside cmd_bootstrap; patch the source module.
    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing", add_wing_mock),
        patch("sage_mcp.cli._bootstrap_mine_candidates", mine_mock),
        patch("sage_mcp.cli._bootstrap_build_registry", build_registry_mock),
    ):
        args = _make_args(dry_run=True)
        cmd_bootstrap(args)

    add_wing_mock.assert_not_called()
    mine_mock.assert_not_called()
    build_registry_mock.assert_not_called()

    out = capsys.readouterr().out
    assert "Dry-run" in out


# ── cmd_bootstrap --no-mine ───────────────────────────────────────────────────


def test_cmd_bootstrap_no_mine_skips_mining(tmp_path, monkeypatch, capsys):
    """--no-mine must register wings and build registry but NOT invoke mine."""
    owner = _make_dev_tree(tmp_path)

    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    add_wing_mock = MagicMock()
    mine_mock = MagicMock()
    build_registry_mock = MagicMock(return_value=(3, True))

    discovered = [
        DiscoveredRepo(slug="repo_a", wing_type="dev", path=str(owner / "repo_a")),
    ]
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)

    # add_wing is locally imported inside cmd_bootstrap; patch the source module.
    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing", add_wing_mock),
        patch("sage_mcp.cli._bootstrap_mine_candidates", mine_mock),
        patch("sage_mcp.cli._bootstrap_build_registry", build_registry_mock),
    ):
        args = _make_args(no_mine=True)
        cmd_bootstrap(args)

    mine_mock.assert_not_called()
    add_wing_mock.assert_called_once_with("repo_a", "dev", path=str(owner / "repo_a"))
    build_registry_mock.assert_called_once()

    out = capsys.readouterr().out
    assert "--no-mine" in out


# ── cmd_bootstrap mine gate ────────────────────────────────────────────────────


def test_cmd_bootstrap_non_tty_without_yes_does_not_mine(tmp_path, monkeypatch, capsys):
    """Non-TTY stdin without --yes: mine helper is called with yes=False."""
    mine_mock = MagicMock(return_value=([], []))
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_x").mkdir()

    discovered = [DiscoveredRepo(slug="repo_x", wing_type="dev", path=str(owner / "repo_x"))]

    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing"),
        patch("sage_mcp.cli._bootstrap_mine_candidates", mine_mock),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(0, True)),
    ):
        args = _make_args(yes=False)
        cmd_bootstrap(args)

    # Mine helper must be invoked (control flow enters the mine branch) with yes=False.
    mine_mock.assert_called_once()
    call_kwargs = mine_mock.call_args
    yes_value = call_kwargs.kwargs.get("yes") if call_kwargs.kwargs else call_kwargs.args[1]
    assert yes_value is False


def test_cmd_bootstrap_yes_mines_each_discovered_repo(tmp_path, monkeypatch, capsys):
    """--yes must invoke _bootstrap_mine_candidates with yes=True."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_y").mkdir()

    discovered = [DiscoveredRepo(slug="repo_y", wing_type="dev", path=str(owner / "repo_y"))]

    mine_mock = MagicMock(return_value=(["repo_y"], []))
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing"),
        patch("sage_mcp.cli._bootstrap_mine_candidates", mine_mock),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(1, True)),
    ):
        args = _make_args(yes=True)
        cmd_bootstrap(args)

    mine_mock.assert_called_once()
    call_kwargs = mine_mock.call_args
    yes_value = call_kwargs.kwargs.get("yes") if call_kwargs.kwargs else call_kwargs.args[1]
    assert yes_value is True


# ── registration idempotency ───────────────────────────────────────────────────


def test_cmd_bootstrap_already_registered_slug_is_skipped(tmp_path, monkeypatch, capsys):
    """A repo whose slug is already registered must not call add_wing."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "existing_wing").mkdir()

    # Returning True = already registered.
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: True)
    discovered = [
        DiscoveredRepo(slug="existing_wing", wing_type="dev", path=str(owner / "existing_wing"))
    ]
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 1)

    add_wing_mock = MagicMock()
    # add_wing is locally imported inside cmd_bootstrap; patch the source module.
    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing", add_wing_mock),
        patch("sage_mcp.cli._bootstrap_mine_candidates", return_value=([], [])),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(0, True)),
    ):
        args = _make_args()
        cmd_bootstrap(args)

    add_wing_mock.assert_not_called()
    out = capsys.readouterr().out
    assert "skip" in out or "already registered" in out


# ── mine failure does not abort the batch ─────────────────────────────────────


def test_bootstrap_mine_failure_collected_not_aborted(tmp_path, monkeypatch, capsys):
    """A single repo mine failure must be collected; other repos still proceed."""
    from sage_mcp.cli import _bootstrap_mine_candidates

    owner = tmp_path / "owner"
    owner.mkdir()
    for name in ("good_repo", "bad_repo"):
        (owner / name).mkdir()

    discovered = [
        DiscoveredRepo(slug="good_repo", wing_type="dev", path=str(owner / "good_repo")),
        DiscoveredRepo(slug="bad_repo", wing_type="dev", path=str(owner / "bad_repo")),
    ]

    def _fake_mine(project_dir, nook_path, wing_override):
        if wing_override == "bad_repo":
            raise RuntimeError("simulated mine failure")

    with patch("sage_mcp.miner.mine", _fake_mine):
        mine_ok, mine_errors = _bootstrap_mine_candidates(
            discovered,
            yes=True,
            nook_path="/fake/nook",
        )

    assert "good_repo" in mine_ok
    assert len(mine_errors) == 1
    assert mine_errors[0][0] == "bad_repo"
    assert "simulated mine failure" in mine_errors[0][1]


# ── _bootstrap_mine_candidates TTY / non-TTY ──────────────────────────────────


def test_bootstrap_mine_candidates_non_tty_no_yes_does_not_mine(tmp_path, capsys):
    """Non-TTY stdin without yes=True must return empty lists."""
    from sage_mcp.cli import _bootstrap_mine_candidates

    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_z").mkdir()

    discovered = [DiscoveredRepo(slug="repo_z", wing_type="dev", path=str(owner / "repo_z"))]

    with (
        patch("sage_mcp.miner.mine") as mine_mock,
        patch.object(sys.stdin, "isatty", return_value=False),
    ):
        mine_ok, mine_errors = _bootstrap_mine_candidates(
            discovered, yes=False, nook_path="/fake/nook"
        )

    mine_mock.assert_not_called()
    assert mine_ok == []
    assert mine_errors == []


def test_bootstrap_mine_candidates_yes_mines(tmp_path):
    """yes=True must mine without prompting."""
    from sage_mcp.cli import _bootstrap_mine_candidates

    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_m").mkdir()

    discovered = [DiscoveredRepo(slug="repo_m", wing_type="dev", path=str(owner / "repo_m"))]

    with patch("sage_mcp.miner.mine") as mine_mock:
        mine_ok, mine_errors = _bootstrap_mine_candidates(
            discovered, yes=True, nook_path="/fake/nook"
        )

    mine_mock.assert_called_once_with(
        project_dir=str(owner / "repo_m"),
        nook_path="/fake/nook",
        wing_override="repo_m",
    )
    assert mine_ok == ["repo_m"]
    assert mine_errors == []


# ── FIX 1 additional coverage tests ──────────────────────────────────────────


def test_cmd_bootstrap_n_new_zero_prints_nothing_to_register(tmp_path, monkeypatch, capsys):
    """When n_new == 0 (all already registered), 'Nothing to register' is printed."""
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: [])
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 2)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: True)

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.cli._bootstrap_mine_candidates", return_value=([], [])),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(0, True)),
    ):
        args = _make_args()
        cmd_bootstrap(args)

    out = capsys.readouterr().out
    assert "Nothing to register" in out


def test_cmd_bootstrap_exits_1_on_mine_errors(tmp_path, monkeypatch):
    """When mine_errors is non-empty, cmd_bootstrap must exit with code 1."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_bad").mkdir()

    discovered = [DiscoveredRepo(slug="repo_bad", wing_type="dev", path=str(owner / "repo_bad"))]
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing"),
        patch(
            "sage_mcp.cli._bootstrap_mine_candidates",
            return_value=([], [("repo_bad", "simulated error")]),
        ),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(0, True)),
    ):
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            cmd_bootstrap(_make_args())
        assert exc_info.value.code == 1


def test_bootstrap_build_registry_exception_returns_zero_false(monkeypatch, capsys):
    """_bootstrap_build_registry must return (0, False) when build_registry raises."""
    with patch(
        "sage_mcp.extensions.skill_registry.build_registry",
        side_effect=RuntimeError("disk exploded"),
    ):
        count, ok = _bootstrap_build_registry()

    assert count == 0
    assert ok is False
    err = capsys.readouterr().err
    assert "failed" in err or "disk exploded" in err


def test_bootstrap_count_already_registered_counts_correctly(tmp_path, monkeypatch):
    """_bootstrap_count_already_registered counts only slugs where is_registered is True."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "reg_a").mkdir()
    (owner / "unreg_b").mkdir()

    monkeypatch.setattr(
        "sage_mcp.extensions.wing_registry.is_registered",
        lambda slug: slug == "reg_a",
    )

    count = _bootstrap_count_already_registered([(str(owner), "dev")])
    assert count == 1


def test_cmd_bootstrap_add_wing_oserror_continues_batch(tmp_path, monkeypatch, capsys):
    """OSError from add_wing for one slug must not abort; remaining slugs proceed."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_ok").mkdir()
    (owner / "repo_fail").mkdir()

    discovered = [
        DiscoveredRepo(slug="repo_fail", wing_type="dev", path=str(owner / "repo_fail")),
        DiscoveredRepo(slug="repo_ok", wing_type="dev", path=str(owner / "repo_ok")),
    ]
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    add_wing_calls: list = []

    def _add_wing(slug, wing_type, path=None):
        add_wing_calls.append(slug)
        if slug == "repo_fail":
            raise OSError("disk full")

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing", side_effect=_add_wing),
        patch("sage_mcp.cli._bootstrap_mine_candidates", return_value=([], [])),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(0, True)),
    ):
        args = _make_args()
        cmd_bootstrap(args)

    # Both slugs attempted; repo_ok registered, repo_fail failed gracefully.
    assert "repo_fail" in add_wing_calls
    assert "repo_ok" in add_wing_calls
    err = capsys.readouterr().err
    assert "repo_fail" in err or "disk full" in err


def test_cmd_bootstrap_config_absent_exits_nonzero(monkeypatch, capsys):
    """cmd_bootstrap must exit nonzero with actionable message when wing_config.json is absent."""
    import pytest

    with patch(
        "sage_mcp.extensions.wing_registry.load_config",
        side_effect=FileNotFoundError("wing_config.json not found"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_bootstrap(_make_args())
        assert exc_info.value.code != 0

    err = capsys.readouterr().err
    assert "sage init" in err or "wing_config" in err


def test_bootstrap_mine_candidates_tty_yes_input_mines(tmp_path):
    """TTY + user inputs 'y' must invoke mine."""
    from sage_mcp.cli import _bootstrap_mine_candidates

    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_tty").mkdir()

    discovered = [DiscoveredRepo(slug="repo_tty", wing_type="dev", path=str(owner / "repo_tty"))]

    with (
        patch("sage_mcp.miner.mine") as mine_mock,
        patch.object(sys.stdin, "isatty", return_value=True),
        patch.object(builtins, "input", return_value="y"),
    ):
        mine_ok, mine_errors = _bootstrap_mine_candidates(
            discovered, yes=False, nook_path="/fake/nook"
        )

    mine_mock.assert_called_once()
    assert mine_ok == ["repo_tty"]
    assert mine_errors == []


def test_bootstrap_mine_candidates_tty_no_input_skips_mine(tmp_path):
    """TTY + user inputs 'n' must NOT invoke mine."""
    from sage_mcp.cli import _bootstrap_mine_candidates

    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_skip").mkdir()

    discovered = [DiscoveredRepo(slug="repo_skip", wing_type="dev", path=str(owner / "repo_skip"))]

    with (
        patch("sage_mcp.miner.mine") as mine_mock,
        patch.object(sys.stdin, "isatty", return_value=True),
        patch.object(builtins, "input", return_value="n"),
    ):
        mine_ok, mine_errors = _bootstrap_mine_candidates(
            discovered, yes=False, nook_path="/fake/nook"
        )

    mine_mock.assert_not_called()
    assert mine_ok == []
    assert mine_errors == []


# ── additional branch coverage ────────────────────────────────────────────────


def test_discover_repos_skips_nonexistent_root(monkeypatch):
    """A root path that does not exist must be skipped silently."""
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    found = discover_repos([("/nonexistent/path/does/not/exist", "dev")])
    assert found == []


def test_bootstrap_count_already_registered_skips_nonexistent_root():
    """_bootstrap_count_already_registered must skip roots that don't exist."""
    count = _bootstrap_count_already_registered([("/nonexistent/path", "dev")])
    assert count == 0


def test_bootstrap_mine_candidates_empty_discovered_returns_empty():
    """_bootstrap_mine_candidates with empty discovered list returns ([], [])."""
    from sage_mcp.cli import _bootstrap_mine_candidates

    mine_ok, mine_errors = _bootstrap_mine_candidates([], yes=True, nook_path="/fake/nook")
    assert mine_ok == []
    assert mine_errors == []


def test_bootstrap_mine_candidates_tty_eoferror_skips_mine(tmp_path, capsys):
    """TTY + EOFError on input must skip mine and print a message."""
    from sage_mcp.cli import _bootstrap_mine_candidates

    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_eof").mkdir()

    discovered = [DiscoveredRepo(slug="repo_eof", wing_type="dev", path=str(owner / "repo_eof"))]

    with (
        patch("sage_mcp.miner.mine") as mine_mock,
        patch.object(sys.stdin, "isatty", return_value=True),
        patch.object(builtins, "input", side_effect=EOFError),
    ):
        mine_ok, mine_errors = _bootstrap_mine_candidates(
            discovered, yes=False, nook_path="/fake/nook"
        )

    mine_mock.assert_not_called()
    assert mine_ok == []
    assert mine_errors == []
    out = capsys.readouterr().out
    assert "EOF" in out or "skipping" in out


def test_bootstrap_build_registry_success_returns_count(tmp_path):
    """_bootstrap_build_registry returns (count, True) on success."""
    with patch(
        "sage_mcp.extensions.skill_registry.build_registry",
        return_value=[{"kind": "agent"}, {"kind": "skill"}],
    ):
        count, ok = _bootstrap_build_registry()

    assert count == 2
    assert ok is True


def test_cmd_bootstrap_add_wing_valueerror_slug_race(tmp_path, monkeypatch, capsys):
    """ValueError from add_wing (duplicate slug race) is treated as a skip, not a crash."""
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "race_slug").mkdir()

    discovered = [DiscoveredRepo(slug="race_slug", wing_type="dev", path=str(owner / "race_slug"))]
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch(
            "sage_mcp.extensions.wing_registry.add_wing",
            side_effect=ValueError("Wing 'race_slug' is already registered."),
        ),
        patch("sage_mcp.cli._bootstrap_mine_candidates", return_value=([], [])),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(0, True)),
    ):
        # Must not raise.
        cmd_bootstrap(_make_args())

    out = capsys.readouterr().out
    assert "skip" in out


def test_discover_repos_nonexistent_root_continue(monkeypatch):
    """discover_repos with a root pointing at a non-dir must continue silently."""
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)
    # Passing a plain file path (not a dir) must silently yield no candidates.
    found = discover_repos([("/dev/null", "dev")])
    assert found == []


def test_default_bootstrap_roots_with_dev_layout(tmp_path, monkeypatch):
    """_default_bootstrap_roots walks ~/dev/github/<owner> and ~/dev/projects/."""
    from sage_mcp.cli import _default_bootstrap_roots

    monkeypatch.delenv("SAGE_WORKSPACE_ROOT", raising=False)

    # Build a fake home with dev/github/owner1/ and dev/projects/
    fake_home = tmp_path / "fakehome"
    owner1 = fake_home / "dev" / "github" / "owner1"
    owner1.mkdir(parents=True)
    (fake_home / "dev" / "projects").mkdir(parents=True)

    with patch("sage_mcp.cli.Path") as mock_path_cls:
        # Let Path() calls through but intercept Path.home()
        real_path = Path
        mock_path_cls.home.return_value = fake_home
        mock_path_cls.side_effect = lambda *a, **k: real_path(*a, **k)

        roots = _default_bootstrap_roots()

    paths = [r[0] for r in roots]
    types = [r[1] for r in roots]

    assert any(str(owner1) in p for p in paths), f"owner1 not in roots: {paths}"
    assert "dev" in types
    assert "project" in types


def test_default_bootstrap_roots_honors_workspace_root_env(tmp_path, monkeypatch):
    """SAGE_WORKSPACE_ROOT overrides the default ~/dev discovery root."""
    from sage_mcp.cli import _default_bootstrap_roots

    workspace_root = tmp_path / "workspace"
    owner = workspace_root / "github" / "owner"
    projects = workspace_root / "projects"
    owner.mkdir(parents=True)
    projects.mkdir()
    monkeypatch.setenv("SAGE_WORKSPACE_ROOT", str(workspace_root))

    assert _default_bootstrap_roots() == [
        (str(owner), "dev"),
        (str(projects), "project"),
    ]


# ── FIX 1: only successfully-registered repos passed to mine ─────────────────


def test_cmd_bootstrap_failed_add_wing_repo_not_passed_to_mine(tmp_path, monkeypatch):
    """A repo whose add_wing raises OSError must NOT be passed to _bootstrap_mine_candidates.

    The mine helper should only receive repos that were successfully registered
    this run — not all discovered repos — so no orphan wing slug is mined into
    an unregistered wing.
    """
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "repo_ok").mkdir()
    (owner / "repo_fail").mkdir()

    discovered = [
        DiscoveredRepo(slug="repo_fail", wing_type="dev", path=str(owner / "repo_fail")),
        DiscoveredRepo(slug="repo_ok", wing_type="dev", path=str(owner / "repo_ok")),
    ]
    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: discovered)
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)
    monkeypatch.setattr("sage_mcp.extensions.wing_registry.is_registered", lambda slug: False)

    mine_received_slugs: list = []

    def _fake_mine_candidates(repos, yes, nook_path):
        mine_received_slugs.extend(r.slug for r in repos)
        return [], []

    def _add_wing(slug, wing_type, path=None):
        if slug == "repo_fail":
            raise OSError("disk full")

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.extensions.wing_registry.add_wing", side_effect=_add_wing),
        patch("sage_mcp.cli._bootstrap_mine_candidates", side_effect=_fake_mine_candidates),
        patch("sage_mcp.cli._bootstrap_build_registry", return_value=(0, True)),
    ):
        cmd_bootstrap(_make_args(yes=True))

    assert "repo_fail" not in mine_received_slugs, (
        f"repo_fail (failed add_wing) must not be passed to mine; got {mine_received_slugs}"
    )
    assert "repo_ok" in mine_received_slugs


# ── FIX 2: invalid JSON in wing_config.json → exit nonzero ───────────────────


def test_cmd_bootstrap_invalid_json_config_exits_nonzero(monkeypatch, capsys):
    """Invalid JSON in wing_config.json must cause cmd_bootstrap to exit nonzero
    with an actionable message, and must not register or mine anything.
    """
    import pytest

    add_wing_mock = MagicMock()
    mine_mock = MagicMock()

    with (
        patch(
            "sage_mcp.extensions.wing_registry.load_config",
            side_effect=ValueError("wing_config.json at /x is not valid JSON: ..."),
        ),
        patch("sage_mcp.extensions.wing_registry.add_wing", add_wing_mock),
        patch("sage_mcp.cli._bootstrap_mine_candidates", mine_mock),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_bootstrap(_make_args())
        assert exc_info.value.code != 0

    add_wing_mock.assert_not_called()
    mine_mock.assert_not_called()

    err = capsys.readouterr().err
    assert "sage init" in err or "wing_config" in err


# ── FIX 3: registry build failure → exit nonzero ─────────────────────────────


def test_cmd_bootstrap_registry_build_failure_exits_nonzero(tmp_path, monkeypatch):
    """When _bootstrap_build_registry raises (build_registry exception), cmd_bootstrap
    must print the failure AND exit nonzero so CI-invisible regressions are surfaced.
    """
    import pytest

    monkeypatch.setattr("sage_mcp.cli.discover_repos", lambda roots: [])
    monkeypatch.setattr("sage_mcp.cli._bootstrap_count_already_registered", lambda roots: 0)

    with (
        patch("sage_mcp.extensions.wing_registry.load_config", return_value={"wings": {}}),
        patch("sage_mcp.cli._bootstrap_mine_candidates", return_value=([], [])),
        patch(
            "sage_mcp.extensions.skill_registry.build_registry",
            side_effect=RuntimeError("registry exploded"),
        ),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cmd_bootstrap(_make_args(no_mine=True))
        assert exc_info.value.code != 0
