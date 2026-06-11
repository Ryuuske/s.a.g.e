---
name: session-lifecycle
description: "Use at session start, at session end, or when a specialist returns `PAUSE: need nook lookup for <query>`. Covers the destination picker, per-repo session-start steps, nook wake-up, wing-slug resolution, keeper dispatch shape, and the session-end handoff. Do not use for agent / skill CRUD (`aidev-agent-creator` / `aidev-skill-creator`), for audit-pairing decisions (`audit-pairing-lookup`), or for direct nook tool calls (only `aidev-keeper` calls `nook_*`)."
---

# Session Lifecycle Skill

This skill consolidates the full session-lifecycle procedures referenced by CLAUDE.md §9 — from establishing the destination at session start through during-session commit and push discipline to session-end handoff. The orchestrator invokes it across these lifecycle moments:

1. **Session start (destination)** — orchestrator-mode destination picker when no repo context exists yet
2. **Session start (per-repo)** — per-repo setup steps after destination is established, including mode classification and nook wake-up
3. **Mid-session** — when any specialist returns `PAUSE: need nook lookup for <query>`, and for atomic-commit and pre-push-diff discipline throughout
4. **Session end** — when the User indicates wrapping up

The orchestrator never calls `nook_*` MCP tools directly. Every nook interaction goes through `aidev-keeper` dispatch. This skill describes the dispatch shape; the keeper's agent file describes the keeper's own behavior.

## Orchestrator mode — establishing the destination (session start, no repo context)

If the User's first message is a generic opener with no concrete task ("Let's get to work", "Hi", "Hey", "Ready", "Let's start", or similar), enter Orchestrator mode and do NOT begin work until the destination is established. Protocol:

1. Acknowledge briefly (one short line, NORMAL prose).
2. Ask: "What are we working on?" Offer three options:
   - **GitHub** — repos cloned locally under `~/dev/github/`. When chosen, run `ls -1d ~/dev/github/*/*/ 2>/dev/null` and present the repos to the User as a numbered list. Wait for selection.
   - **Projects** — local-only projects under `~/dev/projects/`. When chosen, run `ls -1d ~/dev/projects/*/ 2>/dev/null` and present them. If the directory doesn't exist or is empty, say so and offer to create the directory or start a new project.
   - **Other** — free-text task (research, brainstorming, general questions, exploration). No repo context needed; stay in `~/dev` or follow the User's lead.
3. Once destination is chosen, `cd` to it. Confirm with `pwd`. Then state, in one line: `Session focus: <repo or scope>.` This biases the claude.ai auto-titler toward a meaningful name and helps with sidebar grouping.
4. Then proceed to the per-repo session-start steps below.

If the User's first message IS a concrete task with a clear destination (e.g., "Fix the bug in Acme-Ops.V3 docs/02-agent-roster.md §3"), skip the Orchestrator dance, `cd` to the implied repo, and proceed.

## Per-repo session-start steps (after destination established)

1. Read `docs/handoff/LATEST.md` if it exists (per-destination-repo convention; not present in every repo).
2. Check `git status` and `git log -3 --oneline` to anchor on recent state.
3. Confirm any in-flight work in `docs/in-flight/` if that directory exists (per-destination-repo convention).
4. **Classify the mode** (this step is a resident CLAUDE.md §9 invariant; this section is its procedural execution):
   - **AI-dev mode** — the destination is the `sage` framework repo itself, or the work targets agents/skills/hooks/framework files. The `aidev-*` roster handles the work; general specialists are out of lane.
   - **Normal mode** — every other destination. The general specialist roster handles the work; `aidev-*` agents are out of lane.

   The mode determines *which agents* dispatch, NOT whether the nook is consulted. Both modes use the nook via the Keeper.

Then continue to Nook wake-up below.

## During the session

The following bullets are the procedural detail for the atomic-commit and pre-push-diff invariants resident in CLAUDE.md §9:

- Commit early and often. Atomic commits with clear messages.
- **One logical change per commit.** If you produced two unrelated improvements, split them into two commits. Don't bundle a refactor with a feature, or a bug fix with formatting. This protects future-you when bisecting or reverting.
- After every successful edit, run any project test suite if one exists. If no tests exist and you're touching critical code, ask whether to add one.
- **Before `git push` to a protected branch, show the User the complete diff** (`git diff origin/main...HEAD`) and wait for explicit approval. In bypass-permissions mode this is the last human checkpoint. PRs to feature branches don't require this; pushes to `main` always do.

## Nook wake-up (session start)

After completing the per-repo session-start steps (read handoff, check git status, classify mode), perform nook wake-up:

