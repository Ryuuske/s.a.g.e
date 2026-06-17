<!-- GENERATED — DO NOT EDIT. Regenerate with `python3 scripts/gen_docs.py`. -->

# Agent roster — reference

Generated from `agents/*.md` frontmatter. Doctrine (constraint blocks, CoT
classification, shareability principle) lives at
`docs/specs/universal-agent-constraints.md`; pairing policy at
`docs/specs/audit-pairing-matrix.md`.

## Family directory

| Family | Prefix | Count |
|---|---|---|
| AI Development | `aidev-` | 16 |
| General Development | `dev-` | 31 |
| GitHub Project Mechanics | `gh-` | 6 |
| Data Engineering | `data-` | 5 |
| Architecture | `arch-`/`freecad-` | 11 |
| Finance Operations | `fin-` | 7 |
| Business Operations | `biz-` | 4 |
| Documentation | `doc-` | 3 |
| Security | `sec-` | 2 |
| Operations | `ops-` | 2 |
| Research | `research-` | 2 |
| Test / E2E Validation | `test-` | 5 |
| Media | `media-` | 4 |
| **Total** | — | **98** |

## Family: AI Development

### `aidev-adversarial-auditor`

- **Description**: Use to pressure-test an AI-agent, framework, or skill change by actively looking for ways it fails — not just verifying it works. Scoped to AI-dev artifacts (agents/, skills/, framework files). Triggers as the second auditor in the dual-auditor protocol on AI-dev work, when aidev-code-reviewer returns APPROVE but a contrarian read is wanted, or 'find what's wrong with this'. Do not substitute for aidev-code-reviewer (adversarial is contrarian, reviewer is governance/quality).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `aidev-agent-creator`

- **Description**: Use to create, modify, delete, or propagate-update any agent — single entry point for agent CRUD and roster-wide governance updates, producing a spec for aidev-code-implementer. Triggers: 'create an agent for X', 'modify the Y agent', or a dispatch-miss where aidev-agent-manager returns NO_CATALOG_MATCH. Do not use to CRUD skills (aidev-skill-creator), write the file (aidev-code-implementer), activate roster agents (aidev-agent-manager), or frame/plan (aidev-visionary / aidev-planner).
- **Model**: opus · **Tools**: Read, Grep, Glob

### `aidev-agent-designer`

