---
name: gh-scaffold-discipline
description: "Use when scaffolding a new GitHub repository as gh-repo-scaffolder — license selection, project-scale heuristics, CODEOWNERS, issue/PR templates, branch protection, .gitignore, and the .github/ skeleton. Triggers: 'scaffold this new repo', 'apply branch protection to <repo>', 'select the license'. Do not use for PR review (gh-pr-review-discipline), workflow YAML (gh-workflow-discipline), app source scaffolding, deployment, or release tagging."
---

# gh-scaffold-discipline

This skill encodes the runtime decision trees and verbatim template inventory that `gh-repo-scaffolder` (Phase D agent #8) walks when initializing a new GitHub repository. The nine elements (B–J) — license selection, project-scale heuristic, CODEOWNERS structure, ISSUE_TEMPLATE shape, PULL_REQUEST_TEMPLATE shape, branch protection rule set, `.gitignore` class derivation, `.github/` skeleton tree, and template inventory — plus the shared placeholder schema (`## Placeholder schema` section) are the authority source for methodology steps 2, 5, 6, 7, and 8 of the consuming agent. Decision-tree elements B through H are lookup operations against codified rules; template inventory in elements I and J is verbatim template population with canonical placeholder tokens. Per `rules/ai-dev-conventions.md`, `gh-repo-scaffolder` is CoT: No — summarization-class, not logic-heavy. ADR-0031 (`docs/decisions/0031-gh-repo-scaffolder-identifying-info-exemption.md`) grants `gh-repo-scaffolder` a case-a identifying-info ban exemption for `gh` CLI subcommands, `.github/` filesystem path, and GitHub-specific repo-mechanics concept references; this skill inherits the exemption through its consuming agent.

## When this skill binds

Fire this skill when any of these are true:

- You are dispatched as `gh-repo-scaffolder` to scaffold a new repo (any scale tier).
- You are selecting a license SPDX identifier for a project.
- You are determining the project-scale tier (solo / team / oss-community) to derive the required file set.
- You are writing CODEOWNERS structure for a repo.
- You are scaffolding the `.github/` skeleton tree (ISSUE_TEMPLATE/, PULL_REQUEST_TEMPLATE.md, CODE_OF_CONDUCT.md, SECURITY.md).
- You are applying or verifying branch protection rules on a repo.
- You are deriving the `.gitignore` class from a detected project type.
- You are populating a scaffold template with canonical placeholder tokens.

Do NOT fire this skill for:

- PR review or CI verification — `gh-pr-review-discipline` (PR-process review lane).
- Workflow YAML authoring or auditing — `gh-workflow-discipline` (GitHub Actions workflow lane).
- Scaffolding `src/` or any application source tree — `dev-code-implementer` (application source lane).
- Release tagging, version bumping, or CHANGELOG — `gh-release-manager` (pending; CHANGELOG-paired per ADR-0025, out of scope here).
- Splitting a monorepo or setting org-level conventions — out of lane.
- SOP body audit — `biz-sop-discipline`.
- Pre-completion claim verification — `verification-before-completion`.

## When this skill does NOT bind

Refused triggers — do not load this skill when the request is:

- "Review this PR" → `gh-pr-review-discipline`
- "Author this workflow" or "Audit this workflow YAML" → `gh-workflow-discipline`
- "Scaffold the src/ tree" or "Stub the package layout" → `dev-code-implementer`
- "Tag a release" or "Bump the version for this release" → `gh-release-manager` (pending; CHANGELOG-paired per ADR-0025)
- "Split this monorepo" or "Set org-level conventions" → out of lane

## Element B — License selection decision tree

**Purpose:** walk this tree to select the canonical SPDX identifier for a project before fetching the license text.

**Inputs required:** project visibility (public / private), commercial-use intent (yes / no / unknown), viral-license preference (yes / no / unknown), author preference (any named SPDX-id or none).

**Decision tree (walk in order; first match wins):**

1. Author preference names a specific SPDX-id from the canonical eight → use it. Skip remaining steps.
2. Project is **private** with no public redistribution intent → **MIT** (minimal friction for internal use; viral concern does not apply).
3. Project is **public** + commercial-use intent **yes** + viral-license preference **no** → **Apache-2.0** (patent grant, permissive, enterprise-safe).
4. Project is **public** + commercial-use intent **yes** + viral-license preference **yes** → **GPL-3.0** (copyleft ensures derivative works stay open; patent retaliation clause).
5. Project is **public** + commercial-use intent **no** + author wants minimal boilerplate → **MIT** (two-clause, universally understood).
6. Project is **public** + commercial-use intent **no** + author wants BSD heritage → **BSD-3-Clause** (three-clause BSD; no endorsement clause).
7. Project is a **library or toolkit** where commercial use is **unknown** → **Apache-2.0** (safe default for reusable code with patent concerns).
8. Project intends **public domain dedication** with no reservation → **Unlicense** (simple public domain instrument).
9. Project is **documentation, media, or data only** → **CC0-1.0** (Creative Commons public domain dedication for non-code artifacts).
10. Project uses MPL-compatible code or requires **file-level copyleft** (not full project copyleft) → **MPL-2.0** (Mozilla Public License; per-file copyleft, compatible with Apache-2.0 downstream).
11. No prior step matched → **MIT** (safe baseline for general-purpose public repos).

**Canonical eight SPDX identifiers and rationales:**

| SPDX-id | One-line rationale | Canonical URL |
|---|---|---|
| MIT | Minimal text, permissive, universally understood | `https://spdx.org/licenses/MIT.txt` |
| Apache-2.0 | Permissive + explicit patent grant; enterprise-safe | `https://spdx.org/licenses/Apache-2.0.txt` |
| GPL-3.0 | Strong copyleft; derivative works must stay open; patent retaliation | `https://spdx.org/licenses/GPL-3.0-only.txt` |
| BSD-3-Clause | Three-clause BSD; no-endorsement clause; BSD heritage | `https://spdx.org/licenses/BSD-3-Clause.txt` |
| ISC | Functionally identical to MIT; OpenBSD default | `https://spdx.org/licenses/ISC.txt` |
| MPL-2.0 | File-level copyleft; Apache-2.0 compatible downstream | `https://spdx.org/licenses/MPL-2.0.txt` |
| Unlicense | Public domain instrument; simple, no conditions | `https://spdx.org/licenses/Unlicense.txt` |
| CC0-1.0 | Creative Commons public domain dedication; for non-code artifacts | `https://spdx.org/licenses/CC0-1.0.txt` |

Fetch the selected license at the canonical URL above using WebFetch. On fetch failure (HTTP non-2xx, timeout, DNS error), emit the ADR-0027 PAUSE shape per the WebFetch tool-guidance section.

## Element C — Project-scale heuristic

**Purpose:** classify the project tier to derive the required file set before scaffolding begins.

**Inputs required:** contributor count (integer or range), project visibility (public / private), contribution-mode (single-author / invite-only-collaborators / open-contributions).

**Three-tier classification (first match wins):**

| Tier | Criteria | Required file set |
|---|---|---|
| **solo** | Contributor count = 1 AND (private OR public with no contribution intent) | README.md, LICENSE, .gitignore, CODEOWNERS (single-owner pattern) |
| **team** | Contributor count 2–10 OR invite-only-collaborators OR private with defined collaborators | All solo files + CONTRIBUTING.md + PULL_REQUEST_TEMPLATE.md + branch protection (1 required reviewer) |
| **oss-community** | Open contributions OR public with community intent OR contributor count > 10 | All team files + CODE_OF_CONDUCT.md + SECURITY.md + ISSUE_TEMPLATE/ (bug_report + feature_request + config.yml) + branch protection (2 required reviewers) |

When visibility is public but contribution-mode is not stated, default to **team** tier and flag the assumption in the `@@SCAFFOLD MANIFEST` block.

## Element D — CODEOWNERS structure decision tree

**Purpose:** derive the CODEOWNERS pattern for the repo's ownership structure.

**Inputs required:** project-scale tier (from Element C), repo owner type (single-user / org-with-teams), directory ownership intent (whole-repo / path-based).

**Decision tree:**

1. **Solo tier** → single-owner fallback pattern: `* @<OWNER>`. No path-based patterns required.
2. **Team tier, whole-repo ownership** → single-owner or single-team pattern: `* @<OWNER>` or `* <TEAM_HANDLE>`.
3. **Team tier, path-based ownership** → path-based pattern per top-level directory: one line per path; paths listed from most specific to least specific (CODEOWNERS last-match-wins rule does NOT apply — GitHub uses first-match-wins; list specific paths first).
4. **Oss-community tier, whole-repo with maintainer team** → `* <TEAM_HANDLE>` as default; named maintainers added per critical path.
5. **Oss-community tier, path-based with multiple maintainers** → path-based lines; default fallback `* <MAINTAINER_HANDLES>` as last line only.

**Coverage requirement:** every file in the repo must be covered by at least one CODEOWNERS pattern. The default `*` pattern at the end of the file serves as the coverage fallback. A CODEOWNERS file that lacks a default fallback pattern is a blocking gap — add `* @<OWNER>` as the final line.

**Reference shape:** `@user` for individual GitHub handles; `@org/team` for GitHub org team handles. No email addresses, no bare names.

## Element E — ISSUE_TEMPLATE shape decision tree

**Purpose:** determine the ISSUE_TEMPLATE file set per project-scale tier.

| Tier | ISSUE_TEMPLATE shape |
|---|---|
| **solo** | Absent — no `.github/ISSUE_TEMPLATE/` directory. |
| **team** | Minimal or absent. A single `PULL_REQUEST_TEMPLATE.md` is sufficient; ISSUE_TEMPLATE/ is optional. |
| **oss-community** | Full set: `bug_report.md` + `feature_request.md` + `config.yml`. |

**config.yml semantics:**

- `blank_issues_enabled: false` → forces contributors to use a template; appropriate for projects requiring structured issue reports.
- `blank_issues_enabled: true` → permits freeform issues alongside templates; appropriate for projects with an informal contribution culture.
- `contact_links:` → optional array of external links (e.g., Discord, forum, security contact); include only when named contact channels exist.

For oss-community tier, default `blank_issues_enabled: false` unless the brief explicitly states an informal contribution culture.

## Element F — PULL_REQUEST_TEMPLATE.md shape decision tree

**Purpose:** select the PR template checklist depth per project-scale tier.

| Tier | Checklist depth | Default-branch reference | CI expectation | Reviewer pattern |
|---|---|---|---|---|
| **solo** | Minimal: description only, no checklist. | Named `<DEFAULT_BRANCH>`. | No CI expectation stated. | No reviewer assignment. |
| **team** | Standard: description + 3-item checklist (tests passing, documentation updated, linked issue). | Named `<DEFAULT_BRANCH>`. | CI status checks noted by name. | At least 1 reviewer required (per branch protection). |
| **oss-community** | Full: description + 5-item checklist (tests, docs, changelog, linked issue, code of conduct acknowledged). | Named `<DEFAULT_BRANCH>`. | All `<CI_STATUS_CHECK_NAMES>` must pass before merge. | At least 2 reviewers required; tag `<MAINTAINER_HANDLES>`. |

## Element G — Branch protection rule set decision tree

**Purpose:** derive the `gh api` payload for `PUT /repos/{owner}/{repo}/branches/{branch}/protection` per project-scale tier.

**Per-scale settings:**

| Setting | solo | team | oss-community |
|---|---|---|---|
| `required_pull_request_reviews.required_approving_review_count` | 0 | 1 | 2 |
| `required_pull_request_reviews.dismiss_stale_reviews` | false | true | true |
| `required_status_checks.strict` | false | true | true |
| `required_status_checks.contexts` | `[]` | `[<CI_STATUS_CHECK_NAMES>]` | `[<CI_STATUS_CHECK_NAMES>]` |
| `enforce_admins` | false | false | true |
| `restrictions` | null | null | null |
| Force-push policy | always deny | always deny | always deny |
| Deletion policy | always deny | always deny | always deny |

**Payload shape (all tiers; null fields omitted from actual PUT body):**

```json
{
  "required_pull_request_reviews": {
    "required_approving_review_count": <count>,
    "dismiss_stale_reviews": <bool>
  },
  "required_status_checks": {
    "strict": <bool>,
    "contexts": [<CI_STATUS_CHECK_NAMES>]
  },
  "enforce_admins": <bool>,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```

Write this payload to a temp file (e.g., `branch-protection-payload.json`) in the repo root, then execute:

```
gh api repos/{OWNER}/{PROJECT_NAME}/branches/{DEFAULT_BRANCH}/protection --method PUT --input branch-protection-payload.json
```

Remove the temp file after the API call succeeds.

For solo-tier repos, branch protection is optional. Apply only when the brief explicitly requests it.

## Element H — .gitignore class derivation

**Purpose:** map the detected project type to the canonical `github/gitignore` template name.

**Detection input:** the output of `aidev-agent-manager.detect-project` (language toolchain + framework + IDE artifacts + OS artifacts).

**Canonical mapping:**

| Detected toolchain / framework | github/gitignore template name | Notes |
|---|---|---|
| Python (pip / uv / poetry) | `Python` | Covers `__pycache__`, `.venv`, `*.pyc`, `dist/` |
| Python + Jupyter | `Python` + `Jupyter` | Concatenate both |
| Node.js / npm / yarn / pnpm | `Node` | Covers `node_modules/`, `.env`, `dist/` |
| TypeScript (standalone) | `Node` | TypeScript builds via Node toolchain |
| Rust / cargo | `Rust` | Covers `target/`, `Cargo.lock` policy deferred to author |
| Go / go modules | `Go` | Covers `vendor/`, binary outputs |
| Java / Maven | `Java` + `Maven` | Concatenate both |
| Java / Gradle | `Java` + `Gradle` | Concatenate both |
| C / C++ / CMake | `C` + `CMake` | Concatenate both |
| Ruby / Bundler | `Ruby` | Covers `.bundle/`, `vendor/bundle` |
| PHP / Composer | `PHP` + `Composer` | Concatenate both |
| Swift / Xcode | `Swift` + `Xcode` | Concatenate both |
| Kotlin / Android | `Kotlin` + `Android` | Concatenate both |
| Documentation-only | (none) | No `.gitignore` required unless OS artifacts present |
| Unknown | `(prompt author for toolchain)` | PAUSE if unknown; do not guess |

**IDE / OS artifact additions (append after language block):**

- VS Code artifacts → `VisualStudioCode`
- JetBrains IDEs → `JetBrains`
- macOS → `macOS`
- Windows → `Windows`
- Linux → (no standard template; add `.DS_Store` and `Thumbs.db` lines directly)

Fetch template content from `https://raw.githubusercontent.com/github/gitignore/main/<TemplateName>.gitignore` via WebFetch (the WebFetch tool-guidance section permits `raw.githubusercontent.com` for gitignore template fetches). If WebFetch is unavailable or returns an error (HTTP non-2xx, timeout, DNS error), emit the ADR-0027 PAUSE shape for `<TemplateName> gitignore template` and stop. Never substitute training-data recollection of a gitignore template body.

## Element I — .github/ skeleton tree

**Purpose:** enumerate the complete file inventory per scale tier to guide the scaffold pass.

**Solo tier:**
```
CODEOWNERS
```
(README.md, LICENSE, .gitignore are at repo root, not under .github/)

**Team tier:**
```
.github/
  PULL_REQUEST_TEMPLATE.md
  CODEOWNERS
```
(CODEOWNERS is valid at repo root OR at `.github/CODEOWNERS` — GitHub accepts both; prefer repo root for visibility.)

**Oss-community tier:**
```
.github/
  PULL_REQUEST_TEMPLATE.md
  CODEOWNERS
  ISSUE_TEMPLATE/
    bug_report.md
    feature_request.md
    config.yml
  CODE_OF_CONDUCT.md
  SECURITY.md
```

**File placement rule for CODEOWNERS:** GitHub resolves CODEOWNERS in this priority order: repo root → `docs/` → `.github/`. Use repo root for all tiers (highest visibility, simplest resolution).

**Existence check before scaffold:** run Glob to detect any of these paths before writing. If a file exists at the destination path, emit a finding in `@@SCAFFOLD MANIFEST` rather than overwriting silently. Pause and surface the conflict to the orchestrator.

## Element J — Template inventory

**Purpose:** verbatim scaffolds for each file in the skeleton tree. Every template carries placeholder tokens from the placeholder schema. Fill all tokens at write time from the brief.

### README.md

```markdown
# <PROJECT_NAME>

> One-line description of what this project does.

## Overview

<!-- Provide a brief overview. -->

## Getting started

<!-- Installation and quickstart steps. -->

## Usage

<!-- Usage examples. -->

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project is licensed under the [<LICENSE_SPDX> License](LICENSE).
```

### CONTRIBUTING.md (team and oss-community tiers)

```markdown
# Contributing to <PROJECT_NAME>

Thank you for your interest in contributing.

## How to contribute

1. Open an issue describing what you want to change.
2. Fork the repository and create a branch from `<DEFAULT_BRANCH>`.
3. Make your changes with tests.
4. Submit a pull request referencing your issue.

## Code of conduct

Please read [CODE_OF_CONDUCT.md](.github/CODE_OF_CONDUCT.md) before contributing.

## License

By contributing, you agree that your contributions are licensed under the [<LICENSE_SPDX> License](LICENSE).
```

### CODEOWNERS

**Solo tier:**
```
* @<OWNER>
```

**Team tier (whole-repo):**
```
# Default owner for all files.
* @<OWNER>
```

**Oss-community tier (team-based):**
```
# Default owners — all files.
* <TEAM_HANDLE>

# Add path-based overrides below as needed.
# /docs/ @<MAINTAINER_HANDLES>
```

### PULL_REQUEST_TEMPLATE.md (team tier)

```markdown
## Description

<!-- Describe the change and its motivation. -->

## Checklist

- [ ] Tests pass locally.
- [ ] Documentation updated.
- [ ] Linked issue: #<!-- issue number -->
```

### PULL_REQUEST_TEMPLATE.md (oss-community tier)

```markdown
## Description

<!-- Describe the change and its motivation. -->

## Checklist

- [ ] Tests pass (`<CI_STATUS_CHECK_NAMES>`).
- [ ] Documentation updated.
- [ ] CHANGELOG entry added under the appropriate version header.
- [ ] Linked issue: #<!-- issue number -->
- [ ] I have read and agree to the [Code of Conduct](.github/CODE_OF_CONDUCT.md).

## Reviewers

<!-- Tag <MAINTAINER_HANDLES> for review. -->
```

### ISSUE_TEMPLATE/bug_report.md (oss-community tier)

```markdown
---
name: Bug report
about: Report a reproducible problem
labels: bug
---

## Describe the bug

<!-- A clear and concise description of the bug. -->

## Steps to reproduce

1.
2.
3.

## Expected behavior

<!-- What should have happened. -->

## Actual behavior

<!-- What actually happened. -->

## Environment

- OS:
- Version:
```

### ISSUE_TEMPLATE/feature_request.md (oss-community tier)

```markdown
---
name: Feature request
about: Suggest an idea or improvement
labels: enhancement
---

## Problem statement

<!-- Describe the problem this feature would solve. -->

## Proposed solution

<!-- Describe what you want to happen. -->

## Alternatives considered

<!-- Describe alternatives you have evaluated. -->
```

### ISSUE_TEMPLATE/config.yml (oss-community tier)

```yaml
blank_issues_enabled: false
contact_links:
  - name: Security
    url: <SECURITY_CONTACT_EMAIL>
    about: Report security vulnerabilities privately.
```

### CODE_OF_CONDUCT.md (oss-community tier)

This file follows the Contributor Covenant 2.1. Fetch the canonical text via WebFetch from `https://www.contributor-covenant.org/version/2/1/code_of_conduct/code_of_conduct.md` (authorized per the WebFetch tool-guidance section).

If WebFetch is unavailable or returns an error (HTTP non-2xx, timeout, DNS error), emit the ADR-0027 PAUSE shape:

```
PAUSE: need research-docs-lookup for Contributor Covenant 2.1 canonical text reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

After fetching, replace the enforcement contact placeholder with `<COC_CONTACT>`.

### SECURITY.md (oss-community tier)

```markdown
# Security policy

## Supported versions

| Version | Supported |
|---|---|
| Latest | Yes |

## Reporting a vulnerability

Please report security vulnerabilities to **<SECURITY_CONTACT_EMAIL>**.

Do not open a public GitHub issue for security vulnerabilities.

We will acknowledge receipt within 72 hours and provide a timeline for remediation.
```

## Tool guidance sections

**Authority statement:** the tool-guidance sections below are the AUTHORITY source for `gh-repo-scaffolder`'s tool grants. The agent file's `tool_grants` section cites this skill's tool-guidance section by element label. The agent file does NOT cite ADR-0031 as authority for tool grants — ADR-0031 covers identifying-info exemption only.

### Read

**Permitted uses (methodology steps 2, 3, 7, 9):**

- Read the orchestrator brief in full before any scaffold action.
- Read this skill in full at methodology step 2.
- Read existing files at the destination repo path before writing (existence check; content comparison when a file exists at the target and would conflict).
- Read the branch protection payload temp file before submitting the PUT.

**Refused uses:**

- Reading files outside the destination repo path.
- Reading other repos' CODEOWNERS or scaffold artifacts to copy patterns (use this skill's templates instead).

### Glob

**Permitted uses (methodology step 3):**

- Detect existing `.git`, `README.md`, `LICENSE`, `.gitignore`, `.github/`, `CODEOWNERS` in the destination repo path before any write.
- Detect existing ISSUE_TEMPLATE files to avoid overwrite conflicts.

**Refused uses:**

- Glob patterns that escape the destination repo path (e.g., `../`, absolute paths outside the repo).

### Write

**Bounded write targets — destination repo root AND `.github/` subtree only.**

Permitted destination paths:
- `<repo-root>/README.md`
- `<repo-root>/LICENSE`
- `<repo-root>/CONTRIBUTING.md`
- `<repo-root>/.gitignore`
- `<repo-root>/CODEOWNERS`
- `<repo-root>/.github/PULL_REQUEST_TEMPLATE.md`
- `<repo-root>/.github/CODEOWNERS`
- `<repo-root>/.github/ISSUE_TEMPLATE/bug_report.md`
- `<repo-root>/.github/ISSUE_TEMPLATE/feature_request.md`
- `<repo-root>/.github/ISSUE_TEMPLATE/config.yml`
- `<repo-root>/.github/CODE_OF_CONDUCT.md`
- `<repo-root>/.github/SECURITY.md`
- `<repo-root>/branch-protection-payload.json` (temp file; delete after PUT succeeds)

**Refused write targets (enumerated verbatim):**

- `.github/workflows/` — workflow YAML authoring is `gh-workflow-discipline`'s lane.
- `src/` or any application source path — `dev-code-implementer`'s lane.
- Any path outside the destination repo root.
- Any path matching `.git/` (internal git data — never written by this agent).

### WebFetch

**Bounded to three external endpoints, each serving a distinct scaffold need:**

1. `spdx.org` — license text retrieval. Canonical URL shape: `https://spdx.org/licenses/<SPDX-id>.txt`. One fetch per license retrieval per scaffold pass.
2. `raw.githubusercontent.com` — gitignore template content from the GitHub-published `github/gitignore` repository. Canonical URL shape: `https://raw.githubusercontent.com/github/gitignore/main/<TemplateName>.gitignore`. One fetch per template name per scaffold pass.
3. `contributor-covenant.org` — Code of Conduct canonical text. Canonical URL shape: `https://www.contributor-covenant.org/version/2/1/code_of_conduct/code_of_conduct.md`. One fetch per scaffold pass when the oss-community tier is in scope.

**On fetch failure (HTTP non-2xx, timeout, DNS error) for any of the three domains:** emit the ADR-0027 PAUSE shape verbatim:

```
PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

where `<subject>` is the specific license SPDX-id, gitignore template name, or document name. Never substitute training-data recollection.

**Refused WebFetch targets:**

- Any domain other than the three named above.
- Repeated fetches against the same URL within a scaffold pass (caches are out of scope for this skill — re-fetch is a separate decision).
- Fetches with arbitrary query parameters; only canonical URL shapes are authorized.

### Bash

**Network fetches (license text, gitignore templates, CoC text) are routed through the WebFetch tool-guidance section, NOT through Bash. The Bash schema is intentionally bounded to GitHub CLI + local git operations.**

**Permitted commands (schema enumerating EXACTLY):**

```
gh repo create <owner>/<name> --public|--private
gh api repos/{owner}/{repo}/branches/{branch}/protection --method PUT --input <payload-file>
gh api repos/{owner}/{repo} --method PATCH -f default_branch=<branch>
gh label create <name> --color <hex> --description <text>
git init
git remote add origin <url>
git status --porcelain -- <path>
git log -1 --format=%H -- <path>
```

**Refused commands (enumerated verbatim):**

- `gh repo delete` — destructive; out of lane.
- `gh repo edit --visibility` — visibility changes are not scaffold operations.
- `git push --force` — force-push is always refused per CLAUDE.md §12.
- `git push origin main` — pushing to the default branch is not a scaffold operation; the orchestrator gates pushes per CLAUDE.md §9.
- `rm` — file deletion is not a scaffold operation; use Write to overwrite only when explicitly approved.
- `mv` — file moves are not scaffold operations.
- `sudo` — never without explicit User instruction per CLAUDE.md §12.

## Placeholder schema

All nine canonical placeholder tokens. Fill every token from the brief at write time. A scaffold output carrying unfilled placeholder tokens is a blocking gap — emit a finding in `@@SCAFFOLD MANIFEST` for each unfilled token.

| Token | Meaning |
|---|---|
| `<PROJECT_NAME>` | Canonical project name as it appears in the repo URL slug |
| `<OWNER>` | GitHub `@user` or `@org` handle that owns the repo |
| `<DEFAULT_BRANCH>` | Default branch name (typically `main`) |
| `<LICENSE_SPDX>` | Canonical SPDX-id selected in Element B (e.g., `MIT`, `Apache-2.0`) |
| `<TEAM_HANDLE>` | `@org/team` handle for CODEOWNERS team-based ownership |
| `<MAINTAINER_HANDLES>` | Comma-separated `@user` handles for multi-owner directories |
| `<SECURITY_CONTACT_EMAIL>` | Email or URL for SECURITY.md vulnerability disclosure |
| `<COC_CONTACT>` | Email or URL for CODE_OF_CONDUCT.md enforcement contact |
| `<CI_STATUS_CHECK_NAMES>` | Named status checks for branch protection and PR template |

## Block emissions

`gh-repo-scaffolder` emits exactly two block types. Do not reference any other block type.

**Audit verdict block:**

```
@@VERDICT BEGIN
verdict: <APPROVE | REQUEST_CHANGES | REJECT | HOLD | ABORT>
lane: gh-repo-scaffolder
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

`@@VERDICT` structure and verdict-to-findings consistency rules follow `docs/specs/verdict-schema.md` (5-value verdict enum: `APPROVE` | `REQUEST_CHANGES` | `REJECT` | `HOLD` | `ABORT`). Category enum canonical subset: `test | other | governance | manifest`.

**Scaffold manifest block (one per scaffold run; one row per created file):**

```
@@SCAFFOLD MANIFEST BEGIN
path: <relative-path-from-repo-root>
source: <template-J-name | spdx-fetch | contributor-covenant-fetch | author-supplied>
placeholder_flag: <all-filled | unfilled-tokens: <comma-separated token list>>
size: <bytes>
@@SCAFFOLD MANIFEST END
```

One `path:` through `size:` group per created file. All four fields are required per row. A row with `placeholder_flag: unfilled-tokens:` is a blocking gap — surface before emitting `@@VERDICT`.

## ADR citations

- **ADR-0031** (`docs/decisions/0031-gh-repo-scaffolder-identifying-info-exemption.md`) — case-a identifying-info ban exemption for `gh-repo-scaffolder`. Cited by number for identity-intrinsic justification only. This ADR is explicitly NOT the authority for tool grants — tool grants flow from this skill's tool-guidance sections above.
- **ADR-0027** (`docs/decisions/0027-third-party-doc-reference-pause-to-user-pattern.md`) — verbatim PAUSE shape for WebFetch failure and third-party documentation reference lookups. The `[scheduled-annotation: agent pending future session per agent-roster.md step 13]` wrapper is mandatory verbatim at every PAUSE emission site.
- **ADR-0025** (`docs/decisions/0025-adr-0031-corrected-supersession-honors-section-8-append-only.md`) — cited only as a refused-lane discriminator: CHANGELOG-paired version-bump discipline is out of scope for this skill and for `gh-repo-scaffolder`. Release tagging and version bumps route to `gh-release-manager`.

## Anti-patterns

1. **Overwriting an existing file without a conflict finding.** If Glob detects an existing file at a scaffold target path, emit a `@@SCAFFOLD MANIFEST` row with a conflict finding before any write. Never overwrite silently.
2. **Hard-coding license text from training data rather than fetching from `spdx.org`.** License text must be fetched at canonical URL per Element B. Training-data recollections of license text are not authoritative and violate CLAUDE.md §4 capability honesty.
3. **Bash command schema drift.** The Bash permitted-command list is the complete schema. Introducing a command not in the enumerated list (e.g., `gh repo edit`, `rm`, `git push`) without an explicit orchestrator override is a structural violation.
4. **Hedge tokens in scaffold output or skill prose.** The 32-token canonical banned list (re-grep before commit; inline verbatim per `gh-workflow-discipline` Element H precedent rather than cross-skill reference) applies to all emitted text including `@@SCAFFOLD MANIFEST` summaries and `@@VERDICT` summaries:

   `might` / `may` / `maybe` / `perhaps` / `possibly` / `could potentially` / `seems like` / `seems to` / `appears to` / `looks like` / `I think` / `I believe` / `IMO` / `in my opinion` / `kind of` / `sort of` / `somewhat` / `a bit` / `rather` / `probably` / `likely` / `try to` / `attempt to` / `arguably` / `in theory` / `tends to` / `would suggest` / `in some cases` / `ostensibly` / `presumably` / `feasibly` / `could be argued`

   Concrete-sense uses are safe: "appears" in the sense of "X appears in Y" (X is present in Y); "rather than" as conjunction (not as hedging "rather"); "typically" in concrete-default sense (e.g., "default branch name (typically `main`)"). Re-grep with a stoplist before every commit; treat any positive hit outside the safe-use list as a finding.
5. **Inserting a per-finding CoT chain template.** `gh-repo-scaffolder` is CoT: No (summarization-class). This skill contains no `cot_*` field template and emits none. A draft that adds a per-finding CoT chain is a structural violation of the summarization-class classification.
6. **Omitting a tool-guidance section.** All five tool-guidance sections (Read, Glob, Write, WebFetch, Bash) are mandatory. An agent file citing this skill without all five sections is structurally incomplete.
7. **Scope creep into `.github/workflows/`.**  Workflow YAML is `gh-workflow-discipline`'s lane. Writing any file under `.github/workflows/` is a refused Write target per the tool-guidance section.
8. **Dangling block references.** `gh-repo-scaffolder` emits `@@VERDICT` and `@@SCAFFOLD MANIFEST` only. Referencing `@@PR-COMMENT`, `@@WORKFLOW-RATIONALE`, `@@FINDING-SUMMARY`, or any other block type is a structural violation.
9. **Paraphrasing the ADR-0027 PAUSE shape.** The verbatim PAUSE text is: `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]`. Any deviation — omitting the scheduled-annotation, changing the parameter name, substituting synonyms — breaks future `research-docs-lookup` routing per ADR-0027 Consequences clause.

## Scope boundaries

This skill covers:
- Repo-mechanics file scaffolding: README.md, LICENSE, CONTRIBUTING.md, .gitignore, CODEOWNERS, PULL_REQUEST_TEMPLATE.md, ISSUE_TEMPLATE/, CODE_OF_CONDUCT.md, SECURITY.md.
- GitHub-specific repo initialization: `gh repo create`, branch protection via `gh api`, default-branch setting via `gh api`, label creation via `gh label create`.
- License text retrieval from `spdx.org`.

This skill does NOT cover:
- Application source code (`src/`, package layout, module stubs) → `dev-code-implementer`.
- GitHub Actions workflow YAML (`.github/workflows/`) → `gh-workflow-discipline`.
- PR review or CI status verification → `gh-pr-review-discipline`.
- Release tagging, version bumping, or CHANGELOG management → `gh-release-manager`.
- Dependency management or Dependabot config → `gh-dependency-manager` (pending; future session per `agent-roster.md` step 13).
- Org-level convention setting or monorepo splits → out of lane.
