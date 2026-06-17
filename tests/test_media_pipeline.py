"""
tests/test_media_pipeline.py — unit tests for the scripts/media/ pipeline.

Tests validate:
  - JSON schemas (segments.schema.json, manifest.schema.json)
  - File contracts (segments.jsonl format, manifest.json structure)
  - Golden fixture conformance
  - build_manifest helpers (chapters, coverage, keywords)
  - build_index helpers (seconds_to_hms, coverage validation)
  - probe.py clean-fail on corrupt/missing files
  - doctor.py clean-fail on missing venv

No media files are required; tests use small golden text fixtures.
"""

import json
import subprocess
import sys
from pathlib import Path

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
        result = self.bm._extract_summary("Hello world. This is extra.")
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
        # ffprobe will fail on junk bytes
        assert result.returncode == 1

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
