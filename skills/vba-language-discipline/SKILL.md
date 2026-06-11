---
name: vba-language-discipline
description: Use when writing or reviewing VBA (.bas / .cls / .frm), reasoning about On Error policy, Set/Nothing pairing, Application-state restore on the error path, late/early binding, or string-concat cost. Triggers on "writing a new VBA Sub or Function", "On Error GoTo or Resume Next", "is every Set released to Nothing on all exit paths". Do not use for test-failure investigation, test-first design, or Object Browser lookups (PAUSE-route those).
---

# VBA Language Discipline

This skill encodes VBA-language-specific decision trees — Option Explicit and explicit Dim discipline, On Error handler policy, Set and Nothing pairing for object references, Application-state performance-flag restore discipline, module-organization choice (.bas / .cls / .frm), string-concatenation cost class, late-bound vs early-bound binding choice, and Object Browser member-reference verification routing — that the consuming agent applies in both author-mode (writing VBA) and audit-mode (reviewing VBA diffs).

This skill co-loads with `test-driven-development` (no overlap) and contributes VBA-specific verification items to `verification-before-completion` without duplicating its general procedure. It does not narrow `systematic-debugging` — that skill's triggers are bug, test failure, unexpected behavior, and stack trace; there is no VBA-authoring entry point in that skill's trigger set.

Three of the eight decision trees are logic-heavy: On Error handler policy requires handler-mode classification and exit-path enumeration; Set/Nothing pairing requires reference-lifecycle tracing across all exit paths including error labels; Application-state restore-on-error-path requires saved-state/restore-pairing verification at every exit. Two trees are mixed: late-bound vs early-bound binding requires capability-honesty classification plus a stated missing-reference scenario. Three trees are summarization-class: Option Explicit grep and implicit-Variant Dim count; string-concatenation O(n²) loop-pattern grep; single-cell-array-trap grep on `.Value` access from single-cell Range. The skill is classified logic-heavy overall because the reference-lifecycle and restore-pairing trees dominate the consumer's reasoning load.

## When this skill binds

Fire this skill when any of these are true:

- You are writing a new VBA Sub or Function in a .bas / .cls / .frm module.
- You are reasoning about whether a procedure should use `On Error GoTo` or `On Error Resume Next`.
- You are checking whether every object reference is Set and released to Nothing on exit.
- You are verifying whether Application.ScreenUpdating / Calculation / EnableEvents wraps have paired restores on the error path.
- You are checking whether a Function returning a single-cell Range.Value falls into the single-cell array trap.
- You are looking up a VBA Object Browser member signature and are uncertain of its exact arguments.
- You are deciding whether to use late-bound `CreateObject` or early-bound typed reference for an external object.
- You are reviewing a string-concatenation loop and suspect O(n²) cost growth.
- You are checking whether Option Explicit is declared at the module head and every Dim is explicitly typed.

Do NOT fire this skill for:

