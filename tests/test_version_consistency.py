import json
import re
from pathlib import Path

from sage_mcp import __version__

_ROOT = Path(__file__).resolve().parents[1]


def _expected_version() -> str:
    pyproject = _ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match is not None, "Could not find project version in pyproject.toml"
    return match.group(1)


def test_package_version_matches_pyproject():
    assert __version__ == _expected_version()


def test_mcp_initialize_reports_package_version():
    from sage_mcp.mcp_server import handle_request  # deferred — C4 import isolation

    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["serverInfo"]["version"] == _expected_version()


def test_plugin_manifest_version_matches_pyproject():
    """`.claude-plugin/plugin.json` must agree with pyproject.

    A drifted plugin manifest version means `claude plugin update sage@sage`
    cannot resolve the new release, forcing an uninstall/reinstall. Keep it a
    red test, not a silent drift (see ADR-0089 and the version-consistency
    section of docs/guides/releasing.md).
    """
    manifest = json.loads((_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == _expected_version()


def test_marketplace_manifest_version_matches_pyproject():
    """`.claude-plugin/marketplace.json` plugin entry must agree with pyproject.

    The marketplace entry is what `claude plugin install/update sage@sage`
    reads; it must bump in lockstep with every release.
    """
    marketplace = json.loads(
        (_ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    )
    sage_plugins = [p for p in marketplace["plugins"] if p["name"] == "sage"]
    assert sage_plugins, "no 'sage' plugin entry in marketplace.json"
    for plugin in sage_plugins:
        assert plugin["version"] == _expected_version()