1. **Resolve the wing slug.** Match the current directory against `wing_config.json`. Run `sage wing list` to inspect registered wings. If no wing is registered for the current path:
   - **Do not invent a wing.** Inventing a wing slug breaks the nook's wing-based memory isolation.
   - Ask the User which registered wing to use, OR offer to register a new wing with `sage wing add <slug> --type <type>`.

2. **Write the slug to `~/.sage/current_wing`.** Single-line file. This is what the Stop and PreCompact hooks read to decide whether to file an emergency drawer. Without it, those hooks no-op silently.

3. **Dispatch `aidev-keeper`** with `operation=wake-up, wing=<slug>`. The Keeper returns a structured payload:
   - Recent handoff drawers
   - In-flight work
   - Recent ADRs
   - Pending audit findings

   The SessionStart hook (`installer-assets/claude-wakeup-sessionstart.py`) assembles the Tier-0 cached-prefix block — identity core + current-wing L1 halls + compact skill/agent registry (metadata-only: name, one_line, triggers) — and emits it at session start. This is what the nook injects into context at the stable prefix position (WI-3). Full skill/agent bodies are loaded from disk only on invocation; never pre-loaded.

4. **Render the payload to the User in NORMAL prose.** Don't dump the structured payload verbatim — synthesize it into a brief situational summary.

5. **State current understanding** in one paragraph. Wait for User direction.

## Mid-session nook lookups

When any specialist's inline reply contains `PAUSE: need nook lookup for <query>`:

1. **Dispatch `aidev-keeper`** with `operation=search, wing=<current>, query=<query>`.
2. **Embed the Keeper's pointers** in a follow-up brief to the original specialist.
3. **Resume the specialist** with the augmented brief.

Specialists never see the nook; they see only the pointers the Keeper returns. This is the contract — if a specialist tries to call `nook_*` tools directly, that's a lane violation flagged by `aidev-state-reviewer`.

## Session-end handoff

When the User indicates wrapping up:

1. **Commit any uncommitted work** (per CLAUDE.md §9 atomic-commit discipline).
2. **Update `docs/handoff/LATEST.md`** with: what was done, what's in-flight, what's next, any blockers. Per-destination-repo convention — create the file if the repo uses this convention.
3. **Dispatch `aidev-keeper`** with `operation=file-handoff, wing=<current>, content=<session summary you composed>`. The Keeper writes one drawer to the wing's `handoff` hall with `agents=["aidev-keeper"]` and `nook_check_duplicate` idempotency. Applies in both AI-dev and Normal modes.
4. **Clean up `docs/in-flight/`** of files that are no longer relevant (per-destination-repo convention).
5. **Summarize the session in <10 lines.**

## Structured write-back protocol (session end)

At session end, after step 3 above, the orchestrator identifies structured learnings and routes each through `aidev-keeper` to the appropriate nook destination. The Keeper runs the dedup gate (`dedup.write_back_gate`) before each store. **Only `aidev-keeper` writes to the nook** — the orchestrator dispatches; the Keeper decides whether to store, skip, or surface a merge candidate, based on the gate's decision.

The four write-back categories and their destinations:

| Category | What qualifies | Destination |
|---|---|---|
| **Decision** | Any architectural, process, or product decision made this session | `decisions` hall of the active wing |
| **Solved problem** | A non-trivial problem that required multiple attempts to resolve | Episodic record drawer (see shape below) in the active wing's `episodic` hall |
| **New user fact** | A new or updated fact about the user — preferences, standing constraints, role context | `Personal` wing: `core` hall for durable identity facts (preferences, role, standing constraints — things true across all sessions); `detail` hall for retrieval-only personal detail (specific past events, transient context). **Core/detail classification rule:** if you would want this fact injected at every session start, it is `core`; if it only matters when explicitly retrieved, it is `detail`. **Default-safe tiebreaker: when uncertain, route to `detail`** — a misrouted detail fact can be re-promoted to core later, but a transient fact wrongly in core would bloat the always-on Tier-0 block permanently (WI-6 never decays core). **Prefer-detail + decay reconciliation (ADR-0043):** A durable identity fact routed to `detail` is still PROTECTED from meaningful decay by storing it with HIGH confidence (`confidence=1.0` decays negligibly per ADR-0043 — ≈80% strength retained after 90 days), and decay never deletes (floored, always queryable). Reserve `core` (Tier-0, never decays) for facts that MUST be always-resident at every session start. Use high-confidence `detail` for durable-but-not-always-resident facts — the prefer-detail default is safe: a genuinely durable fact stored at `confidence=1.0` survives even in `detail`. |
| **New or improved skill** | A `SKILL.md` was created or materially updated this session | Write/update `SKILL.md` on disk, then update its registry drawer in `skill_registry` |

**Episodic record drawer shape** (solved problems — Hermes pattern per PRD §8):

```
task: <concise description of what was being solved>
what_tried: <approaches attempted, including failed ones>
what_worked: <the approach that succeeded>
what_failed: <what did not work and why; empty string if nothing failed>
```

