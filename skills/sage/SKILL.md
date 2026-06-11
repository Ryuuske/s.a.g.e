---
name: sage
description: "Use when asked to mine, search, check the status of, or set up the S.A.G.E. memory nook — or when recalling work by agent, inspecting drawer metadata, or running any S.A.G.E. CLI command. Do not use for designing the orchestrator agent/skill roster (agent-creation / skill-creation)."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# S.A.G.E.

A searchable memory nook for AI — mine projects and conversations, then search them semantically. Single-user verbatim memory system

## Prerequisites

Ensure `sage` is installed:

```bash
sage --version
```

If not installed (uv recommended):

```bash
git clone https://github.com/Ryuuske/s.a.g.e && cd s.a.g.e && bash install.sh   # or: uv tool install git+https://github.com/Ryuuske/s.a.g.e
```

## Usage

S.A.G.E. provides dynamic instructions via the CLI. To get instructions for any operation:

```bash
sage instructions <command>
```

Where `<command>` is one of: `help`, `init`, `mine`, `search`, `status`.

Run the appropriate instructions command, then follow the returned instructions step by step.