- **Description**: Use to design the shape of a new agent or non-trivial revision to an existing agent — its lane, refused adjacent lanes, tool grants, model choice, methodology, and output discipline. Inherently AI-dev work; the `aidev-` prefix keeps the family consistent with other AI-development agents. Triggers when `aidev-planner`'s plan includes "add agent X" or "rework agent Y." Do not use for skill design (skills are simpler — handle in plan + implement), for tech selection inside an agent (that's `dev-architect`), or for actually writing the agent file (that's `aidev-code-implementer`).
- **Model**: opus · **Tools**: Read, Grep, Glob

### `aidev-agent-manager`

- **Description**: Use to detect the active project type, maintain the per-project active-roster.json (its ONLY writer), and resolve dispatch misses by checking whether a catalog agent applies. Triggers on session start (wake-up detect), a dispatch miss (check-miss), an explicit add/remove-agent request, or detected project drift. Do not use to dispatch agents (orchestrator), design new catalog agents (aidev-agent-creator), or modify agent definition files (aidev-code-implementer).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `aidev-arbiter`

- **Description**: Receive structured decision briefs on framework-internal architectural questions and return a binding verdict with rationale and ADR draft body. Refused lanes: product-level decisions → User §7; framing → aidev-visionary / dev-visionary / fin-visionary / biz-visionary; tech-selection for non-AI-dev → dev-architect; writing the ADR file → aidev-code-implementer.
- **Model**: opus · **Tools**: Read, Grep, Glob

### `aidev-claude-code-researcher`

- **Description**: Use to look up current Claude Code / Anthropic official documentation — model identifiers, API capabilities, tool grants, agent SDK behavior, MCP integration, feature availability, deprecation notices. Triggers when any agent must verify a Claude Code or Anthropic product claim against current docs. Do not use for non-Anthropic API/library docs (research-docs-lookup), fact-checking (research-fact-checker), writing docs (doc-keeper), or designing agents (aidev-agent-creator).
- **Model**: sonnet · **Tools**: Read, WebFetch

### `aidev-code-implementer`

- **Description**: Use to execute approved plans for AI-agent, framework, or skill development — when adding or modifying agents in `agents/`, skills in `skills/`, or their supporting files. Distinct from `dev-code-implementer` (general-purpose) — the orchestrator chooses based on whether the change is to AI-development artifacts. Triggers after the User has explicitly approved a plan ("approved," "go ahead," "ship it") AND the orchestrator has specific implementation steps for AI-dev work. Do not use for planning, design decisions, exploratory work, or speculative changes without a plan.
- **Model**: sonnet · **Tools**: Read, Write, Edit, Bash, Grep, Glob

### `aidev-code-reviewer`

- **Description**: Use to review AI-agent, framework, or skill changes against the approved plan and project conventions — when reviewing changes to `agents/`, `skills/`, or supporting AI-dev files. Distinct from `dev-code-reviewer` (general-purpose). Triggers after `aidev-code-implementer` finishes a logical change, before push to a protected branch, when the User asks for review, or as Auditor #1 in the dual-auditor protocol with `aidev-adversarial-auditor`. Do not use to write or modify code (read-only). Do not use for visual design review (dev-ux-designer) or security-specific review (sec-auditor).
- **Model**: sonnet · **Tools**: Read, Write, Grep, Glob, Bash

### `aidev-eval-engineer`

- **Description**: Use to run evals against AI-dev agents and skills — measure rule adherence, false-positive rate, severity-calibration drift, and lane-bleed resistance over the roster. Triggers when a roster change needs regression eval, when an agent's verdict quality is questioned, when the User asks to eval an agent or skill, or as the regression gate after a propagation batch. Read-only over the artifact under test. Do not use to write or modify the agent/skill under eval (aidev-code-implementer), to design eval cases as a new skill (aidev-skill-creator), or to audit a single diff (aidev-code-reviewer).
- **Model**: opus · **Tools**: Read, Bash, Grep, Glob

### `aidev-keeper`

- **Description**: Use to read from or write to S.A.G.E. memory. The ONLY agent with S.A.G.E. store access (via the blessed Bash→service-layer path); all others receive nook pointers from the orchestrator via a Keeper dispatch. Triggers: session-start wake-up, nook search on PAUSE sentinel, session-end handoff, write-back (decision/solved-problem/user-fact/skill), diary read/write, wing registration. Do not use to mine files (S.A.G.E. mine CLI), design agents, plan work, or review code.
- **Model**: sonnet · **Tools**: Read, Write, Bash

### `aidev-loop-operator`

- **Description**: Use to MONITOR a running autonomous agentic AI-dev loop (a multi-agent run, a no-pause framework-file loop) for stalls — watch loop state, flag infinite loops, repeated identical tool calls, and drift from the goal, and recommend an orchestrator pause. Read-only stall watchdog. Triggers when an approved autonomous agentic run needs a stall observer. Do not drive or advance the loop (the orchestrator runs it directly under the autonomy-loop skill, ADR-0011), do not audit a change (aidev-code-reviewer/aidev-adversarial-auditor), and do not decide a fork (aidev-arbiter).
- **Model**: sonnet · **Tools**: Read, Grep, Glob

### `aidev-planner`

- **Description**: Use to convert a sharpened vision (or a concrete User request) into an executable plan for AI-agent, framework, or skill work. Scoped to AI-dev projects only. Triggers when the vision is settled but no plan exists, when the User asks "what would it take to…", or when the orchestrator needs a plan to present for approval. Do not use for framing (that's `aidev-visionary`), for tech selection (that's `dev-architect`), or after a plan is already approved (then it's `aidev-code-implementer`).
- **Model**: opus · **Tools**: Read, Grep, Glob, Write

### `aidev-skill-creator`

- **Description**: Use to create, modify, or delete any skill (SKILL.md) — the single entry point for skill CRUD, producing a spec for aidev-code-implementer. Triggers: 'create a skill for X', 'modify the Y skill', or when aidev-agent-creator returns a missing_skills_needed block. Do not use to CRUD agents (aidev-agent-creator), write the file (aidev-code-implementer), or activate skills (they load automatically by description match — no per-project activation).
- **Model**: opus · **Tools**: Read, Grep, Glob

### `aidev-state-adversarial-auditor`

- **Description**: Use to pressure-test the live state of the AI-dev roster, framework files, and skills by actively looking for failure modes, dispatch ambiguities, and lane-failure patterns — use only when no diff is in scope; for a diff, see `aidev-adversarial-auditor`. Triggers as the second auditor in the state dual-auditor protocol, or when the orchestrator wants a contrarian read on roster compliance without a change in flight. Do not use to pressure-test a diff (aidev-adversarial-auditor). Do not use for drift/archive integrity (doc-keeper). Do not use for backlog verdicts (general-purpose).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob

### `aidev-state-reviewer`

- **Description**: Use to review the live state of the AI-dev roster, framework files, and skills for governance compliance — when there is NO diff, only state under examination (e.g., roster lane-discipline audit, §16 pairing compliance check, manifest integrity sweep). Distinct from `aidev-code-reviewer` (requires a diff). Triggers when the orchestrator needs a structured state audit of `agents/`, `skills/`, or supporting framework files without a change in flight. Do not use to review a diff (aidev-code-reviewer). Do not use for pure doc lifecycle/hierarchy/archive hygiene (doc-keeper).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob

### `aidev-visionary`

- **Description**: Use for the earliest framing step on AI agent, framework, or skill projects — when the User describes intent in fuzzy terms and you need a one-screen problem statement, success criteria, and refusal scope before any planning. Scoped to AI-dev work (this framework itself, or any future AI-agent / framework / skill project). Do not use for implementation-ready requirements (that's `aidev-planner`), tech selection (that's `dev-architect`), or once a plan already exists.
- **Model**: sonnet · **Tools**: Read, Grep, Glob

## Family: General Development

### `dev-architect`

- **Description**: Use to evaluate technical design decisions, technology selections, system boundaries, refactor scope, and architectural patterns. Triggers when the User asks "should I use X or Y," when an ADR is being proposed or reviewed, when a refactor would change module boundaries, or when the orchestrator faces a non-trivial design choice during planning. Do not use for routine code review (that's dev-code-reviewer), visual design (dev-ux-designer), or implementation (dev-code-implementer). Do not use for AI-dev agent shape — that's `aidev-agent-designer`.
- **Model**: opus · **Tools**: Read, Grep, Glob, WebSearch, WebFetch

### `dev-build-error-resolver`

- **Description**: Use to resolve build, compile, type, and dependency errors deterministically — read the error, trace it to a root cause, propose a fix, and supply a verification command. Triggers when a build/compile/type-check fails, when a dependency or version conflict blocks the build, or when an implementer hands off a failing toolchain run. For language-specific toolchains prefer the matching variant (`-cpp`, `-go`, `-java`, `-kotlin`, `-rust`, `-django`, `-pytorch`, `-ms`). Do not use to review code quality (`dev-code-reviewer`) or to write features (`dev-code-implementer`).
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-cpp`

- **Description**: Use to resolve C/C++ build errors — preprocessing failures, compilation errors, linker/symbol-resolution errors, ABI mismatches, header dependency issues, and template-instantiation failures. Triggers when `cmake`/`make`/`clang`/`gcc`/`ld` exits non-zero, when a symbol is undefined or multiply-defined, or when a template error wall blocks the build. For non-C++ toolchains use the matching variant; for code-quality review use `dev-cpp-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-django`

- **Description**: Use to resolve Django startup, migration, and deployment errors — `manage.py` failures, migration conflicts, `collectstatic` issues, settings/app-loading errors, and dependency mismatches. Triggers when `python manage.py check` fails, when migrations conflict or won't apply, or when a deployment step (`collectstatic`, WSGI/ASGI load) breaks. For non-Django Python build errors use `dev-build-error-resolver`; for code-quality review use `dev-django-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-go`

- **Description**: Use to resolve Go build errors — module versioning and resolution failures, dependency conflicts, generics constraint failures, build-tag misalignment, and `go vet` failures. Triggers when `go build ./...` or `go mod` exits non-zero, when a module version conflict blocks the build, or when a constraint or build tag breaks compilation. For non-Go toolchains use the matching variant; for code-quality review use `dev-go-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-java`

- **Description**: Use to resolve Java build errors on Maven or Gradle — classpath conflicts, dependency version/scope mismatches, annotation-processor failures, and Spring Boot autoconfiguration issues. Triggers when `mvn` or `gradle` exits non-zero, when a dependency scope or version conflict breaks compilation, or when an annotation processor or autoconfiguration fails. For non-Java toolchains use the matching variant; for code-quality review use `dev-java-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-kotlin`

- **Description**: Use to resolve Kotlin/Gradle build errors — classpath conflicts, Android dependency resolution, KMP target configuration, version-catalog issues, and `kotlinc` compilation failures. Triggers when `./gradlew build` exits non-zero, when an Android or KMP dependency conflict blocks the build, or when an `expect`/`actual` mismatch breaks compilation. For non-Kotlin toolchains use the matching variant; for code-quality review use `dev-kotlin-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-ms`

- **Description**: Use to resolve Microsoft-stack build errors — MSBuild failures, NuGet restore and version conflicts, .NET SDK/target-framework mismatches, and F# (`fsc`) compilation errors. Triggers when `dotnet build` or `msbuild` exits non-zero, when NuGet restore conflicts block the build, or when a target-framework or F# type error breaks compilation. For non-Microsoft toolchains use the matching variant; for F# code-quality review use `dev-fsharp-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-pytorch`

- **Description**: Use to resolve PyTorch errors — CUDA/version mismatches, dtype/tensor-shape errors, gradient/numerical-stability issues, dataloader bottlenecks, and training-runtime failures. Triggers when a PyTorch import or CUDA init fails, when a shape/dtype error stops training, or when NaN/inf appears in the loss. For non-PyTorch Python build errors use `dev-build-error-resolver`; for general Python review use `dev-python-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-build-error-resolver-rust`

- **Description**: Use to resolve Rust build errors — borrow-checker failures, trait-bound failures, lifetime mismatches, feature-flag conflicts, and edition-migration errors. Triggers when `cargo build` exits non-zero, when the borrow checker or a trait bound blocks compilation, or when a feature-flag or crate-version conflict breaks the build. For non-Rust toolchains use the matching variant; for code-quality review use `dev-rust-reviewer`.
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-code-implementer`

- **Description**: Use to execute an approved plan. Triggers after the User has explicitly approved a plan ("approved," "go ahead," "ship it") AND the orchestrator has specific implementation steps. Do not use for planning, design decisions, exploratory work, or speculative changes without a plan.
- **Model**: sonnet · **Tools**: Read, Write, Edit, Bash, Grep, Glob

### `dev-code-reviewer`

- **Description**: Use to review completed code against the approved plan and project conventions — for non-AI-dev artifacts only; AI-dev work (agents/, skills/, framework files) goes to `aidev-code-reviewer`. Triggers after dev-code-implementer finishes a logical change, before push to a protected branch, when the User asks for review, or as Auditor #1 in the dual-auditor protocol. Do not use to write or modify code (read-only). Do not use for visual design review (dev-ux-designer) or security-specific review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-cpp-reviewer`

- **Description**: Use to review C/C++ code for memory safety, undefined behavior, RAII compliance, and template footguns — use-after-free, double-free, dangling references, lifetime bugs, raw-pointer ownership, ODR violations. Fires in addition to `dev-code-reviewer` on C/C++ projects. Triggers after a C/C++ change lands, before push to a protected branch, or when the User asks for C/C++-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), build-error resolution (dev-build-error-resolver-cpp), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-database-reviewer`

- **Description**: Use to review database code and design — query patterns, schema/index decisions, migration reversibility, transaction isolation, and injection surface. Pairs with `dev-code-reviewer` on database-touching diffs per the audit-pairing matrix. Triggers after a DB-touching change lands, before push to a protected branch, or when the User asks for DB review. Do not use to write or modify code (read-only). Do not use for Django ORM idiom (dev-django-reviewer), general code quality (dev-code-reviewer), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-django-reviewer`

- **Description**: Use to review Django code for ORM efficiency, migration safety, settings/security hardening, and queryset correctness. Complements `dev-python-reviewer`; fires in addition to `dev-code-reviewer` when a project activates Django review (manage.py present). Triggers after a Django change lands, before push to a protected branch, or when the User asks for Django-specific review. Do not use to write or modify code (read-only). Do not use for general Python idiom (dev-python-reviewer), general code quality (dev-code-reviewer), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-e2e-runner`

- **Description**: Use to execute end-to-end test suites for critical user flows and surface failures with reproduction context. Triggers during the Ship phase per the test-execution gate, when the User asks to run e2e/browser tests, or to classify whether a failing scenario is a real failure or a flake. Do not use to run unit/integration coverage (dev-test-engineer), to design new test cases (dev-test-engineer), or to fix failing tests (route the diagnosis to the implementer).
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-fsharp-reviewer`

- **Description**: Use to review F# code for match exhaustiveness, partial active patterns, async/computation-expression correctness, and type-driven design — non-exhaustive matches, unjustified mutability, units-of-measure soundness, algebraic-data-type modeling. Fires in addition to `dev-code-reviewer` when a project activates F# review. Triggers after an F# change lands, before push to a protected branch, or when the User asks for F#-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), C# review, or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-go-reviewer`

- **Description**: Use to review Go code for goroutine/channel correctness, error-handling discipline, interface design, and context propagation — goroutine leaks, channel deadlocks, error wrapping/comparison, nil-interface traps, defer pitfalls. Fires in addition to `dev-code-reviewer` when go.mod is present. Triggers after a Go change lands, before push to a protected branch, or when the User asks for Go-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), build-error resolution (dev-build-error-resolver-go), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-java-reviewer`

- **Description**: Use to review Java code for null-safety, equals/hashCode contracts, resource management, and concurrency — concurrent-collection misuse, generics variance, Spring annotation correctness, missing transaction boundaries. Fires in addition to `dev-code-reviewer` when pom.xml or build.gradle present. Triggers after a Java change lands, before push to a protected branch, or when the User asks for Java-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), build errors (dev-build-error-resolver-java), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-kotlin-reviewer`

- **Description**: Use to review Kotlin code for null-safety, coroutine scope management, data-class correctness, and platform-type traps — force-unwraps, GlobalScope leaks, lifecycle awareness, KMP expect/actual discipline. Fires in addition to `dev-code-reviewer` when build.gradle.kts is present. Triggers after a Kotlin change lands, before push to a protected branch, or when the User asks for Kotlin-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), build errors (dev-build-error-resolver-kotlin), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-loop-operator`

- **Description**: Use to MONITOR a running autonomous non-AI-dev loop (a long batch script, a multi-step build workflow) for stalls — detect infinite loops, repeated no-progress steps, and drift from the goal, then flag and recommend an orchestrator pause. Read-only observer; the dev analog of aidev-loop-operator. Triggers when an approved long-running workflow needs a stall watchdog. Do not drive or advance the loop (the orchestrator does that), do not audit a diff (the auditor pair), and do not monitor agentic AI-dev loops (aidev-loop-operator).
- **Model**: sonnet · **Tools**: Read, Grep, Glob

### `dev-planner`

- **Description**: Use to convert a sharpened software-dev vision (or concrete User request) into a binding plan at .development/plans/active.md, routing work items to dev-/data-/gh- specialists from the active roster. Software-dev scope only. Triggers when a vision is settled but no plan exists, or 'what would it take to add/fix/refactor X'. Do not use for AI-dev/finance/business-ops planning (aidev-planner / fin-planner / biz-planner), tech selection (dev-architect), or framing (dev-visionary).
- **Model**: opus · **Tools**: Read, Grep, Glob, Write

### `dev-python-reviewer`

- **Description**: Use to review Python code for language-specific correctness and idiom — mutable default arguments, `is` vs `==`, exception-swallow patterns, late binding in closures. Fires in addition to `dev-code-reviewer` when a project activates Python review (pyproject.toml / requirements.txt present). Triggers after a Python change lands, before push to a protected branch, or when the User asks for Python-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), Django ORM review (dev-django-reviewer), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-refactor-cleaner`

