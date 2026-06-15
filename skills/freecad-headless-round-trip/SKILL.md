---
name: freecad-headless-round-trip
description: "Use when driving FreeCAD 1.0 headless from WSL for a NativeIFC import/export round-trip audit, asking whether layers surface after expansion, why annotations do not appear in FreeCAD 1.0, or confirming geometry survives a round-trip. Do not use for genuine round-trip failures (→ systematic-debugging); claiming audit done (→ verification-before-completion); designing the auditor agent (→ aidev-agent-creator)."
---

# FreeCAD Headless Round-Trip Discipline

This skill encodes three decision trees — round-trip fidelity assessment, platform-limitation classification, and genuine-defect finding — that the consuming agent applies when auditing a NativeIFC import/export round-trip in FreeCAD 1.0 driven headlessly from WSL. The skill distinguishes documented FreeCAD platform limitations (non-findings) from genuine model defects (findings requiring remediation).

This skill co-loads with `test-driven-development` (no overlap) and contributes FreeCAD-specific verification items to `verification-before-completion` without duplicating its general procedure. It does not narrow `systematic-debugging` — that skill's triggers are bug, test failure, unexpected behavior, and stack trace; round-trip crash or unexpected failure routes there, not here.

All three decision trees are logic-heavy: round-trip fidelity requires element-class-level pre/post counting and bit-exact vertex comparison (not visual inspection); platform-limitation classification requires knowledge of documented NativeIFC and FreeCAD 1.0 behaviors that produce gaps by design; genuine-defect finding requires independently re-deriving the authoritative value before any finding is emitted. The consuming agent (freecad-model-auditor) should apply CoT throughout all three trees.

## When this skill binds

Fire this skill when any of these are true:

- You are invoking FreeCADCmd from WSL to import or export an IFC model via NativeIFC.
- You are asking whether element layers surface in the FreeCAD document after import.
- You are asking why annotations, embedded dimensions, or text do not appear in FreeCAD 1.0.
- You are confirming whether geometry survives a round-trip (import → export → re-import).
- You are classifying a round-trip gap as a platform limitation vs a model defect.

Do NOT fire this skill for:

- Round-trip genuinely fails or FreeCADCmd crashes → `systematic-debugging`.
- Claiming the audit is complete → `verification-before-completion`.
- Running a full S.A.G.E. install sandbox validation → `sandbox-isolation-protocol`.
- Designing the FreeCAD auditor agent → `agent-creation` via `aidev-agent-creator`.

## Decision tree 1 — Round-trip fidelity

**"Lossless" has a precise definition.** A round-trip is lossless for a given element class only when all three conditions hold simultaneously: Δ count = 0, vertex coordinates are bit-exact between pre- and post-round-trip, and the bounding box matches. "Looks the same" in a rendered view does not satisfy the lossless criterion.

**Procedure:**

1. Before import, count every element by IFC class in the source model. Record as pre-count.
2. Run the NativeIFC import via FreeCADCmd (see invocation rules below).
3. After import, call `ifc_tools.create_children(obj, recursive=True)` to expand all child objects. Without this call layers and nested objects do not exist in the Python document tree.
4. Count elements by class in the FreeCAD document. Record as post-count.
5. For each class, compute Δ = post-count − pre-count.
6. For a sample of geometry-bearing elements, compare vertex coordinates and bounding boxes.
7. **Qto fidelity.** NativeIFC surfaces `IfcElementQuantity` / `BaseQuantities` sets on import — they are accessible post-import and are not discarded like `IfcAnnotation`. The round-trip fidelity comparison therefore includes Qto presence and values: a Qto set present pre-round-trip but absent post-round-trip is a round-trip finding (distinct from the IfcAnnotation platform limitation in Decision tree 2) and is reported as a genuine defect per Decision tree 3. For re-derived scalar quantity values (area, volume, length re-computed from geometry), the value comparison uses a small relative tolerance (e.g. 1e-6) — bit-exact is the bar for vertex coordinates, not for floating-point scalar quantities re-derived through a different code path. The finding is absence of the Qto set, or a value differing beyond that relative tolerance; a sub-tolerance floating-point difference is not a genuine defect.
8. Emit `@@FREECAD-ROUNDTRIP BEGIN` block.

```
@@FREECAD-ROUNDTRIP BEGIN
element class | pre-count | post-count | Δ | vertex bit-exact (yes | no | not checked) | bbox-match (yes | no | not checked) | lossless (yes | no)
@@FREECAD-ROUNDTRIP END
```

A Δ ≠ 0 is a finding unless it is explained by a classified platform limitation (see Decision tree 2). A bit-exact failure on non-annotation geometry that is not explained by a platform limitation is a genuine defect (Decision tree 3).

