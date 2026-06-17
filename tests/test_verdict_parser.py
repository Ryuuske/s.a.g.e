"""Tests for sage.verdict_parser — structured verdict block.

Covers the parse cases the orchestrator hits in practice: well-formed
APPROVE / REQUEST_CHANGES / REJECT / HOLD / ABORT, declared-vs-actual
count mismatch, verdict-finding inconsistency, missing block, and the
split-detection helper.
"""

from __future__ import annotations

from sage_mcp.verdict_parser import (
    Verdict,
    detect_split,
    parse_verdict,
)


def _well_formed_approve():
    return (
        "@@VERDICT BEGIN\n"
        "verdict: APPROVE\n"
        "lane: aidev-code-reviewer\n"
        "report: .development/audits/2026-05-25-foo-aidev-code-reviewer-post.md\n"
        "findings: 0\n"
        "@@VERDICT END\n"
        "\n"
        "All seven angles clean. No blocking findings."
    )


def _well_formed_request_changes():
    return (
        "@@VERDICT BEGIN\n"
        "verdict: REQUEST_CHANGES\n"
        "lane: aidev-code-reviewer\n"
        "report: .development/audits/2026-05-25-bar-aidev-code-reviewer-post.md\n"
        "findings: 2\n"
        "@@FINDING 1\n"
        "severity: 90\n"
        "file: agents/aidev-planner.md\n"
        "line: 12\n"
        "category: manifest\n"
        "summary: forbidden_inputs field missing in manifest block\n"
        "@@FINDING 2\n"
        "severity: 60\n"
        "file: skills/foo/SKILL.md\n"
        "line: 0\n"
        "category: lane\n"
        "summary: refused-adjacent list omits ux-designer\n"
        "@@VERDICT END\n"
    )


