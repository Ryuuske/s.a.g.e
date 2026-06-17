<!--
scope-owned: catalog schema + dispatch protocol + Type→agent mapping
audience: agents + devs
source: hand
review-trigger: roster/dispatch change
-->

# Agent Registry Protocol

The agent catalog (`~/.claude/agent-catalog.json`) is a machine-generated
snapshot of the installed agent roster.  It is the source of truth for
name-existence validation by framework consumers.

## Schema

```json
{
  "version": "1.0.0",
  "generated_at": "2026-06-10T12:00:00Z",
  "agents": [
    {
      "name": "aidev-adversarial-auditor",
      "family": "aidev",
      "one_line": "Pressure-test an AI-agent or framework change by actively looking for ways it fails"
    }
  ]
}
```

Fields:
- `version` — schema version string, currently `"1.0.0"`.
- `generated_at` — ISO 8601 UTC timestamp of the last generation run.
- `agents` — array sorted by `name`.
  - `name` — agent name (matches the `name:` frontmatter field in `agents/<name>.md`).
  - `family` — prefix before the first `-` in the name (e.g., `"aidev"`, `"dev"`, `"ops"`).
  - `one_line` — first sentence of the agent's `description:` frontmatter field,
    truncated at 160 chars.

## Generator

`installer-assets/gen-agent-catalog.py` parses `agents/*.md` YAML frontmatter
(flat line-based parse — frontmatter is always flat) and writes the catalog.

```
python3 installer-assets/gen-agent-catalog.py <agents-dir> <output-path>
```

Exits nonzero with a descriptive message if the agents dir is absent or empty.
Pure stdlib — no S.A.G.E. package import required; safe to run before `pip install`.

## Regenerate on install

`install.sh` (and `install.ps1`) regenerate the catalog on every run by calling
the generator.  The agents roster (`agents/*.md`) is the source of truth;
the catalog is generated state and must not be hand-edited.  On generator
failure, the installer warns and continues — a stale catalog is better than a
failed install.

## Consumers

- `audit-pairing-lookup` — validates agent names referenced in audit reports
  against the catalog (§3 of the skill).
- `aidev-agent-manager` — uses the catalog as the authoritative list of
  installed agents for dispatch and roster queries.

---

<!-- §2-§6 below: the live dispatch/registry protocol, merged 2026-06-10 from the
     original design doc (R-14 single-home merge, Master Run Stage 3b). -->

## 2. Active roster schema — `<repo>/.claude/active-roster.json`

Per-project file, written only by `aidev-agent-manager`. Read by the orchestrator on every dispatch.

```json
{
  "version": "1.0.0",
  "project_path": "/Users/you/code/some-project",
  "detected_types": ["python", "github-project"],
  "first_detected_at": "2026-05-26T00:00:00Z",
  "last_audited": "2026-05-26T00:00:00Z",
  "active_agents": [
    {
      "name": "aidev-visionary",
      "family": "aidev",
      "added_at": "2026-05-26T00:00:00Z",
      "trigger_evidence": "always_on",
      "source": "detect-project"
    },
    {
      "name": "dev-python-reviewer",
      "family": "dev",
      "added_at": "2026-05-26T00:00:00Z",
      "trigger_evidence": "pyproject.toml present at <repo>/pyproject.toml",
      "source": "detect-project"
    },
    {
      "name": "gh-pr-reviewer",
      "family": "gh",
      "added_at": "2026-05-26T00:00:00Z",
      "trigger_evidence": ".github/workflows/ directory present",
      "source": "detect-project"
    }
  ],
  "available_but_inactive": [
    {
      "name": "dev-rust-reviewer",
      "family": "dev",
      "removed_at": "2026-04-15T00:00:00Z",
      "reason": "Rust crate dropped during project pivot to pure-Python"
    }
  ],
  "miss_log": [
    {
      "dispatch_id": "d-2026-05-26-001",
      "unmet_intent": "review M language query performance",
      "result": "ADD",
      "agent_added": "data-power-query-developer",
      "timestamp": "2026-05-26T00:00:00Z"
    }
  ],
  "notes": "Power Query support added on first M file appearance; .github/workflows/ added the gh-* family."
}
```

