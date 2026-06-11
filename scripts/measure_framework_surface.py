#!/usr/bin/env python3
"""Measure the framework's agent/skill surface — sizes, tiers, and overlap.

Deterministic and reusable. Re-derives every metric from source on each run so
the numbers can never drift from a stale audit. Seeds the token-consumption
instrumentation increment.

Metrics:
  - agent `description:` sizes (chars + approx tokens), distribution
  - skill `description:` sizes, distribution
  - agent body sizes + skill body sizes
  - model-tier distribution (opus/sonnet/haiku)
  - agent<->skill methodology overlap (shared substantive lines + Jaccard),
    top pairs surfaced regardless of naming

Usage:
  scripts/measure_framework_surface.py                 # human summary
  scripts/measure_framework_surface.py --format json   # machine-readable JSON
  scripts/measure_framework_surface.py --repo <path>   # override repo root
  scripts/measure_framework_surface.py --check         # validate contracts A+C (exit 1 on violation)
  scripts/measure_framework_surface.py --check --format json  # check + machine-readable
  scripts/measure_framework_surface.py --check --advisory-contracts A  # Contract A advisory only

Token approximation is chars/4 (English prose proxy); the JSON carries raw
chars so a real tokenizer can replace the proxy later without re-running.

Contract checks (--check mode):
  Contract A — description budget (agent ≤150 tok, skill ≤120 tok) + line-number-ref ban
               (ADR-0035; enumeration ban superseded by ADR-0085).
               Reports violations as A:<file>:<reason>.  Exit code 1 on any A violation.
  Contract B — agent↔named-discipline-skill overlap advisory.
               Reports as B:<agent>↔<skill>:<n> shared lines (advisory — calibration
               pending ADR-0036).  Does NOT affect the exit code.
  Contract C — model-tier checkable subset (cot:yes → opus; adversarial lanes + arbiter
               → opus; unknown model → flag).
               Reports violations as C:<file>:<reason>.  Exit code 1 on any C violation.
  Contracts D, E — process/spec contracts, not checked by this static script.
               A one-line note is printed.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

CHARS_PER_TOKEN = 4  # English-prose proxy; raw chars retained for re-derivation
FRONTMATTER_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")
# substantive line for overlap: drop blanks, markdown headers, list/structure-only
_TRIVIAL = re.compile(r"^[\s\-*#>|`_=]*$")

# Contract A — description budgets (ADR-0035)
AGENT_DESC_TOKEN_CAP = 150
SKILL_DESC_TOKEN_CAP = 120

# Contract A — banned construct patterns (ADR-0035, enumeration clause superseded by ADR-0085)
# (1) baked-in line-number cross-references: "line 832", "lines 254-255", or "file.py:123".
#     Anchored to require either the "line(s) N" form or a filename-with-extension before ":N",
#     so it does NOT false-positive on "host:8080", "section:4", or ratios (finding R-2).
_LINE_REF = re.compile(
    r"\blines?\s+\d+(?:[-–]\d+)?\b|\b[\w./-]+\.[A-Za-z]{1,6}:\d+(?:[-–]\d+)?\b",
    re.IGNORECASE,
)
# NOTE: _TRIGGER_ENUM removed per ADR-0085. Trigger-phrase enumeration is now permitted
# within the token budget. Only token-cap and _LINE_REF remain as Contract-A checks.

# Contract C — adversarial audit lanes + arbiter MUST be opus (ADR-0037)
_MUST_BE_OPUS = {
    "aidev-adversarial-auditor",
    "aidev-state-adversarial-auditor",
    "aidev-arbiter",
}
# Contract C — allowed deviation: aidev-code-reviewer=sonnet is explicitly permitted (ADR-0037).
# Forward-defensive guard: aidev-code-reviewer carries no `cot: yes` field today, so the
# cot:yes→opus branch below does not currently flag it. This set encodes the ADR-0037
# allowed-deviation so that IF a cot:yes field is ever added to the reviewer, it still is
# not flagged. The guard is intentionally latent, not dead (findings AA-4 / R-3).
_ALLOWED_SONNET_DEVIATION = {"aidev-code-reviewer"}
_VALID_TIERS = {"opus", "sonnet", "haiku"}

# Contract B — agent→its-OWN-named-discipline-skill mapping (ADR-0036).
# Genuine *-discipline-skill pairs only: an agent and the discipline skill it names as its
# methodology single-source. Co-used orchestration skills (audit-pairing-lookup,
# session-lifecycle, verification-before-completion) are NOT discipline pairs and are
# excluded — the de-dup rule is about an agent duplicating ITS OWN skill's prose, not
# shared orchestration boilerplate (finding AA-3 / R-1).
_AGENT_SKILL_MAP: dict[str, str] = {
    "aidev-eval-engineer": "skill-eval",
    "biz-process-builder": "biz-sop-discipline",
    "biz-process-reviewer": "biz-sop-discipline",
    "data-power-query-developer": "m-language-discipline",
    "data-vba-developer": "vba-language-discipline",
    "fin-transaction-categorizer": "fin-categorization-audit-discipline",
    "gh-pr-reviewer": "gh-pr-review-discipline",
    "gh-workflow-author": "gh-workflow-discipline",
    "gh-repo-scaffolder": "gh-scaffold-discipline",
}


def approx_tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """Return (frontmatter_lines, body_text). Empty frontmatter if no fence."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body = "\n".join(lines[i + 1 :])
            return lines[1:i], body
    return [], text  # unterminated fence -> treat as no frontmatter


