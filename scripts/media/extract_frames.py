"""
extract_frames.py — smart frame extraction with pHash deduplication.

Strategy (priority order per the PRD smart-frame strategy):
  1. Scene-change frames (PySceneDetect ContentDetector)
  2. Topic-midpoint frames (one per chapter boundary, at chapter midpoint)
  3. UI-cue frames (near transcript "click/open/select..." keywords)
  4. Fallback interval frames (every N seconds where no other frame exists)

Each frame records its `reason`. pHash deduplication removes near-identical frames.
Output: frames/*.jpg in the job directory.

Usage:
    ~/.venvs/media/bin/python scripts/media/extract_frames.py \
        <source_file> <job_dir> [--segments transcript/segments.jsonl]
"""

import argparse
import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "ffmpeg"
    if local.is_file():
        return str(local)
    raise RuntimeError("ffmpeg not found — run scripts/media/setup.sh")


def load_config() -> dict:
    config_path = Path(__file__).parent / "config" / "defaults.yaml"
    if config_path.exists():
        import yaml  # noqa: PLC0415

        with config_path.open() as fh:
            return yaml.safe_load(fh) or {}
    return {}


def load_segments(segments_path: Path) -> list[dict]:
    if not segments_path.exists():
        return []
    segments = []
    with segments_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                segments.append(json.loads(line))
    return segments


def _jpeg_qv(jpeg_quality: int) -> int:
    """Convert JPEG quality (0-100) to ffmpeg -q:v scale (2-31, lower=better)."""
    return max(2, int(31 - (min(100, max(0, jpeg_quality)) / 100.0) * 29))


def extract_frame_at(
    source: Path, t: float, output_path: Path, ffmpeg: str, jpeg_quality: int = 90
) -> bool:
    """Extract a single frame at time t (seconds) to output_path. Returns True on success."""
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(t),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        str(_jpeg_qv(jpeg_quality)),
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0 or not output_path.exists():
        return False
    # reject very small files (black / blank frames)
    if output_path.stat().st_size < 2048:
        output_path.unlink(missing_ok=True)
        return False
    return True


def get_phash(image_path: Path) -> str | None:
    try:
        import imagehash  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        img = Image.open(image_path)
        return str(imagehash.phash(img))
    except Exception:
        return None


def is_near_duplicate(phash_str: str, seen_hashes: list[str], max_distance: int) -> bool:
    import imagehash  # noqa: PLC0415

    h = imagehash.hex_to_hash(phash_str)
    for existing in seen_hashes:
        if h - imagehash.hex_to_hash(existing) <= max_distance:
            return True
    return False


def _scene_timeout_handler(signum: int, frame: object) -> None:
    raise TimeoutError("scene-detect wall-clock timeout exceeded")


def detect_scene_changes(source: Path, threshold: float, timeout_sec: int = 1800) -> list[float]:
    """Return list of scene-change timestamps in seconds using PySceneDetect."""
    try:
        from scenedetect import SceneManager, open_video  # noqa: PLC0415
        from scenedetect.detectors import ContentDetector  # noqa: PLC0415

        old_handler = signal.signal(signal.SIGALRM, _scene_timeout_handler)
        signal.alarm(timeout_sec)
        try:
            video = open_video(str(source))
            scene_manager = SceneManager()
            scene_manager.add_detector(ContentDetector(threshold=threshold))
            scene_manager.detect_scenes(video)
            scene_list = scene_manager.get_scene_list()
        except TimeoutError:
            print(
                f"  [ERROR] scene-detect timed out after {timeout_sec}s. "
                "Increase timeouts.scene_detect_sec in defaults.yaml or check the video file.",
                file=sys.stderr,
            )
            sys.exit(1)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        timestamps = []
        for scene_start, _scene_end in scene_list:
            t = scene_start.get_seconds()
            if t > 0.0:
                timestamps.append(round(t, 3))
        return timestamps
    except SystemExit:
        raise
    except Exception as exc:
        print(
            f"  [WARN] PySceneDetect failed: {exc} — falling back to interval only", file=sys.stderr
        )
        return []


def compute_chapter_midpoints(
    segments: list[dict], duration: float, target_count: int
) -> list[float]:
    """Split duration into target_count chapters and return midpoints."""
    if not segments or duration <= 0:
        return []
    chapter_dur = duration / target_count
    midpoints = []
    for i in range(target_count):
        mid = (i + 0.5) * chapter_dur
        if mid < duration:
            midpoints.append(round(mid, 3))
    return midpoints


def find_ui_cue_times(segments: list[dict], keywords: list[str]) -> list[float]:
    """Return timestamps near UI-action keywords in transcript."""
    times = []
    kw_lower = [k.lower() for k in keywords]
    for seg in segments:
        text = seg.get("text", "").lower()
        if any(kw in text for kw in kw_lower):
            t_start = seg.get("t_start", 0.0)
            t_end = seg.get("t_end", t_start)
            mid = (t_start + t_end) / 2.0
            times.append(round(mid, 3))
    return times


def compute_fallback_times(
    duration: float, interval: float, covered: set[float], tolerance: float = 2.0
) -> list[float]:
    """Return interval timestamps where no frame within tolerance seconds already exists."""
    times = []
    t = interval
    while t < duration:
        near = any(abs(t - c) <= tolerance for c in covered)
        if not near:
            times.append(round(t, 3))
        t += interval
    return times