- **Description**: Use to detect dead code, unused imports, and orphaned helpers across a codebase via static scan and usage-graph walk. Triggers when the User asks to find dead code, when a module feels cluttered with unreachable branches, or when a pre-refactor cleanup pass is requested. Do not use to delete code (flag-only), to review a diff for correctness (dev-code-reviewer), or to clean orphans the current change introduced (the implementer owns those).
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-rust-reviewer`

- **Description**: Use to review Rust code for ownership/borrow correctness, `unsafe` discipline, lifetime soundness, and panic-free paths — unnecessary clones, lifetime-bound mistakes, idiomatic Result/Option use. Fires in addition to `dev-code-reviewer` when Cargo.toml is present. Triggers after a Rust change lands, before push to a protected branch, or when the User asks for Rust-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), build-error resolution (dev-build-error-resolver-rust), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-selector`

- **Description**: Use to select the single smallest safe surviving patch from a validated candidate set produced by the patch-tournament skill — when the tournament hands off 2+ survivors that cleared the validation spine and the orchestrator needs a principled winner before the §16 audit pipeline. Do not use to write or fix code (dev-code-implementer), run §16 auditor passes (dev-code-reviewer), compose candidates or route budget (patch-tournament / codex-budget), or certify release readiness (ops-release-readiness).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, Write