## Decision tree 2 — Platform-limitation classification

FreeCAD 1.0 NativeIFC has documented behaviors that produce round-trip gaps by design. These are non-findings — they require documentation, not remediation.

**Known platform limitations (non-findings) as of FreeCAD 1.0.x:**

**Limitation 1 — Annotations discarded by NativeIFC design.** NativeIFC `ifc_tools.filter_elements` discards every `IfcAnnotation` entity by design (source: upstream comment "skip annotations for now"). Embedded dimensions, text labels, section markers, and north arrows modelled as `IfcAnnotation` never appear in FreeCAD 1.0 after NativeIFC import. Concluding "annotation geometry is missing — model defect" without classifying this as a platform limitation is a false positive.

**Limitation 2 — Legacy importIFC is unwired and crashes headless.** FreeCAD 1.0's `Init.py` registers only `nativeifc.ifc_import` as the IFC import handler; the legacy `importIFC` module is no longer wired. Calling `importIFC.insert(path, docname)` headlessly produces a `settings.USE_BREP_DATA` `AttributeError` (the attribute was removed from the bundled ifcopenshell). Additionally, `exportIFC` fails headless with an `UnboundLocalError` on `reps`. The round-trip export step must use NativeIFC's own save method, not `exportIFC`.

**Classification procedure:**

1. For each round-trip gap, check whether it matches a known limitation.
2. If yes, classify as PLATFORM-LIMITATION, cite the limitation number, and do not escalate to Decision tree 3.
3. If no match, escalate to Decision tree 3 for genuine-defect assessment.
4. Emit `@@FREECAD-LIMITATION BEGIN` block for every classified gap.

```
@@FREECAD-LIMITATION BEGIN
observed gap | classification (platform-limitation | model-defect) | cited cause | FreeCAD version
@@FREECAD-LIMITATION END
```

Always state the FreeCAD version (`1.0.x` as applicable) alongside any limitation claim. Limitation scope is version-bound; a future release may resolve a current limitation.

## Decision tree 3 — Genuine-defect finding

A genuine defect is a round-trip gap that is not explained by a classified platform limitation and represents a deviation from the model's authoritative state.

**Finding rules:**

- Re-derive the authoritative value independently. Do not trust the implementer's stated dimensions or counts. Read the source IFC file directly using ifcopenshell (not FreeCAD) to establish the authoritative pre-round-trip state.
- Compare the independently derived authoritative value against the post-round-trip state.
- If they differ and no platform limitation explains the difference, emit a finding.
- The audit is read-only — this skill never repairs the model. Findings are reported; remediation is out of scope.

**Emit `@@FREECAD-AUDIT-FINDING BEGIN` block (genuine defects only):**

```
@@FREECAD-AUDIT-FINDING BEGIN
element class | authoritative value (independently derived) | post-round-trip state | deviation | finding severity (informational | moderate | blocking)
@@FREECAD-AUDIT-FINDING END
```

## Invocation rules

> **REFERENCE:** Temp-cwd isolation, WSL-only %TEMP% path translation, and the fail-closed no-new-untracked-files assertion are governed by `freecad-wsl-invocation-hygiene` (single source of truth) — do NOT duplicate those rules here.

**FreeCADCmd path from WSL:**

```bash
"/mnt/c/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe" <script.py>
```

Quote the path — it contains spaces. The `.exe` extension is required from WSL.

**Temp-file isolation:** before import, copy the `.ifc` file to `%TEMP%` (accessible from WSL as `/mnt/c/Users/<user>/AppData/Local/Temp/`). Clean up after the audit. Never write to or overwrite the original model file.

**Config isolation:** never write to the real `~/.FreeCAD` config during audit. If a FreeCAD script requires config access, verify it targets the temp location. A script that modifies the real FreeCAD config during an audit run is a safety violation — stop and report.

**NativeIFC import syntax:**

```python
from nativeifc import ifc_import
import FreeCAD
doc = FreeCAD.newDocument(docname)
ifc_import.insert(path_to_ifc, docname)
```

Do not use `importIFC.insert` — it is unwired and crashes headless (see Limitation 2 above).

**Child-object expansion (required before layer/object counts):**

```python
from nativeifc import ifc_tools
for obj in doc.Objects:
    ifc_tools.create_children(obj, recursive=True)
```

Calling `doc.Objects` before expansion returns only the root IFC document object. Layers and all element objects exist only after `create_children` is called. A layer count of zero before expansion is a false negative, not a model defect.

**Round-trip export:** use NativeIFC's own save, not `exportIFC`. The correct pattern is to call the document's save method via the NativeIFC API. Do not use `exportIFC.export` headlessly — it fails with `UnboundLocalError` on `reps` (Limitation 2).

## Inline invariants