def extract_field(fm_lines: list[str], key: str) -> str | None:
    """Manual frontmatter field read. Captures a value plus any continuation
    lines until the next top-level `key:` — robust to colons inside the value
    (agent descriptions contain many), which breaks naive YAML scalar parsing."""
    prefix = key + ":"
    out: list[str] = []
    capturing = False
    for ln in fm_lines:
        if not capturing:
            if ln.startswith(prefix):
                capturing = True
                out.append(ln[len(prefix) :].strip())
            continue
        # capturing: stop at the next top-level key
        if FRONTMATTER_KEY.match(ln):
            break
        out.append(ln.strip())
    if not capturing:
        return None
    return " ".join(p for p in out if p).strip()


def substantive_lines(body: str) -> set[str]:
    """Normalized non-trivial lines for overlap measurement."""
    norm: set[str] = set()
    for ln in body.splitlines():
        s = ln.strip()
        if len(s) < 12 or _TRIVIAL.match(s):
            continue
        norm.add(re.sub(r"\s+", " ", s).lower())
    return norm


def dist(values: list[int]) -> dict:
    if not values:
        return {"count": 0}
    sv = sorted(values)
    return {
        "count": len(sv),
        "min": sv[0],
        "max": sv[-1],
        "mean": round(statistics.mean(sv), 1),
        "median": int(statistics.median(sv)),
        "p90": sv[min(len(sv) - 1, int(round(0.9 * (len(sv) - 1))))],
    }


