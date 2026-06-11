"""Parse the structured auditor verdict block defined in
``docs/specs/verdict-schema.md``.

The orchestrator scans every auditor's inline reply for a
``@@VERDICT BEGIN`` / ``@@VERDICT END`` block and routes split-verdict
detection, blocking-finding tallies, and telemetry off the parsed
result. Free-form prose around the block is ignored — auditors keep
emitting their compressed summary after the block, the parser just
does not look at it.

This module is intentionally strict. Auditors are expected to emit the
exact field names. Tolerance shifts the bug burden onto the orchestrator,
which is what the schema was introduced to remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

VERDICT_BEGIN = "@@VERDICT BEGIN"
VERDICT_END = "@@VERDICT END"
FINDING_PREFIX = "@@FINDING"

VALID_VERDICTS = frozenset({"APPROVE", "REQUEST_CHANGES", "REJECT", "HOLD", "ABORT"})
VALID_CATEGORIES = frozenset(
    {
        "governance",
        "security",
        "test",
        "ux",
        "lane",
        "manifest",
        "drift",
        "docs",
        "other",
    }
)

BLOCKING_THRESHOLD = 80  # per CLAUDE.md
SUMMARY_MAX = 200


@dataclass
class Finding:
    severity: int
    file: str
    line: int
    category: str
    summary: str


@dataclass
class Verdict:
    verdict: Optional[str] = None
    lane: Optional[str] = None
    report: Optional[str] = None
    findings: list[Finding] = field(default_factory=list)
    declared_finding_count: Optional[int] = None
    valid: bool = False
    parser_errors: list[str] = field(default_factory=list)
    parser_warnings: list[str] = field(default_factory=list)

    @property
    def blocking_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity >= BLOCKING_THRESHOLD]

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocking_findings)


def _parse_int(raw: str, field_name: str, errors: list[str]) -> Optional[int]:
    try:
        return int(raw.strip())
    except ValueError:
        errors.append(f"field '{field_name}' is not an integer: {raw!r}")
        return None


def _parse_kv(line: str) -> Optional[tuple[str, str]]:
    if ":" not in line:
        return None
    key, _, val = line.partition(":")
    return key.strip(), val.strip()


def _locate_block(lines: list[str], out: Verdict) -> Optional[tuple[int, int]]:
    """Find ``@@VERDICT BEGIN``…``@@VERDICT END`` indices, append errors."""
    begin_idx = end_idx = None
    extras = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s == VERDICT_BEGIN:
            if begin_idx is None:
                begin_idx = i
            else:
                extras += 1
        elif s == VERDICT_END and begin_idx is not None and end_idx is None:
            end_idx = i
    if begin_idx is None:
        out.parser_errors.append("no verdict block found")
        return None
    if end_idx is None:
        out.parser_errors.append("verdict block opened but not closed")
        return None
    if extras:
        out.parser_errors.append(f"{extras} extra @@VERDICT BEGIN marker(s) ignored")
    return begin_idx, end_idx


def _split_header_and_findings(body: list[str]) -> tuple[list[str], list[list[str]]]:
    header: list[str] = []
    finding_blocks: list[list[str]] = []
    current: Optional[list[str]] = None
    for raw in body:
        s = raw.strip()
        if s.startswith(FINDING_PREFIX):
            if current is not None:
                finding_blocks.append(current)
            current = []
            continue
        if current is None:
            header.append(s)
        else:
            current.append(s)
    if current is not None:
        finding_blocks.append(current)
    return header, finding_blocks


def _parse_header(header: list[str], out: Verdict) -> None:
    for line in header:
        if not line:
            continue
        kv = _parse_kv(line)
        if kv is None:
            out.parser_errors.append(f"unparsed header line: {line!r}")
            continue
        key, val = kv
        if key == "verdict":
            if val not in VALID_VERDICTS:
                out.parser_errors.append(f"invalid verdict: {val!r}")
            out.verdict = val
        elif key == "lane":
            out.lane = val
        elif key == "report":
            out.report = val
        elif key == "findings":
            out.declared_finding_count = _parse_int(val, "findings", out.parser_errors)
        else:
            # Unknown fields are non-fatal: recorded in warnings per verdict-schema.md:90.
            # They do NOT set valid=False so forward-compatible auditor extensions
            # are accepted rather than rejected.
            out.parser_warnings.append(f"unknown header field: {key!r}")


def _parse_finding_block(idx: int, block: list[str], out: Verdict) -> Optional[Finding]:
    fields: dict[str, str] = {}
    for line in block:
        if not line:
            continue
        kv = _parse_kv(line)
        if kv is None:
            out.parser_errors.append(f"finding {idx}: unparsed line: {line!r}")
            continue
        fields[kv[0]] = kv[1]

    severity = _parse_int(fields.get("severity", ""), f"finding {idx} severity", out.parser_errors)
    line_no = _parse_int(fields.get("line", "0"), f"finding {idx} line", out.parser_errors)
    category = fields.get("category", "")
    if category not in VALID_CATEGORIES:
        valid_list = ", ".join(sorted(VALID_CATEGORIES))
        out.parser_errors.append(
            f"finding {idx}: invalid category {category!r}; valid: {valid_list}"
        )
    summary = fields.get("summary", "")
    if len(summary) > SUMMARY_MAX:
        out.parser_errors.append(
            f"finding {idx}: summary exceeds {SUMMARY_MAX} chars ({len(summary)})"
        )
    if "\n" in summary:
        out.parser_errors.append(f"finding {idx}: summary contains newline")
    if severity is None or line_no is None:
        return None
    if not (0 <= severity <= 100):
        out.parser_errors.append(f"finding {idx}: severity out of 0-100: {severity}")
        return None
    return Finding(
        severity=severity,
        file=fields.get("file", "n/a"),
        line=line_no,
        category=category,
        summary=summary,
    )


def _check_verdict_consistency(out: Verdict) -> None:
    if out.declared_finding_count is not None and out.declared_finding_count != len(out.findings):
        out.parser_errors.append(
            f"declared findings={out.declared_finding_count} but parsed {len(out.findings)}"
        )
    if out.verdict == "APPROVE" and out.has_blocking:
        out.parser_errors.append(
            f"verdict APPROVE inconsistent with {len(out.blocking_findings)} blocking finding(s)"
        )
    if out.verdict == "REQUEST_CHANGES" and not out.has_blocking:
        out.parser_errors.append("verdict REQUEST_CHANGES requires at least one blocking finding")
    if out.verdict == "REJECT" and not any(f.severity == 100 for f in out.findings):
        out.parser_errors.append("verdict REJECT requires a finding with severity 100")
    if out.verdict == "HOLD" and len(out.findings) != 1:
        out.parser_errors.append("verdict HOLD requires exactly one finding describing the gap")
    if out.verdict == "ABORT":
        if len(out.findings) != 1 or out.findings[0].severity != 100:
            out.parser_errors.append("verdict ABORT requires exactly one finding with severity 100")


def parse_verdict(text: str) -> Verdict:
    """Extract the first ``@@VERDICT BEGIN``…``@@VERDICT END`` block.

    If no block is present, returns an invalid ``Verdict`` with
    ``parser_errors=['no verdict block found']``. Multiple blocks
    indicate either an auditor bug or the orchestrator concatenating
    two replies — the parser surfaces the first block only and notes
    the extras.
    """
    out = Verdict()
    if not text:
        out.parser_errors.append("empty input")
        return out

    lines = text.splitlines()
    located = _locate_block(lines, out)
    if located is None:
        return out
    begin_idx, end_idx = located

    body = lines[begin_idx + 1 : end_idx]
    header, finding_blocks = _split_header_and_findings(body)
    _parse_header(header, out)

    for idx, block in enumerate(finding_blocks, start=1):
        finding = _parse_finding_block(idx, block, out)
        if finding is not None:
            out.findings.append(finding)

    _check_verdict_consistency(out)

    out.valid = not out.parser_errors and out.verdict is not None
    return out


def detect_split(verdicts: list[Verdict]) -> Optional[dict]:
    """Return a split-detection summary if the verdicts disagree.

    Two valid verdicts agree when both carry the *identical* verdict label
    (e.g. both APPROVE, both REQUEST_CHANGES). REJECT and REQUEST_CHANGES
    are distinct dispositions and are therefore treated as a split even
    though both are blocking. HOLD or ABORT from any lane is treated as
    no-agreement — the orchestrator must resolve it before the change ships.

    Returns ``None`` when the verdicts agree, otherwise a dict with the
    fields the orchestrator needs to write an ADR per CLAUDE.md.
    """
    if not verdicts:
        return None
    if any(not v.valid for v in verdicts):
        return {
            "kind": "parser_error",
            "lanes": [v.lane or "?" for v in verdicts],
            "errors": [e for v in verdicts for e in v.parser_errors],
        }
    labels = [v.verdict for v in verdicts]
    if any(label in {"HOLD", "ABORT"} for label in labels):
        return {"kind": "hold_or_abort", "labels": labels, "lanes": [v.lane for v in verdicts]}
    if all(label == "APPROVE" for label in labels):
        return None
    blocking = [v.has_blocking for v in verdicts]
    if all(blocking) or not any(blocking):
        # All agree changes are needed, or all agree changes are not needed.
        if all(label == labels[0] for label in labels):
            return None
    return {
        "kind": "split",
        "labels": labels,
        "lanes": [v.lane for v in verdicts],
        "blocking_counts": [len(v.blocking_findings) for v in verdicts],
    }