### `dev-test-engineer`

- **Description**: Use to assess test adequacy, design new test cases, identify regression risk, and audit test brittleness. Triggers when a code change ships without tests, when the User asks "what tests should I add," when reviewing test coverage, or as part of dual-auditor pairing for general code changes. Do not use to run release gates (ops-release-readiness) or general code review (dev-code-reviewer).
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash

### `dev-typescript-reviewer`

- **Description**: Use to review TypeScript code for type soundness and language-specific correctness — type narrowing, `any`-leaks, strict-null violations, unsafe assertions, async/promise patterns, React/Node idioms. Fires in addition to `dev-code-reviewer` when tsconfig.json or package.json present. Triggers after a TS change lands, before push to a protected branch, or when the User asks for TS-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), visual design (dev-ux-designer), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

### `dev-ux-designer`

- **Description**: Use to establish or maintain the design system, define screen anatomies, set look-and-feel standards, write microcopy, and review UI changes for design fidelity. Triggers when the User asks about layout, color, typography, spacing, microcopy, or "how should this feel"; when establishing design tokens for a new project; when a UI surface is being added or modified; or as Auditor #2 in the dual-auditor protocol for UI-touching diffs. Do not use for non-UI code (dev-code-reviewer), behavior changes that don't touch UI surface, CLI-only projects, or security review (sec-auditor).
- **Model**: sonnet · **Tools**: Read, Write, Edit, Grep, Glob

### `dev-vba-reviewer`