### Field semantics

- **`detected_types`** — project-type slugs that fired during detection. Drives which catalog agents get added.
- **`active_agents`** — the dispatch surface the orchestrator routes against.
- **`available_but_inactive`** — previously-active agents removed during reconciliation. Kept for history; not dispatched.
- **`miss_log`** — append-only record of `check-miss` operations. Helpful for the user to see what intents have driven roster expansion.
- **`source`** — `detect-project` (initial scan), `check-miss` (added during dispatch), or `explicit` (user requested).

---

## 3. Project-type detection rules

Project types are slugs the agent-manager derives from on-disk file patterns and manifest contents. Detection is deterministic — no LLM reasoning at this step. The flat catalog schema (`name`/`family`/`one_line`) carries no `file_patterns` or `project_type_triggers` fields; detection evidence and agent activation are self-contained in this section (ADR-0096).

| Project type slug | Trigger evidence |
|---|---|
| `python` | `pyproject.toml` OR `setup.py` OR `requirements.txt` OR ≥1 `*.py` file |
| `typescript` | `tsconfig.json` OR (`package.json` with `"typescript"` in deps) |
| `javascript` | `package.json` (without TypeScript) OR ≥1 `*.js` file outside `node_modules/` |
| `rust` | `Cargo.toml` |
| `go` | `go.mod` |
| `power-query` | ≥1 `*.pq` file OR M code pattern in `*.xlsx`/`*.xlsm` |
| `vba` | ≥1 `*.bas` / `*.cls` / `*.frm` file OR `*.xlsm` file |
| `excel-workbook` | ≥1 `*.xlsx` / `*.xlsm` file |
| `github-project` | `.github/workflows/` directory OR `.github/CODEOWNERS` |
| `home-assistant` | `configuration.yaml` AND (`automations.yaml` OR `scripts.yaml`) at repo root |
| `notion-integration` | `notion.config.json` OR mention of Notion API in dependencies |
| `finance-workbook` | `*.xlsx`/`*.xlsm` AND a directory matching `finance/` OR `accounting/` OR `ledger/` OR `bookkeeping/` OR `budget/` (case-insensitive) |
| `ai-dev-framework` | `agents/` directory AND `skills/` directory AND `~/.claude/CLAUDE.md` present |
| `docker-project` | `Dockerfile` OR `docker-compose.yml` |
| `terraform-project` | ≥1 `*.tf` file |

Multiple types can fire concurrently — a repo with `pyproject.toml` AND `.github/workflows/` is both `python` and `github-project`. The agent-manager unions the activations.

### Type→agent mapping

For each detected type slug from the evidence table above, the agent-manager activates the agents listed here. Row keys are exact slugs from the detection evidence table; no slug is invented. Always-on agents (§4) are not listed here — they activate unconditionally. The `ai-dev-framework` type activates the `aidev-*` family, which is already always-on; no separate row is needed.

| Type slug | Agents activated |
|---|---|
| `python` | `dev-python-reviewer` |
| `typescript` | `dev-typescript-reviewer` |
| `rust` | `dev-rust-reviewer`, `dev-build-error-resolver-rust` |
| `go` | `dev-go-reviewer`, `dev-build-error-resolver-go` |
| `power-query` | `data-power-query-developer` |
| `vba` | `dev-vba-reviewer`, `data-vba-developer` |
| `excel-workbook` | `data-excel-architect`, `data-pivot-architect`, `data-cleaner` |
| `finance-workbook` | `fin-reconciler`, `fin-transaction-categorizer` |
| `github-project` | `gh-workflow-author`, `gh-pr-reviewer` |
| `docker-project` | `ops-deployment-runner` |