This flat text format is retrievable via both BM25/FTS5 (exact token match) and vector similarity (semantic match).

**Dedup gate behavior** — before each store the Keeper calls `dedup.write_back_gate`, which:
1. Scrubs secrets from the content (`secret_scrub.scrub_secrets`).
2. Runs a cosine similarity query against existing nook drawers.
3. Returns one of three decisions with a `dedup_ran` flag:
   - `STORE` (dedup_ran=True) — no near-match (similarity < 0.85); proceed to store.
   - `STORE` (dedup_ran=False) — vector backend degraded; storing unverified (Keeper logs a warning).
   - `MERGE-CANDIDATE` — near-match in the 0.85–0.90 band; Keeper stores the new drawer **and** creates a nook tunnel linking it to the near-match drawer (`merge-candidate` label) for WI-6 consolidation.
   - `SKIP` — near-exact duplicate (similarity ≥ 0.90); suppress; do not store.

The gate reuses the same cosine similarity path as `nook_check_duplicate` (the MCP tool). It does NOT write to the nook itself. When the vector backend is disabled, `tool_check_duplicate` returns `vector_disabled: true`; the Keeper's `query_fn` wrapper must raise on that sentinel so the gate correctly sets `dedup_ran=False` (a silent empty-list return is indistinguishable from "no near-matches").

**Confidence tagging for user_fact (WI-5 / ADR-0043):** when routing a `user_fact` write-back, pass `confidence=1.0` for facts you are certain of (durable identity, explicit user statements). Pass a lower value (e.g. `confidence=0.8`) for inferred or uncertain facts. WI-6 decay uses this tag to weight down uncertain drawers without touching durable `core` identity facts (which are also distinguished by `hall=core`). **Durable facts in `detail` MUST use `confidence=1.0`** — this is the protection mechanism that keeps them retrievable even after decay cycles. A durable fact stored at `confidence=0.5` in `detail` will decay significantly; a durable fact at `confidence=1.0` retains ≈80% strength after 90 days and is always queryable above the floor.

**What does NOT qualify for write-back:** transient tool calls, intermediate reasoning steps, routine status messages, and anything already captured in a prior session's drawer (the dedup gate handles this automatically).

## Hook integration

The Stop and PreCompact hooks read `~/.sage/last_keeper_dispatch` to decide whether to file an emergency drawer. Every Keeper operation updates that timestamp.

- **If the Keeper was dispatched at least once in the last 30 minutes**: hooks correctly skip the emergency write. The structured drawers the Keeper filed cover the session already.
- **If the Keeper was never dispatched**: the Stop hook files a single emergency drawer with the last 4000 characters of the session. Usable but lower-quality than a Keeper-curated handoff.

The mechanical implication: **dispatch the Keeper at least once per session.** The session-start wake-up satisfies this; even sessions with no real work get one wake-up dispatch.

## Nook tool access rule

The `nook_*` MCP tools (`nook_search`, `nook_add_drawer`, `nook_diary_write`, `nook_register_wing`, `nook_check_duplicate`, and others) are reserved for `aidev-keeper`. The orchestrator dispatches the Keeper; the Keeper touches the nook. The orchestrator and every other specialist see the nook through pointers in their briefs, never via direct tool calls.

If a specialist's agent file grants `nook_*` tools, that's an agent-design finding. Flag via `aidev-state-reviewer`. The Keeper is the sole mediator.

## When this skill does NOT apply

- **For nook content updates that aren't lifecycle-triggered** — those go through `aidev-keeper` dispatch initiated by the relevant specialist, not by this skill.
- **For agent CRUD** — use `aidev-agent-creator`.
- **For skill CRUD** — use `aidev-skill-creator`.
- **For audit pairing** — use the `audit-pairing-lookup` skill against `docs/specs/audit-pairing-matrix.md`.

## Relationship to Anthropic's built-in auto memory

Anthropic released built-in auto memory at `~/.claude/projects/<project>/memory/MEMORY.md` (v2.1.59+, April 2026). It covers some of what the nook does — Claude's accumulated notes per project, loaded at session start, indexed via MEMORY.md.

The nook and auto memory can coexist. Distinguishing roles:

- **Auto memory** = Claude's per-project notes, written by Claude, no curation overhead, lives in `~/.claude/projects/<project>/memory/`.
- **Nook (this skill's domain)** = wing-isolated structured memory with explicit drawers, halls, keeper-mediated access, idempotency checks, audit pairing recall, cross-session ADR tracking.

If your setup uses only auto memory, replace this skill's nook-specific procedures with auto memory equivalents (`/memory` for inspection, automatic loading at session start). The lifecycle structure (session start / mid-session / session end) holds either way.
