---
name: gh-workflow-discipline
description: "Use when authoring or auditing GitHub Actions workflow YAML as gh-workflow-author — seven runtime trees: permissions deny-by-default + exploit-chain reasoning, third-party action SHA-pinning, secrets exposure prevention, matrix design, caching patterns, trigger surface safety, job-graph dependencies. Triggers on 'author this workflow', 'audit this workflow YAML', 'pin this third-party action'. Do not use for PR review or non-workflow CI config."
---

# GitHub Actions Workflow Discipline

This skill encodes seven runtime decision trees that `gh-workflow-author` consults at methodology step 5 to classify permissions scopes via exploit-chain reasoning, verify third-party action SHA-pinning, enforce secrets exposure prevention, design matrix builds, design caching keys, classify workflow trigger surface safety, and reason about job-graph dependencies — in both AUTHOR and AUDIT modes.

This skill is consumed exclusively by `gh-workflow-author` (implementer-shaped, Phase D agent #7). It does not overlap with `gh-pr-review-discipline` (PR-process review lane, consumed by `gh-pr-reviewer` per `docs/specs/audit-pairing-matrix.md` line 30) or with `verification-before-completion` (pre-completion claim/output check). ADR-0030 (`.development/decisions/0030-gh-workflow-author-identifying-info-exemption.md`) grants `gh-workflow-author` a case-a exemption for GitHub Actions schema, `gh workflow` CLI, and GitHub-Actions-specific concept references; this skill inherits the exemption through its consuming agent. ADR-0029 (`.development/decisions/0029-gh-pr-reviewer-identifying-info-exemption.md`) is the sibling case-a precedent establishing the per-agent ADR requirement; it applies narrowly to `gh-pr-reviewer` and does not extend to this skill's consuming agent. The dual-role AUTHOR + AUDIT binding follows the `gh-workflow-diff` row at `docs/specs/audit-pairing-matrix.md` line 31 — `gh-workflow-author` runs in both author-mode (writing new workflow YAML) and audit-mode (reviewing a workflow diff as primary auditor on the `gh-workflow-diff` row).

The seven trees are logic-heavy per `rules/ai-dev-conventions.md` CoT injection classification: exploit-chain inference, severity scoring (0–100 per finding), and classification under conflicting rules. The higher-severity-wins rule applies across all trees — this skill never softens a borderline call to avoid a `REQUEST_CHANGES` verdict.

## When this skill binds

Fire this skill when any of these are true:

- You are authoring a GitHub Actions workflow and selecting `permissions:` scopes.
- You are auditing a workflow YAML diff for security or correctness.
- You are asked "is this permissions: block deny-by-default?"
- You are pinning or verifying a third-party action reference by SHA.
- You are asked "is this secret being echoed to stdout?"
- You are designing the matrix strategy for a CI job.
- You are asked "is this pull_request_target safe?"
- You are asked "what concurrency group should this deploy use?"

Do NOT fire this skill for:

- PR-process review — `gh-pr-review-discipline` handles nit/constructive/blocker classification on PRs.
- CI verification on a PR before approving — `gh-pr-review-discipline` (CI verification on a PR audit, not workflow YAML authoring).
- Pre-completion claim verification — `verification-before-completion` governs the overall procedure.
- Root-cause inference on a failed workflow run — `systematic-debugging` carries the chain shape; this skill supplies the workflow-domain rule-set.
- SOP body audit — `biz-sop-discipline`.
- Auditor-pairing resolution — `audit-pairing-lookup` reads the matrix.
- `/codex:*` dispatch reflex decisions — `codex-routing-reflex` is orchestrator-consumed per ADR-0028 and is out of lane here.
- Non-GitHub CI config (Travis, CircleCI, Jenkins) — not in lane; this skill is GitHub-Actions-specific.
- Excel transform debugging — `m-language-discipline`.

## Element A — Permissions deny-by-default + exploit-chain reasoning

Every workflow declares a top-level or job-level `permissions:` block. Default is deny-by-default — start with `permissions: {}` (empty block grants nothing) and add minimum scopes per step requirement. For each granted scope, emit the exploit-chain rationale in the `@@WORKFLOW-RATIONALE` block (author-mode) or in the `cot_2_standard_expectation` field (audit-mode). Granting a scope without the exploit-chain is a structural violation.

**Scope decision tree (walk in order; first match naming a required scope wins):**

- `contents` — `read` for `actions/checkout`; `write` for push, tag, or release. Exploit-chain (write): attacker controls repo history; force-push to default branch; rewrite published tags.
- `id-token` — `write` for OIDC token requests (cloud federated identity, sigstore/cosign). Exploit-chain (write): attacker exchanges OIDC token at third-party cloud's federation endpoint for cloud credentials.
- `pull-requests` — `read` for PR metadata; `write` for PR comments, approval, or state mutation. Exploit-chain (write): attacker approves PRs programmatically, bypassing required-reviewer constraints.
- `actions` — `read` for other workflow runs or artifacts; `write` for cancel, re-run, or delete-artifacts. Exploit-chain (write): attacker re-runs prior workflows with new code, deletes audit-trail artifacts.
- `checks` — `read` or `write` for the check-run API. Exploit-chain (write): attacker creates fake green check-runs satisfying required-checks branch protection.
- `deployments` — `read` or `write` for the deployment API. Exploit-chain (write): attacker creates fake deployment records, manipulates environment state.
- `issues` — `read` or `write` for the issue API. Exploit-chain (write): attacker closes security-tracker issues, impersonates maintainers in comments.
- `packages` — `read` or `write` for GitHub Packages publish. Exploit-chain (write): attacker publishes malicious package versions to downstream users.
- `statuses` — `read` or `write` for the commit-status API (legacy). Exploit-chain: same as `checks` on the legacy API surface.
- `security-events` — `read` or `write` for code-scanning alerts. Exploit-chain (write): attacker dismisses code-scanning findings, hides vulnerabilities.
- `attestations` — `read` or `write` for artifact attestations. Exploit-chain (write): attacker creates fake provenance attestations on malicious artifacts.
- `models` — `read` or `write` for the GitHub Models API. Exploit-chain (write): attacker creates or modifies model endpoints, intercepts inference requests.
- `pages` — `read` or `write` for GitHub Pages deploy. Exploit-chain (write): attacker overwrites the public Pages site.
- `discussions` — `read` or `write` for the Discussions API. Exploit-chain (write): attacker posts impersonation content, closes threads.
- `repository-projects` — `read` or `write` for the repository Projects API. Exploit-chain (write): attacker creates or modifies project columns and cards, manipulates project state used for release planning.

**Canonical permissions scope enum:** `read | write | none`

Forbidden scope synonyms: `ro`, `rw`, `full`, `all`, `*`

## Element B — Third-party action SHA-pinning protocol

Every `uses: <org>/<action>@<ref>` to any external action (owner not equal to the current repo owner) declares `<ref>` as a 40-character lowercase hex commit SHA. The `actions/*` GitHub-maintained namespace is included in the SHA-pin requirement on defense-in-depth grounds — even when the action's maintainer key is uncompromised and the org is GitHub itself, the tag-resolution surface is the same for any external repo (the tag is a mutable pointer maintained by the action team's release tooling), so the SHA-pin invariant ("the workflow runs the exact commit the workflow author reviewed") requires SHA-pinning regardless of namespace. Only first-party actions (same repository as the workflow file) are permitted to use tag refs, on the basis that the workflow author already controls the same repo and the same review surface covers both files.