- General test-failure investigation on a VBA procedure → `systematic-debugging`.
- Writing a failing test for a VBA Sub or Function first → `test-driven-development`.
- Claiming a VBA macro is done and verifying it → `verification-before-completion` (this skill contributes VBA-specific items to that check but does not replace it).
- Designing an agent that writes VBA → `agent-creation` via `aidev-agent-creator`.
- Reviewing a VBA diff for overall correctness → `dev-vba-reviewer` [scheduled-annotation: dev-vba-reviewer pending future session (agent defined at docs/reference/agent-roster.md line 414; data-vba-diff matrix row at docs/specs/audit-pairing-matrix.md line 34)].
- Looking up the current VBA Object Browser member signature → emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]` and stop; do not WebFetch or WebSearch directly. (See "When this skill PAUSEs" below for the full broken-route history and orchestrator routing.)
- Any Power Query M language decision → `m-language-discipline`.

## Decision tree 1 — Option Explicit and explicit Dim discipline

Every module must declare `Option Explicit` at the head. Every variable declaration (`Dim`) must carry an explicit `As <Type>` clause. Auto-typed `Variant` from bare `Dim x` is a silent correctness risk: a typo in a variable name creates a new `Variant` that evaluates to `Empty` rather than surfacing a compile error.

**Audit procedure (author-mode and audit-mode):**

1. Grep each .bas / .cls / .frm file for `Option Explicit`. A module without it is a finding: "missing Option Explicit".
2. Grep for `Dim ` in each module. For each `Dim` statement, confirm it carries `As <Type>`. A bare `Dim x` without `As <Type>` is an implicit-Variant finding.
3. Count implicit-Variant Dim occurrences per module. Emit `@@VBA-OPTION-EXPLICIT-AUDIT BEGIN` block (one row per module).

**Author-mode rule:** every module declares `Option Explicit` on line 1. Every `Dim` carries an explicit `As <Type>` before work is considered done.

## Decision tree 2 — On Error handler policy

VBA offers three error-handling modes: `On Error GoTo <label>` (structured — preferred), `On Error Resume Next` (suppression — restricted), and `On Error GoTo 0` (reset — used to close a suppression scope). Bare `On Error Resume Next` without a matching `On Error GoTo 0` within the same procedure suppresses all errors silently for the remainder of the procedure's lifetime, including errors the author did not intend to suppress.

**Placement rules:**

- `On Error GoTo <label>` is the default mode for any procedure with object-creation, file-I/O, or external-object calls.
- `On Error Resume Next` is permitted only for a targeted, named scenario (e.g., "test whether a sheet exists without raising an error"). It must be followed by `Err.Clear` and then `On Error GoTo 0` (or `On Error GoTo <label>`) within the same procedure before any further error-prone statement.
- `On Error Resume Next` without `On Error GoTo 0` (or back to `GoTo <label>`) within the same procedure is a finding: "bare Resume Next — suppression leaks to end of procedure".
- Every procedure using `On Error GoTo <label>` must have a cleanup label that runs on the error path (see Decision tree 3 for the restore requirement).

**Audit procedure:**

1. For each procedure, identify the error-handling mode(s) in use.
2. For every `On Error Resume Next`, confirm a matching `On Error GoTo 0` (or `On Error GoTo <label>`) exists within the same procedure.
3. Emit `@@VBA-ERROR-HANDLING-AUDIT BEGIN` block (one row per procedure).

## Decision tree 3 — Set and Nothing pairing for object references

Every `Set x = <expr>` that initialises an object reference must have a corresponding `Set x = Nothing` at every exit path, including the error-handler label. A reference not released on the error path leaks the object for the duration of the host process's lifetime.

**Pairing rules:**

- Every `Set` site receives a paired `Set x = Nothing` at: the natural end of the procedure AND every early-exit (`Exit Sub`, `Exit Function`) AND the error-handler cleanup label.
- In procedures using `On Error GoTo <CleanupLabel>`, the cleanup label is the single forced exit for the error path — place all `Nothing` releases there and use `Resume CleanupLabel` or fall through to it consistently.
- A `Set x = <expr>` with no corresponding `Set x = Nothing` on any exit path is a finding: "object not released — Nothing missing".

**Audit procedure:**

1. For each procedure, enumerate every `Set` assignment that introduces a new object reference.
2. For each such assignment, trace all exit paths (natural return, `Exit Sub`/`Exit Function`, and error-handler label). Confirm `Set x = Nothing` appears on each path.
3. Emit `@@VBA-OBJECT-LIFECYCLE-AUDIT BEGIN` block (one row per Set assignment).

### Worked example — Set/Nothing pairing with error-path release

Consider a Sub that creates an application instance, opens a workbook, does work, and must release the reference on every exit path:

```vba
Option Explicit

