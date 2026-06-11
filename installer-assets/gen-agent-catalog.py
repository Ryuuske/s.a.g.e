#!/usr/bin/env python3
"""gen-agent-catalog.py — Parse agents/*.md frontmatter → agent-catalog.json.

Usage:
    python3 gen-agent-catalog.py <agents-dir> <output-path>

Output schema:
    {
        "version": "1.0.0",
        "generated_at": "<ISO8601 UTC>",
        "agents": [
            {"name": "<name>", "family": "<family>", "one_line": "<desc>"},
            ...
        ]
    }

Agents are sorted by name.  family is derived from the name prefix before the
first '-'.  one_line is the first sentence of the description (first '. ' or
first 160 chars, whichever is shorter).

Exits nonzero with a message on empty/missing agents dir or zero parsed agents.
Pure stdlib — no sage package import (install.sh runs this before/independent
of package install).

Consumer contract: docs/specs/agent-registry-protocol.md
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML frontmatter from agent markdown text.

    Only flat string fields are extracted (name, description).
    The frontmatter block is delimited by an opening AND a closing '---' line.
    Returns an empty dict if no frontmatter is found — including when the
    closing fence is absent (an unclosed block would otherwise let body
    'key: value' lines overwrite real frontmatter fields).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        # Parse "key: value" or 'key: "quoted value"'
        if ":" in line:
            key, _, rest = line.partition(":")
            key = key.strip()
            val = rest.strip()
            # Strip surrounding quotes (single or double)
            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            fields[key] = val
    if not closed:
        return {}
    return fields


def _one_line_description(description: str) -> str:
    """Extract the first sentence from a description string.

    Truncates at the first '. ' occurrence or 160 chars, whichever is shorter.
    Surrounding whitespace is stripped; punctuation is preserved.
    """
    if not description:
        return ""
    # Find first sentence boundary: '. ' (period + space)
    idx = description.find(". ")
    if idx != -1:
        sentence = description[:idx].strip()
    else:
        sentence = description.strip()
    # Hard cap at 160 chars
    if len(sentence) > 160:
        sentence = sentence[:160].rstrip()
    return sentence


def _family_from_name(name: str) -> str:
    """Derive the agent family from the name prefix before the first '-'."""
    if not name:
        return ""
    return name.split("-", 1)[0]


def generate_catalog(agents_dir: Path, output_path: Path) -> None:
    """Parse agents and write the catalog JSON to output_path."""
    if not agents_dir.is_dir():
        print(
            f"ERROR: agents dir does not exist or is not a directory: {agents_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    md_files = sorted(agents_dir.glob("*.md"))
    if not md_files:
        print(
            f"ERROR: no .md files found in agents dir: {agents_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    agents: list[dict[str, str]] = []
    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"WARNING: could not read {md_file.name}: {exc}", file=sys.stderr)
            continue
        fields = _parse_frontmatter(text)
        name = fields.get("name", "").strip()
        description = fields.get("description", "").strip()
        if not name:
            # No (or unclosed) frontmatter `name:` — not an agent file. Skip
            # rather than fall back to the filename stem: a stray README.md or
            # notes file in agents/ must not become a phantom catalog entry.
            print(
                f"WARNING: skipping {md_file.name}: no frontmatter 'name:' field",
                file=sys.stderr,
            )
            continue
        family = _family_from_name(name)
        one_line = _one_line_description(description) or name
        agents.append({"name": name, "family": family, "one_line": one_line})

    if not agents:
        print(
            f"ERROR: zero agents parsed from {agents_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Sort by name for deterministic output
    agents.sort(key=lambda a: a["name"])

    catalog = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agents": agents,
    }

    if output_path.is_symlink():
        # Refuse symlinked output: os.replace would swap the LINK itself, but a
        # pre-planted link must not silently redirect where the catalog lands.
        print(
            f"ERROR: output path is a symlink, refusing to write: {output_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so a crash mid-write never leaves a truncated/invalid
    # catalog behind. The temp file comes from tempfile.mkstemp: an
    # UNPREDICTABLE name opened with O_CREAT|O_EXCL, so a pre-planted symlink
    # at a guessable name (e.g. catalog.json.tmp) can never be followed
    # (PR #45 review — predictable-tmp symlink bypass).
    fd, tmp_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=output_path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(catalog, indent=2, ensure_ascii=False))
        os.replace(tmp_name, output_path)
    except BaseException:
        # Best-effort cleanup so a failed write doesn't leave a temp sidecar.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    print(f"generated: {output_path} ({len(agents)} agents)")


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <agents-dir> <output-path>", file=sys.stderr)
        sys.exit(1)
    agents_dir = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    generate_catalog(agents_dir, output_path)


if __name__ == "__main__":
    main()
