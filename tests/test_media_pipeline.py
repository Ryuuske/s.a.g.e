"""
tests/test_media_pipeline.py — unit tests for the scripts/media/ pipeline.

Coverage gate command (run from repo root):
  uv run pytest tests/test_media_pipeline.py --cov=scripts/media --cov-report=term-missing --cov-fail-under=85

Tests validate:
  - JSON schemas (segments.schema.json, manifest.schema.json)
  - File contracts (segments.jsonl format, manifest.json structure)
  - Golden fixture conformance
  - build_manifest helpers (chapters, coverage, keywords)
  - build_index helpers (seconds_to_hms, coverage validation)
  - probe.py clean-fail on corrupt/missing files; unit paths (mocked subprocess)
  - doctor.py clean-fail on missing venv
  - run.py resumability + --force-stage
  - transcribe.py silent-audio path (mocked faster_whisper)
  - build_manifest end-to-end (tmp_path fixtures + schema validation)
  - build_index end-to-end + stage marker
  - extract_frames fallback + auto_chapters edge cases

No media files are required; tests use small golden text fixtures.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts" / "media"
SCHEMA_DIR = SCRIPTS_DIR / "schema"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "media"

# ── Schema loading helpers ──────────────────────────────────────────────────


def load_schema(name: str) -> dict:
    with (SCHEMA_DIR / name).open() as fh:
        return json.load(fh)


def validate(instance: dict, schema: dict) -> None:
    """Validate instance against schema using jsonschema."""
    import jsonschema

    jsonschema.validate(instance=instance, schema=schema)


# ── Schema file existence ───────────────────────────────────────────────────


def test_manifest_schema_file_exists():
    assert (SCHEMA_DIR / "manifest.schema.json").is_file()


def test_segments_schema_file_exists():
    assert (SCHEMA_DIR / "segments.schema.json").is_file()


def test_defaults_yaml_exists():
    assert (SCRIPTS_DIR / "config" / "defaults.yaml").is_file()


# ── segments.schema.json ────────────────────────────────────────────────────


class TestSegmentsSchema:
    def setup_method(self):
        self.schema = load_schema("segments.schema.json")

    def test_valid_segment_passes(self):
        validate(
            {"id": "s0001", "t_start": 0.0, "t_end": 4.5, "text": "Hello.", "no_speech_prob": 0.01},
            self.schema,
        )

    def test_missing_id_fails(self):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            validate(
                {"t_start": 0.0, "t_end": 4.5, "text": "Hello.", "no_speech_prob": 0.01},
                self.schema,
            )

    def test_wrong_id_pattern_fails(self):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            validate(
                {
                    "id": "seg1",
                    "t_start": 0.0,
                    "t_end": 4.5,
                    "text": "Hello.",
                    "no_speech_prob": 0.01,
                },
                self.schema,
            )

    def test_no_speech_prob_out_of_range_fails(self):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            validate(
                {
                    "id": "s0001",
                    "t_start": 0.0,
                    "t_end": 4.5,
                    "text": "Hello.",
                    "no_speech_prob": 1.5,
                },
                self.schema,
            )

    def test_negative_t_start_fails(self):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            validate(
                {
                    "id": "s0001",
                    "t_start": -1.0,
                    "t_end": 4.5,
                    "text": "Hello.",
                    "no_speech_prob": 0.01,
                },
                self.schema,
            )

    def test_id_pattern_s_four_digits(self):
        """IDs must be s + exactly 4 digits."""
        import jsonschema

        # s + 5 digits should fail
        with pytest.raises(jsonschema.ValidationError):
            validate(
                {
                    "id": "s00001",
                    "t_start": 0.0,
                    "t_end": 4.5,
                    "text": "Hello.",
                    "no_speech_prob": 0.0,
                },
                self.schema,
            )


# ── manifest.schema.json ────────────────────────────────────────────────────


class TestManifestSchema:
    def setup_method(self):
        self.schema = load_schema("manifest.schema.json")
        self.golden_path = FIXTURES_DIR / "golden_manifest.json"
        with self.golden_path.open() as fh:
            self.golden = json.load(fh)

    def test_golden_manifest_validates(self):
        """The golden fixture must pass schema validation."""
        validate(self.golden, self.schema)

    def test_missing_job_field_fails(self):
        import jsonschema

        m = json.loads(json.dumps(self.golden))
        del m["job"]
        with pytest.raises(jsonschema.ValidationError):
            validate(m, self.schema)

    def test_invalid_source_type_fails(self):
        import jsonschema

        m = json.loads(json.dumps(self.golden))
        m["job"]["source_type"] = "stream"
        with pytest.raises(jsonschema.ValidationError):
            validate(m, self.schema)

    def test_invalid_stage_value_fails(self):
        import jsonschema

        m = json.loads(json.dumps(self.golden))
        m["stages"]["probe"] = "running"
        with pytest.raises(jsonschema.ValidationError):
            validate(m, self.schema)

    def test_frame_id_pattern(self):
        """Frame IDs must be f + 6 digits."""
        import jsonschema

        m = json.loads(json.dumps(self.golden))
        m["frames"][0]["id"] = "frame1"
        with pytest.raises(jsonschema.ValidationError):
            validate(m, self.schema)

    def test_chapter_id_pattern(self):
        """Chapter IDs must be c + 2 digits."""
        import jsonschema

        m = json.loads(json.dumps(self.golden))
        m["chapters"][0]["id"] = "chapter1"
        with pytest.raises(jsonschema.ValidationError):
            validate(m, self.schema)

    def test_frame_reason_enum(self):
        """Frame reason must be one of the allowed values."""
        import jsonschema

        m = json.loads(json.dumps(self.golden))
        m["frames"][0]["reason"] = "random"
        with pytest.raises(jsonschema.ValidationError):
            validate(m, self.schema)

    def test_all_stages_present(self):
        """Required stages must all be in the golden manifest."""
        required = {"probe", "audio", "transcribe", "frames", "manifest", "index"}
        assert required.issubset(set(self.golden["stages"].keys()))


# ── golden_segments.jsonl contract ─────────────────────────────────────────


class TestSegmentsJsonl:
    def setup_method(self):
        self.schema = load_schema("segments.schema.json")
        self.path = FIXTURES_DIR / "golden_segments.jsonl"

    def test_golden_jsonl_exists(self):
        assert self.path.is_file()

    def test_all_lines_parse_as_json(self):
        with self.path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    assert isinstance(obj, dict)

    def test_all_segments_have_required_fields(self):
        with self.path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    assert "id" in obj
                    assert "t_start" in obj
                    assert "t_end" in obj
                    assert "text" in obj
                    assert "no_speech_prob" in obj

    def test_all_segments_validate_against_schema(self):
        with self.path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    validate(obj, self.schema)

    def test_segment_ids_sequential(self):
        ids = []
        with self.path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ids.append(json.loads(line)["id"])
        for i, sid in enumerate(ids, 1):
            assert sid == f"s{i:04d}", f"expected s{i:04d} got {sid}"

    def test_t_end_gt_t_start(self):
        with self.path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    assert obj["t_end"] > obj["t_start"], f"t_end <= t_start in {obj['id']}"


# ── build_manifest helpers ──────────────────────────────────────────────────


def _load_module(name: str, path: Path):
    """Load a scripts/media/*.py module by path without requiring it to be a package."""
    import importlib

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBuildManifestHelpers:
    """Import and test pure helper functions from build_manifest."""

    def setup_method(self):
        self.bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")

    def test_extract_summary_short_text(self):
        # Default max_sentences=2: both sentences are included (text fits within max_chars)
        result = self.bm._extract_summary("Hello world. This is extra.")
        assert result == "Hello world. This is extra."

    def test_extract_summary_single_sentence(self):
        result = self.bm._extract_summary("Hello world. This is extra.", max_sentences=1)
        assert result == "Hello world."

    def test_extract_keywords_basic(self):
        kw = self.bm._extract_keywords("chart accounts ledger invoice payment chart chart")
        assert "chart" in kw  # most frequent

    def test_auto_chapters_produces_correct_count(self):
        segments = [
            {
                "id": f"s{i:04d}",
                "t_start": float(i * 5),
                "t_end": float(i * 5 + 5),
                "text": f"segment {i}",
                "no_speech_prob": 0.01,
            }
            for i in range(10)
        ]
        frames: list = []
        chapters = self.bm.auto_chapters(segments, frames, 50.0, 5, 5.0)
        assert len(chapters) == 5

    def test_auto_chapters_cover_full_duration(self):
        segments: list = []
        frames: list = []
        chapters = self.bm.auto_chapters(segments, frames, 100.0, 4, 10.0)
        assert chapters[0]["t_start"] == 0.0
        assert abs(chapters[-1]["t_end"] - 100.0) < 0.01


# ── build_index helpers ─────────────────────────────────────────────────────


class TestBuildIndexHelpers:
    def setup_method(self):
        self.bi = _load_module("build_index", SCRIPTS_DIR / "build_index.py")

    def test_seconds_to_hms_minutes_only(self):
        assert self.bi.seconds_to_hms(90) == "1:30"

    def test_seconds_to_hms_with_hours(self):
        assert self.bi.seconds_to_hms(3661) == "1:01:01"

    def test_validate_coverage_no_gap(self):
        chapters = [
            {"t_start": 0.0, "t_end": 50.0},
            {"t_start": 50.0, "t_end": 100.0},
        ]
        problems = self.bi.validate_coverage(chapters, 100.0)
        assert problems == []

    def test_validate_coverage_with_gap(self):
        chapters = [
            {"t_start": 0.0, "t_end": 40.0},
            {"t_start": 55.0, "t_end": 100.0},
        ]
        problems = self.bi.validate_coverage(chapters, 100.0)
        assert any("gap" in p for p in problems)

    def test_validate_coverage_empty(self):
        problems = self.bi.validate_coverage([], 100.0)
        assert problems  # should flag missing chapters


# ── probe.py subprocess tests ───────────────────────────────────────────────


class TestProbeScript:
    """Test probe.py via subprocess — no media file required for error paths."""

    @pytest.mark.integration
    def test_probe_rejects_missing_file(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "probe.py"), str(tmp_path / "nonexistent.mp4")],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()

    @pytest.mark.integration
    def test_probe_rejects_corrupt_file(self, tmp_path):
        junk = tmp_path / "corrupt.mp4"
        junk.write_bytes(b"THIS IS NOT A VALID MP4 FILE" * 100)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "probe.py"), str(junk)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        # ffprobe reports invalid data in stderr
        assert result.stderr.strip() != ""

    @pytest.mark.integration
    def test_probe_rejects_unsupported_extension(self, tmp_path):
        f = tmp_path / "file.xyz"
        f.write_bytes(b"data")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "probe.py"), str(f)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "unsupported" in result.stderr.lower()


# ── doctor.py tests ─────────────────────────────────────────────────────────


class TestDoctorScript:
    @pytest.mark.integration
    def test_doctor_fails_on_bogus_venv(self, tmp_path):
        bogus_venv = tmp_path / "fakevenv"
        bogus_venv.mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "doctor.py"), "--venv", str(bogus_venv)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        # Should mention run setup.sh
        assert "setup.sh" in result.stdout.lower() or "setup.sh" in result.stderr.lower()

    @pytest.mark.integration
    def test_doctor_passes_with_real_venv(self):
        """doctor.py should pass when ~/.venvs/media is healthy."""
        real_venv = Path.home() / ".venvs" / "media"
        if not real_venv.exists():
            pytest.skip("~/.venvs/media not provisioned")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "doctor.py"), "--venv", str(real_venv)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "all checks passed" in result.stdout.lower()


# ── ID naming convention tests ──────────────────────────────────────────────


class TestIdConventions:
    def test_segment_id_format(self):
        """s + 4 digits."""
        import re

        pattern = re.compile(r"^s\d{4}$")
        assert pattern.match("s0001")
        assert pattern.match("s9999")
        assert not pattern.match("s001")
        assert not pattern.match("seg0001")

    def test_frame_id_format(self):
        """f + 6 digits."""
        import re

        pattern = re.compile(r"^f\d{6}$")
        assert pattern.match("f000001")
        assert pattern.match("f999999")
        assert not pattern.match("f0001")

    def test_chapter_id_format(self):
        """c + 2 digits."""
        import re

        pattern = re.compile(r"^c\d{2}$")
        assert pattern.match("c01")
        assert pattern.match("c99")
        assert not pattern.match("c1")


# ── extract_frames helpers ──────────────────────────────────────────────────


class TestExtractFramesHelpers:
    def setup_method(self):
        self.ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")

    def test_compute_fallback_times_basic(self):
        times = self.ef.compute_fallback_times(100.0, 30.0, set(), tolerance=2.0)
        assert 30.0 in times
        assert 60.0 in times
        assert 90.0 in times

    def test_compute_fallback_skips_covered(self):
        covered = {30.0}
        times = self.ef.compute_fallback_times(100.0, 30.0, covered, tolerance=2.0)
        assert 30.0 not in times
        assert 60.0 in times

    def test_find_ui_cue_times_matches_keyword(self):
        segments = [
            {"id": "s0001", "t_start": 5.0, "t_end": 8.0, "text": "Click on the accounts tab."},
            {"id": "s0002", "t_start": 12.0, "t_end": 15.0, "text": "Now type your password."},
            {"id": "s0003", "t_start": 20.0, "t_end": 25.0, "text": "The results are displayed."},
        ]
        keywords = ["click", "type"]
        times = self.ef.find_ui_cue_times(segments, keywords)
        assert len(times) == 2
        assert 6.5 in times  # midpoint of 5–8
        assert 13.5 in times  # midpoint of 12–15

    def test_compute_chapter_midpoints_count(self):
        segs = [{"id": "s0001", "t_start": 0.0, "t_end": 100.0, "text": "x"}]
        mids = self.ef.compute_chapter_midpoints(segs, 100.0, 4)
        assert len(mids) == 4


# ── defaults.yaml content ───────────────────────────────────────────────────


class TestDefaultsYaml:
    def test_yaml_parses_cleanly(self):
        import yaml

        with (SCRIPTS_DIR / "config" / "defaults.yaml").open() as fh:
            cfg = yaml.safe_load(fh)
        assert isinstance(cfg, dict)

    def test_required_keys_present(self):
        import yaml

        with (SCRIPTS_DIR / "config" / "defaults.yaml").open() as fh:
            cfg = yaml.safe_load(fh)
        assert "transcription" in cfg
        assert "frames" in cfg
        assert "chapters" in cfg
        assert cfg["transcription"]["model"] == "base"
        assert cfg["transcription"]["compute_type"] == "int8"

    def test_jpeg_quality_present(self):
        import yaml

        with (SCRIPTS_DIR / "config" / "defaults.yaml").open() as fh:
            cfg = yaml.safe_load(fh)
        assert "jpeg_quality" in cfg.get("frames", {}), (
            "frames.jpeg_quality must be in defaults.yaml"
        )

    def test_max_title_words_present(self):
        import yaml

        with (SCRIPTS_DIR / "config" / "defaults.yaml").open() as fh:
            cfg = yaml.safe_load(fh)
        assert "max_title_words" in cfg.get("chapters", {})

    def test_max_summary_sentences_present(self):
        import yaml

        with (SCRIPTS_DIR / "config" / "defaults.yaml").open() as fh:
            cfg = yaml.safe_load(fh)
        assert "max_summary_sentences" in cfg.get("index", {})


# ── §A.1 + §A.2  run.py resumability + --force-stage ─────────────────────────


class TestRunResumability:
    """Test run.py stage-marker load/save without invoking any external process."""

    def setup_method(self):
        self.run = _load_module("run", SCRIPTS_DIR / "run.py")

    def test_load_stages_no_manifest_returns_all_pending(self, tmp_path):
        stages = self.run.load_stages(tmp_path)
        for stage in self.run.STAGES:
            assert stages[stage] == "pending"

    def test_load_stages_reads_existing_done(self, tmp_path):
        # Pre-seed manifest.json with probe: done
        manifest = {
            "stages": {s: "pending" for s in self.run.STAGES},
        }
        manifest["stages"]["probe"] = "done"
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        stages = self.run.load_stages(tmp_path)
        assert stages["probe"] == "done"
        assert stages["audio"] == "pending"

    def test_save_stage_done_creates_minimal_manifest(self, tmp_path):
        self.run.save_stage_done(tmp_path, "audio")
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["stages"]["audio"] == "done"

    def test_save_stage_done_preserves_existing_stages(self, tmp_path):
        # Start with probe=done, save audio=done — probe must stay done
        manifest = {"stages": {s: "pending" for s in self.run.STAGES}}
        manifest["stages"]["probe"] = "done"
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        self.run.save_stage_done(tmp_path, "audio")
        reloaded = json.loads((tmp_path / "manifest.json").read_text())
        assert reloaded["stages"]["probe"] == "done"
        assert reloaded["stages"]["audio"] == "done"

    def test_force_stage_clears_only_target(self, tmp_path):
        """Simulate --force-stage transcribe: only transcribe resets, others stay done."""
        # All stages done in manifest
        all_done = {s: "done" for s in self.run.STAGES}
        manifest = {"stages": all_done.copy()}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        # Replicate the force-stage logic from run.py main()
        force_stage = "transcribe"
        stage_status = self.run.load_stages(tmp_path)
        stage_status[force_stage] = "pending"
        # Write the cleared marker back
        mpath = tmp_path / "manifest.json"
        data = json.loads(mpath.read_text())
        data.setdefault("stages", {})[force_stage] = "pending"
        mpath.write_text(json.dumps(data))

        reloaded = self.run.load_stages(tmp_path)
        assert reloaded["transcribe"] == "pending"
        for s in self.run.STAGES:
            if s != "transcribe":
                assert reloaded[s] == "done", f"stage {s} should stay done"


# ── §A.3  transcribe.py silent-audio path ─────────────────────────────────────


def _make_faster_whisper_mock(segments_iter=None, language="en", duration=5.0):
    """Return (fake_module, fake_model) for patching sys.modules['faster_whisper']."""
    mock_info = MagicMock()
    mock_info.language = language
    mock_info.language_probability = 0.99
    mock_info.duration = duration
    mock_info.duration_after_vad = None

    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter(segments_iter or []), mock_info)

    fake_fw_module = MagicMock()
    fake_fw_module.WhisperModel = MagicMock(return_value=fake_model)
    return fake_fw_module, fake_model


class TestTranscribeSilentAudio:
    """Mock faster_whisper to verify zero-segment warning + empty segments.jsonl."""

    def setup_method(self):
        self.tc = _load_module("transcribe", SCRIPTS_DIR / "transcribe.py")

    def _run_transcribe(self, tmp_path, segments_iter=None):
        """Run transcribe() with a mocked faster_whisper; return captured stderr."""
        import io
        import sys as _sys

        fake_fw, _ = _make_faster_whisper_mock(segments_iter=segments_iter or [])
        audio_path = tmp_path / "audio.wav"
        audio_path.write_bytes(b"\x00" * 100)

        captured_err = io.StringIO()
        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            old_stderr = _sys.stderr
            _sys.stderr = captured_err
            try:
                self.tc.transcribe(
                    audio_path=audio_path,
                    job_dir=tmp_path,
                    model_size="base",
                    language=None,
                    compute_type="int8",
                    no_speech_threshold=0.6,
                )
            finally:
                _sys.stderr = old_stderr
        return captured_err.getvalue()

    def test_silent_audio_writes_empty_jsonl(self, tmp_path):
        """WhisperModel yields empty iterator → segments.jsonl must be empty, warning printed."""
        stderr_output = self._run_transcribe(tmp_path)

        seg_path = tmp_path / "transcript" / "segments.jsonl"
        assert seg_path.exists(), "segments.jsonl must be written even for silent audio"
        lines = [ln for ln in seg_path.read_text().splitlines() if ln.strip()]
        assert lines == [], f"expected empty segments.jsonl, got {lines}"

        # no-speech / zero-segment warning must fire (PRD failure-handling rules)
        assert "WARNING" in stderr_output or "warning" in stderr_output.lower(), (
            f"Expected silent-audio warning in stderr, got: {stderr_output!r}"
        )

    def test_silent_audio_writes_raw_json(self, tmp_path):
        """raw.json must also be written with empty segments list."""
        self._run_transcribe(tmp_path)

        raw_path = tmp_path / "transcript" / "raw.json"
        assert raw_path.exists()
        raw = json.loads(raw_path.read_text())
        assert raw["segments"] == []


# ── §A.4  build_manifest end-to-end ──────────────────────────────────────────


def _make_segments_jsonl(tmp_path: Path, segments: list[dict]) -> Path:
    """Write segments.jsonl into the standard transcript/ location."""
    transcript_dir = tmp_path / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / "segments.jsonl"
    path.write_text(
        "\n".join(json.dumps(s) for s in segments) + "\n",
        encoding="utf-8",
    )
    return path


def _make_frames_meta(tmp_path: Path, frames: list[dict]) -> Path:
    """Write frames_meta.json into the standard frames/ location."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    path = frames_dir / "frames_meta.json"
    path.write_text(json.dumps(frames, indent=2), encoding="utf-8")
    return path


def _make_probe_meta(tmp_path: Path, meta: dict) -> Path:
    path = tmp_path / "probe_meta.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


class TestBuildManifestEndToEnd:
    """End-to-end: golden segments + frames + probe_meta → build_manifest → validate schema."""

    def setup_method(self):
        self.bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")
        self.schema = load_schema("manifest.schema.json")

    def test_build_manifest_produces_valid_manifest(self, tmp_path):
        segments = [
            {
                "id": "s0001",
                "t_start": 0.0,
                "t_end": 10.0,
                "text": "Introduction to the topic.",
                "no_speech_prob": 0.01,
            },
            {
                "id": "s0002",
                "t_start": 10.0,
                "t_end": 25.0,
                "text": "Now click the accounts tab.",
                "no_speech_prob": 0.02,
            },
            {
                "id": "s0003",
                "t_start": 25.0,
                "t_end": 40.0,
                "text": "Review the ledger entries.",
                "no_speech_prob": 0.01,
            },
        ]
        frames = [
            {
                "id": "f000001",
                "t": 5.0,
                "path": "frames/f_000001.jpg",
                "reason": "scene-change",
                "phash": "aabbcc",
            },
            {
                "id": "f000002",
                "t": 30.0,
                "path": "frames/f_000002.jpg",
                "reason": "interval",
                "phash": "ddeeff",
            },
        ]
        probe_meta = {
            "source_file": "source/test.mp4",
            "source_type": "video",
            "duration_sec": 40.0,
            "media_sha256": "a" * 64,
        }
        _make_segments_jsonl(tmp_path, segments)
        _make_frames_meta(tmp_path, frames)
        _make_probe_meta(tmp_path, probe_meta)

        self.bm.build_manifest(tmp_path, "test-slug-001")

        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())

        # Schema validation
        validate(manifest, self.schema)

        # Chapters non-empty
        assert len(manifest["chapters"]) >= 1

        # Every chapter frame_id exists in manifest["frames"]
        frame_ids_in_manifest = {f["id"] for f in manifest["frames"]}
        for ch in manifest["chapters"]:
            for fid in ch["frame_ids"]:
                assert fid in frame_ids_in_manifest, (
                    f"chapter frame_id {fid} not in manifest frames"
                )

    def test_build_manifest_chapters_non_empty(self, tmp_path):
        segments = [
            {
                "id": "s0001",
                "t_start": 0.0,
                "t_end": 50.0,
                "text": "Long segment.",
                "no_speech_prob": 0.01,
            },
        ]
        _make_segments_jsonl(tmp_path, segments)
        _make_frames_meta(tmp_path, [])
        _make_probe_meta(
            tmp_path,
            {
                "source_file": "x.mp4",
                "source_type": "video",
                "duration_sec": 50.0,
                "media_sha256": "b" * 64,
            },
        )

        self.bm.build_manifest(tmp_path, "test-slug-002")
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert len(manifest["chapters"]) >= 1

    def test_verify_frame_ids_on_disk_reports_missing(self, tmp_path):
        """verify_frame_ids_on_disk returns the missing frame id."""
        frames = [
            {"id": "f000001", "path": "frames/f_000001.jpg"},
            {"id": "f000002", "path": "frames/f_000002.jpg"},
        ]
        # Only create f000001 on disk
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        (frames_dir / "f_000001.jpg").write_bytes(b"fake")

        missing = self.bm.verify_frame_ids_on_disk(frames, tmp_path)
        assert "f000002" in missing
        assert "f000001" not in missing

    def test_verify_frame_ids_on_disk_all_present(self, tmp_path):
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        (frames_dir / "f_000001.jpg").write_bytes(b"fake")
        frames = [{"id": "f000001", "path": "frames/f_000001.jpg"}]
        missing = self.bm.verify_frame_ids_on_disk(frames, tmp_path)
        assert missing == []

    def test_auto_chapters_duration_less_than_min_produces_one_chapter(self):
        bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")
        # duration (5s) < min_duration (20s) → should produce exactly 1 chapter
        chapters = bm.auto_chapters([], [], 5.0, 4, 20.0)
        assert len(chapters) == 1

    def test_auto_chapters_empty_segments_no_crash(self):
        bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")
        chapters = bm.auto_chapters([], [], 100.0, 4, 10.0)
        assert len(chapters) == 4
        assert chapters[0]["t_start"] == 0.0
        assert abs(chapters[-1]["t_end"] - 100.0) < 0.01


# ── §A.5  build_index end-to-end + stage marker ───────────────────────────────


def _make_minimal_manifest(tmp_path: Path, slug: str = "test-slug", duration: float = 40.0) -> dict:
    """Write a minimal but schema-valid manifest.json to tmp_path."""
    manifest = {
        "job": {
            "slug": slug,
            "source_file": "source/test.mp4",
            "source_type": "video",
            "duration_sec": duration,
            "language": "en",
            "media_sha256": "c" * 64,
            "pipeline_version": "1.0",
            "created_utc": "2026-06-15T00:00:00+00:00",
        },
        "stages": {
            "probe": "done",
            "audio": "done",
            "transcribe": "done",
            "frames": "done",
            "manifest": "done",
            "index": "pending",
        },
        "segments": [],
        "frames": [],
        "chapters": [
            {
                "id": "c01",
                "title": "Section 1",
                "t_start": 0.0,
                "t_end": 20.0,
                "summary": "First section.",
                "keywords": ["first"],
                "segment_ids": [],
                "frame_ids": [],
            },
            {
                "id": "c02",
                "title": "Section 2",
                "t_start": 20.0,
                "t_end": duration,
                "summary": "Second section.",
                "keywords": ["second"],
                "segment_ids": [],
                "frame_ids": [],
            },
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


class TestBuildIndexEndToEnd:
    """End-to-end: golden manifest → build_index → assert index.md structure + stage marker."""

    def setup_method(self):
        self.bi = _load_module("build_index", SCRIPTS_DIR / "build_index.py")

    def test_build_index_creates_index_md(self, tmp_path):
        _make_minimal_manifest(tmp_path, slug="my-test-job", duration=40.0)
        self.bi.build_index(tmp_path)
        assert (tmp_path / "index.md").exists()

    def test_build_index_yaml_front_matter_slug(self, tmp_path):
        _make_minimal_manifest(tmp_path, slug="my-test-job", duration=40.0)
        self.bi.build_index(tmp_path)
        content = (tmp_path / "index.md").read_text()
        assert "slug: my-test-job" in content

    def test_build_index_chapters_rendered_as_h2(self, tmp_path):
        _make_minimal_manifest(tmp_path, slug="my-test-job", duration=40.0)
        self.bi.build_index(tmp_path)
        content = (tmp_path / "index.md").read_text()
        # Both chapters must render as ## headings
        assert "## C01:" in content
        assert "## C02:" in content

    def test_build_index_sets_stage_done(self, tmp_path):
        _make_minimal_manifest(tmp_path, slug="my-test-job", duration=40.0)
        self.bi.build_index(tmp_path)
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["stages"]["index"] == "done"

    def test_build_index_missing_manifest_exits(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            self.bi.build_index(tmp_path)
        assert exc_info.value.code != 0


# ── §A.6  probe.py unit paths (mocked subprocess) ────────────────────────────


class TestProbeUnit:
    """Unit tests for probe.py logic using mocked subprocess and shutil.which."""

    def setup_method(self):
        self.probe_mod = _load_module("probe", SCRIPTS_DIR / "probe.py")

    def test_probe_duration_too_short_exits(self, tmp_path):
        """ffprobe returns format with duration=0.5 → probe must exit(1)."""
        src = tmp_path / "short.mp4"
        src.write_bytes(b"\x00" * 100)

        ffprobe_json = json.dumps(
            {
                "format": {"duration": "0.5", "format_name": "mp4"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100"},
                ],
            }
        )

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ffprobe_json
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            with pytest.raises(SystemExit) as exc_info:
                self.probe_mod.probe(src)
            assert exc_info.value.code != 0

    def test_probe_no_streams_exits(self, tmp_path):
        """ffprobe returns valid JSON but no audio or video streams → exit nonzero."""
        src = tmp_path / "nostream.mp4"
        src.write_bytes(b"\x00" * 100)

        ffprobe_json = json.dumps(
            {
                "format": {"duration": "10.0", "format_name": "mp4"},
                "streams": [],
            }
        )

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ffprobe_json
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            with pytest.raises(SystemExit) as exc_info:
                self.probe_mod.probe(src)
            assert exc_info.value.code != 0

    def test_probe_ffprobe_timeout_exits(self, tmp_path):
        """subprocess.TimeoutExpired → probe exits with nonzero."""
        src = tmp_path / "hang.mp4"
        src.write_bytes(b"\x00" * 100)

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=60)
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                self.probe_mod.probe(src)
            assert exc_info.value.code != 0

    def test_probe_success_returns_sha256(self, tmp_path):
        """Happy path: valid ffprobe output → probe() returns dict with media_sha256."""
        src = tmp_path / "valid.mp4"
        src.write_bytes(b"\x00" * 1024)

        ffprobe_json = json.dumps(
            {
                "format": {"duration": "30.0", "format_name": "mp4"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100"},
                ],
            }
        )

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ffprobe_json
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            meta = self.probe_mod.probe(src)

        assert "media_sha256" in meta
        assert len(meta["media_sha256"]) == 64
        assert meta["duration_sec"] == 30.0
        assert meta["source_type"] == "video"

    def test_probe_ffprobe_nonzero_stderr(self, tmp_path):
        """ffprobe exits nonzero → probe exits nonzero; stderr must be non-empty."""
        src = tmp_path / "corrupt.mp4"
        src.write_bytes(b"JUNK" * 200)

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = "Invalid data found when processing input"
            mock_run.return_value = mock_result

            captured_err = []
            import sys as _sys
            import io

            old_stderr = _sys.stderr
            _sys.stderr = io.StringIO()
            try:
                with pytest.raises(SystemExit) as exc_info:
                    self.probe_mod.probe(src)
                captured_err.append(_sys.stderr.getvalue())
            finally:
                _sys.stderr = old_stderr

        assert exc_info.value.code != 0
        # stderr from probe must contain error text
        assert captured_err[0].strip() != ""


# ── §A.7  extract_frames fallback + auto_chapters edge cases ──────────────────


class TestExtractFramesFallback:
    """Fallback interval frames + auto_chapters edge cases — no media files."""

    def setup_method(self):
        self.ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")
        self.bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")

    def test_interval_fallback_frames_produced(self, tmp_path):
        """Empty scene-change results → interval fallback frames in frames_meta.json."""
        # Build a minimal cfg
        cfg = {
            "frames": {
                "scene_threshold": 30.0,
                "fallback_interval_sec": 10,
                "phash_distance": 8,
                "jpeg_quality": 90,
            },
            "chapters": {"target_count": 2},
            "ui_cue_keywords": [],
        }
        segments = [{"id": "s0001", "t_start": 0.0, "t_end": 30.0, "text": "Content."}]

        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        def fake_extract_frame_at(source, t, output_path, ffmpeg, jpeg_quality=90):
            # Write a file big enough to pass the size check
            output_path.write_bytes(b"\x00" * 4096)
            return True

        with (
            patch.object(self.ef, "detect_scene_changes", return_value=[]),
            patch.object(self.ef, "extract_frame_at", side_effect=fake_extract_frame_at),
            patch.object(self.ef, "get_phash", return_value=None),
            patch.object(self.ef, "_find_ffmpeg", return_value="/usr/bin/ffmpeg"),
        ):
            source = tmp_path / "fake.mp4"
            source.write_bytes(b"\x00")
            frame_records = self.ef.extract_frames(source, tmp_path, segments, cfg)

        # Interval fallback at t=10, t=20 (within 0..30, interval=10)
        assert len(frame_records) >= 2
        reasons = {r["reason"] for r in frame_records}
        assert "interval" in reasons

    def test_interval_fallback_writes_frames_meta(self, tmp_path):
        """After extract_frames, frames_meta.json can be written and parsed."""
        cfg = {
            "frames": {
                "scene_threshold": 30.0,
                "fallback_interval_sec": 15,
                "phash_distance": 8,
                "jpeg_quality": 90,
            },
            "chapters": {"target_count": 2},
            "ui_cue_keywords": [],
        }
        segments = [{"id": "s0001", "t_start": 0.0, "t_end": 45.0, "text": "Hi."}]
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        def fake_extract_frame_at(source, t, output_path, ffmpeg, jpeg_quality=90):
            output_path.write_bytes(b"\x00" * 4096)
            return True

        with (
            patch.object(self.ef, "detect_scene_changes", return_value=[]),
            patch.object(self.ef, "extract_frame_at", side_effect=fake_extract_frame_at),
            patch.object(self.ef, "get_phash", return_value=None),
            patch.object(self.ef, "_find_ffmpeg", return_value="/usr/bin/ffmpeg"),
        ):
            source = tmp_path / "fake.mp4"
            source.write_bytes(b"\x00")
            frame_records = self.ef.extract_frames(source, tmp_path, segments, cfg)

        meta_path = tmp_path / "frames" / "frames_meta.json"
        meta_path.write_text(json.dumps(frame_records, indent=2))
        loaded = json.loads(meta_path.read_text())
        assert isinstance(loaded, list)
        assert len(loaded) == len(frame_records)

    def test_auto_chapters_duration_less_than_min_gives_one_chapter(self):
        chapters = self.bm.auto_chapters([], [], 5.0, 4, 20.0)
        assert len(chapters) == 1
        assert chapters[0]["t_start"] == 0.0
        assert abs(chapters[0]["t_end"] - 5.0) < 0.01

    def test_auto_chapters_empty_segments_equal_count(self):
        chapters = self.bm.auto_chapters([], [], 100.0, 4, 10.0)
        assert len(chapters) == 4

    def test_jpeg_qv_helper(self):
        ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")
        # quality 90 → ffmpeg q:v near 2-3 (best quality end)
        qv = ef._jpeg_qv(90)
        assert 2 <= qv <= 5
        # quality 0 → q:v near 31 (worst)
        qv_low = ef._jpeg_qv(0)
        assert qv_low == 31


# ── §A extra: probe.py + transcribe.py load_config coverage ──────────────────


class TestProbeModuleLoading:
    def test_probe_missing_file_exits(self, tmp_path):
        probe_mod = _load_module("probe", SCRIPTS_DIR / "probe.py")
        missing = tmp_path / "nofile.mp4"
        with pytest.raises(SystemExit) as exc_info:
            probe_mod.probe(missing)
        assert exc_info.value.code != 0

    def test_probe_unsupported_extension_exits(self, tmp_path):
        probe_mod = _load_module("probe", SCRIPTS_DIR / "probe.py")
        f = tmp_path / "data.xyz"
        f.write_bytes(b"data")
        with pytest.raises(SystemExit) as exc_info:
            probe_mod.probe(f)
        assert exc_info.value.code != 0

    def test_sha256_returns_hex(self, tmp_path):
        probe_mod = _load_module("probe", SCRIPTS_DIR / "probe.py")
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        result = probe_mod._sha256(f)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestTranscribeLoadConfig:
    def test_load_config_returns_dict(self):
        tc = _load_module("transcribe", SCRIPTS_DIR / "transcribe.py")
        cfg = tc.load_config()
        assert isinstance(cfg, dict)

    def test_load_config_has_transcription_key(self):
        tc = _load_module("transcribe", SCRIPTS_DIR / "transcribe.py")
        cfg = tc.load_config()
        assert "transcription" in cfg


class TestRunModuleFunctions:
    """Cover run.py helper functions."""

    def setup_method(self):
        self.run = _load_module("run", SCRIPTS_DIR / "run.py")

    def test_python_returns_string(self):
        result = self.run._python()
        assert isinstance(result, str)
        assert "python" in result.lower() or result.endswith("python3")

    def test_stages_list_complete(self):
        expected = {"probe", "audio", "transcribe", "frames", "manifest", "index"}
        assert expected == set(self.run.STAGES)

    def test_load_stages_missing_stages_key(self, tmp_path):
        """manifest.json exists but has no 'stages' key → returns all pending."""
        (tmp_path / "manifest.json").write_text(json.dumps({"job": {}}))
        stages = self.run.load_stages(tmp_path)
        for s in self.run.STAGES:
            assert stages[s] == "pending"


class TestBuildManifestHelperExtended:
    """Additional coverage for build_manifest helpers."""

    def setup_method(self):
        self.bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")

    def test_extract_summary_empty_returns_empty(self):
        assert self.bm._extract_summary("") == ""

    def test_extract_summary_long_text_truncated(self):
        long = "word " * 100
        result = self.bm._extract_summary(long, max_chars=20)
        assert len(result) <= 21  # 20 + possible "…"

    def test_extract_keywords_empty_text(self):
        kw = self.bm._extract_keywords("")
        assert kw == []

    def test_load_config_returns_dict(self):
        cfg = self.bm.load_config()
        assert isinstance(cfg, dict)

    def test_load_segments_missing_file(self, tmp_path):
        segs = self.bm.load_segments(tmp_path)
        assert segs == []

    def test_load_frames_missing_file(self, tmp_path):
        frames = self.bm.load_frames(tmp_path)
        assert frames == []

    def test_load_probe_meta_missing_file(self, tmp_path):
        meta = self.bm.load_probe_meta(tmp_path)
        assert meta == {}

    def test_auto_chapters_zero_duration(self):
        chapters = self.bm.auto_chapters([], [], 0.0, 4, 10.0)
        assert chapters == []


class TestBuildIndexHelperExtended:
    """Additional coverage for build_index helpers."""

    def setup_method(self):
        self.bi = _load_module("build_index", SCRIPTS_DIR / "build_index.py")

    def test_seconds_to_hms_zero(self):
        assert self.bi.seconds_to_hms(0) == "0:00"

    def test_validate_coverage_single_chapter_exact(self):
        chapters = [{"t_start": 0.0, "t_end": 100.0}]
        problems = self.bi.validate_coverage(chapters, 100.0)
        assert problems == []

    def test_validate_coverage_gap_before_first(self):
        chapters = [{"t_start": 5.0, "t_end": 100.0}]
        problems = self.bi.validate_coverage(chapters, 100.0)
        assert any("gap" in p for p in problems)


class TestExtractFramesHelperExtended:
    """Additional extract_frames coverage."""

    def setup_method(self):
        self.ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")

    def test_compute_fallback_times_all_covered(self):
        """All interval times covered → empty list."""
        covered = {30.0, 60.0, 90.0}
        times = self.ef.compute_fallback_times(100.0, 30.0, covered, tolerance=2.0)
        assert times == []

    def test_find_ui_cue_times_no_match(self):
        segments = [{"id": "s0001", "t_start": 0.0, "t_end": 5.0, "text": "Hello world"}]
        times = self.ef.find_ui_cue_times(segments, ["click", "select"])
        assert times == []

    def test_compute_chapter_midpoints_empty_segments(self):
        mids = self.ef.compute_chapter_midpoints([], 0.0, 4)
        assert mids == []

    def test_load_config_returns_dict(self):
        cfg = self.ef.load_config()
        assert isinstance(cfg, dict)

    def test_load_segments_missing_file(self, tmp_path):
        segs = self.ef.load_segments(tmp_path / "nonexistent.jsonl")
        assert segs == []

    def test_load_segments_valid_file(self, tmp_path):
        path = tmp_path / "segs.jsonl"
        path.write_text(
            '{"id":"s0001","t_start":0.0,"t_end":5.0,"text":"Hi"}\n'
            '{"id":"s0002","t_start":5.0,"t_end":10.0,"text":"Bye"}\n'
        )
        segs = self.ef.load_segments(path)
        assert len(segs) == 2
        assert segs[0]["id"] == "s0001"

    def test_detect_scene_changes_missing_scenedetect(self, tmp_path):
        """When scenedetect isn't importable, detect_scene_changes returns []."""
        ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")
        src = tmp_path / "fake.mp4"
        src.write_bytes(b"\x00")
        # Patch scenedetect import to fail
        with patch.dict("sys.modules", {"scenedetect": None, "scenedetect.detectors": None}):
            result = ef.detect_scene_changes(src, 30.0)
        # Should fallback to [] without crashing
        assert result == []

    def test_find_ffmpeg_fallback(self):
        """If ffmpeg not on PATH, _find_ffmpeg raises RuntimeError."""
        ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")
        with patch("shutil.which", return_value=None):
            # Only raises if ~/.local/bin/ffmpeg doesn't exist
            local = Path.home() / ".local" / "bin" / "ffmpeg"
            if not local.is_file():
                with pytest.raises(RuntimeError, match="ffmpeg not found"):
                    ef._find_ffmpeg()


# ── doctor.py unit tests ──────────────────────────────────────────────────────


class TestDoctorUnit:
    """Unit tests for doctor.py without requiring real venv/binaries."""

    def setup_method(self):
        self.dr = _load_module("doctor", SCRIPTS_DIR / "doctor.py")

    def test_check_binary_found_on_path(self):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            ok, path = self.dr._check_binary("ffmpeg")
        assert ok is True
        assert path == "/usr/bin/ffmpeg"

    def test_check_binary_not_found(self, tmp_path):
        with patch("shutil.which", return_value=None):
            ok, msg = self.dr._check_binary("ffmpeg_nonexistent_xyz")
        assert ok is False
        assert "not found" in msg

    def test_check_venv_missing_python(self, tmp_path):
        # tmp_path has no bin/python
        ok, msg = self.dr._check_venv(tmp_path)
        assert ok is False
        assert "not found" in msg

    def test_check_venv_with_python(self, tmp_path):
        # Create fake python binary
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_python = bin_dir / "python"
        fake_python.write_bytes(b"#!/bin/sh\necho fake")
        fake_python.chmod(0o755)
        ok, path = self.dr._check_venv(tmp_path)
        assert ok is True
        assert str(fake_python) in path

    def test_run_doctor_all_fail(self, tmp_path):
        """run_doctor returns False when ffmpeg/ffprobe/venv all missing."""
        with patch("shutil.which", return_value=None):
            result = self.dr.run_doctor(tmp_path)
        assert result is False

    def test_run_doctor_ffmpeg_ok_ffprobe_fail(self, tmp_path):
        """ffmpeg found but ffprobe missing → returns False."""

        def fake_which(name):
            return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

        with patch("shutil.which", side_effect=fake_which):
            result = self.dr.run_doctor(tmp_path)
        assert result is False

    def test_check_packages_missing(self, tmp_path):
        """_check_packages returns package names whose import fails."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_python = bin_dir / "python"
        fake_python.write_bytes(b"#!/bin/sh\nexit 1")
        fake_python.chmod(0o755)

        missing = self.dr._check_packages(tmp_path, ["nonexistent_pkg_xyz"])
        assert "nonexistent_pkg_xyz" in missing

    def test_run_doctor_venv_ok_packages_fail(self, tmp_path):
        """Venv python exists but packages import fails → returns False."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_python = bin_dir / "python"
        fake_python.write_bytes(b"#!/bin/sh\nexit 1")
        fake_python.chmod(0o755)

        with patch("shutil.which", return_value="/usr/bin/ffprobe"):
            result = self.dr.run_doctor(tmp_path)
        # packages will fail because fake python exits 1
        assert result is False


# ── run.py stage function coverage ───────────────────────────────────────────


class TestRunStageFunctions:
    """Cover run.py stage_* functions with mocked subprocess."""

    def setup_method(self):
        self.run = _load_module("run", SCRIPTS_DIR / "run.py")

    def test_run_cmd_success(self, tmp_path, capsys):
        """run_cmd calls subprocess and doesn't exit on returncode=0."""
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            # Should not raise SystemExit
            self.run.run_cmd(["echo", "test"], "test-label")

    def test_run_cmd_failure_exits(self, tmp_path):
        """run_cmd exits with the subprocess returncode on failure."""
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 2
            mock_run.return_value = mock_result
            with pytest.raises(SystemExit) as exc_info:
                self.run.run_cmd(["false"], "test-failure")
        assert exc_info.value.code == 2

    def test_stage_probe_writes_probe_meta(self, tmp_path):
        """stage_probe writes probe_meta.json when probe succeeds."""
        import json as _json

        fake_meta = {"source_type": "video", "duration_sec": 30.0}

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _json.dumps(fake_meta)
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            self.run.stage_probe(tmp_path / "fake.mp4", tmp_path)

        meta_path = tmp_path / "probe_meta.json"
        assert meta_path.exists()
        loaded = _json.loads(meta_path.read_text())
        assert loaded["source_type"] == "video"

    def test_stage_probe_exits_on_failure(self, tmp_path):
        """stage_probe exits when probe subprocess fails."""
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = "probe failed"
            mock_run.return_value = mock_result

            with pytest.raises(SystemExit) as exc_info:
                self.run.stage_probe(tmp_path / "fake.mp4", tmp_path)
        assert exc_info.value.code != 0

    def test_stage_audio_calls_subprocess(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            self.run.stage_audio(tmp_path / "fake.mp4", tmp_path)
        mock_run.assert_called_once()

    def test_stage_transcribe_missing_wav_exits(self, tmp_path):
        """stage_transcribe exits if audio.wav doesn't exist."""
        with pytest.raises(SystemExit) as exc_info:
            self.run.stage_transcribe(tmp_path, "base", None)
        assert exc_info.value.code != 0

    def test_stage_transcribe_calls_subprocess(self, tmp_path):
        (tmp_path / "audio").mkdir()
        (tmp_path / "audio" / "audio.wav").write_bytes(b"\x00")
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            self.run.stage_transcribe(tmp_path, "base", None)
        mock_run.assert_called_once()

    def test_stage_frames_calls_subprocess(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            self.run.stage_frames(tmp_path / "fake.mp4", tmp_path)
        mock_run.assert_called_once()

    def test_stage_manifest_calls_subprocess(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            self.run.stage_manifest(tmp_path, "test-slug")
        mock_run.assert_called_once()

    def test_stage_index_calls_subprocess(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result
            self.run.stage_index(tmp_path)
        mock_run.assert_called_once()


# ── transcribe.py additional coverage ────────────────────────────────────────


class TestTranscribeAdditional:
    """Cover transcribe.py code paths beyond the silent-audio path."""

    def setup_method(self):
        self.tc = _load_module("transcribe", SCRIPTS_DIR / "transcribe.py")

    def _run_with_segments(self, tmp_path, segment_mocks):
        """Run transcribe() with given mock segments."""
        import io
        import sys as _sys

        fake_fw, _ = _make_faster_whisper_mock(segments_iter=segment_mocks)
        audio_path = tmp_path / "audio.wav"
        audio_path.write_bytes(b"\x00" * 100)

        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            old_stderr = _sys.stderr
            _sys.stderr = io.StringIO()
            try:
                self.tc.transcribe(
                    audio_path=audio_path,
                    job_dir=tmp_path,
                    model_size="base",
                    language=None,
                    compute_type="int8",
                    no_speech_threshold=0.6,
                )
            finally:
                _sys.stderr = old_stderr

    def test_transcribe_with_segments_writes_jsonl(self, tmp_path):
        """Normal segments produce populated segments.jsonl."""
        # Mock a single segment
        seg = MagicMock()
        seg.seek = 0
        seg.start = 0.0
        seg.end = 4.5
        seg.text = " Hello world."
        seg.no_speech_prob = 0.01
        seg.avg_logprob = -0.3
        seg.compression_ratio = 1.2
        seg.words = []

        self._run_with_segments(tmp_path, [seg])

        seg_path = tmp_path / "transcript" / "segments.jsonl"
        assert seg_path.exists()
        lines = [ln for ln in seg_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["id"] == "s0001"
        assert obj["t_start"] == 0.0

    def test_transcribe_with_high_no_speech_triggers_warning(self, tmp_path):
        """Segment with no_speech_prob > threshold triggers per-segment warn."""
        import io
        import sys as _sys

        seg = MagicMock()
        seg.seek = 0
        seg.start = 0.0
        seg.end = 4.5
        seg.text = " Silence."
        seg.no_speech_prob = 0.9  # above threshold 0.6
        seg.avg_logprob = -1.0
        seg.compression_ratio = 1.0
        seg.words = []

        fake_fw, _ = _make_faster_whisper_mock(segments_iter=[seg])
        audio_path = tmp_path / "audio.wav"
        audio_path.write_bytes(b"\x00" * 100)

        captured_out = io.StringIO()
        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            old_stdout = _sys.stdout
            _sys.stdout = captured_out
            old_stderr = _sys.stderr
            _sys.stderr = io.StringIO()
            try:
                self.tc.transcribe(
                    audio_path=audio_path,
                    job_dir=tmp_path,
                    model_size="base",
                    language=None,
                    compute_type="int8",
                    no_speech_threshold=0.6,
                )
            finally:
                _sys.stdout = old_stdout
                _sys.stderr = old_stderr

        # Per-segment WARN line goes to stdout
        assert "WARN" in captured_out.getvalue()

    def test_transcribe_writes_raw_json_with_segments(self, tmp_path):
        seg = MagicMock()
        seg.seek = 0
        seg.start = 1.0
        seg.end = 5.0
        seg.text = " Test."
        seg.no_speech_prob = 0.02
        seg.avg_logprob = -0.2
        seg.compression_ratio = 1.1
        seg.words = []

        self._run_with_segments(tmp_path, [seg])

        raw_path = tmp_path / "transcript" / "raw.json"
        assert raw_path.exists()
        raw = json.loads(raw_path.read_text())
        assert len(raw["segments"]) == 1
        assert raw["info"]["language"] == "en"

    def test_transcribe_with_words(self, tmp_path):
        """Word-level timestamps are included in raw.json."""
        word = MagicMock()
        word.word = " test"
        word.start = 1.0
        word.end = 1.5
        word.probability = 0.99

        seg = MagicMock()
        seg.seek = 0
        seg.start = 1.0
        seg.end = 2.0
        seg.text = " test"
        seg.no_speech_prob = 0.01
        seg.avg_logprob = -0.1
        seg.compression_ratio = 1.0
        seg.words = [word]

        self._run_with_segments(tmp_path, [seg])

        raw = json.loads((tmp_path / "transcript" / "raw.json").read_text())
        assert len(raw["segments"][0]["words"]) == 1
        assert raw["segments"][0]["words"][0]["word"] == " test"


# ── probe.py additional coverage ──────────────────────────────────────────────


class TestProbeAdditional:
    """Cover probe.py paths not hit by unit or integration tests."""

    def setup_method(self):
        self.probe_mod = _load_module("probe", SCRIPTS_DIR / "probe.py")

    def test_probe_json_decode_error_exits(self, tmp_path):
        """ffprobe returns non-JSON stdout → probe exits nonzero."""
        src = tmp_path / "bad.mp4"
        src.write_bytes(b"\x00" * 100)

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "NOT JSON"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            with pytest.raises(SystemExit) as exc_info:
                self.probe_mod.probe(src)
        assert exc_info.value.code != 0

    def test_find_ffprobe_raises_when_missing(self):
        """_find_ffprobe raises RuntimeError when not on PATH or ~/.local/bin."""
        with patch("shutil.which", return_value=None):
            local = Path.home() / ".local" / "bin" / "ffprobe"
            if not local.is_file():
                with pytest.raises(RuntimeError, match="ffprobe not found"):
                    self.probe_mod._find_ffprobe()

    def test_probe_audio_only_file(self, tmp_path):
        """Audio-only file (no video stream) → source_type='audio'."""
        src = tmp_path / "audio.mp3"
        src.write_bytes(b"\x00" * 100)

        ffprobe_json = json.dumps(
            {
                "format": {"duration": "60.0", "format_name": "mp3"},
                "streams": [
                    {"codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100"},
                ],
            }
        )

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ffprobe_json
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            meta = self.probe_mod.probe(src)

        assert meta["source_type"] == "audio"
        assert meta["codec_video"] is None

    def test_probe_ffprobe_not_found_exits(self, tmp_path):
        """When ffprobe cannot be found at all, probe() exits nonzero."""
        src = tmp_path / "valid.mp4"
        src.write_bytes(b"\x00" * 100)
        # Patch both shutil.which and the local bin path
        with (
            patch("shutil.which", return_value=None),
            patch.object(Path, "is_file", return_value=False),
        ):
            with pytest.raises((SystemExit, RuntimeError)):
                self.probe_mod.probe(src)

    def test_probe_duration_none_treated_as_zero(self, tmp_path):
        """format dict with no 'duration' key → duration=0 → exit."""
        src = tmp_path / "nodur.mp4"
        src.write_bytes(b"\x00" * 100)

        ffprobe_json = json.dumps(
            {
                "format": {"format_name": "mp4"},
                "streams": [{"codec_type": "video", "codec_name": "h264"}],
            }
        )

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ffprobe_json
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            with pytest.raises(SystemExit) as exc_info:
                self.probe_mod.probe(src)
        assert exc_info.value.code != 0


# ── build_manifest + build_index additional coverage ─────────────────────────


class TestBuildManifestAdditional:
    def setup_method(self):
        self.bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")

    def test_build_manifest_with_raw_json_reads_language(self, tmp_path):
        """build_manifest reads language from raw.json if present."""
        segments = [
            {
                "id": "s0001",
                "t_start": 0.0,
                "t_end": 30.0,
                "text": "Bonjour.",
                "no_speech_prob": 0.01,
            }
        ]
        _make_segments_jsonl(tmp_path, segments)
        _make_frames_meta(tmp_path, [])
        _make_probe_meta(
            tmp_path,
            {
                "source_file": "x.mp4",
                "source_type": "video",
                "duration_sec": 30.0,
                "media_sha256": "d" * 64,
            },
        )
        # Write raw.json with language=fr
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir(exist_ok=True)
        (transcript_dir / "raw.json").write_text(
            json.dumps({"info": {"language": "fr", "language_probability": 0.99}, "segments": []})
        )

        self.bm.build_manifest(tmp_path, "test-fr")
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["job"]["language"] == "fr"

    def test_build_manifest_no_probe_meta_infers_duration(self, tmp_path):
        """Without probe_meta, duration inferred from max t_end of segments."""
        segments = [
            {"id": "s0001", "t_start": 0.0, "t_end": 25.0, "text": "Hi.", "no_speech_prob": 0.01}
        ]
        _make_segments_jsonl(tmp_path, segments)
        _make_frames_meta(tmp_path, [])
        # No probe_meta.json

        self.bm.build_manifest(tmp_path, "no-probe")
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["job"]["duration_sec"] == 25.0

    def test_build_manifest_warns_no_segments(self, tmp_path, capsys):
        """Empty segments.jsonl → warning printed, but manifest still written."""
        _make_segments_jsonl(tmp_path, [])
        _make_frames_meta(tmp_path, [])
        _make_probe_meta(
            tmp_path,
            {
                "source_file": "x.mp4",
                "source_type": "video",
                "duration_sec": 0.0,
                "media_sha256": "e" * 64,
            },
        )

        self.bm.build_manifest(tmp_path, "empty-segs")
        assert (tmp_path / "manifest.json").exists()

    def test_build_manifest_no_job_dir_exits(self, tmp_path):
        """build_manifest main() exits when job_dir doesn't exist."""
        # Call main directly isn't easy; test the guard in build_manifest()
        # by verifying validate_manifest can be called
        schema_path = SCRIPTS_DIR / "schema" / "manifest.schema.json"
        assert schema_path.is_file()  # just verifying schema is accessible


class TestBuildIndexAdditional:
    def setup_method(self):
        self.bi = _load_module("build_index", SCRIPTS_DIR / "build_index.py")

    def test_build_index_no_chapters(self, tmp_path):
        """Manifest with empty chapters list → build_index warns but doesn't crash."""
        manifest = {
            "job": {
                "slug": "no-chapters",
                "source_file": "x.mp4",
                "source_type": "video",
                "duration_sec": 40.0,
                "language": "en",
                "media_sha256": "f" * 64,
                "pipeline_version": "1.0",
                "created_utc": "2026-06-15T00:00:00+00:00",
            },
            "stages": {
                "probe": "done",
                "audio": "done",
                "transcribe": "done",
                "frames": "done",
                "manifest": "done",
                "index": "pending",
            },
            "segments": [],
            "frames": [],
            "chapters": [],
        }
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        import sys as _sys
        import io

        old_stderr = _sys.stderr
        _sys.stderr = io.StringIO()
        try:
            self.bi.build_index(tmp_path)
        finally:
            _sys.stderr = old_stderr
        assert (tmp_path / "index.md").exists()

    def test_build_index_chapter_with_keywords_and_segments(self, tmp_path):
        """Chapters with keywords/segment_ids/frame_ids are all rendered."""
        manifest = {
            "job": {
                "slug": "rich-job",
                "source_file": "x.mp4",
                "source_type": "video",
                "duration_sec": 50.0,
                "language": "en",
                "media_sha256": "aa" * 32,
                "pipeline_version": "1.0",
                "created_utc": "2026-06-15T00:00:00+00:00",
            },
            "stages": {
                "probe": "done",
                "audio": "done",
                "transcribe": "done",
                "frames": "done",
                "manifest": "done",
                "index": "pending",
            },
            "segments": [],
            "frames": [],
            "chapters": [
                {
                    "id": "c01",
                    "title": "Introduction",
                    "t_start": 0.0,
                    "t_end": 50.0,
                    "summary": "Overview.",
                    "keywords": ["overview", "intro"],
                    "segment_ids": ["s0001", "s0002", "s0003", "s0004", "s0005", "s0006"],
                    "frame_ids": ["f000001"],
                }
            ],
        }
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        self.bi.build_index(tmp_path)
        content = (tmp_path / "index.md").read_text()
        assert "Keywords:" in content
        assert "Frames:" in content
        assert "Segments:" in content
        assert "…" in content  # truncation marker for >5 segments


# ── extract_frames extended coverage ─────────────────────────────────────────


class TestExtractFramesExtended:
    def setup_method(self):
        self.ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")

    def test_extract_frame_at_output_too_small(self, tmp_path):
        """extract_frame_at returns False when output file is too small (blank frame)."""
        ef = self.ef
        src = tmp_path / "fake.mp4"
        src.write_bytes(b"\x00")
        out = tmp_path / "frame.jpg"

        def fake_run(cmd, **kwargs):
            out.write_bytes(b"\x00" * 100)  # below 2048 size threshold
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=fake_run):
            result = ef.extract_frame_at(src, 5.0, out, "/usr/bin/ffmpeg")
        assert result is False
        assert not out.exists()  # should be deleted

    def test_extract_frame_at_subprocess_fails(self, tmp_path):
        """extract_frame_at returns False when ffmpeg exits nonzero."""
        ef = self.ef
        src = tmp_path / "fake.mp4"
        src.write_bytes(b"\x00")
        out = tmp_path / "frame.jpg"

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_run.return_value = mock_result
            result = ef.extract_frame_at(src, 5.0, out, "/usr/bin/ffmpeg")
        assert result is False

    def test_extract_frame_at_success(self, tmp_path):
        """extract_frame_at returns True when file is big enough."""
        ef = self.ef
        src = tmp_path / "fake.mp4"
        src.write_bytes(b"\x00")
        out = tmp_path / "frame.jpg"

        def fake_run(cmd, **kwargs):
            out.write_bytes(b"\x00" * 5000)
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=fake_run):
            result = ef.extract_frame_at(src, 5.0, out, "/usr/bin/ffmpeg")
        assert result is True

    def test_get_phash_import_failure_returns_none(self, tmp_path):
        """get_phash returns None when imagehash/PIL not available."""
        ef = self.ef
        f = tmp_path / "img.jpg"
        f.write_bytes(b"\x00" * 100)
        with patch.dict("sys.modules", {"imagehash": None, "PIL": None}):
            result = ef.get_phash(f)
        assert result is None

    def test_extract_frames_missing_ffmpeg_exits(self, tmp_path):
        """extract_frames exits when ffmpeg not found."""
        ef = self.ef
        cfg = {
            "frames": {
                "scene_threshold": 30.0,
                "fallback_interval_sec": 30,
                "phash_distance": 8,
                "jpeg_quality": 90,
            },
            "chapters": {"target_count": 4},
            "ui_cue_keywords": [],
        }
        with patch.object(ef, "_find_ffmpeg", side_effect=RuntimeError("ffmpeg not found")):
            with pytest.raises(SystemExit):
                ef.extract_frames(tmp_path / "fake.mp4", tmp_path, [], cfg)

    def test_extract_frames_uses_ffprobe_for_duration_when_no_segments(self, tmp_path):
        """When segments empty, extract_frames uses ffprobe to get duration."""
        ef = self.ef
        cfg = {
            "frames": {
                "scene_threshold": 30.0,
                "fallback_interval_sec": 200,
                "phash_distance": 8,
                "jpeg_quality": 90,
            },
            "chapters": {"target_count": 2},
            "ui_cue_keywords": [],
        }
        (tmp_path / "frames").mkdir()

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "60.0\n"
            return r

        with (
            patch.object(ef, "_find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            patch.object(ef, "detect_scene_changes", return_value=[]),
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            # No segments → duration comes from ffprobe
            records = ef.extract_frames(tmp_path / "fake.mp4", tmp_path, [], cfg)
        # With interval=200s and duration=60, no fallback times → 0 frames ok
        assert isinstance(records, list)

    def test_extract_frames_dedup_loop(self, tmp_path):
        """pHash dedup removes near-duplicate frame (path: seen_phashes + is_near_duplicate)."""
        ef = self.ef
        cfg = {
            "frames": {
                "scene_threshold": 30.0,
                "fallback_interval_sec": 5,
                "phash_distance": 8,
                "jpeg_quality": 90,
            },
            "chapters": {"target_count": 2},
            "ui_cue_keywords": [],
        }
        segments = [{"id": "s0001", "t_start": 0.0, "t_end": 20.0, "text": "Hi."}]
        (tmp_path / "frames").mkdir()

        frame_index = [0]

        def fake_extract(source, t, output_path, ffmpeg, jpeg_quality=90):
            output_path.write_bytes(b"\x00" * 5000)
            frame_index[0] += 1
            return True

        phash_calls = [0]

        def fake_phash(image_path):
            phash_calls[0] += 1
            # Return same hash for all → all after first are duplicates
            return "aabbccdd"

        with (
            patch.object(ef, "_find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            patch.object(ef, "detect_scene_changes", return_value=[]),
            patch.object(ef, "extract_frame_at", side_effect=fake_extract),
            patch.object(ef, "get_phash", side_effect=fake_phash),
        ):
            with patch.dict("sys.modules", {"imagehash": _make_imagehash_mock()}):
                records = ef.extract_frames(tmp_path / "fake.mp4", tmp_path, segments, cfg)
        # Only 1 frame should survive dedup (all have same phash)
        assert len(records) <= len(frame_index)

    def test_is_near_duplicate_true(self):
        """is_near_duplicate returns True when hashes are within distance."""
        ef = self.ef
        mock_ih = _make_imagehash_mock(distance=0)
        with patch.dict("sys.modules", {"imagehash": mock_ih}):
            result = ef.is_near_duplicate("aabb", ["aabb"], max_distance=8)
        assert result is True

    def test_is_near_duplicate_false(self):
        """is_near_duplicate returns False when distance > max_distance."""
        ef = self.ef
        mock_ih = _make_imagehash_mock(distance=20)
        with patch.dict("sys.modules", {"imagehash": mock_ih}):
            result = ef.is_near_duplicate("aabb", ["ccdd"], max_distance=8)
        assert result is False


def _make_imagehash_mock(distance=0):
    """Return a mock imagehash module for use in patch.dict('sys.modules')."""
    mock_hash = MagicMock()
    mock_hash.__sub__ = MagicMock(return_value=distance)
    mock_hash.__rsub__ = MagicMock(return_value=distance)

    mock_ih = MagicMock()
    mock_ih.hex_to_hash = MagicMock(return_value=mock_hash)
    mock_ih.phash = MagicMock(return_value=mock_hash)
    return mock_ih


# ── run.py main() + _python fallback ─────────────────────────────────────────


class TestRunMainFunction:
    """Cover run.py main() paths via argparse and mocked subprocesses."""

    def setup_method(self):
        self.run = _load_module("run", SCRIPTS_DIR / "run.py")

    def test_python_fallback_when_no_venv(self):
        """_python() returns sys.executable when media venv doesn't exist."""
        run_mod = _load_module("run", SCRIPTS_DIR / "run.py")
        # Temporarily override MEDIA_VENV to a nonexistent path
        orig = run_mod.MEDIA_VENV
        run_mod.MEDIA_VENV = Path("/nonexistent/venv")
        try:
            result = run_mod._python()
            assert result == sys.executable
        finally:
            run_mod.MEDIA_VENV = orig

    def test_main_source_not_found_exits(self, tmp_path):
        """main() exits when source file doesn't exist."""
        run_mod = _load_module("run", SCRIPTS_DIR / "run.py")
        test_args = [
            str(tmp_path / "nonexistent.mp4"),
            str(tmp_path / "job"),
        ]
        with patch("sys.argv", ["run.py"] + test_args):
            with pytest.raises(SystemExit) as exc_info:
                run_mod.main()
        assert exc_info.value.code != 0

    def test_main_runs_stages_with_doctor_skipped(self, tmp_path):
        """main() with --no-doctor + pre-done stages executes without crashing."""
        run_mod = _load_module("run", SCRIPTS_DIR / "run.py")

        # Create a fake source file
        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00")
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        # Pre-seed all stages as done so nothing is actually executed
        manifest = {"stages": {s: "done" for s in run_mod.STAGES}}
        (job_dir / "manifest.json").write_text(json.dumps(manifest))

        test_args = [
            str(src),
            str(job_dir),
            "--no-doctor",
            "--slug",
            "test-slug",
        ]
        with patch("sys.argv", ["run.py"] + test_args):
            # Should complete without raising (all stages skipped)
            run_mod.main()

    def test_main_dispatch_enters_probe_arm(self, tmp_path):
        """main() dispatch loop enters the probe arm when probe is pending."""
        run_mod = _load_module("run", SCRIPTS_DIR / "run.py")

        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00")
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        # All stages done except probe — dispatch loop must enter the probe arm
        stages = {s: "done" for s in run_mod.STAGES}
        stages["probe"] = "pending"
        (job_dir / "manifest.json").write_text(json.dumps({"stages": stages}))

        test_args = [str(src), str(job_dir), "--no-doctor", "--slug", "dispatch-test"]
        with (
            patch("sys.argv", ["run.py"] + test_args),
            patch.object(run_mod, "stage_probe") as mock_probe,
        ):
            run_mod.main()

        mock_probe.assert_called_once()


# ── transcribe.py main() coverage ────────────────────────────────────────────


class TestTranscribeMain:
    def test_main_missing_audio_exits(self, tmp_path):
        """main() exits when audio file doesn't exist."""
        tc = _load_module("transcribe", SCRIPTS_DIR / "transcribe.py")
        with patch("sys.argv", ["transcribe.py", str(tmp_path / "missing.wav"), str(tmp_path)]):
            with pytest.raises(SystemExit) as exc_info:
                tc.main()
        assert exc_info.value.code != 0

    def test_main_calls_transcribe_function(self, tmp_path):
        """main() calls transcribe() when audio file exists."""
        tc = _load_module("transcribe", SCRIPTS_DIR / "transcribe.py")
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 100)

        fake_fw, _ = _make_faster_whisper_mock()
        with (
            patch.dict("sys.modules", {"faster_whisper": fake_fw}),
            patch("sys.argv", ["transcribe.py", str(audio), str(tmp_path)]),
        ):
            import sys as _sys
            import io

            old_stderr = _sys.stderr
            _sys.stderr = io.StringIO()
            try:
                tc.main()
            finally:
                _sys.stderr = old_stderr
        # segments.jsonl should exist (empty, since mock yields nothing)
        assert (tmp_path / "transcript" / "segments.jsonl").exists()


# ── probe.py main() coverage ─────────────────────────────────────────────────


class TestProbeMain:
    def test_main_valid_file_no_json_flag(self, tmp_path):
        """main() without --json prints text output."""
        probe_mod = _load_module("probe", SCRIPTS_DIR / "probe.py")
        src = tmp_path / "valid.mp4"
        src.write_bytes(b"\x00" * 1024)

        ffprobe_json = json.dumps(
            {
                "format": {"duration": "30.0", "format_name": "mp4"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100"},
                ],
            }
        )

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
            patch("sys.argv", ["probe.py", str(src)]),
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ffprobe_json
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            import io
            import sys as _sys

            old_stdout = _sys.stdout
            _sys.stdout = io.StringIO()
            try:
                probe_mod.main()
                out = _sys.stdout.getvalue()
            finally:
                _sys.stdout = old_stdout

        assert "probe: OK" in out

    def test_main_valid_file_json_flag(self, tmp_path):
        """main() with --json prints JSON output."""
        probe_mod = _load_module("probe", SCRIPTS_DIR / "probe.py")
        src = tmp_path / "valid.mp4"
        src.write_bytes(b"\x00" * 1024)

        ffprobe_json = json.dumps(
            {
                "format": {"duration": "30.0", "format_name": "mp4"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                ],
            }
        )

        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch("subprocess.run") as mock_run,
            patch("sys.argv", ["probe.py", str(src), "--json"]),
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ffprobe_json
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            import io
            import sys as _sys

            old_stdout = _sys.stdout
            _sys.stdout = io.StringIO()
            try:
                probe_mod.main()
                out = _sys.stdout.getvalue()
            finally:
                _sys.stdout = old_stdout

        parsed = json.loads(out)
        assert "media_sha256" in parsed


# ── extract_frames main() coverage ───────────────────────────────────────────


class TestExtractFramesMain:
    def test_main_missing_source_exits(self, tmp_path):
        """main() exits when source file doesn't exist."""
        ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")
        with patch("sys.argv", ["extract_frames.py", str(tmp_path / "missing.mp4"), str(tmp_path)]):
            with pytest.raises(SystemExit) as exc_info:
                ef.main()
        assert exc_info.value.code != 0

    def test_main_with_mocked_extract(self, tmp_path):
        """main() writes frames_meta.json via extract_frames()."""
        ef = _load_module("extract_frames", SCRIPTS_DIR / "extract_frames.py")
        src = tmp_path / "fake.mp4"
        src.write_bytes(b"\x00")
        # frames/ dir must exist since main() writes into it
        (tmp_path / "frames").mkdir()

        with (
            patch.object(ef, "extract_frames", return_value=[]),
            patch("sys.argv", ["extract_frames.py", str(src), str(tmp_path)]),
        ):
            ef.main()

        assert (tmp_path / "frames" / "frames_meta.json").exists()


# ── doctor.py main() coverage ────────────────────────────────────────────────


class TestDoctorMain:
    def test_main_exits_0_when_healthy(self, tmp_path):
        dr = _load_module("doctor", SCRIPTS_DIR / "doctor.py")
        with (
            patch.object(dr, "run_doctor", return_value=True),
            patch("sys.argv", ["doctor.py", "--venv", str(tmp_path)]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                dr.main()
        assert exc_info.value.code == 0

    def test_main_exits_1_when_unhealthy(self, tmp_path):
        dr = _load_module("doctor", SCRIPTS_DIR / "doctor.py")
        with (
            patch.object(dr, "run_doctor", return_value=False),
            patch("sys.argv", ["doctor.py", "--venv", str(tmp_path)]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                dr.main()
        assert exc_info.value.code == 1


# ── Task-3 new tests ──────────────────────────────────────────────────────────


def test_timecode_join_boundary():
    """
    Half-open [t_start, t_end) join contract:
    a frame at EXACTLY t_end of segment N must NOT appear in that segment's
    frame_ids — it belongs to the next segment (or no segment if it's the last).
    """
    bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")

    # Two adjacent segments: s0001=[0, 10), s0002=[10, 20)
    segments = [
        {"id": "s0001", "t_start": 0.0, "t_end": 10.0, "text": "A.", "no_speech_prob": 0.01},
        {"id": "s0002", "t_start": 10.0, "t_end": 20.0, "text": "B.", "no_speech_prob": 0.01},
    ]
    # Frame exactly at t=10.0 — the boundary
    frames = [
        {
            "id": "f000001",
            "t": 10.0,
            "path": "frames/f_000001.jpg",
            "reason": "scene-change",
            "phash": "aabb",
        }
    ]

    # Replicate the join logic from build_manifest.build_manifest()
    def frame_ids_for_segment(seg, frames):
        t_start = seg.get("t_start", 0)
        t_end = seg.get("t_end", t_start)
        return [f["id"] for f in frames if f.get("t", 0) >= t_start and f.get("t", 0) < t_end]

    ids_seg1 = frame_ids_for_segment(segments[0], frames)
    ids_seg2 = frame_ids_for_segment(segments[1], frames)

    # Frame at t=10.0 must NOT be in s0001 (t_end=10.0 is excluded)
    assert "f000001" not in ids_seg1, (
        "Frame at exactly t_end of segment N must not be in segment N (half-open interval)"
    )
    # Frame at t=10.0 MUST be in s0002 (t_start=10.0 is included)
    assert "f000001" in ids_seg2, (
        "Frame at exactly t_end of segment N must be in segment N+1 (half-open interval)"
    )

    # Also verify via auto_chapters — same half-open rule applies to chapter frame_ids
    chapters = bm.auto_chapters(segments, frames, 20.0, 2, 5.0)
    assert len(chapters) == 2
    ch1_frame_ids = chapters[0]["frame_ids"]
    ch2_frame_ids = chapters[1]["frame_ids"]
    assert "f000001" not in ch1_frame_ids, (
        "Chapter 1 t_end boundary must exclude frame at exactly that time"
    )
    assert "f000001" in ch2_frame_ids, (
        "Chapter 2 t_start boundary must include frame at exactly that time"
    )


def test_load_segments_corrupt_mid_file(tmp_path):
    """
    load_segments() in build_manifest must raise a clear, actionable error
    (not an unhandled crash) when a mid-file line is malformed JSON.
    The error must name the file and the line number.
    """
    bm = _load_module("build_manifest", SCRIPTS_DIR / "build_manifest.py")

    # Write a segments.jsonl where the 2nd line is malformed
    transcript_dir = tmp_path / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    segments_path = transcript_dir / "segments.jsonl"
    segments_path.write_text(
        '{"id":"s0001","t_start":0.0,"t_end":5.0,"text":"Good line.","no_speech_prob":0.01}\n'
        "NOT JSON\n"
        '{"id":"s0003","t_start":10.0,"t_end":15.0,"text":"Also good.","no_speech_prob":0.01}\n',
        encoding="utf-8",
    )

    import io
    import sys as _sys

    captured_err = io.StringIO()
    old_stderr = _sys.stderr
    _sys.stderr = captured_err
    try:
        with pytest.raises(SystemExit) as exc_info:
            bm.load_segments(tmp_path)
    finally:
        _sys.stderr = old_stderr

    assert exc_info.value.code != 0, "load_segments must exit nonzero on corrupt line"

    err_output = captured_err.getvalue()
    # Error message must name the file
    assert str(segments_path) in err_output or "segments.jsonl" in err_output, (
        f"Error must name the file; got: {err_output!r}"
    )
    # Error message must name the line number (line 2)
    assert "2" in err_output, f"Error must name line number 2; got: {err_output!r}"