Sub ExportData(ByVal filePath As String)
    Dim appHost    As Object
    Dim wbTarget   As Object
    Dim wsData     As Object

    On Error GoTo Cleanup

    Set appHost  = CreateObject("Excel.Application")
    appHost.Visible = False

    Set wbTarget = appHost.Workbooks.Open(filePath)
    Set wsData   = wbTarget.Worksheets(1)

    ' ... authoring work on wsData ...

Cleanup:
    ' Scoped Resume Next so a secondary error in teardown does not skip remaining Nothing releases; closed at GoTo 0 below.
    On Error Resume Next
    If Not wsData  Is Nothing Then Set wsData  = Nothing
    If Not wbTarget Is Nothing Then
        wbTarget.Close SaveChanges:=False
        Set wbTarget = Nothing
    End If
    If Not appHost Is Nothing Then
        appHost.Quit
        Set appHost = Nothing
    End If
    On Error GoTo 0
    If Err.Number <> 0 Then
        Err.Raise Err.Number, Err.Source, Err.Description
    End If
End Sub
```

Three `Set` sites: `appHost`, `wbTarget`, `wsData`. The cleanup label is the single exit for both natural return and the error path (`On Error GoTo Cleanup`). All three `Set x = Nothing` calls appear there. The guard `If Not x Is Nothing Then` prevents a double-release if the Set assignment itself failed. The `On Error Resume Next` at the head of the Cleanup label is required: teardown calls such as `wbTarget.Close` (file locked, save dialog) or `appHost.Quit` (host already exited, COM disconnected) can themselves raise — without scoped Resume Next a secondary teardown error exits the procedure mid-cleanup, skipping remaining Nothing releases. The matching `On Error GoTo 0` closes the suppression scope after all teardowns are complete and before the re-raise check; this is the scoped Resume Next pattern this skill prescribes (no bare Resume Next without matching GoTo 0).

The `@@VBA-OBJECT-LIFECYCLE-AUDIT` block for this procedure emits the lifecycle state after all teardowns complete:

```
@@VBA-OBJECT-LIFECYCLE-AUDIT BEGIN
variable  | type   | Set site               | Nothing release present | finding
appHost   | Object | Set of appHost          | Cleanup label          | none
wbTarget  | Object | Set of wbTarget         | Cleanup label          | none
wsData    | Object | Set of wsData           | Cleanup label          | none
@@VBA-OBJECT-LIFECYCLE-AUDIT END
```

A version that omits the cleanup label and places `Set x = Nothing` only at natural end would be a finding: the error path does not release the references.

## Decision tree 4 — Application-state performance-flag restore discipline

Host applications expose performance flags (`Application.ScreenUpdating`, `Application.Calculation`, `Application.EnableEvents`, `Application.DisplayAlerts`) that macros disable for speed. A procedure that sets these to `False` and exits on an error path without restoring them leaves the host application in a degraded state for the remainder of the session.

**Restore rules:**

- Every `Application.<Property> = False` must have a paired `Application.<Property> = True` (or restoration to the saved prior value) in the cleanup label, not only at the natural end of the procedure.
- Save the prior value before disabling: `Dim calcMode As XlCalculation : calcMode = Application.Calculation`. Restore to `calcMode`, not to a hard-coded constant, when the prior value is not guaranteed to be `xlCalculationAutomatic`.
- A restore present only at natural-end (outside the error-handler cleanup label) is a finding: "restore missing on error path — Application state leaks on error exit".

**Audit procedure:**

1. Grep the procedure for `Application.ScreenUpdating`, `Application.Calculation`, `Application.EnableEvents`, `Application.DisplayAlerts` set to `False` or a disabling constant.
2. For each, confirm the restore appears inside the cleanup label (not only after the `Exit Sub` / `Exit Function` natural exit).
3. Emit `@@VBA-APP-STATE-AUDIT BEGIN` block (one row per Application-property wrap).

## Decision tree 5 — Module-organization choice

VBA organises code across three module types: standard modules (.bas — no instance state, utility procedures and macros), class modules (.cls — instance state, `Class_Initialize` / `Class_Terminate`, event sinks), and form modules (.frm — bound to a UserForm, contains UI event handlers). Each type has capabilities the others do not.

**Placement rules:**

- Form event handlers (e.g., `CommandButton1_Click`, `UserForm_Initialize`) belong in .frm; placing them in .bas requires manual connection and loses the automatic event-sink wiring.
- `Class_Initialize` and `Class_Terminate` belong in .cls; they have no meaning in .bas or .frm.
- Utility Subs and Functions with no instance state belong in .bas. Placing stateless utilities in .cls adds per-instance allocation cost with no benefit.
- A form event handler in .bas or a `Class_Initialize` in .frm is a finding: "module-type capability mismatch".

**Audit procedure:**

1. For each procedure being authored or reviewed, identify its type (event handler, class lifecycle, utility).
2. Confirm the target module type matches the procedure's capability requirement.
3. Emit `@@VBA-MODULE-ORG-DECISION BEGIN` block when introducing or moving code.

## Decision tree 6 — String-concatenation cost class

In VBA, the `&` operator builds a new string by copying both operands into a new allocation on each call. A loop that accumulates `s = s & element` for N elements performs O(N²) copy work — a 10,000-element loop copies roughly 50 million characters for a string of average element length 10.

**Detection rule:**

- Grep for `& ` or `&"` inside any `For`, `Do`, or `While` loop body.
- For each hit, assess whether the accumulated string grows with iteration count.
- If yes, surface as an inline finding: "O(n²) string-concat trap — use array join pattern instead (`Join(arr, delimiter)` after building a `String` array, or write to a `Collection` and join at loop exit)".
- Single-concatenation operations outside a loop are not findings.