**Refused ref shapes:**

- Tag refs (`@v4`, `@v4.0.1`) — refused: tags are mutable on any third-party repo, including the `actions/*` namespace.
- Branch refs (`@main`, `@master`, `@HEAD`) — refused: branches mutate on every push; `HEAD` resolves to whatever the current default-branch tip is.
- Qualified refs (`@refs/heads/main`, `@refs/tags/v4`) — refused: same mutation surface as bare branch or tag refs.
- Short SHA (`@a1b2c3d`, any hex string under 40 characters) — refused: collision-prone in SHA-1 space, not enforced by Actions.
- Non-hex 40-character ref (`@xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` or any 40-char string containing non-`[0-9a-f]` characters) — refused: not a valid commit SHA; treated as an unknown ref by Actions and resolves to a tag or branch of that name if one exists.
- Empty ref (`@` with no value, or whitespace between `@` and the next token) — refused: parse error or ambiguous resolution.
- Missing ref (`actions/checkout` with no `@`) — refused: YAML parse error.

**SHA discovery method:**

- For a tag-claimed pin: `git ls-remote --tags <repo>` locates the commit the tag points to. Cite the discovery command in `@@WORKFLOW-RATIONALE`.
- For a release-tag claim: the upstream release page's "View commit" link reveals the SHA.