These hold unconditionally before any decision tree is entered.

**Read-only.** This skill is read-only. The audit never repairs the model, modifies the source IFC, or writes to any production path. Findings are reported; remediation is a separate, explicitly approved step.

**Independent re-derivation.** Authoritative values are derived by reading the source IFC directly using ifcopenshell (not FreeCAD) as an independent reference. Accepting the implementer's stated counts or dimensions without independent verification is a false baseline.

**Version-scoped limitation claims.** Every platform-limitation classification names the FreeCAD version (`1.0.x`) in the `@@FREECAD-LIMITATION` block. A limitation claim without a version is unscoped and cannot be re-assessed when the version changes.

## Output blocks

The consuming agent emits structured blocks for each decision tree applied. All blocks use the delimiter pattern established across the agent roster.

**Round-trip fidelity:**
```
@@FREECAD-ROUNDTRIP BEGIN
element class | pre-count | post-count | Δ | vertex bit-exact (yes | no | not checked) | bbox-match (yes | no | not checked) | lossless (yes | no)
@@FREECAD-ROUNDTRIP END
```

**Platform limitation:**
```
@@FREECAD-LIMITATION BEGIN
observed gap | classification (platform-limitation | model-defect) | cited cause | FreeCAD version
@@FREECAD-LIMITATION END
```

**Genuine defect finding:**
```
@@FREECAD-AUDIT-FINDING BEGIN
element class | authoritative value (independently derived) | post-round-trip state | deviation | finding severity (informational | moderate | blocking)
@@FREECAD-AUDIT-FINDING END
```

## Anti-patterns

- **Counting child objects before calling `ifc_tools.create_children(obj, recursive=True)`.** Child objects and layers do not exist in the Python document tree until after expansion. A zero layer count before expansion is a false negative, not a model defect.
- **Calling `importIFC.insert` headlessly.** The legacy module is unwired and crashes with `AttributeError` on `settings.USE_BREP_DATA`. Use `nativeifc.ifc_import.insert` instead.
- **Using `exportIFC.export` headlessly.** Fails with `UnboundLocalError` on `reps`. Use NativeIFC's own save.
- **Claiming "no annotations" without classifying Limitation 1.** NativeIFC discards all `IfcAnnotation` by design. Missing annotations in FreeCAD 1.0 are a platform limitation, not a model defect.
- **Accepting "looks the same" as lossless.** Lossless requires Δ = 0, bit-exact vertices, and matching bbox. Visual similarity is not sufficient.
- **Trusting the implementer's stated counts or dimensions as the authoritative baseline.** Always re-derive from the source IFC independently using ifcopenshell.
- **Writing to the real `~/.FreeCAD` config during an audit run.** Audit is read-only. Config isolation is required — use temp paths.
- **Omitting the FreeCAD version from a platform-limitation claim.** Version-unscoped limitation claims cannot be re-assessed when the version changes.
- **Repairing the model during the audit.** The skill is read-only. Remediation is a separate, explicitly approved step outside this skill's scope.

## Output guidance

### Semantic guidance

- Never claim a round-trip is lossless without confirming Δ = 0, bit-exact vertices, and bbox-match for the assessed element classes.
- Never call a missing `IfcAnnotation` a model defect — it is a FreeCAD 1.0 NativeIFC platform limitation (Limitation 1).
- Every `@@FREECAD-LIMITATION` block names the FreeCAD version.
- Every `@@FREECAD-AUDIT-FINDING` names the independently derived authoritative value and its source.
- The audit is read-only — no claim of repair or remediation within this skill's scope.

### Tool guidance

- **Read** — view the audit script in full before applying any decision tree (CLAUDE.md §4).
- **Bash** — invoke `"/mnt/c/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe" <script.py>` with the quoted path; copy the IFC to `%TEMP%` first.
- **Grep** — scan for `importIFC`, `exportIFC`, `create_children`, `ifc_import`, `filter_elements`, `.FreeCAD` config paths.
- **No Write or Edit under this skill alone** — writes route through `aidev-code-implementer`.
- **No WebFetch or WebSearch** — API-signature uncertainty emits a `PAUSE:` line only (ADR-0027 shape); the orchestrator dispatches `research-docs-lookup`.

## When NOT to use this skill

- Round-trip genuinely fails or FreeCADCmd crashes → `systematic-debugging`.
- Pre-completion verification → `verification-before-completion` (load this skill alongside it for the FreeCAD-specific items, but `verification-before-completion` governs the overall procedure).
- Running a full S.A.G.E. install sandbox validation → `sandbox-isolation-protocol`.
- Designing the FreeCAD auditor agent → `agent-creation` via `aidev-agent-creator`.
- Any non-FreeCAD IFC audit pipeline.
