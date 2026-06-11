<!--
scope-owned: mission + design principles
audience: all
source: hand
review-trigger: direction change
-->

# S.A.G.E. · Mission

Memory is identity. An assistant that forgets every conversation cannot build real understanding of the person it works with, the projects in flight, or the decisions already made. S.A.G.E. exists to close that gap: a single-user, local-first memory framework that one operator runs on one workspace. The memory store's data path involves no external services — drawers, embeddings, and indexes live on the operator's machine (vendored chromadb); optional surfaces such as LLM-refined mining and the Codex budget tooling call out only when the operator opts in. S.A.G.E. is built to be stranger-installable, and every install stays local-first by design.

## What S.A.G.E. is

A single-user verbatim memory store with:

- **Wings, rooms, drawers** — the building blocks. Wings group by destination repo or project, rooms group by topic or session, drawers hold the exact text. Nothing is summarised. Nothing leaves the machine.
- **Agent-keyed metadata** — every drawer records which agent filed it (the orchestrator, the Keeper, the Stop hook, a specialist). This is the load-bearing extension that makes dispatch-reliability monitoring and per-agent recall possible.
- **Cross-wing tunnels** — explicit, named links between rooms in different wings. The orchestrator follows them during wake-up so context from one repo can inform work in another without polluting either wing.
- **Wing taxonomy with explicit registration** — 6 wing types (`dev`, `project`, `knowledge`, `ops`, `meta`, `personal`) and a registered wing list at `wing_config.json`. Writes to an unregistered wing fail loudly.
- **Absorbed orchestrator** — the S.A.G.E. agent roster, skills, and Claude Code hooks live in this repo. The Keeper is the only agent that touches the nook MCP surface; everything else goes through it.

## Design principles

These are non-negotiable. Every change must honour them.

- **Verbatim always.** Never summarise, paraphrase, or lossy-compress user data. If the operator said it, the nook stores exactly what they said. Search returns the original words.
- **Single-user, single-machine.** No telemetry, no phone-home, no cloud sync. The system physically cannot leak data because it never leaves the machine. External LLM providers (Anthropic, OpenAI, etc.) are supported via BYOK only and are never required.
- **Append-only ingest.** Mines never destroy existing data to rebuild. A crash mid-operation leaves the existing nook untouched.
- **Background everything.** Filing, indexing, and pipeline work happen via hooks. Nothing interrupts the operator's conversation.
- **Audit-trailed orchestration.** Every specialist verdict is recorded in a structured block, parsed by the framework, and logged to per-turn telemetry the operator can mine.
- **Performance targets (aspirational).** Hooks under 500ms. Wake-up injection under 100ms. Memory should feel instant. These are operator-observable targets, not test-asserted SLOs — see CHANGELOG known-limitations.

## Scope

- **In:** verbatim storage, agent-keyed drawers, cross-wing tunnels, wing taxonomy, hook-driven session-end + pre-compact handoff, the multi-agent orchestrator absorbed from S.A.G.E., dual-auditor governance.
- **Out:** summarisation of user content, cloud sync, telemetry to external services, features that require API keys for core memory operations.

S.A.G.E. is a public, stranger-installable memory framework: any operator can run it on their own machine. The single-user, single-machine, local-first stance is a design principle that travels with every install — not a limit on who may use it.
