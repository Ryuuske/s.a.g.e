#!/usr/bin/env python3
"""Generate docs/reference/* and AGENTS.md from repo sources.

Master Run Stage 3d (Delta 5 / C-03: drift control = generation + gates).
Sources: agents/*.md frontmatter, skills/*/SKILL.md, commands/*.md,
hooks/hooks.json, wing_config.json, pyproject.toml, .claude-plugin/plugin.json,
and root CLAUDE.md (mirrored into AGENTS.md).

Outputs (all carry a GENERATED banner; never hand-edit):
  docs/reference/agent-roster.md   one row table + per-family entries
  docs/reference/skills.md         every skill + description
  docs/reference/commands.md       every command-skill
  docs/reference/surface.md        counts, hooks, wings, version facts
  AGENTS.md                        generated mirror of the root CLAUDE.md stub

Run: python3 scripts/gen_docs.py [--check]
``--check`` exits 1 if any output differs from regeneration (regen-diff gate).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BANNER = "<!-- GENERATED — DO NOT EDIT. Regenerate with `python3 scripts/gen_docs.py`. -->\n\n"

FAMILIES = [
    ("aidev-", "AI Development"),
    ("dev-", "General Development"),
    ("gh-", "GitHub Project Mechanics"),
    ("data-", "Data Engineering"),
    (("arch-", "freecad-"), "Architecture"),
    ("fin-", "Finance Operations"),
    ("biz-", "Business Operations"),
    ("doc-", "Documentation"),
    ("sec-", "Security"),
    ("ops-", "Operations"),
    ("research-", "Research"),
    ("test-", "Test / E2E Validation"),
    ("media-", "Media"),
]


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    fields: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if km and not line.startswith(" "):
            key = km.group(1)
            fields[key] = km.group(2).strip().strip('"').strip("'")
        elif key and line.startswith(" "):
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields


def agents() -> list[dict[str, str]]:
    out = []
    for f in sorted((REPO / "agents").glob("*.md")):
        fm = frontmatter(f.read_text(encoding="utf-8"))
        name = fm.get("name", f.stem)
        fam = next((label for pre, label in FAMILIES if name.startswith(pre)), "Other")
        # str.startswith accepts a tuple natively, so tuple prefixes work above.
        out.append(
            {
                "name": name,
                "family": fam,
                "description": fm.get("description", ""),
                "model": fm.get("model", "inherit"),
                "tools": fm.get("tools", ""),
            }
        )
    return out


def gen_roster(rows: list[dict[str, str]]) -> str:
    by_fam: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        by_fam.setdefault(r["family"], []).append(r)
    lines = [
        BANNER,
        "# Agent roster — reference\n\n",
        "Generated from `agents/*.md` frontmatter. Doctrine (constraint blocks, CoT\n",
        "classification, shareability principle) lives at\n",
        "`docs/specs/universal-agent-constraints.md`; pairing policy at\n",
        "`docs/specs/audit-pairing-matrix.md`.\n\n",
        "## Family directory\n\n",
        "| Family | Prefix | Count |\n|---|---|---|\n",
    ]
    total = 0
    for pre, label in FAMILIES:
        n = len(by_fam.get(label, []))
        total += n
        pre_str = "/".join(f"`{p}`" for p in pre) if isinstance(pre, tuple) else f"`{pre}`"
        lines.append(f"| {label} | {pre_str} | {n} |\n")
    other = len(by_fam.get("Other", []))
    if other:
        total += other
        lines.append(f"| Other | — | {other} |\n")
    lines.append(f"| **Total** | — | **{total}** |\n\n")
    for pre, label in FAMILIES + ([("", "Other")] if other else []):
        fam_rows = by_fam.get(label)
        if not fam_rows:
            continue
        lines.append(f"## Family: {label}\n\n")
        for r in sorted(fam_rows, key=lambda x: x["name"]):
            lines.append(f"### `{r['name']}`\n\n")
            lines.append(f"- **Description**: {r['description']}\n")
            lines.append(f"- **Model**: {r['model']} · **Tools**: {r['tools'] or '(default)'}\n\n")
    return "".join(lines)


def gen_skills() -> tuple[str, int]:
    rows = []
    for f in sorted((REPO / "skills").glob("*/SKILL.md")):
        fm = frontmatter(f.read_text(encoding="utf-8"))
        rows.append((fm.get("name", f.parent.name), fm.get("description", "")))
    body = [
        BANNER,
        "# Skills — reference\n\nGenerated from `skills/*/SKILL.md` frontmatter.\n\n",
        "| Skill | Description |\n|---|---|\n",
    ]
    for name, desc in rows:
        body.append(f"| `{name}` | {desc} |\n")
    return "".join(body), len(rows)


def gen_commands() -> tuple[str, int]:
    rows = []
    for f in sorted((REPO / "commands").glob("*.md")):
        fm = frontmatter(f.read_text(encoding="utf-8"))
        rows.append((f.stem, fm.get("description", "")))
    body = [
        BANNER,
        "# Command-skills — reference\n\nGenerated from `commands/*.md` frontmatter.\n\n",
        "| Command | Description |\n|---|---|\n",
    ]
    for name, desc in rows:
        body.append(f"| `/{name}` | {desc} |\n")
    return "".join(body), len(rows)


def gen_surface(agent_rows: list[dict[str, str]], n_skills: int, n_commands: int) -> str:
    hooks = json.loads((REPO / "hooks/hooks.json").read_text(encoding="utf-8"))
    hook_events = (
        sorted(hooks.get("hooks", {}).keys()) if isinstance(hooks.get("hooks"), dict) else []
    )
    wings = json.loads((REPO / "wing_config.json").read_text(encoding="utf-8"))
    wing_types = wings.get("wing_types", wings.get("types", []))
    if isinstance(wing_types, dict):
        wing_types = sorted(wing_types.keys())
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    requires = re.search(r'^requires-python = "([^"]+)"', pyproject, re.M).group(1)
    plugin = json.loads((REPO / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    body = [
        BANNER,
        "# Framework surface — reference\n\n",
        "The single home for countable facts (no hand-written doc may state these).\n\n",
        "| Fact | Value | Source |\n|---|---|---|\n",
        f"| Agents | {len(agent_rows)} | `agents/*.md` |\n",
        f"| Skills | {n_skills} | `skills/*/SKILL.md` |\n",
        f"| Command-skills | {n_commands} | `commands/*.md` |\n",
        f"| Hook events | {len(hook_events)} ({', '.join(hook_events) or '—'}) | `hooks/hooks.json` |\n",
        f"| Wing types | {len(wing_types)} ({', '.join(map(str, wing_types)) or '—'}) | `wing_config.json` |\n",
        f"| Package version | {version} | `pyproject.toml` |\n",
        f"| Plugin version | {plugin.get('version')} | `.claude-plugin/plugin.json` |\n",
        f"| Python floor | {requires} | `pyproject.toml` |\n",
    ]
    return "".join(body)


def gen_agents_md() -> str:
    stub = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    return (
        "<!-- GENERATED — DO NOT EDIT. AGENTS.md mirrors the root CLAUDE.md stub for\n"
        "     non-Claude agent harnesses. Edit CLAUDE.md, then run\n"
        "     `python3 scripts/gen_docs.py`. -->\n\n"
        + stub.replace("# CLAUDE.md", "# AGENTS.md", 1)
    )


def main() -> int:
    agent_rows = agents()
    skills_md, n_skills = gen_skills()
    commands_md, n_commands = gen_commands()
    outputs = {
        REPO / "docs/reference/agent-roster.md": gen_roster(agent_rows),
        REPO / "docs/reference/skills.md": skills_md,
        REPO / "docs/reference/commands.md": commands_md,
        REPO / "docs/reference/surface.md": gen_surface(agent_rows, n_skills, n_commands),
        REPO / "AGENTS.md": gen_agents_md(),
    }
    check = "--check" in sys.argv
    drift = []
    for path, body in outputs.items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                drift.append(str(path.relative_to(REPO)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
    if check:
        if drift:
            print("DRIFT: " + ", ".join(drift))
            return 1
        print(f"gen_docs check OK ({len(outputs)} outputs)")
        return 0
    print(
        f"gen_docs wrote {len(outputs)} outputs ({len(agent_rows)} agents, {n_skills} skills, {n_commands} commands)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