def measure(repo: Path) -> dict:
    agents_dir = repo / "agents"
    skills_dir = repo / "skills"

    agents = []
    for f in sorted(agents_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        desc = extract_field(fm, "description") or ""
        model = (extract_field(fm, "model") or "unset").strip()
        cot_raw = extract_field(fm, "cot")
        cot = cot_raw.lower().strip() if cot_raw else None
        agents.append(
            {
                "name": extract_field(fm, "name") or f.stem,
                "file": f.name,
                "desc_chars": len(desc),
                "desc_tokens": approx_tokens(len(desc)),
                "body_chars": len(body),
                "body_tokens": approx_tokens(len(body)),
                "model": model,
                "cot": cot,
                "_desc_raw": desc,
                "_lines": substantive_lines(body),
            }
        )

    skills = []
    for d in sorted(skills_dir.glob("*/")):
        sf = d / "SKILL.md"
        if not sf.exists():
            continue
        text = sf.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        desc = extract_field(fm, "description") or ""
        skills.append(
            {
                "name": extract_field(fm, "name") or d.name,
                "file": f"skills/{d.name}/SKILL.md",
                "desc_chars": len(desc),
                "desc_tokens": approx_tokens(len(desc)),
                "body_chars": len(body),
                "body_tokens": approx_tokens(len(body)),
                "_desc_raw": desc,
                "_lines": substantive_lines(body),
            }
        )

    # model-tier distribution
    tiers: dict[str, int] = {}
    for a in agents:
        tiers[a["model"]] = tiers.get(a["model"], 0) + 1

    # agent<->skill overlap: shared substantive lines + Jaccard, all pairs
    overlap = []
    for a in agents:
        for s in skills:
            al, sl = a["_lines"], s["_lines"]
            if not al or not sl:
                continue
            inter = al & sl
            if len(inter) < 3:
                continue
            union = len(al | sl)
            overlap.append(
                {
                    "agent": a["name"],
                    "skill": s["name"],
                    "shared_lines": len(inter),
                    "jaccard": round(len(inter) / union, 3) if union else 0.0,
                }
            )
    overlap.sort(key=lambda o: (o["shared_lines"], o["jaccard"]), reverse=True)

    # strip private fields from emitted rows
    for coll in (agents, skills):
        for row in coll:
            row.pop("_lines", None)
            row.pop("_desc_raw", None)
            row.pop("cot", None)  # cot is internal; not in the surface report

    return {
        "repo": str(repo),
        "agent_count": len(agents),
        "skill_count": len(skills),
        "model_tier_distribution": dict(sorted(tiers.items())),
        "agent_description": dist([a["desc_tokens"] for a in agents]),
        "skill_description": dist([s["desc_tokens"] for s in skills]),
        "agent_body": dist([a["body_tokens"] for a in agents]),
        "skill_body": dist([s["body_tokens"] for s in skills]),
        "overlap_pairs": overlap,
        "agents": agents,
        "skills": skills,
    }


def _check(
    repo: Path,
    report_agents: list[dict],
    report_skills: list[dict],
    overlap_pairs: list[dict],
    advisory_contracts: frozenset[str] | None = None,
) -> dict:
    """Evaluate static contracts A, B, C.  Returns a check-results dict.

    Contract A: description budget + line-number-ref ban -> exit-code-affecting
                (unless its letter is in advisory_contracts, in which case it is
                reported but does not contribute to the exit code).
    Contract B: named discipline-skill overlap advisory -> advisory only (no exit-code effect).
    Contract C: model-tier checkable subset -> exit-code-affecting.
    Contracts D, E: process/spec contracts -- printed as a note, not checked here.

    advisory_contracts: frozenset of uppercase contract letters (e.g. frozenset({"A"}))
        whose violations are printed but not counted toward the exit code.
        None / empty frozenset means all blocking contracts remain blocking (default).
    """
    if advisory_contracts is None:
        advisory_contracts = frozenset()
    violations_a: list[str] = []
    violations_c: list[str] = []
    advisory_b: list[str] = []

    # --- Contract A: description budgets + banned constructs ---
    agents_dir = repo / "agents"
    skills_dir = repo / "skills"

    for f in sorted(agents_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        desc = extract_field(fm, "description") or ""
        tok = approx_tokens(len(desc))
        if tok > AGENT_DESC_TOKEN_CAP:
            violations_a.append(
                f"A:{f.name}:description over budget ({tok} tok > {AGENT_DESC_TOKEN_CAP})"
            )
        if _LINE_REF.search(desc):
            violations_a.append(f"A:{f.name}:description contains baked-in line-number reference")

    for d in sorted(skills_dir.glob("*/")):
        sf = d / "SKILL.md"
        if not sf.exists():
            continue
        text = sf.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        desc = extract_field(fm, "description") or ""
        tok = approx_tokens(len(desc))
        skill_file = f"skills/{d.name}/SKILL.md"
        if tok > SKILL_DESC_TOKEN_CAP:
            violations_a.append(
                f"A:{skill_file}:description over budget ({tok} tok > {SKILL_DESC_TOKEN_CAP})"
            )
        if _LINE_REF.search(desc):
            violations_a.append(
                f"A:{skill_file}:description contains baked-in line-number reference"
            )

    # --- Contract B: advisory overlap for named discipline-skill pairs ---
    # Build skill name -> overlap entry lookup
    overlap_by_pair: dict[tuple[str, str], int] = {}
    for o in overlap_pairs:
        overlap_by_pair[(o["agent"], o["skill"])] = o["shared_lines"]

    for agent_stem, skill_name in _AGENT_SKILL_MAP.items():
        shared = overlap_by_pair.get((agent_stem, skill_name), 0)
        if shared >= 3:
            advisory_b.append(
                f"B:{agent_stem}<->{skill_name}:{shared} shared lines"
                f" (advisory — calibration pending ADR-0036)"
            )

    # --- Contract C: model-tier checkable subset ---
    for f in sorted(agents_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        model = (extract_field(fm, "model") or "unset").strip().lower()
        cot_raw = extract_field(fm, "cot")
        cot = cot_raw.lower().strip() if cot_raw else None

        stem = f.stem
        # Unknown model tier
        if model not in _VALID_TIERS and model != "unset":
            violations_c.append(f"C:{f.name}:unknown model tier '{model}'")
        # Adversarial audit lanes + arbiter must be opus
        if stem in _MUST_BE_OPUS and model != "opus":
            violations_c.append(f"C:{f.name}:{stem} must be opus (got '{model}')")
        # cot:yes agents must be opus, except the ADR-0037 allowed-deviation set
        # (latent guard — see _ALLOWED_SONNET_DEVIATION; reviewer carries no cot:yes today)
        if cot == "yes" and model != "opus" and stem not in _ALLOWED_SONNET_DEVIATION:
            violations_c.append(f"C:{f.name}:cot:yes agent must be model:opus (got '{model}')")

    # Determine which violations are blocking (not in the advisory set).
    blocking_a = violations_a if "A" not in advisory_contracts else []
    blocking_c = violations_c if "C" not in advisory_contracts else []

    return {
        "contract_a_violations": violations_a,
        "contract_a_advisory": "A" in advisory_contracts,
        "contract_b_advisory": advisory_b,
        "contract_c_violations": violations_c,
        "contract_c_advisory": "C" in advisory_contracts,
        "contracts_d_e_note": (
            "Contracts D and E are process/spec contracts — "
            "checked by orchestrator-self-check skill and state-reviewer at dispatch time, "
            "not by this static script."
        ),
        "exit_code": 1 if (blocking_a or blocking_c) else 0,
    }


def human(report: dict) -> str:
    lines = []
    lines.append(f"Framework surface — {report['repo']}")
    lines.append(f"  {report['agent_count']} agents, {report['skill_count']} skills")
    lines.append(f"  model tiers: {report['model_tier_distribution']}")

    def row(label: str, d: dict) -> str:
        if not d.get("count"):
            return f"  {label:<22} (none)"
        return (
            f"  {label:<22} n={d['count']:<3} min={d['min']:<5} "
            f"median={d['median']:<5} mean={d['mean']:<7} p90={d['p90']:<5} max={d['max']}"
        )

    lines.append("\nSizes (approx tokens):")
    lines.append(row("agent description", report["agent_description"]))
    lines.append(row("skill description", report["skill_description"]))
    lines.append(row("agent body", report["agent_body"]))
    lines.append(row("skill body", report["skill_body"]))

    lines.append("\nLargest agent descriptions (tokens):")
    for a in sorted(report["agents"], key=lambda x: x["desc_tokens"], reverse=True)[:8]:
        lines.append(f"  {a['desc_tokens']:>4}  {a['name']}")
    lines.append("\nLargest skill descriptions (tokens):")
    for s in sorted(report["skills"], key=lambda x: x["desc_tokens"], reverse=True)[:8]:
        lines.append(f"  {s['desc_tokens']:>4}  {s['name']}")
    lines.append("\nLargest agent bodies (tokens):")
    for a in sorted(report["agents"], key=lambda x: x["body_tokens"], reverse=True)[:8]:
        lines.append(f"  {a['body_tokens']:>5}  {a['name']}")

    lines.append("\nTop agent<->skill overlap (shared substantive lines, Jaccard):")
    if not report["overlap_pairs"]:
        lines.append("  (none above threshold)")
    for o in report["overlap_pairs"][:15]:
        lines.append(
            f"  {o['shared_lines']:>3} lines  J={o['jaccard']:<5}  {o['agent']} <-> {o['skill']}"
        )
    return "\n".join(lines)


def human_check(check_result: dict) -> str:
    lines = []
    lines.append("\n=== Contract check results ===")

    violations_a = check_result["contract_a_violations"]
    violations_c = check_result["contract_c_violations"]
    advisory_b = check_result["contract_b_advisory"]
    a_is_advisory = check_result.get("contract_a_advisory", False)
    c_is_advisory = check_result.get("contract_c_advisory", False)

    # Contract A
    if violations_a:
        if a_is_advisory:
            lines.append(
                f"\nContract A — ADVISORY ({len(violations_a)} violation(s), non-blocking):"
            )
        else:
            lines.append(f"\nContract A — FAIL ({len(violations_a)} violation(s)):")
        for v in violations_a:
            lines.append(f"  {v}")
    else:
        lines.append("\nContract A — PASS")

    # Contract B (advisory — never fails)
    if advisory_b:
        lines.append(f"\nContract B — ADVISORY ({len(advisory_b)} pair(s)):")
        for v in advisory_b:
            lines.append(f"  {v}")
    else:
        lines.append("\nContract B — ADVISORY (no pairs above threshold)")

    # Contract C
    if violations_c:
        if c_is_advisory:
            lines.append(
                f"\nContract C — ADVISORY ({len(violations_c)} violation(s), non-blocking):"
            )
        else:
            lines.append(f"\nContract C — FAIL ({len(violations_c)} violation(s)):")
        for v in violations_c:
            lines.append(f"  {v}")
    else:
        lines.append("\nContract C — PASS")

    # Contracts D, E
    lines.append(f"\nContracts D, E — {check_result['contracts_d_e_note']}")

    # Summary
    exit_code = check_result["exit_code"]
    if exit_code == 0:
        lines.append("\nOverall: PASS (exit 0)")
    else:
        # Count only blocking violations
        blocking_a = violations_a if not a_is_advisory else []
        blocking_c = violations_c if not c_is_advisory else []
        total = len(blocking_a) + len(blocking_c)
        lines.append(f"\nOverall: FAIL — {total} blocking violation(s) (exit 1)")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--format", choices=["human", "json"], default="human")
    ap.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repo root (default: parent of scripts/)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help=(
            "Validate static contracts A (description budget + line-number-ref ban; "
            "ADR-0035 / ADR-0085) and C (model-tier checkable subset). "
            "Exit 0 = all pass, exit 1 = any violation. "
            "Contract B overlap is reported as advisory (no exit-code effect). "
            "Contracts D and E are process/spec — a note is printed."
        ),
    )
    ap.add_argument(
        "--advisory-contracts",
        default="",
        metavar="LETTERS",
        help=(
            "Comma-separated list of contract letters (e.g. 'A' or 'A,C') to treat as "
            "advisory under --check: their violations are still printed but do NOT "
            "contribute to the exit code.  Contracts not listed remain blocking as usual. "
            "Default: empty (all blocking contracts remain blocking)."
        ),
    )
    args = ap.parse_args()
    report = measure(args.repo)

    # Parse --advisory-contracts into a frozenset of uppercase letters.
    advisory_contracts: frozenset[str] = frozenset(
        letter.strip().upper() for letter in args.advisory_contracts.split(",") if letter.strip()
    )

    # F1 — allowlist: only A and B may be made advisory (ADR-0035 / ADR-0085).
    # Contract C (model-tier / opus-tier guard) must always block — accepting C here
    # would silently disable it.  Accepting unknown letters would mask typos.
    _ADVISORY_ALLOWED: frozenset[str] = frozenset({"A", "B"})
    if "C" in advisory_contracts:
        ap.error(
            "--advisory-contracts: Contract C (model-tier guard) must always block "
            "and cannot be made advisory."
        )
    unknown = advisory_contracts - _ADVISORY_ALLOWED
    if unknown:
        ap.error(
            f"--advisory-contracts: unknown contract letter(s): {', '.join(sorted(unknown))}. "
            f"Valid letters are: {', '.join(sorted(_ADVISORY_ALLOWED))}."
        )

    # Re-derive internal data for check mode (measure() strips private fields)
    check_result = None
    if args.check:
        check_result = _check(
            args.repo,
            report["agents"],
            report["skills"],
            report["overlap_pairs"],
            advisory_contracts=advisory_contracts,
        )

    if args.format == "json":
        out = dict(report)
        if check_result is not None:
            out["check"] = check_result
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(human(report))
        if check_result is not None:
            print(human_check(check_result))

    return check_result["exit_code"] if check_result is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
