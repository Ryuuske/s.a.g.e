#!/usr/bin/env bash
# setup.sh — create ~/.venvs/media and install media pipeline dependencies.
#
# Idempotent: detects an existing healthy venv and verifies/skips installation.
# Re-run safely at any time.
#
# Requirements:
#   - python3.12 on PATH (system or pyenv)
#   - pip (comes with python)
#
# After this script completes, run:
#   python3 scripts/media/doctor.py
# to verify all dependencies are healthy.

set -euo pipefail

VENV_DIR="${HOME}/.venvs/media"
PYTHON="${PYTHON:-python3}"

REQUIRED_PACKAGES=(
    "faster-whisper"
    "scenedetect[opencv]"
    "Pillow"
    "imagehash"
    "pyyaml"
    "jsonschema"
)

VERIFY_IMPORTS=(
    "faster_whisper"
    "scenedetect"
    "PIL"
    "imagehash"
    "yaml"
    "jsonschema"
)

echo "setup.sh — media pipeline environment"
echo "  venv  : $VENV_DIR"
echo "  python: $($PYTHON --version 2>&1)"

# Check if venv already exists and is healthy
if [[ -f "$VENV_DIR/bin/python" ]]; then
    echo ""
    echo "  [INFO] venv exists — verifying installed packages..."
    ALL_OK=true
    for pkg in "${VERIFY_IMPORTS[@]}"; do
        if ! "$VENV_DIR/bin/python" -c "import $pkg" 2>/dev/null; then
            echo "  [MISSING] $pkg"
            ALL_OK=false
        fi
    done

    if $ALL_OK; then
        echo "  [OK] all packages verified — venv is healthy, nothing to do."
        echo ""
        echo "setup.sh: done (existing venv healthy)"
        exit 0
    else
        echo "  [WARN] some packages missing — reinstalling into existing venv..."
    fi
else
    echo ""
    echo "  [INFO] creating new venv at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
    echo "  [OK] venv created"
fi

# Install / re-install packages
echo ""
echo "  Installing packages (this may take several minutes on first run)..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install "${REQUIRED_PACKAGES[@]}" --quiet

echo ""
echo "  Verifying imports..."
MISSING=()
for pkg in "${VERIFY_IMPORTS[@]}"; do
    if "$VENV_DIR/bin/python" -c "import $pkg" 2>/dev/null; then
        echo "  [OK] $pkg"
    else
        echo "  [FAIL] $pkg"
        MISSING+=("$pkg")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo ""
    echo "setup.sh: ERROR — failed to install: ${MISSING[*]}"
    echo "  Check network connectivity and re-run this script."
    exit 1
fi

echo ""
echo "setup.sh: done — media venv ready at $VENV_DIR"
echo "  Next: python3 scripts/media/doctor.py"