The `javascript`, `home-assistant`, `notion-integration`, and `terraform-project` slugs have no specialist agents in the current roster; `check-miss` will return `NO_CATALOG_MATCH` for intents that require one — route to `aidev-agent-creator`. This table is the single source of truth for deterministic activation; the catalog carries no `file_patterns` or `project_type_triggers` fields (ADR-0096). A drift test (`tests/test_type_agent_mapping.py`) asserts every agent name here resolves to a real `agents/<name>.md`.

---

## 4. Orchestrator integration protocol

### Dispatch flow

```
User intent received
  ↓
Orchestrator parses intent, identifies semantic class (review / implement / etc.)
  ↓
Orchestrator reads <repo>/.claude/active-roster.json
  ↓
Match in active_agents?
  ├─ Yes → dispatch agent, complete
  └─ No  → dispatch aidev-agent-manager with operation=check-miss
              ↓
            Manager returns one of:
              ├─ ADD <name>          → orchestrator dispatches the now-active agent
              ├─ NO_CATALOG_MATCH    → orchestrator dispatches aidev-agent-creator
              └─ CIRCUIT_BREAK       → orchestrator escalates to user
```

### Session-start protocol

On wake-up (per `aidev-keeper.wake-up` for S.A.G.E. projects, or session start for others):

1. Orchestrator checks `<repo>/.claude/active-roster.json` existence.
2. If absent: dispatch `aidev-agent-manager` with `operation: detect-project`.
3. If present and `last_audited` is older than 30 days: dispatch `operation: refresh`.
4. Otherwise: read the active roster and proceed.

### Dispatch-ID semantics

Each user request generates a `dispatch_id` (e.g., `d-<YYYY-MM-DD>-<NNN>`). The orchestrator passes this on every `check-miss` invocation related to that request. The agent-manager tracks per-dispatch-ID invocation counts to enforce the circuit breaker (max 2 invocations per dispatch_id).

### Always-on agents are not in active-roster.json by default

To keep active-roster.json focused on project-specific decisions, always-on agents are activated implicitly (hardcoded). The catalog carries no `always_on` field (ADR-0096); the always-on set is defined here and in `aidev-agent-manager`. The orchestrator routes to them without checking the roster file. Active-roster.json contains only agents whose activation depended on project type — the interesting, project-specific subset.

The always-on set is: the full `aidev-*` family, `dev-code-implementer`, `dev-code-reviewer`, `ops-release-readiness`. `aidev-agent-manager` is implicitly always reachable because it must be invokable before the active roster exists.

---

## 5. Failure modes and mitigations

### Dispatch loops
**Risk:** Orchestrator misses → manager adds → orchestrator retries → still misses → manager called again → infinite.
**Mitigation:** Circuit breaker. Manager tracks `dispatch_attempt_count` per `dispatch_id`. Hard cap of 2. Beyond that, return `CIRCUIT_BREAK` and the orchestrator escalates to the user.

### Stale active roster
**Risk:** Project shape changes (TypeScript frontend added to a Python repo), active roster doesn't reflect it, orchestrator misroutes or misses.
**Mitigation:** (a) The agent-manager's `list-active` operation runs a cheap drift check on every read and flags drift to the orchestrator. (b) Scheduled `refresh` operation (monthly cadence, or on user signal "we restructured").

### Phantom catalog entries
**Risk:** Manager adds an agent name that isn't in the catalog (typo, hallucination), orchestrator dispatches to a non-existent agent.
**Mitigation:** Every `add-agent` operation validates against `~/.claude/agent-catalog.json` by exact `name:` match. Validation failure = refuse the add and return error.

### Race condition on roster file
**Risk:** Two parallel orchestrator dispatches both miss, both call manager, both write — last-write-wins corrupts the file.
**Mitigation:** Single-writer enforcement is the structural answer. If race conditions are possible in the orchestrator (parallel dispatch model), wrap the roster file with a file lock during the manager's read-modify-write cycle. Bash `flock` works if the platform supports it; otherwise a `.lock` sentinel file with timestamp.

