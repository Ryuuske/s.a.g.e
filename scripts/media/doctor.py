"""
doctor.py — preflight dependency check for the media pipeline.

Checks:
  1. ffmpeg + ffprobe on PATH (or ~/.local/bin).
  2. ~/.venvs/media Python interpreter exists.
  3. Required Python packages importable from that venv.

Exits 0 on success, 1 on any failure (with a clear "run setup.sh" message).

Usage:
    python3 scripts/media/doctor.py
    python3 scripts/media/doctor.py --venv /custom/path/to/venv
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_PACKAGES = [
    "faster_whisper",
    "scenedetect",
    "PIL",
    "imagehash",
    "yaml",
    "jsonschema",
]

DEFAULT_VENV = Path.home() / ".venvs" / "media"
SETUP_SCRIPT = Path(__file__).parent / "setup.sh"


def _check_binary(name: str) -> tuple[bool, str]:
    """Return (ok, path_or_error)."""
    # check PATH first, then ~/.local/bin
    found = shutil.which(name)
    if found:
        return True, found
    local_bin = Path.home() / ".local" / "bin" / name
    if local_bin.is_file():
        return True, str(local_bin)
    return False, f"{name} not found on PATH or in ~/.local/bin"


def _check_venv(venv_path: Path) -> tuple[bool, str]:
    python = venv_path / "bin" / "python"
    if not python.is_file():
        return False, f"venv Python not found at {python}"
    return True, str(python)


def _check_packages(venv_path: Path, packages: list[str]) -> list[str]:
    """Return list of missing package names."""
    python = venv_path / "bin" / "python"
    missing = []
    for pkg in packages:
        result = subprocess.run(
            [str(python), "-c", f"import {pkg}"],
            capture_output=True,
        )
        if result.returncode != 0:
            missing.append(pkg)
    return missing


def run_doctor(venv_path: Path) -> bool:
    """Run all checks. Returns True if everything is healthy."""
    ok = True
    findings: list[str] = []

    # 1. ffmpeg
    ff_ok, ff_path = _check_binary("ffmpeg")
    if ff_ok:
        print(f"  [OK] ffmpeg: {ff_path}")
    else:
        findings.append(f"  [FAIL] {ff_path}")
        ok = False

    # 2. ffprobe
    fp_ok, fp_path = _check_binary("ffprobe")
    if fp_ok:
        print(f"  [OK] ffprobe: {fp_path}")
    else:
        findings.append(f"  [FAIL] {fp_path}")
        ok = False

    # 3. venv
    venv_ok, venv_msg = _check_venv(venv_path)
    if venv_ok:
        print(f"  [OK] venv: {venv_msg}")
    else:
        findings.append(f"  [FAIL] {venv_msg}")
        ok = False

    # 4. packages (only if venv exists)
    if venv_ok:
        missing = _check_packages(venv_path, REQUIRED_PACKAGES)
        if missing:
            findings.append(f"  [FAIL] missing packages: {', '.join(missing)}")
            ok = False
        else:
            print(f"  [OK] packages: {', '.join(REQUIRED_PACKAGES)}")

    # Summary
    if ok:
        print("\ndoctor: all checks passed — pipeline ready.")
    else:
        print("\ndoctor: one or more checks FAILED:")
        for f in findings:
            print(f)
        print(f"\n  Run:  bash {SETUP_SCRIPT}\n  then re-run doctor.py")

    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Media pipeline preflight check")
    parser.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_VENV,
        help=f"Path to media venv (default: {DEFAULT_VENV})",
    )
    args = parser.parse_args()

    print(f"doctor.py — checking dependencies (venv: {args.venv})")
    healthy = run_doctor(args.venv)
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