- **Description**: Use to review VBA macros for language-specific correctness — single-cell array truncation, implicit type coercion, Excel object-model misuse, deprecated functions, and error-handling discipline. Fires in addition to `dev-code-reviewer` when `.bas`/`.cls`/`.frm` or workbook-embedded macros present. Triggers after a VBA change lands, before it ships, or when the User asks for VBA-specific review. Do not use to write or modify code (read-only). Do not use for general code quality (dev-code-reviewer), VBA authoring (data-vba-developer), or security review (sec-auditor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob

### `dev-visionary`

- **Description**: Use for the earliest framing step on non-AI-dev software projects — scripts, tools, services, CLIs, automation, data pipelines, refactors — when the User describes intent in fuzzy terms and you need a one-screen problem statement, success criteria, and refusal scope before any planning happens. Scoped to software-dev work only. Do not use for AI-dev / agent / framework framing (that's `aidev-visionary`), finance / budget / reporting framing (that's `fin-visionary`), business-ops / SOP framing (that's `biz-visionary`), or once a plan already exists.
- **Model**: sonnet · **Tools**: Read, Grep, Glob

## Family: GitHub Project Mechanics

### `gh-dependency-manager`

- **Description**: Use to assess Dependabot / Renovate dependency PRs for breaking-change risk and to propose Dependabot config tuning. Triggers: 'is this Dependabot bump safe to merge', 'review the Renovate PR', 'tune our dependabot.yml'. Do not use for general PR review (gh-pr-reviewer), workflow YAML (gh-workflow-author), release tagging (gh-release-manager), code authoring (dev-code-implementer), or security exploit-chain depth (sec-auditor).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, WebFetch

### `gh-issue-triager`

- **Description**: Use to triage GitHub issues — classify category (bug / feature / question / duplicate), assess severity, propose labels and assignees, and link duplicates. Triggers: 'triage issue #N', 'classify and label this issue', 'is this a duplicate'. Do not use for PR review (gh-pr-reviewer), dependency PRs (gh-dependency-manager), workflow YAML (gh-workflow-author), release work (gh-release-manager), or code authoring (dev-code-implementer). Read-only on issues — never closes.
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash

### `gh-pr-reviewer`

- **Description**: Use to review pull requests on GitHub projects (contributor or maintainer perspective). Triggers: "review PR #N", "audit-pairing row gh-pr-review fires", "score PR comments by severity / tone-tag (constructive | blocker | nit) and emit @@VERDICT", "is this PR ready to approve — check CI first". Do not use to write code (dev-code-implementer), to review repo-internal diffs (dev-code-reviewer / aidev-code-reviewer), or for security exploit-chain depth (sec-auditor — tertiary on this row).
- **Model**: opus · **Tools**: Read, Grep, Glob, Write, Bash, WebFetch

### `gh-release-manager`

- **Description**: Use to make a semver bump decision, assemble release notes, and tag/publish a GitHub release. Dual-role: PLAN classifies patch/minor/major from a diff range and drafts notes; EXECUTE tags and releases after the orchestrator confirms the PLAN. Case-a exemption per ADR-0063. Triggers: 'assemble release notes for X', 'tag and release Y'. Do not use for workflow YAML (gh-workflow-author), scaffolding (gh-repo-scaffolder), PR review (gh-pr-reviewer), or visibility (User only).
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Edit, Bash

### `gh-repo-scaffolder`

- **Description**: Use to initialize a new GitHub repo: assemble the standard project-mechanics file set (README, LICENSE, CODEOWNERS, .github/) from scaled templates, then apply branch protection via gh api. Dual-role implementer + self-auditor (gh-scaffold row; doc-keeper lane 2). Case-a exemption per ADR-0031. Triggers: 'scaffold a repo'. Do not use for workflow YAML (gh-workflow-author), PR review (gh-pr-reviewer), releases (gh-release-manager), or app code (dev-code-implementer).
- **Model**: sonnet · **Tools**: Read, Glob, Write, WebFetch, Bash

### `gh-workflow-author`

- **Description**: Use to author or review GitHub Actions workflow YAML (.github/workflows/*.yml) — jobs, steps, permissions, secrets refs, matrix builds, caching, third-party action SHAs. Dual-role: AUTHOR mode writes workflows; AUDIT mode is auditor_primary on gh-workflow-diff. Triggers: 'write CI workflow for X', 'review workflow permission scoping', 'gh-workflow-diff fires'. Do not use for non-workflow YAML, PR review (gh-pr-reviewer), or repo scaffolding (gh-repo-scaffolder).
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Write, Edit, Bash, WebFetch

## Family: Data Engineering

### `data-cleaner`

- **Description**: Use to clean messy tabular data — malformed rows, inconsistent headers, mixed-language columns, encoding drift, and null patterns — by classifying the mess and proposing a cleaning pipeline. Triggers when a brief hands over a dirty dataset or spreadsheet for normalization or dedup. Do not use to author M transforms (data-power-query-developer), to design workbook structure (data-excel-architect), or to design pivots (data-pivot-architect).
- **Model**: opus · **Tools**: Read, Write, Edit, Grep, Glob, Bash

### `data-excel-architect`

- **Description**: Use to design spreadsheet workbook structure — sheet roles, named ranges, table design, color tokens, navigation patterns, and formula strategy. The spreadsheet analog of dev-ux-designer. Triggers when a brief requests workbook architecture or a design spec before generation. Do not use to author M transforms (data-power-query-developer), VBA macros (data-vba-developer), PivotTable layout (data-pivot-architect), or to clean data (data-cleaner).
- **Model**: opus · **Tools**: Read, Write, Edit, Grep, Glob, Bash

### `data-pivot-architect`

- **Description**: Use to design PivotTable and data-model structure — field roles (Rows/Columns/Values/Filters), slicers, value-field aggregations, and refresh behavior — before any pivot is generated. Triggers when a brief requests pivot design, especially over a Power Query or data-model source. Do not use to author the M source query (data-power-query-developer), design workbook structure (data-excel-architect), author VBA (data-vba-developer), or clean data (data-cleaner).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash

### `data-power-query-developer`

- **Description**: Use to author or review M language code — the functional query language used to build data-transform pipelines — for .pq source files and M code embedded in workbook XML or report files (.xlsx queries, .pbix datasets). Triggers when the orchestrator dispatches as auditor_primary on a data-pq-diff row (audit mode), or when a brief requests new or refactored M transforms (author mode). Do not use for VBA macro authoring/review, general-purpose code implementation in other languages, workbook structure design, or PivotTable design.
- **Model**: opus · **Tools**: Read, Write, Edit, Grep, Glob, Bash

### `data-vba-developer`

- **Description**: Use to author VBA — the procedural BASIC-family language embedded in Office documents — as .bas standard modules, .cls class modules, and .frm form modules per an approved brief. Triggers when a brief requests new or refactored VBA procedures, error-handling rewrites, performance-wrap additions, or object-reference cleanup in a .bas/.cls/.frm module. Do not use for VBA diff audit (route to dev-vba-reviewer), M language authoring (route to data-power-query-developer), general-purpose code implementation in other languages, workbook structure design, or AI-dev artifact authoring.
- **Model**: opus · **Tools**: Read, Write, Edit, Grep, Glob, Bash

## Family: Architecture

### `arch-concept-designer`

- **Description**: Use to generate 2–N distinct concept/schematic massing-and-layout option schemes from a brief + site constraints, compare their tradeoffs, and emit a read-only concept-options document for client/orchestrator choice before detailed BIM. Never mutates the model. Do not use for: detailed BIM/IFC authoring (→ freecad-architect), structural design (→ arch-structural-engineer), MEP (→ arch-mep-engineer), materials/RAL (→ arch-spec-writer), framing (→ arch-visionary), planning (→ arch-planner), 3D render (→ arch-visualizer), code/norm compliance (→ research-fact-checker), cost/QTO (→ fin-*).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, Write

### `arch-documenter`

- **Description**: Use to assemble the issued sheet set — the client-deliverable documentation PDF — from existing model views (IfcConvert SVG/plan output, produced sections/elevations) and schedules, applying titleblocks, sheet numbering, and layout. Never mutates the model. Do not use for BIM model edits (→ freecad-architect), 3D/photoreal render (→ arch-visualizer), PDF dim extraction (→ arch-pdf-extractor), material/finish/RAL spec (→ arch-spec-writer), or model-vs-drawing audit (→ freecad-model-auditor).
- **Model**: opus · **Tools**: Read, Write, Bash, Grep, Glob

### `arch-mep-engineer`

- **Description**: Use to derive MEP system layouts — electrical/water/drainage/heating routes and vent/chimney shafts — for a parametric IFC BIM model, emitting a structured MEP spec and change-order for freecad-architect. Read-only on the model. Do not use for model mutation (→ freecad-architect), structural design (→ arch-structural-engineer), cost/QTO (→ fin-* family), code/norm compliance (→ research-fact-checker), model-vs-drawing audit (→ freecad-model-auditor), or PDF dim extraction (→ arch-pdf-extractor).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash

### `arch-pdf-extractor`

- **Description**: Use to perform rotation-corrected dimension extraction from architectural PDF drawings — produces structured, verifiable dimension data (sills, heads, openings, levels, grid spacing) from vector content, read-only. Triggers when extracting dimensions from a rotated architectural PDF, calibrating scale from grid or face-pair, or producing a verifiable table for downstream model audit. Do not use for model edits or IFC/BIM authoring (→ freecad-architect) or model-vs-drawing audit verdict (→ freecad-model-auditor).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash

### `arch-planner`

- **Description**: Use to convert a sharpened architecture vision (or concrete client request) into a binding plan at .development/plans/active.md, sequencing the project by discipline dependency and routing work items to the arch-* family. Architecture scope only. Triggers when a vision is settled but no plan exists, or 'what would it take to take this house/dwelling from brief to issued documentation'. Do not use for AI-dev/software/finance/business-ops planning, framing (→ arch-visionary), tech selection (→ dev-architect), or model edits (→ freecad-architect).
- **Model**: opus · **Tools**: Read, Grep, Glob, Write

### `arch-spec-writer`

- **Description**: Use to select and derive material/finish/RAL-colour assignments for a parametric IFC BIM model, emit a material change-order (IfcMaterial/IfcMaterialLayerSet/IfcSurfaceStyle/RAL→colour) for freecad-architect, and author the materials/finishes schedule and BOM document. Read-only on model geometry. Do not use for applying materials/IFC writes (→ freecad-architect), cost/QTO pricing (→ fin-* family), model-vs-drawing audit (→ freecad-model-auditor), PDF extraction (→ arch-pdf-extractor), material-property facts (→ research-fact-checker), or AI-dev files (→ aidev-code-implementer).
- **Model**: opus · **Tools**: Read, Grep, Glob, Write

### `arch-structural-engineer`

- **Description**: Use to derive the structural design for a parametric IFC BIM model — foundation-system selection (piles/grillage/plinth/precast slab), framing layout, lintel scheduling — and emit a structural spec + change-order for freecad-architect. Read-only on the model. Do not use for model edits (→ freecad-architect), model-vs-drawing audit (→ freecad-model-auditor), PDF extraction (→ arch-pdf-extractor), code-compliance verdicts (→ research-fact-checker), cost/QTO (→ fin-* family), or AI-dev framework files (→ aidev-code-implementer).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, Write

### `arch-visionary`

- **Description**: Use for the earliest framing step on architectural projects — house/dwelling/extension/renovation — when the User describes intent in fuzzy terms and you need a one-screen problem statement, success criteria, and refusal scope before any planning or design. Scoped to architecture/built-environment work only. Do not use for AI-dev/software/finance/business-ops framing, tech/tool selection, or once a plan already exists (→ arch-planner).
- **Model**: sonnet · **Tools**: Read, Grep, Glob

### `arch-visualizer`

- **Description**: Use to drive the render pipeline — export an IFC model to a renderable scene, bind materials/cameras/lighting from the brief, and produce client-facing 3D/photoreal image artifacts + a render manifest. Never mutates the model or authors the authoritative material spec. Do not use for: BIM model edits/IFC regen (→ freecad-architect), 2D issued sheet-set assembly (→ arch-documenter), authoritative material/RAL spec (→ arch-spec-writer), PDF dim extraction (→ arch-pdf-extractor), model-vs-drawing verdict (→ freecad-model-auditor), concept/massing design (→ arch-concept-designer).
- **Model**: opus · **Tools**: Read, Write, Bash, Grep, Glob

### `freecad-architect`

- **Description**: Use to execute an approved change-order against a parametric IFC BIM model — edits the parametric spec, make/verify/render scripts, and builder modules; regenerates the IFC; runs the BUILD→VERIFY→render loop. The single actor that mutates the model. Do not use for PDF dimension extraction (arch-pdf-extractor), model-vs-drawing audit (freecad-model-auditor), cost/QTO (fin-* family), code-compliance checking (research-fact-checker), general application code (dev-code-implementer), or AI-dev framework-file authoring (aidev-code-implementer).
- **Model**: opus · **Tools**: Read, Edit, Write, Bash, Grep, Glob

### `freecad-model-auditor`

- **Description**: Use to audit a BIM model change before acceptance — drives FreeCAD 1.0 headless from WSL via NativeIFC, independently re-derives dimensions from the authoritative drawing, runs a round-trip fidelity pass, and emits a scored model-vs-drawing verdict. Read-only; never edits the model. Triggers when a BIM model change needs an audit gate, an IFC round-trip must be proven lossless, or a dimension must be independently re-derived. Do not use for BIM model edits (→ freecad-architect) or primary PDF dimension extraction (→ arch-pdf-extractor).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash

## Family: Finance Operations

### `fin-budget-planner`

- **Description**: Use to build a budget and analyze projected-vs-actual variance — business operating budgets, household budgets, savings plans, debt-paydown plans. Triggers: 'build a budget for X', 'why is category Y over budget', 'analyze budget-vs-actual variance for period Z'. Do not use for cash-flow projection (fin-cash-flow-analyst), statement assembly (fin-statement-builder), reconciliation (fin-reconciler), transaction categorization (fin-transaction-categorizer), or tax/investment advice (REFUSE OUTRIGHT).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, Write

### `fin-cash-flow-analyst`

- **Description**: Use to project and analyze cash flow — business runway, personal cash-flow forecasting, what-if scenarios for major decisions (job change, large purchase, hiring, investment timing). Triggers: 'project our runway', 'forecast cash flow through Q4', 'what if we hire in month 3'. Do not use for budget construction/variance (fin-budget-planner), statement assembly (fin-statement-builder), reconciliation (fin-reconciler), or tax/investment advice (REFUSE OUTRIGHT).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, Write

### `fin-planner`

- **Description**: Use to convert a sharpened finance vision into a binding plan at .development/plans/active.md, sequencing budget/cash-flow/reporting/categorization/reconciliation by period dependency. Finance scope only. Triggers when a fin-visionary vision is settled, or 'what would it take to close Q3 / reconcile Account X'. Do not use for AI-dev/software/business-ops planning (aidev-planner / dev-planner / biz-planner), framing (fin-visionary), or tax/investment advice (REFUSE OUTRIGHT).
- **Model**: opus · **Tools**: Read, Grep, Glob, Write

### `fin-reconciler`

- **Description**: Use to reconcile two sources — bank statement vs ledger, two ledgers, statement vs records, business books vs personal records — and classify breaks (timing / amount / classification / missing / duplicate). Triggers: 'reconcile the Sep bank statement to the ledger', 'why don't these two ledgers tie out'. Do not use for transaction categorization (fin-transaction-categorizer), budgets (fin-budget-planner), statement assembly (fin-statement-builder), or tax/investment advice (REFUSE OUTRIGHT).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, Write

### `fin-statement-builder`

- **Description**: Use to assemble standard financial statements — P&L, balance sheet, cash-flow statement for business; net-worth statement, income/expense summary for personal — as a styled .xlsx deliverable matching the statement type's conventions. Triggers: 'build the Q3 P&L', 'assemble a balance sheet from these books'. Do not use for reconciliation (fin-reconciler), budgets (fin-budget-planner), cash-flow projection (fin-cash-flow-analyst), categorization (fin-transaction-categorizer), or tax/investment advice (REFUSE OUTRIGHT).
- **Model**: sonnet · **Tools**: Read, Grep, Glob, Bash, Write

### `fin-transaction-categorizer`

- **Description**: Use to audit categorization-rule and category-schema diffs for domain correctness — right transaction routed to right category, schema invariants hold, edge cases defensible. Triggers as auditor_primary on the fin-categorization-diff row, 'does this rule route transaction X to category Y', or a post-change regression check. Do not use for authoring rules/schema (fin-statement-builder / fin-reconciler), code quality (dev-code-reviewer), or AI-dev review (aidev-code-reviewer).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, Write

### `fin-visionary`

- **Description**: Use for the earliest framing step on finance, budget, cash-flow, reporting, categorization, or reconciliation work — when the User describes intent in fuzzy terms and you need a one-screen problem statement, success criteria, and refusal scope before any planning happens. Scoped to finance work only. Do not use for AI-dev / agent / framework framing (that's `aidev-visionary`), software tool framing (that's `dev-visionary`), business-ops / SOP framing (that's `biz-visionary`), tax or investment recommendations (refuse outright — consult a qualified professional), or once a plan already exists.
- **Model**: sonnet · **Tools**: Read, Grep, Glob

## Family: Business Operations

### `biz-planner`

- **Description**: Use to convert a sharpened business-ops vision into a binding plan at .development/plans/active.md, sequencing SOP/process/workflow design and rollout by role-dependency. Business-ops scope only. Triggers when a biz-visionary vision is settled, or 'what would it take to roll out process X'. Do not use for AI-dev/software/finance planning (aidev-planner / dev-planner / fin-planner), framing (biz-visionary), tech selection (dev-architect), or tax/investment advice (REFUSE OUTRIGHT).
- **Model**: opus · **Tools**: Read, Grep, Glob, Write

### `biz-process-builder`

- **Description**: Use to author one numbered SOP / runbook / process-document artifact at <repo>/docs/sops/<slug>.md from an approved biz-planner plan. Triggers: 'author the SOP at docs/sops/<slug>.md per work item #N', 'write the runbook for <process-name> per the active plan'. Do not use for process framing (biz-visionary), rollout sequencing (biz-planner), SOP completeness audit (biz-process-reviewer), rollout comms (doc-internal-comms), or code/config authoring (dev-code-implementer).
- **Model**: sonnet · **Tools**: Read, Write, Grep, Glob

### `biz-process-reviewer`

- **Description**: Use to audit one SOP / runbook artifact at <repo>/docs/sops/<slug>.md against the approved plan for substance completeness — verifiable outputs, named owners, exception tracing, audit-log compliance — emitting findings plus one @@VERDICT. Triggers: 'audit the SOP at docs/sops/<slug>.md per biz-sop-output row'. Do not use for SOP authoring (biz-process-builder), format/style audit (doc-keeper), or categorization-rule audit (fin-transaction-categorizer).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob

### `biz-visionary`

- **Description**: Use for the earliest framing step on business-process, SOP, workflow, or team-operations work — fuzzy intent into a one-screen problem statement, success criteria, and refusal scope before any planning. Business-ops scope only. Do not use for AI-dev/software/finance framing (aidev-visionary / dev-visionary / fin-visionary), mechanism-shaped automation (route by mechanism: script→dev-visionary, agent→aidev-visionary), or once a plan or SOP already exists.
- **Model**: sonnet · **Tools**: Read, Grep, Glob

## Family: Documentation

### `doc-changelog-keeper`

- **Description**: Use to maintain CHANGELOG.md per Keep-a-Changelog and to flag changes that landed without a changelog entry. Triggers when a diff ships user-visible impact, when a release is being assembled, or when the changelog has drifted from shipped history. Do not use to write user-facing prose docs (doc-keeper / doc-internal-comms), to make semver/release decisions (gh-release-manager), or to audit doc/code drift (doc-keeper).
- **Model**: sonnet · **Tools**: Read, Write, Edit, Grep, Glob, Bash

### `doc-internal-comms`

- **Description**: Use to draft internal comms in NORMAL prose — status updates, leadership memos, project reports, incident reports, FAQs, handoff notes, newsletters. Triggers when a brief requests an internal status note or report and supplies the underlying facts. Do not use to write reference/user-facing product docs (doc-keeper), to maintain changelogs (doc-changelog-keeper), or to make decisions the comm reports on (the orchestrator owns those).
- **Model**: sonnet · **Tools**: Read, Write, Edit, Grep, Glob, Bash

### `doc-keeper`

- **Description**: Use to detect doc/code drift, audit doc accuracy, maintain <repo>/.claude/docs-map.json, and write user-facing documentation. Triggers when docs are modified, when code changes affect documented claims, when docs-map.json needs updating, or during pre-release review. Excludes docs/design-system/ (dev-ux-designer's lane). Do not use for code review (dev-code-reviewer) or design system docs (dev-ux-designer owns those).
- **Model**: sonnet · **Tools**: Read, Write, Edit, Grep, Glob, Bash

## Family: Security

### `sec-auditor`

- **Description**: Use to perform security review on code changes. Triggers when changes touch authentication, secrets, file I/O, network, subprocess invocation, deserialization, cryptography, or dependency manifests. Acts as Auditor #2 in the dual-auditor protocol for security-touching diffs. Do not use for release readiness (ops-release-readiness) or general code quality (dev-code-reviewer).
- **Model**: opus · **Tools**: Read, Write, Grep, Glob, Bash, WebSearch, WebFetch

### `sec-secrets-scanner`

- **Description**: Use to scan a diff or tree for committed secrets — API keys, tokens, private keys, connection strings, high-entropy strings. Triggers before any push, when a diff touches config/env/credential files, when the User asks "are there secrets in here", or as the secrets pass alongside dev-code-reviewer on a security-touching diff. Narrow detection lane. Do not use for full security review (sec-auditor) or general code quality (dev-code-reviewer).
- **Model**: sonnet · **Tools**: Read, Bash, Grep, Glob

## Family: Operations

### `ops-deployment-runner`

- **Description**: Use to execute an approved deployment — version bumps, tags, release notes, publish, push — as discrete verified steps. Triggers after ops-release-readiness returns SHIP and the User has approved the release. Mirrors dev-code-implementer's execution discipline — atomic steps, stop on first non-zero exit, never a deploy step without a verification and a rollback. Do not use to decide whether to ship (ops-release-readiness) or for security review (sec-auditor).
- **Model**: sonnet · **Tools**: Read, Bash, Grep, Glob

### `ops-release-readiness`

- **Description**: Use proactively before merging any PR or tagging a release. Audits the whole change for ship-readiness, not just code quality. Returns SHIP / HOLD / BLOCK with a prioritized fix list. Do not use for per-commit review (dev-code-reviewer) or security-specific review (sec-auditor).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash

## Family: Research

### `research-docs-lookup`

- **Description**: Use to look up API / library / language documentation for non-Anthropic sources (Notion API, openpyxl, Microsoft M language, GitHub Actions docs, etc.) and return the relevant section with a source citation. Triggers: 'look up the openpyxl API for X', 'what does the Notion API say about Y'. Do not use for Anthropic / Claude Code docs (aidev-claude-code-researcher), fact verification (research-fact-checker), or writing code from the docs (dev-code-implementer).
- **Model**: sonnet · **Tools**: WebSearch, WebFetch

### `research-fact-checker`

- **Description**: Use to verify a factual claim (in user input, agent output, or docs) against authoritative sources — identify claim type (current-state / historical / numeric), select the right source class, and return verified / refuted / ambiguous with a confidence score. Triggers: 'verify this claim', 'is this figure correct', 'fact-check this statement'. Do not use for docs lookup (research-docs-lookup), Anthropic-doc checks (aidev-claude-code-researcher), or opinion/prediction questions (REFUSE — not factual).
- **Model**: opus · **Tools**: Read, Grep, Glob, Bash, WebSearch, WebFetch

## Family: Test / E2E Validation

### `test-agent-exerciser`

- **Description**: Use to exercise representative S.A.G.E. roster agents through the documented dispatch path and verify behavior: correct briefing per §17 manifest, lane discipline (refusing out-of-lane asks), memory routed through the keeper (aidev-keeper, the sole store-access agent — not the nook directly), and structured verdicts where applicable. Triggers: Phase 5 agent-behavior. Do not use to run install.sh (test-install-verifier), exercise the nook CLI directly (test-nook-operator), or build the sandbox (test-sandbox-engineer).
- **Model**: sonnet · **Tools**: Bash, Read, Grep

### `test-evidence-reporter`

- **Description**: Use to mechanically aggregate per-phase E2E evidence blocks into the final review-ready report: isolation proof, roster-of-what-was-needed, amendments list, PASS/FAIL table, severity-ranked doc-vs-reality gaps. Assembles and formats; the orchestrator owns the final verdict and any judgment calls. Triggers: end of run, report assembly. Do not use to run tests, score verdicts (the phase agents do that via e2e-evidence-discipline), or decide PASS/FAIL for a phase.
- **Model**: sonnet · **Tools**: Read, Write, Grep, Glob

### `test-install-verifier`

- **Description**: Use to run S.A.G.E.'s install.sh inside a prepared sandbox and verify the install + plugin/MCP boot against what the docs claim. Covers: S.A.G.E. + sage-mcp on PATH, editable install, ~/.claude payload, captured cron entry, plugin payload present, sage-mcp starts with no command-not-found, advertised MCP tools reachable. Triggers: Phase 1 install, Phase 2 plugin/MCP boot. Do not use to build the sandbox (test-sandbox-engineer), to mine/search the nook (test-nook-operator), or to dispatch roster agents (test-agent-exerciser).
- **Model**: sonnet · **Tools**: Bash, Read, Grep, Glob

### `test-nook-operator`

- **Description**: Use to exercise S.A.G.E.'s Nook through the documented CLI inside a sandbox: wing registration, S.A.G.E. init, current-wing marker, S.A.G.E. mine, S.A.G.E. search/recall, and the cross-session memory crux (mine project A, end session, fresh-session wake-up recall, wing A/B isolation). Triggers: Phase 3 start-a-project, Phase 4 search/recall, Phase 6 cross-session memory. Do not use to run install.sh (test-install-verifier), build the sandbox (test-sandbox-engineer), or dispatch roster agents through their personas (test-agent-exerciser).
- **Model**: sonnet · **Tools**: Bash, Read, Write

### `test-sandbox-engineer`

- **Description**: Use to build and PROVE an isolated sandbox for a S.A.G.E. end-to-end run, and to tear it down with proof the real environment is untouched. The only crew agent that constructs the jail (throwaway HOME, redirected CLAUDE_DIR/SAGE_NOOK_PATH/CLAUDE_CONFIG_DIR, fresh venv, crontab shim). Triggers: pre-install sandbox build, isolation proof gate, post-run teardown + real-state diff. Do not use to run install.sh (test-install-verifier), to mine/search the nook (test-nook-operator), or to assemble the report (test-evidence-reporter).
- **Model**: sonnet · **Tools**: Bash, Read, Write

## Family: Media

### `media-indexer`

- **Description**: Use to refine a job package's chapter map — adjust chapter boundaries, rewrite titles and one-line summaries, tune keywords in index.md so the read-index-first navigation is accurate and gap-free. Triggers on 'refine the chapters', 'improve the index', 'the chapter titles/boundaries are off'. Do not use for: running the pipeline (→ media-transcriber), fixing transcription mishears or domain terms (→ media-proofreader), writing a manual/quick-ref from the index (→ media-manual-author), or the deterministic first-pass chapter segmentation that belongs in build_index.py (→ scripts/media/).
- **Model**: opus · **Tools**: Read, Write, Edit, Grep, Glob

### `media-manual-author`

- **Description**: Use to author a quick-reference guide or full manual about a topic from an existing job package — read index.md first, match the topic to chapter(s), load only those segments+frames via the timecode join, compose, render to md/pdf/docx via pandoc/docgen, cite timecodes. Triggers: 'create a quick reference for X', 'write a full manual with screenshots for Y', 'document how the video explains Z'. Do not use for running the pipeline (→ media-transcriber), transcript fixes (→ media-proofreader), chapter refinement (→ media-indexer).
- **Model**: opus · **Tools**: Read, Write, Bash, Grep, Glob

### `media-proofreader`

- **Description**: Use to proofread a transcribed job package — read segments.jsonl, fix transcription mishears, flag uncertain domain terms/acronyms, write proofed.md (timecoded) plus an append-only corrections.md. Triggers: 'proofread the transcript', 'fix the mishears', 'clean up the transcription'. Do not use for running the pipeline / re-transcribing (→ media-transcriber), chapter refinement (→ media-indexer), manual authoring (→ media-manual-author), or deterministic normalization (→ scripts/media/).
- **Model**: opus · **Tools**: Read, Write, Edit, Grep, Glob

### `media-transcriber`

- **Description**: Use to run the scripts/media/ ingestion pipeline end-to-end on a source file and sanity-verify the job package (audio, segments, frames>0, manifest validates, index covers duration). Thin wrapper over proven scripts. Triggers: 'ingest this video/audio', 'transcribe and package <file>', 'run the media pipeline on <source>'. Do not use for transcript fixes (→ media-proofreader), chapter refinement (→ media-indexer), manual authoring (→ media-manual-author), or reimplementing script logic (→ scripts/media/).
- **Model**: sonnet · **Tools**: Read, Bash, Grep, Glob