**Audit escalation rule:** if a prior audit logged tag-pinning at severity ≥80 on the same `uses:` line and the subsequent commit did not remediate, the audit lane escalates severity by 10 points on the new audit.

## Element C — Secrets exposure prevention

`secrets.*` interpolation inside any string-evaluating shell construct in a `run:` body is a blocking finding. Safe consumption: env-var injection at the step level, then passing the env var by name to the consuming binary.

**Banned constructs:**

- `echo "${{ secrets.X }}"` — interpolated into echo stdout.
- `printf '%s' "${{ secrets.X }}"` — printf surface.
- `cat <<< "${{ secrets.X }}"` — herestring.
- `tee /file <<< "${{ secrets.X }}"` — herestring plus tee.
- `curl -d "${{ secrets.X }}" ...` — interpolated into a network argument visible in `ps`.
- `eval "command ${{ secrets.X }}"` — eval double-expansion.
- `set -x` followed by any line touching `${{ secrets.X }}` — trace mode prints the substituted secret.
- `VAR="${{ secrets.X }}"` followed by any later command that interpolates `"$VAR"` into a shell-string context (echo, printf, cat, curl, grep, eval, herestring, set -x trace) — variable-assignment-then-interpolate is the same leak class as direct interpolation, one step removed.
- `grep "${{ secrets.X }}" <file>` (or `awk`, `sed`, `cut`, any pattern-matcher consuming the secret as a pattern argument) — the secret appears in process args visible to `ps` and to other unprivileged processes.
- `${VAR:-${{ secrets.X }}}`, `${VAR:=${{ secrets.X }}}`, or any default-value substitution chain that places `secrets.X` into a shell-string expansion — the substituted value enters the same expansion context as the variable it defaults from.
- `[[ "${{ secrets.X }}" == "$expected" ]]` or any test expression with `set -x` trace enabled — trace mode prints the test line including the substituted secret.
- `${{ secrets.X }}` interpolated into a script-file body written via heredoc or `cat > file <<EOF` — the secret lands on disk in the script body, leaking through file inspection.

**Safe pattern:**

```yaml
- run: my-tool --token "$MY_TOKEN"
  env:
    MY_TOKEN: ${{ secrets.MY_TOKEN }}
```

Step-level `env:` block injects the secret as an env var; the `run:` body passes the env var by name to the consuming binary, never expanding it into a shell string.

**Audit lane re-grep:** audit-mode re-greps the diff for banned constructs before emitting the verdict. Mechanical re-grep, verbatim token matching.

## Element D — Matrix build design

Matrix builds fan out independent test cells. A matrix exceeding 20 cells without explicit rationale citing a named risk is refused (severity 60–79, constructive finding).

**Decision tree:**

- When to use: cross-version, cross-platform, or cross-config testing where each cell is independent.
- When NOT to use: cells with cross-cell ordering dependencies (matrix cells run in parallel; sequencing is not guaranteed); cells sharing mutable state (cache-poisoning vector per Element E).

**Inclusion/exclusion grammar:**

- `matrix.include` — adds specific full cells independent of the Cartesian product.
- `strategy.matrix.<key>: [a, b]` combined with `include: [{<key>: c, extra: x}]` — extends the product by adding `extra: x` to specific cells.
- `matrix.exclude` — removes specific cells from the product.

**fail-fast consideration:** `fail-fast: true` (default) cancels remaining cells when one fails — appropriate for "must work on all" axes. `fail-fast: false` keeps all cells running — appropriate for "diagnostic across cells" axes where one failure is independent signal.

**Cell count limit:** 20 as an **advisory floor**. The consuming agent searches the destination repo for a published convention in this order before applying the floor:

1. `.development/decisions/*.md` — grep for `matrix cell` or `matrix size` headings; cite the ADR slug + filename if matched.
2. `CONTRIBUTING.md` — search for a `## Workflow design` or `## CI conventions` section; cite the heading if matched.
3. `docs/workflows/README.md` or `docs/workflows/conventions.md` — cite the file path if present.

If any of the three sources yields a numeric limit, cite the convention (path + heading) in `@@WORKFLOW-RATIONALE` and apply the repo's limit. If none yields a numeric limit, apply the advisory floor of 20 and tag `@@WORKFLOW-RATIONALE` with `(advisory floor — no repo convention cited at .development/decisions/*.md, CONTRIBUTING.md, or docs/workflows/)`. Matrix exceeding the applied limit without an explicit rationale citing a named risk is a constructive finding (severity 60–79).

## Element E — Caching patterns

`actions/cache@<sha>` keys are composed deterministically. A mutable cache-key input is a cache-poisoning vector.

**Key composition discipline (walk in order; first match wins. When a workflow's cache spans multiple classes — e.g., a Docker build that also installs apt packages inside the image — concatenate the matching templates' key segments into a single composite key; each template's segments preserve their ordering and the templates separate with a `--` literal):**

*Dependency-manager cache (lockfile present):*

- Lockfile hash — `hashFiles('**/uv.lock')` (or the appropriate lockfile) for dependency-identity invariance.
- OS — `runner.os` for platform partitioning so a Linux cache does not poison a macOS run.
- Dep-manager version — pin the manager version contributing to the key (e.g., `uv-0.5.x`) so a manager upgrade triggers a fresh cache.
- Composition example: `key: ${{ runner.os }}-uv-0.5-${{ hashFiles('**/uv.lock') }}` — triple-component key.

*Docker layer cache (no lockfile; container-build context):*

- Dockerfile hash — `hashFiles('Dockerfile', '.dockerignore')` for build-instruction invariance.
- Base image SHA — pin the base image by digest (e.g., `python@sha256:...`) and include the digest substring in the key for base-image invariance.
- OS — `runner.os` per platform partitioning.
- Composition example: `key: ${{ runner.os }}-docker-${{ hashFiles('Dockerfile') }}-base-py3sha256abc` — three-component key with base-image-SHA literal substring.

*System-package cache (apt, yum, brew; no lockfile):*

- Package list hash — `hashFiles('.github/apt-packages.txt')` (or equivalent file naming the package set) for package-set invariance.
- OS + OS version — `runner.os` plus the runner's image tag (e.g., `ubuntu-22.04`, available via `${{ matrix.runner }}` or hardcoded) — distro upgrades change available packages.
- Composition example: `key: ubuntu-22.04-apt-${{ hashFiles('.github/apt-packages.txt') }}` — two-component key with OS-version literal.

*Generic artifact cache (compiled binaries, generated assets, no lockfile and no package list):*

- Source-input hash — `hashFiles(<glob of every input the artifact derives from>)`. The glob must enumerate every input, not approximate.
- Tooling version — pin every tool whose output contributes to the artifact, in the key.
- OS — `runner.os`.
- Composition example: `key: ${{ runner.os }}-build-toolchain-1.2.3-${{ hashFiles('src/**/*.rs', 'Cargo.toml', 'rust-toolchain.toml') }}` — three-component key with toolchain version literal.

**restore-keys rule:** `restore-keys:` falls back to less-specific prefixes on key miss. Read-only fallbacks — do not overwrite the cache at the more-specific key. Acceptable restore-keys for the example above: `${{ runner.os }}-uv-0.5-` and `${{ runner.os }}-uv-`.

**Poison-cache vector:** any mutable input in the key (branch name, PR number, caller-controlled env var) lets a malicious PR populate the cache that the main branch later reads. Banned: `key: ${{ github.head_ref }}-...`, `key: ${{ inputs.user_supplied }}-...`. Mitigation: scope cache to the base ref, not the head ref; do not use caller-controlled inputs in the key.

## Element F — Workflow trigger surface safety

The `on:` block declares the trigger surface. Some triggers run with a restricted token in the PR-fork context; others run with a full token in the base-repo context. High-risk trigger: `pull_request_target`.

**Trigger table:**

| Trigger | Context | Safety note |
|---|---|---|
| `on: push` | Repo context, default `GITHUB_TOKEN` | Safe by default for the repo's own commits |
| `on: pull_request` | PR-fork context, restricted `GITHUB_TOKEN` (read-only on base repo) | Safe for read-only workflows, including running tests on PR code |
| `on: pull_request_target` | Base-repo context, FULL repo `GITHUB_TOKEN` | Canonical pwn-request vector — see rule below |
| `on: workflow_run` | Base-repo context, FULL repo `GITHUB_TOKEN` (when triggered by upstream `pull_request` workflow) | Canonical artifact-supply-chain pwn vector — see rule below |
| `on: schedule` | Cron-triggered | Design idempotently; schedules run late under high Actions load |
| `on: workflow_dispatch` | Manual trigger | Validate `inputs:` if declared |
| `on: workflow_call` | Reusable workflow | Caller's `permissions:` is the called workflow's ceiling — design downstream callers explicitly |

**pull_request_target rule:** `pull_request_target` runs in the base-repo context with the full repo `GITHUB_TOKEN`. If the workflow checks out the PR head ref and executes any code from that ref (build scripts, test scripts, dep installs with install hooks), attacker code from the PR runs with the base repo's full token. This is a blocker (severity 90+) absent an explicit threat-model chain.

**Emission rule (pull_request_target):** any `pull_request_target` trigger in the diff requires a verbatim line in `@@WORKFLOW-RATIONALE` (author-mode) or a finding (audit-mode) stating: "pull_request_target chosen because `<stated reason>`; checkout of PR head ref: `<yes | no>`; if yes, threat model: `<verbatim chain>`". A `pull_request_target` with `actions/checkout: ref: ${{ github.event.pull_request.head.sha }}` is a blocker (severity 90+) absent the threat-model chain.

**workflow_run rule:** `on: workflow_run` triggers fire after another named workflow completes. When the upstream workflow is `pull_request`-triggered (i.e., ran in restricted PR-fork context potentially producing artifacts under attacker control), the downstream `workflow_run` executes in the BASE-REPO context with FULL `GITHUB_TOKEN`. The canonical artifact-supply-chain pwn pattern: workflow_A runs on `pull_request` (restricted), produces artifacts the attacker can shape (test output, build artifacts, cached dirs); workflow_B runs on `workflow_run: workflows: [A]` (full token), downloads workflow_A's artifacts via `actions/download-artifact`, and executes them (runs the binary, parses the JSON, sources the script). Attacker controls the artifact content → executes in workflow_B's full-token context.

**Emission rule (workflow_run):** any `on: workflow_run` trigger in the diff whose upstream workflow includes `pull_request` (or `pull_request_target`) in its `on:` block requires a verbatim line in `@@WORKFLOW-RATIONALE` (author-mode) or a finding (audit-mode) stating: "workflow_run trigger from upstream <name>; upstream trigger includes pull_request: <yes | no>; artifact handling: <list of download-artifact steps and what each artifact is used for>; threat model: <verbatim chain explaining why the artifact handling is safe under attacker control of artifact content>". A `workflow_run` downstream of a `pull_request` upstream that executes downloaded artifacts (sources a script, runs a binary, parses JSON with an interpreter that auto-executes) is a blocker (severity 90+) absent the threat-model chain.

## Element G — Job-graph dependencies

`needs:` builds the job dependency graph; `if:` adds conditional gates; `concurrency:` bounds parallel runs.

**needs: rules:**

- `needs: [a, b]` — this job runs after both a and b succeed. If either fails, this job is skipped (the failure propagates as a skip, not a failure of this job).
- Cycle detection: GitHub Actions parses the `needs:` graph and refuses cyclic declarations at parse time. Audit-mode re-checks for cycles when `needs:` is modified.
- Failure propagation: a failed `needs:` ancestor skips this job. Use `if: always()` to run regardless of upstream failure, or `if: failure()` to run only on upstream failure.

**if: rules:**

- Job-level `if:` gates the entire job (skipped if false).
- Step-level `if:` gates the step (skipped if false; other steps continue).
- Security implication: `if: github.actor != 'dependabot[bot]'` reads `github.actor`, which is attacker-controllable in fork PRs (attacker can rename their account). Do not use `github.actor` as a security boundary. Use `github.event.pull_request.head.repo.fork` (boolean) or `github.repository_owner` for first-party-only gates.

**concurrency: rules:**

- `concurrency.group:` keys group concurrent runs. The same group key causes runs to queue (or cancel if cancel-in-progress is set).
- `concurrency.cancel-in-progress: true` — a new run cancels the prior run in the same group. Cancel-in-progress race vector: two pushes within seconds; the second push's workflow cancels the first push's workflow mid-deploy, leaving a partially-deployed state. Mitigation: `cancel-in-progress: false` for deploy jobs; reserve cancel-in-progress for read-only verification jobs.

## Element H — Canonical banned-vague-fill list

The consuming agent re-greps its own emitted output verbatim against this list at methodology step 4 (author-mode rationale chain) and step 7 (audit-mode verification). The list is canonical — do not paraphrase — so the re-grep is mechanical. Adapted verbatim from `gh-pr-review-discipline` Element D.

**Banned hedge tokens (verbatim):**

- `might`
- `may`
- `maybe`
- `perhaps`
- `possibly`
- `could potentially`
- `seems like`
- `seems to`
- `appears to`
- `looks like`
- `I think`
- `I believe`
- `IMO`
- `in my opinion`
- `kind of`
- `sort of`
- `somewhat`
- `a bit`
- `rather`
- `probably`
- `likely`
- `try to`
- `attempt to`
- `arguably`
- `in theory`
- `tends to`
- `would suggest`
- `in some cases`
- `ostensibly`
- `presumably`
- `feasibly`
- `could be argued`

## Element I — Canonical enum values and category-subset convention

**Verdict enum:** `APPROVE | REQUEST_CHANGES | REJECT | HOLD | ABORT`

**Category enum (canonical subset):** `test | other | governance | manifest`

Post-sweep canonicalization per Phase D handoff §5 lesson #1 and `gh-pr-review-discipline` Element D precedent. The wider 9-value `docs/specs/verdict-schema.md` enum carries `security`, but the canonical subset does not. Security findings emit `category: other` AND prefix the summary with the literal `[security]`. Example: `[security] secrets.MY_TOKEN echoed at .github/workflows/deploy.yml:42 — shell-string interpolation`.

**Permissions scope enum:** `read | write | none`

Forbidden permissions synonyms: `ro`, `rw`, `full`, `all`, `*`

**Severity type:** integer 0–100

Forbidden severity synonyms: `low`, `medium`, `high`, `minor`, `major`, `trivial`, `significant`

## Element J — HOLD/ABORT emission criteria

**HOLD criteria (transient — re-dispatch when the cause condition clears):**

- Upstream third-party action repo HTTP 503 during SHA verification — re-dispatch on recovery.
- Lockfile in cache-key composition not yet committed (workflow author phase) — re-dispatch when the lockfile lands.
- Required `@@WORKFLOW-RATIONALE` input (e.g., stated reason for `pull_request_target`) unverifiable but transient (author composing rationale in parallel turn) — re-dispatch when rationale lands.

**ABORT criteria (structural — re-dispatch will not succeed without changing the brief):**

- Workflow path unreachable (refused: target file does not fall under `.github/workflows/`).
- Audit-mode dispatched on a workflow the same orchestrator turn authored (self-audit drift; refuse to audit own output without an intervening turn).
- Workflow YAML parse-error (refused: cannot reason about a file that does not parse as YAML).

**HOLD single-finding discipline (per `docs/specs/verdict-schema.md` line 63):** `HOLD` requires exactly one finding describing the missing input or gap. If multiple HOLD causes apply simultaneously, aggregate them into one finding: `HOLD causes: <comma-separated cause list>`. Any other findings discovered during the audit defer to the next dispatch round when the HOLD cause has cleared. `ABORT` carries exactly one finding (severity 100) per `docs/specs/verdict-schema.md` line 64.

## Output blocks

`gh-workflow-author` emits `@@VERDICT` in both modes and `@@WORKFLOW-RATIONALE` in author-mode only. Do not reference `@@PR-COMMENT`, `@@FINDING-SUMMARY`, or any other block shape.

**Audit verdict block (both modes, verdict-first per `docs/specs/verdict-schema.md` line 21):**

```
@@VERDICT BEGIN
verdict: <APPROVE | REQUEST_CHANGES | REJECT | HOLD | ABORT>
lane: gh-workflow-author
report: <relative-path | none>
findings: <integer>
@@FINDING 1
severity: <0-100>
file: <relative-path | n/a>
line: <integer | 0>
category: <test | other | governance | manifest>
summary: <one-line, ≤200 chars, no newlines>
@@VERDICT END
```

**Author-mode rationale block (author-mode only):**

```
@@WORKFLOW-RATIONALE BEGIN
permissions_chain: <scope>:<read|write|none> — <exploit-chain rationale per scope granted>
sha_pins: <uses: line> — <40-char SHA> — <discovery method>
pull_request_target: <yes | no> — <stated reason if yes> — checkout of PR head ref: <yes | no> — <threat model if yes>
matrix_cells: <count> — <named risk if >20>
cache_keys: <key composition> — <poison-vector assessment>
@@WORKFLOW-RATIONALE END
```

**Per-finding chain (audit-mode):** every finding carries the mandatory 4-step CoT chain inline. Steps are non-reorderable.

```
cot_1_specific_code: <file>:<line> — <≤80-char excerpt or field/job reference>
cot_2_standard_expectation: <rule or ADR> — what the diff should have done
cot_3_gap: <one-line concrete delta between expectation and actual>
cot_4_suggested_fix: <concrete diff direction, ≤2 sentences>
```

**Mandatory completeness rule:** if any of the 4 steps cannot be filled with concrete content, the finding is speculative per CLAUDE.md §4 no-fabrication and must be dropped, not softened. No-skip / no-compression rule: the 4-step chain is mandatory per finding regardless of perceived obviousness.

## When this skill PAUSEs

When the consuming agent needs to look up third-party documentation (GitHub Actions docs, third-party action READMEs, GitHub REST API docs), emit verbatim:

```
PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

`<subject>` is a verbatim placeholder filled by the consuming agent with the specific reference subject. Do not paraphrase. The `[scheduled-annotation: ...]` wrapper is MANDATORY at every emission site — any deviation breaks the future `research-docs-lookup` routing per ADR-0027 Consequences clause.

Domain-bounded WebFetch to `github.com` is permitted for workflow-cited action READMEs and release pages (per ADR-0030 case-a exemption) only when the consuming agent needs to confirm a SHA or verify a release tag. Third-party documentation references outside that bound use the PAUSE shape.

## Anti-patterns

- **Granting a `permissions:` scope without exploit-chain rationale.** Exploit-chain emission is mandatory per Element A. A scope without a chain is a structural violation.
- **Pinning a third-party action by tag, branch, or short SHA.** All three refused shapes per Element B. Tags are mutable; branches mutate on every push; short SHAs are collision-prone.
- **Echoing or printing a `secrets.*` value into a shell string in a `run:` body.** Banned constructs are enumerated in Element C. Safe pattern: step-level `env:` injection, not shell-string expansion.
- **Fanning matrix to more than 20 cells without explicit rationale citing a named risk.** The cell-count limit in Element D is deterministic, not a heuristic.
- **Composing a cache key from a mutable input (branch name, PR number, caller-supplied env var).** Cache-poisoning vector per Element E. Scope cache to base ref, not head ref.
- **Using `on: pull_request_target` with `actions/checkout` of the PR head ref absent a threat-model chain.** Blocker (severity 90+) per Element F emission rule.
- **Using `github.actor` as a security boundary in an `if:` gate.** Attacker-controllable in fork PRs per Element G.
- **Using `cancel-in-progress: true` on a deploy job.** Cancel-in-progress race vector per Element G — reserve for read-only verification jobs.
- **Softening a blocker to constructive to avoid `REQUEST_CHANGES`.** The higher-severity-wins rule is binding across all trees.
- **Skipping Element H hedge-language re-grep at step 4 or step 7.** Re-grep is mechanical (canonical list, verbatim token matching). Skipping it lets hedge tokens leak into emitted rationale or findings.
- **Paraphrasing the ADR-0027 PAUSE shape.** The scheduled-annotation wrapper is canonical verbatim. Any deviation breaks the future `research-docs-lookup` routing.
- **Emitting a finding's 4-step chain with unfilled placeholders or vague step-4 fixes.** "Clean this up", "be more careful", "consider refactoring" are hedge-language violations. A step-4 that cannot be filled concretely means the finding must be dropped.
- **Referencing block types that do not exist.** `gh-workflow-author` emits `@@VERDICT` and `@@WORKFLOW-RATIONALE` only. Do not reference `@@PR-COMMENT`, `@@FINDING-SUMMARY`, or any other block shape.
- **Embedding parenthetical opt-outs in hard rules.** An "(except when ...)" wrapper converts a hard rule to a soft rule. Hard rules in this skill carry no embedded exceptions.

## Output guidance

### Semantic guidance

- Every `@@VERDICT` block carries all findings with complete 4-step CoT chains. No field carries a vague or generic description.
- Severity is a literal integer 0–100. No symbolic levels.
- Verdict is one of the five canonical literals. No synonyms.
- Category is one of the four canonical subset values: `test | other | governance | manifest`. Security findings use `category: other` and `[security]` prefix.
- `@@WORKFLOW-RATIONALE` permissions_chain names every granted scope with its exploit-chain rationale. No scope granted without a chain.
- No hedge language in any emitted block. Re-grep against Element H's canonical list at methodology step 4 (author-mode) and step 7 (audit-mode).
- A finding with a speculative step 4 is dropped, not softened. Capability honesty per CLAUDE.md §4.
- No employer, client, project, software product (beyond GitHub Actions and `gh` per ADR-0030 case-a exemption), or internal convention names in output. Per `rules/ai-dev-conventions.md` identifying-info ban + ADR-0023 case-b.

### Formatting guidance

- `@@VERDICT BEGIN` / `@@VERDICT END` delimiters are exact verbatim — do not paraphrase field names or delimiter strings.
- `@@WORKFLOW-RATIONALE BEGIN` / `@@WORKFLOW-RATIONALE END` delimiters follow the same pattern.
- Verdict block appears first in the inline reply per `docs/specs/verdict-schema.md` line 21.
- `@@WORKFLOW-RATIONALE` appears after `@@VERDICT END` in author-mode replies.
- `findings:` field matches the actual count of `@@FINDING N` blocks exactly.

### Tool guidance

Primary tool surface under this skill:

- `Write` and `Edit` — write or modify `.github/workflows/*.yml` files. Scoped to the GitHub Actions vendor-specified filesystem path per ADR-0030.
- `gh workflow view` — inspect existing workflow runs and their outputs.
- `gh workflow run` — trigger workflow dispatch.
- `git ls-remote --tags <repo>` — resolve a tag to its commit SHA for Element B SHA discovery.
- Domain-bounded WebFetch to `github.com` for action release pages and README SHA verification (ADR-0030 case-a exemption). For any third-party documentation reference (GitHub Actions docs, non-release GitHub pages), emit the ADR-0027 PAUSE shape verbatim and stop.

**No direct Write or Edit on non-workflow files under this skill alone** — non-workflow writes route through `aidev-code-implementer`.

## When NOT to use this skill

- PR-process review → `gh-pr-review-discipline` (PR lane, distinct from workflow YAML authoring).
- Pre-completion claim verification → `verification-before-completion`.
- SOP body audit → `biz-sop-discipline`.
- Language-specific code review (M transforms) → `m-language-discipline`.
- Language-specific code review (VBA macros) → `vba-language-discipline`.
- Audit-pairing resolution → `audit-pairing-lookup` reads the matrix.
- `/codex:*` dispatch reflex decisions → `codex-routing-reflex` is orchestrator-consumed per ADR-0028; out of lane here.
- Non-GitHub-Actions CI config (Travis CI, CircleCI, Jenkins, GitLab CI) → not in lane; this skill is GitHub-Actions-specific.
- Root-cause inference for a failed workflow run → `systematic-debugging` (chain shape); this skill supplies the workflow-domain rule-set as a reference, not the debugging procedure.
- Looking up third-party documentation (GitHub Actions docs, third-party action READMEs, GitHub REST API docs) → emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]` (ADR-0027).
