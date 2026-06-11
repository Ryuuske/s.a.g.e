"""Schema packaging guard (PR #34 review).

The estate model schema is loaded at runtime for validation. The wheel only
packages ``src/sage_mcp`` (`packages = ["src/sage_mcp"]`), so the contract copy under
``docs/`` does NOT ship — an installed user's estate build would fail validation
with FileNotFoundError. The fix vendors a copy into the package; these tests
guard that the copy ships, stays byte-identical to the docs contract, and is the
one the loader uses.

WHERE: tests/estate/test_schema_packaging.py
"""

import pathlib
import subprocess
import sys
import zipfile

import pytest

from sage_mcp.estate.adapter import estate_model as em

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOCS_SCHEMA = _ROOT / "docs" / "projects" / "sage-estate-dashboard" / "estate-model.schema.json"
_PKG_SCHEMA = _ROOT / "src" / "sage_mcp" / "estate" / "estate-model.schema.json"


def test_packaged_schema_exists_inside_the_package():
    """The schema is vendored under src/sage_mcp so the wheel ships it."""
    assert _PKG_SCHEMA.is_file(), (
        "packaged schema missing — installed estate builds would fail validation"
    )


def test_packaged_schema_is_byte_identical_to_docs_contract():
    """The packaged runtime copy must not drift from the docs/ contract copy."""
    assert _PKG_SCHEMA.read_bytes() == _DOCS_SCHEMA.read_bytes(), (
        "packaged schema has drifted from docs/ contract — re-sync the copies"
    )


def test_loader_prefers_packaged_copy():
    """_load_schema resolves the packaged copy first (the wheel-safe path)."""
    assert em._PACKAGED_SCHEMA_PATH == _PKG_SCHEMA
    schema = em._load_schema()
    assert isinstance(schema, dict)
    assert schema.get("$defs"), "loaded schema missing $defs — wrong file?"


def test_loader_falls_back_to_docs_when_packaged_absent(monkeypatch, tmp_path):
    """If the packaged copy is missing (unusual), the docs copy is the fallback."""
    monkeypatch.setattr(em, "_PACKAGED_SCHEMA_PATH", tmp_path / "nope.json")
    schema = em._load_schema()  # must not raise — falls back to _REPO_SCHEMA_PATH
    assert schema.get("$defs")


def test_built_wheel_actually_ships_the_schema(tmp_path):
    """Build the real wheel and prove the schema is inside it.

    The source-tree assertions above guard that the vendored copy *exists*; they
    do NOT prove the build backend's ``artifacts`` directive actually carries it
    into the distributed wheel. This is the only test that exercises the stated
    failure mode end-to-end (an installed user getting a schema-less wheel).
    Skips cleanly if the ``build`` frontend is unavailable in the environment.
    """
    pytest.importorskip("build", reason="PEP 517 build frontend not installed")
    pytest.importorskip("hatchling", reason="project build backend not in-env")

    out = tmp_path / "dist"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(out),
            str(_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"wheel build failed:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(out.glob("s_a_g_e_mcp-*.whl"))
    assert wheels, f"no wheel produced in {out}"

    with zipfile.ZipFile(wheels[0]) as whl:
        names = whl.namelist()
        assert "sage_mcp/estate/estate-model.schema.json" in names, (
            "built wheel does not ship the estate schema — the `artifacts` "
            "directive in pyproject.toml is not carrying it; installed estate "
            "builds would fail validation with FileNotFoundError"
        )
