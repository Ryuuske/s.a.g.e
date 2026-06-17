"""
transcribe.py — transcribe audio.wav using faster-whisper.

Produces:
  - transcript/raw.json      (full whisper output including word timestamps)
  - transcript/segments.jsonl  (one JSON object per line, streaming-readable)

Usage:
    ~/.venvs/media/bin/python scripts/media/transcribe.py \
        <audio_wav> <job_dir> [--model base] [--language en] [--compute-type int8]
"""

import argparse
import json
import sys
from pathlib import Path


def load_config(job_dir: Path) -> dict:
    """Load defaults.yaml from config alongside scripts, falling back to built-ins."""
    config_path = Path(__file__).parent / "config" / "defaults.yaml"
    if config_path.exists():
        import yaml  # noqa: PLC0415

        with config_path.open() as fh:
            return yaml.safe_load(fh) or {}
    return {}


def transcribe(
    audio_path: Path,
    job_dir: Path,
    model_size: str,
    language: str | None,
    compute_type: str,
    no_speech_threshold: float,
) -> None:
    from faster_whisper import WhisperModel  # noqa: PLC0415

    transcript_dir = job_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    raw_path = transcript_dir / "raw.json"
    segments_path = transcript_dir / "segments.jsonl"

    print(f"transcribe: loading model '{model_size}' (compute_type={compute_type}) ...")
    model = WhisperModel(model_size, device="cpu", compute_type=compute_type)

    print(f"transcribe: transcribing {audio_path} ...")
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=False,
    )

    print(
        f"transcribe: detected language '{info.language}' (probability {info.language_probability:.2f})"
    )

    # Collect all segments (streaming iterator — must consume)
    raw_segments = []
    jsonl_lines = []
    seg_index = 1
    total_no_speech = 0.0
    seg_count = 0

    for seg in segments_iter:
        seg_id = f"s{seg_index:04d}"
        no_speech = float(seg.no_speech_prob) if seg.no_speech_prob is not None else 0.0

        # raw output (includes word-level timestamps)
        words = []
        if seg.words:
            for w in seg.words:
                words.append(
                    {
                        "word": w.word,
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "probability": round(w.probability, 4),
                    }
                )

        raw_seg = {
            "id": seg_id,
            "seek": seg.seek,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "no_speech_prob": round(no_speech, 4),
            "avg_logprob": round(seg.avg_logprob, 4),
            "compression_ratio": round(seg.compression_ratio, 4),
            "words": words,
        }
        raw_segments.append(raw_seg)

        # segments.jsonl line (minimal, streaming-readable)
        jsonl_obj = {
            "id": seg_id,
            "t_start": round(seg.start, 3),
            "t_end": round(seg.end, 3),
            "text": seg.text.strip(),
            "no_speech_prob": round(no_speech, 4),
        }
        jsonl_lines.append(jsonl_obj)

        if no_speech > no_speech_threshold:
            print(
                f"  [WARN] segment {seg_id} high no_speech_prob={no_speech:.2f}: {seg.text[:60]!r}"
            )

        total_no_speech += no_speech
        seg_count += 1
        seg_index += 1

    if seg_count == 0:
        print(
            "transcribe: WARNING — no segments produced (silent/music-only file?)", file=sys.stderr
        )

    avg_no_speech = total_no_speech / seg_count if seg_count > 0 else 0.0
    if avg_no_speech > no_speech_threshold:
        print(
            f"transcribe: WARNING — avg no_speech_prob={avg_no_speech:.2f} > {no_speech_threshold} "
            "(possibly silent or music-heavy content)",
            file=sys.stderr,
        )

    # Write raw.json
    raw_output = {
        "info": {
            "language": info.language,
            "language_probability": round(info.language_probability, 4),
            "duration": round(info.duration, 3),
            "duration_after_vad": round(info.duration_after_vad, 3)
            if info.duration_after_vad
            else None,
        },
        "segments": raw_segments,
    }
    with raw_path.open("w", encoding="utf-8") as fh:
        json.dump(raw_output, fh, ensure_ascii=False, indent=2)
    print(f"transcribe: wrote {raw_path}")

    # Write segments.jsonl
    with segments_path.open("w", encoding="utf-8") as fh:
        for obj in jsonl_lines:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print(f"transcribe: wrote {segments_path} ({seg_count} segments)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe audio.wav with faster-whisper")
    parser.add_argument("audio_wav", type=Path, help="Path to audio.wav")
    parser.add_argument("job_dir", type=Path, help="Job directory (output root)")
    parser.add_argument(
        "--model", default=None, help="Whisper model size (default from config: base)"
    )
    parser.add_argument("--language", default=None, help="Force language (default: auto-detect)")
    parser.add_argument(
        "--compute-type", default=None, help="Compute type (default from config: int8)"
    )
    args = parser.parse_args()

    if not args.audio_wav.exists():
        print(f"transcribe: ERROR — audio file not found: {args.audio_wav}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(args.job_dir)
    tc = cfg.get("transcription", {})

    model_size = args.model or tc.get("model", "base")
    compute_type = args.compute_type or tc.get("compute_type", "int8")
    language = args.language or tc.get("language") or None
    no_speech_threshold = float(tc.get("no_speech_threshold", 0.6))

    transcribe(
        audio_path=args.audio_wav,
        job_dir=args.job_dir,
        model_size=model_size,
        language=language,
        compute_type=compute_type,
        no_speech_threshold=no_speech_threshold,
    )


if __name__ == "__main__":
    main()
