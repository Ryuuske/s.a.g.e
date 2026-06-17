"""
probe.py — validate a media source file before ingestion.

Outputs a metadata dict to stdout (JSON) and exits 0 on success.
Exits 1 with a clear error message on corrupt, missing, or unsupported input.

Usage:
    python3 scripts/media/probe.py <source_file>
    python3 scripts/media/probe.py <source_file> --json   # always print JSON
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".mp3", ".wav", ".m4a", ".webm", ".avi"}
MIN_DURATION_SEC = 1.0


def _find_ffprobe() -> str:
    """Return ffprobe path or raise RuntimeError."""
    found = shutil.which("ffprobe")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "ffprobe"
    if local.is_file():
        return str(local)
    raise RuntimeError("ffprobe not found — run scripts/media/setup.sh")


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def probe(source: Path) -> dict:
    """
    Validate and read metadata from source.

    Returns a dict with keys: source_file, source_type, duration_sec,
    codec_video, codec_audio, width, height, sample_rate, media_sha256.

    Raises SystemExit(1) on any hard failure.
    """
    if not source.exists():
        print(f"probe: ERROR — file not found: {source}", file=sys.stderr)
        sys.exit(1)

    ext = source.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        print(
            f"probe: ERROR — unsupported extension '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        ffprobe = _find_ffprobe()
    except RuntimeError as exc:
        print(f"probe: ERROR — {exc}", file=sys.stderr)
        sys.exit(1)

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(source),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print("probe: ERROR — ffprobe timed out (corrupt or very large file?)", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(
            f"probe: ERROR — ffprobe failed (corrupt or unreadable file):\n  {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("probe: ERROR — ffprobe output is not valid JSON (corrupt file?)", file=sys.stderr)
        sys.exit(1)

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    # duration check
    try:
        duration = float(fmt.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0

    if duration < MIN_DURATION_SEC:
        print(
            f"probe: ERROR — duration {duration:.2f}s is too short (< {MIN_DURATION_SEC}s) "
            "or could not be read (corrupt file?)",
            file=sys.stderr,
        )
        sys.exit(1)

    # classify streams
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams and not audio_streams:
        print("probe: ERROR — no audio or video streams found (corrupt file?)", file=sys.stderr)
        sys.exit(1)

    source_type = "video" if video_streams else "audio"
    vs = video_streams[0] if video_streams else {}
    as_ = audio_streams[0] if audio_streams else {}

    meta = {
        "source_file": str(source),
        "source_type": source_type,
        "duration_sec": round(duration, 3),
        "codec_video": vs.get("codec_name"),
        "codec_audio": as_.get("codec_name"),
        "width": vs.get("width"),
        "height": vs.get("height"),
        "sample_rate": as_.get("sample_rate"),
        "format_name": fmt.get("format_name", ""),
        "media_sha256": _sha256(source),
    }

    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a media source file")
    parser.add_argument("source", type=Path, help="Path to source media file")
    parser.add_argument("--json", action="store_true", help="Always output JSON")
    args = parser.parse_args()

    meta = probe(args.source)

    if args.json:
        print(json.dumps(meta, indent=2))
    else:
        print("probe: OK")
        print(f"  file        : {meta['source_file']}")
        print(f"  type        : {meta['source_type']}")
        print(f"  duration    : {meta['duration_sec']:.1f}s")
        print(f"  video codec : {meta['codec_video']}")
        print(f"  audio codec : {meta['codec_audio']}")
        print(f"  resolution  : {meta['width']}x{meta['height']}")
        print(f"  sha256      : {meta['media_sha256'][:16]}…")


if __name__ == "__main__":
    main()
