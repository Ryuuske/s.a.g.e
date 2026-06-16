#!/usr/bin/env bash
# extract_audio.sh — extract 16kHz mono normalized WAV from a media source.
#
# Usage:
#   bash scripts/media/extract_audio.sh <source_file> <output_wav>
#
# Requires ffmpeg on PATH or in ~/.local/bin.
# Exits 0 on success, 1 on failure.

set -euo pipefail

SOURCE="${1:-}"
OUTPUT="${2:-}"

if [[ -z "$SOURCE" || -z "$OUTPUT" ]]; then
    echo "usage: extract_audio.sh <source_file> <output_wav>" >&2
    exit 1
fi

if [[ ! -f "$SOURCE" ]]; then
    echo "extract_audio: ERROR — source file not found: $SOURCE" >&2
    exit 1
fi

# Locate ffmpeg
FFMPEG="$(command -v ffmpeg 2>/dev/null || echo "")"
if [[ -z "$FFMPEG" ]]; then
    LOCAL_FFMPEG="$HOME/.local/bin/ffmpeg"
    if [[ -f "$LOCAL_FFMPEG" ]]; then
        FFMPEG="$LOCAL_FFMPEG"
    else
        echo "extract_audio: ERROR — ffmpeg not found on PATH or in ~/.local/bin" >&2
        echo "  Run: bash scripts/media/setup.sh" >&2
        exit 1
    fi
fi

mkdir -p "$(dirname "$OUTPUT")"

# Wall-clock cap: readable from defaults.yaml AUDIO_EXTRACT_TIMEOUT_SEC env var,
# with a hardcoded fallback of 1800s (30 min) suitable for a single-operator tool.
AUDIO_EXTRACT_TIMEOUT_SEC="${AUDIO_EXTRACT_TIMEOUT_SEC:-1800}"

echo "extract_audio: extracting 16kHz mono WAV (timeout=${AUDIO_EXTRACT_TIMEOUT_SEC}s)..."
echo "  source : $SOURCE"
echo "  output : $OUTPUT"

timeout "${AUDIO_EXTRACT_TIMEOUT_SEC}" "$FFMPEG" \
    -y \
    -i "$SOURCE" \
    -vn \
    -acodec pcm_s16le \
    -ar 16000 \
    -ac 1 \
    -af loudnorm \
    "$OUTPUT" \
    2>&1
FFMPEG_EXIT=$?
if [[ $FFMPEG_EXIT -eq 124 ]]; then
    echo "extract_audio: ERROR — ffmpeg timed out after ${AUDIO_EXTRACT_TIMEOUT_SEC}s. " \
         "Set AUDIO_EXTRACT_TIMEOUT_SEC or check the source file." >&2
    exit 1
elif [[ $FFMPEG_EXIT -ne 0 ]]; then
    exit $FFMPEG_EXIT
fi

if [[ ! -f "$OUTPUT" ]]; then
    echo "extract_audio: ERROR — output file not created" >&2
    exit 1
fi

SIZE=$(du -sh "$OUTPUT" | cut -f1)
echo "extract_audio: done — $OUTPUT ($SIZE)"