class TestWellFormed:
    def test_approve_no_findings(self):
        v = parse_verdict(_well_formed_approve())
        assert v.valid
        assert v.verdict == "APPROVE"
        assert v.lane == "aidev-code-reviewer"
        assert v.report == ".development/audits/2026-05-25-foo-aidev-code-reviewer-post.md"
        assert v.findings == []
        assert v.has_blocking is False

    def test_request_changes_with_blocking(self):
        v = parse_verdict(_well_formed_request_changes())
        assert v.valid
        assert v.verdict == "REQUEST_CHANGES"
        assert len(v.findings) == 2
        assert v.findings[0].severity == 90
        assert v.findings[0].category == "manifest"
        assert v.findings[1].severity == 60
        assert v.has_blocking is True
        assert len(v.blocking_findings) == 1

    def test_reject_with_severity_100(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: REJECT\n"
            "lane: aidev-adversarial-auditor\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 100\n"
            "file: agents/foo.md\n"
            "line: 0\n"
            "category: governance\n"
            "summary: agent grants Bash without methodology justification\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert v.valid
        assert v.verdict == "REJECT"

    def test_hold_requires_single_finding(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: HOLD\n"
            "lane: aidev-state-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 50\n"
            "file: n/a\n"
            "line: 0\n"
            "category: other\n"
            "summary: prior audit report path missing from brief\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert v.valid
        assert v.verdict == "HOLD"

    def test_abort_requires_severity_100(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: ABORT\n"
            "lane: aidev-code-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 100\n"
            "file: n/a\n"
            "line: 0\n"
            "category: manifest\n"
            "summary: required input plan.md missing from brief\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert v.valid
        assert v.verdict == "ABORT"


class TestConsistency:
    def test_approve_with_blocking_finding_flagged(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 85\n"
            "file: foo.py\n"
            "line: 12\n"
            "category: other\n"
            "summary: this is a blocking finding\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("inconsistent" in e for e in v.parser_errors)

    def test_request_changes_without_blocking_flagged(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: REQUEST_CHANGES\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 30\n"
            "file: foo.py\n"
            "line: 1\n"
            "category: other\n"
            "summary: tiny nit\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("blocking" in e for e in v.parser_errors)

    def test_declared_count_mismatch(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 3\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("declared findings=3 but parsed 0" in e for e in v.parser_errors)

    def test_invalid_category_flagged(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 20\n"
            "file: foo.py\n"
            "line: 5\n"
            "category: bogus\n"
            "summary: a small nit\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("invalid category" in e for e in v.parser_errors)


class TestMalformed:
    def test_empty_input(self):
        v = parse_verdict("")
        assert not v.valid
        assert "empty input" in v.parser_errors

    def test_prose_only_no_verdict_line_fails(self):
        v = parse_verdict("just a summary, no verdict label")
        assert not v.valid
        assert any("no verdict block found" in e for e in v.parser_errors)

    def test_unclosed_block(self):
        v = parse_verdict("@@VERDICT BEGIN\nverdict: APPROVE\n")
        assert not v.valid
        assert any("not closed" in e for e in v.parser_errors)

    def test_extra_begin_marker_noted(self):
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 0\n"
            "@@VERDICT BEGIN\n"  # stray duplicate
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        # Block is still parsed; orchestrator gets a warning, not a hard fail.
        assert any("extra" in e.lower() and "begin" in e.lower() for e in v.parser_errors)


class TestProseOnlyRejected:
    """Prose-only verdicts (no @@VERDICT BEGIN block) are not valid."""

    def test_verdict_label_in_prose_does_not_validate(self):
        v = parse_verdict("VERDICT: APPROVE. All clean. Report path.")
        assert not v.valid
        assert any("no verdict block found" in e for e in v.parser_errors)


class TestSplitDetection:
    def _approve(self, lane: str) -> Verdict:
        return parse_verdict(
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            f"lane: {lane}\n"
            "report: none\n"
            "findings: 0\n"
            "@@VERDICT END\n"
        )

    def _request_changes(self, lane: str) -> Verdict:
        return parse_verdict(
            "@@VERDICT BEGIN\n"
            "verdict: REQUEST_CHANGES\n"
            f"lane: {lane}\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 90\n"
            "file: foo.py\n"
            "line: 1\n"
            "category: other\n"
            "summary: blocker\n"
            "@@VERDICT END\n"
        )

    def test_both_approve_no_split(self):
        a = self._approve("code-reviewer")
        b = self._approve("test-engineer")
        assert detect_split([a, b]) is None

    def test_one_approve_one_request_changes_is_split(self):
        a = self._approve("code-reviewer")
        b = self._request_changes("test-engineer")
        result = detect_split([a, b])
        assert result is not None
        assert result["kind"] == "split"

    def test_both_request_changes_no_split(self):
        a = self._request_changes("code-reviewer")
        b = self._request_changes("test-engineer")
        assert detect_split([a, b]) is None

    def test_invalid_verdict_returns_parser_error_kind(self):
        """detect_split with an invalid (non-parseable) verdict block returns kind=parser_error."""
        bad = parse_verdict("no verdict block here at all")
        good = self._approve("code-reviewer")
        result = detect_split([bad, good])
        assert result is not None
        assert result["kind"] == "parser_error"
        assert "no verdict block found" in result["errors"]

    def test_hold_verdict_returns_hold_or_abort_kind(self):
        """detect_split with a HOLD verdict from any lane returns kind=hold_or_abort."""
        hold = parse_verdict(
            "@@VERDICT BEGIN\n"
            "verdict: HOLD\n"
            "lane: aidev-state-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 50\n"
            "file: n/a\n"
            "line: 0\n"
            "category: other\n"
            "summary: required input missing from brief\n"
            "@@VERDICT END\n"
        )
        approve = self._approve("aidev-code-reviewer")
        result = detect_split([hold, approve])
        assert result is not None
        assert result["kind"] == "hold_or_abort"
        assert "HOLD" in result["labels"]


class TestParseEdgeCases:
    """Additional coverage for error paths not exercised by the main suites."""

    def test_non_integer_findings_field_flagged(self):
        """findings: value that is not an integer produces a parser error."""
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: notanumber\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("is not an integer" in e for e in v.parser_errors)

    def test_invalid_verdict_value_and_unknown_header_field(self):
        """An unrecognised verdict value produces a fatal error; an unknown header field is a warning."""
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: WIBBLE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 0\n"
            "extrafield: surprise\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("invalid verdict" in e for e in v.parser_errors)
        # Unknown fields go to parser_warnings, not parser_errors (verdict-schema.md:90)
        assert any("unknown header field" in w for w in v.parser_warnings)
        assert not any("unknown header field" in e for e in v.parser_errors)

    def test_finding_summary_too_long_flagged(self):
        """A summary exceeding SUMMARY_MAX characters produces a parser error."""
        from sage_mcp.verdict_parser import SUMMARY_MAX

        long_summary = "x" * (SUMMARY_MAX + 1)
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 20\n"
            "file: foo.py\n"
            "line: 1\n"
            "category: other\n"
            f"summary: {long_summary}\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert any("summary exceeds" in e for e in v.parser_errors)

    def test_reject_without_severity_100_flagged(self):
        """REJECT verdict requires exactly one finding with severity 100; without it is invalid."""
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: REJECT\n"
            "lane: aidev-adversarial-auditor\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 90\n"
            "file: agents/foo.md\n"
            "line: 0\n"
            "category: governance\n"
            "summary: serious but not severity-100\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("REJECT requires a finding with severity 100" in e for e in v.parser_errors)

    def test_hold_with_wrong_finding_count_flagged(self):
        """HOLD verdict requires exactly one finding; zero findings triggers a consistency error."""
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: HOLD\n"
            "lane: aidev-state-reviewer\n"
            "report: none\n"
            "findings: 0\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any("HOLD requires exactly one finding" in e for e in v.parser_errors)

    def test_unknown_header_field_does_not_invalidate(self):
        """Per verdict-schema.md:90, an unknown field is a warning, not a fatal error."""
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: APPROVE\n"
            "lane: code-reviewer\n"
            "report: none\n"
            "findings: 0\n"
            "extrafield: surprise\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert v.valid, f"parser_errors: {v.parser_errors}"
        assert v.verdict == "APPROVE"
        assert any("unknown header field" in w for w in v.parser_warnings)
        assert not any("unknown header field" in e for e in v.parser_errors)

    def test_detect_split_empty_list_returns_none(self):
        """detect_split([]) returns None — the empty-list contract."""
        assert detect_split([]) is None

    def test_abort_with_wrong_severity_flagged(self):
        """ABORT verdict with a non-100 severity finding is a consistency error."""
        text = (
            "@@VERDICT BEGIN\n"
            "verdict: ABORT\n"
            "lane: aidev-code-reviewer\n"
            "report: none\n"
            "findings: 1\n"
            "@@FINDING 1\n"
            "severity: 80\n"
            "file: n/a\n"
            "line: 0\n"
            "category: manifest\n"
            "summary: abort but wrong severity\n"
            "@@VERDICT END\n"
        )
        v = parse_verdict(text)
        assert not v.valid
        assert any(
            "ABORT requires exactly one finding with severity 100" in e for e in v.parser_errors
        )