## Decision tree 7 — Late-bound vs early-bound binding

Early-bound references (declared `As LibraryName.ClassName`, with the library set as a reference) give compile-time type checking and IntelliSense. Late-bound `CreateObject("ProgID")` avoids requiring the reference to be registered but loses compile-time safety and may run slower on repeated instantiation.

**Binding rules:**

- Default choice is early-bound. Early-bound requires the library to be registered and referenced; it fails at compile time if missing rather than at runtime in a user session.
- Late-bound is justified only when: (a) the library may not be registered on all target machines, or (b) the macro is distributed as a shared template across machines with differing library versions. The justification must be stated explicitly in the `@@VBA-BINDING-DECISION` block rationale.
- A late-bound `CreateObject` without a stated missing-reference scenario in the rationale is a finding: "late-bound without stated justification".

**Audit procedure:**

1. Grep for `CreateObject(`.
2. For each call, check whether the rationale for late binding is stated.
3. Emit `@@VBA-BINDING-DECISION BEGIN` block (one row per external object reference introduced).

## Decision tree 8 — Single-cell array trap

`Range.Value` on a multi-cell Range returns a two-dimensional Variant array. `Range.Value` on a single-cell Range returns a scalar. Code that branches on `IsArray(rng.Value)` to detect the array case silently receives a scalar for single-cell selections, producing a different behavior path. This is the single-cell array trap.

**Detection rule:**

- Grep for `.Value` applied to a Range variable or expression.
- If the range might be single-cell (e.g., user-selected range, named range with dynamic extent, function argument typed `As Range`), confirm the code does not assume array behavior unconditionally.
- A `IsArray` check that does not handle the single-cell scalar case is a finding: "single-cell array trap — `.Value` on single-cell Range returns scalar, not array".
- If the range is guaranteed multi-cell by construction (e.g., a fixed `Range("A1:B10")`), the trap is not a finding.

Surface single-cell-array-trap findings inline (one line per finding).

## Function-reference verification

Never guess a VBA Object Browser member signature. If you are uncertain whether a host-application method takes a particular argument order or whether an optional parameter is positional or named, emit:

```
PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]
```

Stop there. Do not attempt the call with a guessed signature. A wrong argument order in a host-application call may silently pass the wrong value rather than raising a runtime error.

### When this skill PAUSEs

The PAUSE shape above is the ADR-0027 pattern. `research-docs-lookup` does not yet exist in the active roster. `aidev-claude-code-researcher` explicitly refuses non-Anthropic documentation queries — that is the broken route; it has been excluded from this skill and is why `research-docs-lookup` is the named receiver.

When the PAUSE fires, the orchestrator's established convention routes it to the User: the named agent's manifest absence triggers User-escalation fallback per the orchestrator's general dispatch behavior on unresolved agent names. ADR-0027 (this skill's binding source) cites ADR-0024 only as a directional precedent for the gap-naming-with-user-action-remediation pattern, not as the specific authority for PAUSE routing; the orchestrator routing itself is convention rather than ADR-codified. If a future ADR formalizes the unresolved-agent-name PAUSE routing rule, this subsection updates to cite it directly.

When `research-docs-lookup` lands per agent-roster.md step 13, the scheduled-annotation resolves and the PAUSE routes directly to that agent. If research-docs-lookup ships with the PAUSE shape this skill emits today, no skill edit is required (same-shape resolution); if its design diverges, a follow-on ADR aligns shapes and this skill amends accordingly per ADR-0021 brief-correction discipline.

## Output blocks

The consuming agent emits structured blocks for each decision tree applied. All blocks use the delimiter pattern established across the agent roster.

**Option Explicit audit:**
```
@@VBA-OPTION-EXPLICIT-AUDIT BEGIN
module path | Option Explicit present | implicit-Variant Dim count | finding
@@VBA-OPTION-EXPLICIT-AUDIT END
```

**Error handling audit:**
```
@@VBA-ERROR-HANDLING-AUDIT BEGIN
procedure name | error-handling mode (GoTo label | Resume Next scoped | bare Resume Next | none) | rationale | finding
@@VBA-ERROR-HANDLING-AUDIT END
```

**Object lifecycle audit:**
```
@@VBA-OBJECT-LIFECYCLE-AUDIT BEGIN
variable | type | Set site | Nothing release present (cleanup label | natural-end only | missing) | finding
@@VBA-OBJECT-LIFECYCLE-AUDIT END
```

**Application-state audit:**
```
@@VBA-APP-STATE-AUDIT BEGIN
property | save-restore wrap (yes | no) | restore on error path (yes | no) | finding
@@VBA-APP-STATE-AUDIT END
```

**Module-organization decision:**
```
@@VBA-MODULE-ORG-DECISION BEGIN
subject | candidate module types | decision (.bas | .cls | .frm) | rationale
@@VBA-MODULE-ORG-DECISION END
```

**Binding decision:**
```
@@VBA-BINDING-DECISION BEGIN
object | binding choice (early-bound | late-bound) | rationale (≤1 line; late-bound must name missing-reference scenario)
@@VBA-BINDING-DECISION END
```

String-concatenation O(n²) findings surface inline (one line per finding), not in a block.

Single-cell-array-trap findings surface inline (one line per finding), not in a block.

Object Browser member-reference uncertainty surfaces as a standalone `PAUSE:` line before any code is emitted.

## Anti-patterns

