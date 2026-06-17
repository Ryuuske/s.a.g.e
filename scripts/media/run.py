"""
run.py — orchestrate the full media-ingestion pipeline.

Stages (in order):
  probe → audio → transcribe → frames → manifest → index

Each stage writes its artifacts and marks itself "done" in manifest.json stages.
A re-run reads the existing manifest.json and skips any stage already marked "done".

Usage:
    ~/.venvs/media/bin/python scripts/media/run.py \
        <source_file> <job_dir> --slug <slug> [--force-stage <stage>]

    --force-stage restarts a specific stage (clears its done marker).
    --no-doctor skips the preflight check (useful if doctor was already run).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


STAGES = ["probe", "audio", "transcribe", "frames", "manifest", "index"]
SCRIPTS_DIR = Path(__file__).parent
MEDIA_VENV = Path.home() / ".venvs" / "media"


def _python() -> str:
    """Return the media-venv Python path."""
    p = MEDIA_VENV / "bin" / "python"
    if p.is_file():
        return str(p)
    # Fallback: current interpreter (for testing)
    return sys.executable


def load_stages(job_dir: Path) -> dict[str, str]:
    """Load stage markers from manifest.json. Returns dict of stage->status."""
    manifest_path = job_dir / "manifest.json"
    if not manifest_path.exists():
        return {s: "pending" for s in STAGES}
    with manifest_path.open() as fh:
        manifest = json.load(fh)
    return manifest.get("stages", {s: "pending" for s in STAGES})


def save_stage_done(job_dir: Path, stage: str) -> None:
    """Mark a stage as done in manifest.json (creates minimal manifest if missing)."""
    manifest_path = job_dir / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open() as fh:
            manifest = json.load(fh)
    else:
        manifest = {"stages": {s: "pending" for s in STAGES}}
    manifest.setdefault("stages", {})[stage] = "done"
    with manifest_path.open("w") as fh:
        json.dump(manifest, fh, indent=2)


def run_cmd(cmd: list[str], label: str) -> None:
    """Run a subprocess command. Exits 1 on failure."""
    print(f"\n[run] {label}")
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\nrun: FATAL — {label} failed (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)


def stage_probe(source: Path, job_dir: Path) -> None:
    py = _python()
    probe_script = SCRIPTS_DIR / "probe.py"
    run_cmd([py, str(probe_script), str(source), "--json"], "probe")

    # Capture probe metadata
    result = subprocess.run(
        [py, str(probe_script), str(source), "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"run: ERROR — probe failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    try:
        meta = json.loads(result.stdout)
    except json.JSONDecodeError:
        meta = {}

    probe_meta_path = job_dir / "probe_meta.json"
    with probe_meta_path.open("w") as fh:
        json.dump(meta, fh, indent=2)
    print("  probe_meta.json written")


def stage_audio(source: Path, job_dir: Path) -> None:
    audio_dir = job_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = audio_dir / "audio.wav"

    extract_sh = SCRIPTS_DIR / "extract_audio.sh"
    run_cmd(["bash", str(extract_sh), str(source), str(wav_path)], "audio")


def stage_transcribe(job_dir: Path, model: str, language: str | None) -> None:
    wav_path = job_dir / "audio" / "audio.wav"
    if not wav_path.exists():
        print(f"run: ERROR — audio.wav not found at {wav_path}", file=sys.stderr)
        sys.exit(1)

    py = _python()
    transcribe_script = SCRIPTS_DIR / "transcribe.py"
    cmd = [py, str(transcribe_script), str(wav_path), str(job_dir), "--model", model]
    if language:
        cmd += ["--language", language]
    run_cmd(cmd, "transcribe")


def stage_frames(source: Path, job_dir: Path) -> None:
    py = _python()
    frames_script = SCRIPTS_DIR / "extract_frames.py"
    run_cmd([py, str(frames_script), str(source), str(job_dir)], "frames")


def stage_manifest(job_dir: Path, slug: str) -> None:
    py = _python()
    manifest_script = SCRIPTS_DIR / "build_manifest.py"
    run_cmd([py, str(manifest_script), str(job_dir), "--slug", slug], "manifest")


def stage_index(job_dir: Path) -> None:
    py = _python()
    index_script = SCRIPTS_DIR / "build_index.py"
    run_cmd([py, str(index_script), str(job_dir)], "index")


def main() -> None:
    parser = argparse.ArgumentParser(description="Orchestrate the full media-ingestion pipeline")
    parser.add_argument("source", type=Path, help="Source media file")
    parser.add_argument("job_dir", type=Path, help="Job output directory")
    parser.add_argument(
        "--slug", default=None, help="Job slug (default: derived from job_dir name)"
    )
    parser.add_argument("--model", default="base", help="Whisper model size (default: base)")
    parser.add_argument("--language", default=None, help="Force transcription language")
    parser.add_argument(
        "--force-stage",
        choices=STAGES,
        default=None,
        help="Force re-run of a specific stage (clears its done marker)",
    )
    parser.add_argument("--no-doctor", action="store_true", help="Skip preflight check")
    args = parser.parse_args()

    source = args.source.resolve()
    job_dir = args.job_dir.resolve()
    slug = args.slug or job_dir.name

    if not source.exists():
        print(f"run: ERROR — source not found: {source}", file=sys.stderr)
        sys.exit(1)

    job_dir.mkdir(parents=True, exist_ok=True)

    # Preflight
    if not args.no_doctor:
        py = _python()
        doctor_script = SCRIPTS_DIR / "doctor.py"
        result = subprocess.run([py, str(doctor_script)])
        if result.returncode != 0:
            print("run: FATAL — doctor.py preflight failed", file=sys.stderr)
            sys.exit(1)

    # Load stage markers
    stage_status = load_stages(job_dir)

    # Force-reset a stage AND every downstream stage: re-running an upstream
    # stage invalidates the outputs of all stages that depend on it (e.g.
    # forcing `transcribe` makes the existing frames/manifest/index stale, so
    # they must re-run, not SKIP on their still-`done` markers).
    if args.force_stage:
        if args.force_stage not in STAGES:
            print(
                f"run: FATAL — unknown stage {args.force_stage!r}; "
                f"valid: {', '.join(STAGES)}",
                file=sys.stderr,
            )
            sys.exit(1)
        cascade = STAGES[STAGES.index(args.force_stage):]
        for st in cascade:
            stage_status[st] = "pending"
        # Persist the cleared markers before we start
        manifest_path = job_dir / "manifest.json"
        if manifest_path.exists():
            with manifest_path.open() as fh:
                manifest = json.load(fh)
            stages_block = manifest.setdefault("stages", {})
            for st in cascade:
                stages_block[st] = "pending"
            with manifest_path.open("w") as fh:
                json.dump(manifest, fh, indent=2)

    print(f"\nrun.py — job: {slug}")
    print(f"  source : {source}")
    print(f"  job_dir: {job_dir}")
    print(f"  model  : {args.model}")
    print(f"  stages : {stage_status}\n")

    # Execute stages in order
    for stage in STAGES:
        status = stage_status.get(stage, "pending")
        if status == "done":
            print(f"[SKIP] {stage} — already done")
            continue

        print(f"[RUN ] {stage}")
        if stage == "probe":
            stage_probe(source, job_dir)
        elif stage == "audio":
            stage_audio(source, job_dir)
        elif stage == "transcribe":
            stage_transcribe(job_dir, args.model, args.language)
        elif stage == "frames":
            stage_frames(source, job_dir)
        elif stage == "manifest":
            stage_manifest(job_dir, slug)
        elif stage == "index":
            stage_index(job_dir)

        save_stage_done(job_dir, stage)
        print(f"[DONE] {stage}")

    print(f"\nrun.py: pipeline complete — package at {job_dir}")


if __name__ == "__main__":
    main()
