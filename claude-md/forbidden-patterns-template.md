# forbidden-patterns.md
#
# Each entry is a regex pattern followed by a brief rationale.
# Agents that read this file (dev-code-implementer, dev-code-reviewer,
# aidev-code-implementer, aidev-code-reviewer, aidev-adversarial-auditor,
# sec-auditor) run every uncommented pattern against the diff.
# Any non-empty match attributable to the change is a finding.
#
# All patterns below are commented out. Destinations enable what they need.

# console\.log\( — debug noise; should not ship in committed code
# TODO(?!\s+[A-Z]+-\d+) — TODO without a ticket reference; orphan reminders accumulate
# \beval\( — eval without an explanation comment; rarely defensible

# See ADR-0013 for context on why this file exists and how it is bootstrapped.