def extract_frames(
    source: Path,
    job_dir: Path,
    segments: list[dict],
    cfg: dict,
) -> list[dict]:
    """
    Extract frames per smart strategy. Returns list of frame dicts for manifest.
    """

    frames_cfg = cfg.get("frames", {})
    scene_threshold = float(frames_cfg.get("scene_threshold", 30.0))
    fallback_interval = float(frames_cfg.get("fallback_interval_sec", 30))
    phash_distance = int(frames_cfg.get("phash_distance", 8))
    jpeg_quality = int(frames_cfg.get("jpeg_quality", 90))
    chapters_cfg = cfg.get("chapters", {})
    target_count = int(chapters_cfg.get("target_count", 8))
    ui_keywords = cfg.get("ui_cue_keywords", [])
    timeouts_cfg = cfg.get("timeouts", {})
    scene_detect_timeout = int(timeouts_cfg.get("scene_detect_sec", 1800))

    frames_dir = job_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        ffmpeg = _find_ffmpeg()
    except RuntimeError as exc:
        print(f"extract_frames: ERROR — {exc}", file=sys.stderr)
        sys.exit(1)

    # Probe duration
    duration = 0.0
    if segments:
        duration = max(s.get("t_end", 0) for s in segments)
    if duration <= 0:
        # fallback: probe via ffprobe
        ffprobe = shutil.which("ffprobe") or str(Path.home() / ".local" / "bin" / "ffprobe")
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        try:
            duration = float(result.stdout.strip())
        except ValueError:
            duration = 300.0  # fallback

    # --- Stage 1: scene-change frames ---
    print(f"extract_frames: detecting scene changes (threshold={scene_threshold}) ...")
    scene_times = detect_scene_changes(source, scene_threshold, timeout_sec=scene_detect_timeout)
    print(f"  scene changes: {len(scene_times)} found")

    # --- Stage 2: topic midpoints ---
    midpoint_times = compute_chapter_midpoints(segments, duration, target_count)
    print(f"  topic midpoints: {len(midpoint_times)}")

    # --- Stage 3: UI cue times ---
    ui_times = find_ui_cue_times(segments, ui_keywords)
    print(f"  ui-cue timestamps: {len(ui_times)}")

    # Build candidate list: (time, reason) in priority order
    candidates: list[tuple[float, str]] = []
    for t in scene_times:
        candidates.append((t, "scene-change"))
    for t in midpoint_times:
        candidates.append((t, "topic-midpoint"))
    for t in ui_times:
        candidates.append((t, "ui-cue"))

    covered_times = {t for t, _ in candidates}

    # --- Stage 4: fallback interval ---
    fallback_times = compute_fallback_times(duration, fallback_interval, covered_times)
    print(f"  fallback interval frames: {len(fallback_times)}")
    for t in fallback_times:
        candidates.append((t, "interval"))

    # Sort by time, deduplicate very close times (within 0.5s, keep first)
    candidates.sort(key=lambda x: x[0])
    deduped_candidates: list[tuple[float, str]] = []
    last_t = -999.0
    for t, reason in candidates:
        if t - last_t >= 0.5:
            deduped_candidates.append((t, reason))
            last_t = t

    print(f"extract_frames: extracting {len(deduped_candidates)} candidate frames ...")

    # Extract frames and apply pHash dedup
    frame_records: list[dict] = []
    seen_phashes: list[str] = []
    frame_index = 1

    for t, reason in deduped_candidates:
        # Format: f_000123_HH-MM-SS.jpg
        total_secs = int(t)
        hh = total_secs // 3600
        mm = (total_secs % 3600) // 60
        ss = total_secs % 60
        time_str = f"{hh:02d}-{mm:02d}-{ss:02d}"
        fname = f"f_{frame_index:06d}_{time_str}.jpg"
        fpath = frames_dir / fname

        ok = extract_frame_at(source, t, fpath, ffmpeg, jpeg_quality=jpeg_quality)
        if not ok:
            print(f"  [SKIP] frame at t={t:.1f}s — extraction failed or blank")
            continue

        # pHash dedup
        phash_str = get_phash(fpath)
        if phash_str and seen_phashes:
            try:
                if is_near_duplicate(phash_str, seen_phashes, phash_distance):
                    print(f"  [DEDUP] frame at t={t:.1f}s ({reason}) removed by pHash")
                    fpath.unlink(missing_ok=True)
                    continue
            except Exception as _dedup_exc:  # noqa: BLE001
                # pHash comparison failed (e.g. corrupt hash); keep the frame
                print(
                    f"  [WARN] pHash dedup error at t={t:.1f}s ({reason}): {_dedup_exc}",
                    file=sys.stderr,
                )

        if phash_str:
            seen_phashes.append(phash_str)

        frame_id = f"f{frame_index:06d}"
        frame_records.append(
            {
                "id": frame_id,
                "t": round(t, 3),
                "path": f"frames/{fname}",
                "reason": reason,
                "phash": phash_str or "",
            }
        )
        frame_index += 1

    print(f"extract_frames: {len(frame_records)} frames kept after dedup (in {frames_dir})")
    return frame_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract smart frames from a media source")
    parser.add_argument("source", type=Path, help="Source media file")
    parser.add_argument("job_dir", type=Path, help="Job directory root")
    parser.add_argument(
        "--segments",
        type=Path,
        default=None,
        help="Path to segments.jsonl (default: <job_dir>/transcript/segments.jsonl)",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"extract_frames: ERROR — source not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    seg_path = args.segments or (args.job_dir / "transcript" / "segments.jsonl")
    segments = load_segments(seg_path)
    cfg = load_config()

    frames = extract_frames(args.source, args.job_dir, segments, cfg)

    # Write frame list to job_dir/frames/frames_meta.json (consumed by build_manifest)
    meta_path = args.job_dir / "frames" / "frames_meta.json"
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump(frames, fh, indent=2)
    print(f"extract_frames: wrote {meta_path}")


if __name__ == "__main__":
    main()
