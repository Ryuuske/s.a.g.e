---
paths:
  - "src/**/*"
  - "lib/**/*"
  - "**/*.{ts,tsx,js,jsx,py,rs,go,java,cpp,c,h,hpp,kt,fs,rb,php,swift,m}"
  - "tests/**/*"
  - "test/**/*"
  - "**/*.{test,spec}.*"
  - "package.json"
  - "Cargo.toml"
  - "go.mod"
  - "pom.xml"
  - "build.gradle*"
  - "requirements*.txt"
  - "pyproject.toml"
  - "**/Dockerfile"
  - "**/docker-compose*.yml"
  - ".github/workflows/**"
---

# Software-dev work conventions

These conventions apply when working on application code, scripts, automation, web services, or tooling. For lifecycle entry points use `dev-visionary` and `dev-planner` per CLAUDE.md §9 (Session lifecycle — mode-classification and intake dispatch).

## Plan as the routing source

Every Build phase dispatch reads the plan's specialist routing from `<repo>/.development/plans/active.md`. The plan names which `dev-*` specialists handle which work items, including which language-specific reviewer activates per project (via `aidev-agent-manager.detect-project` signals — `Cargo.toml` activates `dev-rust-reviewer`, `go.mod` activates `dev-go-reviewer`, `manage.py` + `requirements.txt` activates `dev-django-reviewer`, etc.).

## Test strategy is mandatory in the plan

`dev-planner` must specify a test strategy for the Build phase. Plans whose Build items don't name a test approach are blocked at the User approval gate (§2). Test adequacy is reviewed by `dev-test-engineer` during the Review phase; test execution happens during the Ship phase via `dev-e2e-runner` and `dev-test-engineer`'s unit/integration suite invocations.

## Audit pairings

The audit-pairing matrix routes by what the diff touches:

- **UI-touching** (`src/components/`, `*.qml`, `*.tsx`) — `dev-code-reviewer` + `dev-ux-designer`
- **Security-touching** (auth, secrets, file I/O, network, subprocess, deserialization, crypto, deps) — `dev-code-reviewer` + `sec-auditor`
- **Database-touching** — `dev-code-reviewer` + `dev-database-reviewer`
- **Neither** — `dev-code-reviewer` + `dev-test-engineer`
- **Test-only** (`tests/` directory only) — `dev-test-engineer` solo

Language-specific reviewers (Python, TypeScript, Rust, Go, Java, Kotlin, C/C++, F#, Django, VBA) fire in addition when activated per project.

## Build error resolution

Build failures are resolved by `dev-build-error-resolver` (general) or the language-specific variants (`dev-build-error-resolver-rust`, `-go`, `-cpp`, `-java`, `-kotlin`, `-django`, `-pytorch`, `-ms`). Each cites the build stage explicitly (preprocessing / compilation / linking / runtime / module resolution / dependency conflict) and never proposes a fix without a verification command.

## Atomic commits

One logical change per commit. Refactor + feature = two commits. Bug fix + formatting = two commits. The atomicity rule from CLAUDE.md §9 holds especially strictly for software dev because `git bisect` / `git revert` is the standard rollback path. A bisect across non-atomic commits returns useless answers.

## Test execution gates Ship

The Ship phase does not start until: (1) all unit and integration tests pass per `dev-test-engineer`'s execution; (2) e2e tests pass per `dev-e2e-runner` with <95% flake threshold over 10 runs; (3) `ops-release-readiness` 8-checklist clears.

## Match existing style (IMPLEMENTER_DISCIPLINE)

When the codebase has established conventions (naming, file layout, error handling patterns, async style), match them. Style critique is `dev-architect`'s lane in Design and `dev-code-reviewer`'s lane in Review — not the implementer's lane during Build. Introducing inconsistent style is a finding even when the new style is objectively better.