- **Bare `On Error Resume Next` without matching `On Error GoTo 0` within the same procedure.** Silent suppression leaks to the end of the procedure. Every `Resume Next` scope must close with `On Error GoTo 0` or `On Error GoTo <label>` before the next error-prone statement.
- **`Set x = <expr>` without paired `Set x = Nothing` at every exit path including error labels.** References not released on the error path leak for the duration of the host process's lifetime.
- **`Application.<Property> = False` with restore only at the natural end of the procedure.** The host application state leaks on any error-path exit. Restore must appear in the cleanup label.
- **Defaulting to late-bound `CreateObject` without a stated missing-reference scenario.** Late-bound loses compile-time type checking. Early-bound is the default; late-bound requires a named justification in the `@@VBA-BINDING-DECISION` rationale.
- **`s = s & x` in a loop for large N.** O(n²) string-concatenation trap. Use array-accumulate-then-`Join` or equivalent.
- **Trusting auto-typed Variant from single-cell `Range.Value` return.** Single-cell Range.Value returns a scalar, not an array. Code that unconditionally applies array operations to the result is the single-cell array trap.
- **Form event handlers in .bas or `Class_Initialize` in .frm.** Module-type capability mismatch — the event-sink wiring is absent outside the module type that owns the event.
- **Missing `Option Explicit` combined with implicit-Variant `Dim`.** A typo in a variable name creates a new auto-Variant at `Empty` rather than a compile error, producing silent wrong-value propagation.

## Output guidance

### Semantic guidance

- Never claim a VBA procedure is "correct" without naming: (1) Option Explicit state of the module, (2) every `Set` assignment's `Nothing` release state across all exit paths, and (3) every Application-property disable/restore pair's error-path restore state.
- Never insert `On Error Resume Next` without a matching `On Error GoTo 0` (or `On Error GoTo <label>`) within the same procedure.
- Never insert `Application.ScreenUpdating = False` (or Calculation / EnableEvents / DisplayAlerts) without a paired restore in the cleanup label, not only at natural end.
- Default choice is early-bound. Late-bound requires a stated missing-reference scenario in the `@@VBA-BINDING-DECISION` rationale.
- No product names beyond unavoidable file extensions (.bas, .cls, .frm) and host-application names where the macro is bound to the host (e.g., `Excel.Application` in a macro that opens an Excel workbook — unavoidable case-b per ADR-0023).
- In author-mode: every module declares `Option Explicit` and every `Dim` carries an explicit `As <Type>` before work is considered done.
- Single-cell-array trap is a named finding whenever `.Value` is applied to a Range that may be single-cell by construction.

### Tool guidance

- **Read** — view the .bas / .cls / .frm file in full before applying any decision tree (CLAUDE.md §4; do not edit a file you have not read).
- **Grep** — scan for: `Option Explicit`, `Dim ` (implicit-Variant check), `On Error `, `Set `, `= Nothing`, `Application.ScreenUpdating`, `Application.Calculation`, `Application.EnableEvents`, `Application.DisplayAlerts`, `& ` inside loop bodies (string-concat), `CreateObject(`, `.Value` on Range expressions.
- **Glob** — locate .bas / .cls / .frm files when the brief names a module area without an exact path.
- **No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.
- **No WebFetch or WebSearch** — Object Browser member-reference uncertainty emits a `PAUSE:` line only (ADR-0027 shape); the orchestrator routes to `research-docs-lookup` or to the User until that agent ships.

## When NOT to use this skill

- General test-failure investigation on any VBA procedure → `systematic-debugging`.
- Test-first design for any VBA Sub or Function → `test-driven-development`.
- General pre-completion verification → `verification-before-completion` (load this skill alongside it for the VBA-specific items, but `verification-before-completion` governs the overall procedure).
- Looking up a VBA Object Browser member signature → emit `PAUSE: need research-docs-lookup for <subject> reference lookup [scheduled-annotation: agent pending future session per agent-roster.md step 13]`; the orchestrator routes this to the User until `research-docs-lookup` ships (ADR-0027).
- Reviewing a VBA diff for overall correctness → `dev-vba-reviewer` [scheduled-annotation: dev-vba-reviewer pending future session (agent defined at docs/reference/agent-roster.md line 414; data-vba-diff matrix row at docs/specs/audit-pairing-matrix.md line 34)].
- Any language other than VBA.
- Any Power Query M language decision → `m-language-discipline`.