### No-catalog-match with valid intent
**Risk:** User asks for something genuinely outside the catalog (e.g., "review my Home Assistant YAML" before `home-` family exists), manager returns `NO_CATALOG_MATCH`, user gets confused.
**Mitigation:** Manager returns the semantic class it identified (e.g., "smart-home-yaml-review") so the orchestrator can frame the escalation: "No catalog agent for smart-home-yaml-review. Dispatch `aidev-agent-creator` to propose one?"

### Detection misfiring on stray files
**Risk:** A `*.py` file appears for some reason (e.g., a vendored script) but the project isn't really Python — manager activates `dev-python-reviewer` unnecessarily.
**Mitigation:** File patterns are graded — manifests (`pyproject.toml`) score higher than loose files (`*.py`). The agent-manager's detection rules require either a manifest match OR ≥3 loose-file matches to activate a language type. (Implementation: per-pattern weights in the catalog, but simple enough that the §3 table above can encode the rule directly.)

### Manager hallucination on `check-miss`
**Risk:** The one LLM-reasoning step (`check-miss` semantic mapping) hallucinates a catalog entry.
**Mitigation:** The required CoT chain in `aidev-agent-manager.md` forces the manager to write the reasoning chain *before* the result. Combined with the strict catalog validation on add, hallucinations get caught at the validation step rather than reaching the orchestrator.

---

## 6. Bootstrap sequence

For a project that has no `~/.claude/agent-catalog.json` yet (first installation), the bootstrap is:

1. User installs the framework. `install.sh` / `install.ps1` GENERATE `~/.claude/agent-catalog.json` from the shipped roster (`agents/*.md` frontmatter) via `installer-assets/gen-agent-catalog.py`, on every install run — the roster is the source of truth and the catalog is regenerated state. (The original design seeded an empty stub to be populated by `aidev-agent-manager` on first `detect-project`; that population step never shipped, leaving the catalog permanently empty and suppressing every `audit-pairing-lookup` drift check — superseded by install-time generation. Catalog schema and generation semantics: `docs/specs/agent-registry-protocol.md`.)
2. User opens a project in any repo. Orchestrator detects `<repo>/.claude/active-roster.json` is absent.
3. Orchestrator dispatches `aidev-agent-manager.detect-project`.
4. Manager scans, derives detected types, computes initial active roster (always-on + triggered), writes the file.
5. From that point on, the registry protocol runs normally.

Catalog updates (adding a new agent type, retiring an old one) go through `aidev-planner` → `aidev-code-implementer` → audit per the centralized pairing matrix (the orchestrator looks up the appropriate auditors via the `audit-pairing-lookup` skill against `docs/specs/audit-pairing-matrix.md`). The catalog is part of the roster's tested surface.

---

## 7. Open design choices

These need an ADR before implementation lands:

1. **Per-machine catalog vs per-user catalog.** If you sync `~/.claude/` across machines (dotfiles repo), the catalog travels. If not, each machine has its own. Recommend dotfiles-tracking.
2. **Catalog versioning.** Major-version compatibility lets older active-rosters survive minor catalog updates. Decide on a `min_catalog_version` field per active roster, with the manager refusing to operate if the catalog is older than expected.
3. **Always-on vs always-active.** The current design has always-on agents activated implicitly without appearing in active-roster.json. Alternative: always-on agents appear in active-roster.json with `source: always_on`. The implicit version keeps the file leaner; the explicit version is more discoverable. Recommend implicit; document this clearly in the orchestrator's routing code.
4. **Circuit breaker reset.** The 2-invocation cap is per `dispatch_id`. After user re-asks (new `dispatch_id`), the count resets. Confirm this matches your orchestrator's intent-grouping behavior.
