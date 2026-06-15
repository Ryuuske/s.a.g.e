---
name: freecad-wsl-invocation-hygiene
description: "Use when invoking FreeCADCmd.exe / IfcConvert.exe from WSL against a project repo; preventing a FreeCAD/WSL temp-file leak into the project tree; fail-closed no-new-untracked-files check after any WSL binary invocation. Not for: round-trip fidelity (→ freecad-headless-round-trip); install jail (→ sandbox-isolation-protocol); crash (→ systematic-debugging); visual form (→ bim-visual-verification); render/camera (→ ifc-render-pipeline-discipline)."
---

# FreeCAD WSL Invocation Hygiene

This skill governs the INVOCATION ENVELOPE for every WSL→Windows binary call that operates on or adjacent to a project repo — specifically FreeCADCmd.exe and IfcConvert.exe. It encodes three interlocking disciplines: temp-cwd isolation, WSL-only %TEMP% translation, and a fail-closed no-new-untracked-files assertion.

## Leak mechanism and motivation

A Windows binary invoked from WSL (FreeCADCmd.exe, IfcConvert.exe, any .exe) resolves `%TEMP%` or relative log paths to a Windows `C:\Users\...\AppData\Local\Temp` string. When that string is opened or written from the Linux side — or when the tool logs relative to the repo working directory — the artifact lands in the project repo working tree as a literal backslash-named file (e.g., `C:\Users\someuser\AppData\Local\Temp\freecad_session.log`). This is a real observed failure: a `C:\Users\...\Temp` log file appeared in a BIM project repo after a headless FreeCAD audit invoked without temp-cwd isolation.

This skill governs the INVOCATION ENVELOPE only — it does not duplicate the round-trip decision trees in `freecad-headless-round-trip` or the form-correctness review in `bim-visual-verification`. Those skills govern what happens inside the invocation; this skill governs how the invocation is set up and torn down safely.

This skill co-loads with `freecad-headless-round-trip` (round-trip fidelity, platform-limitation classification) and `bim-visual-verification` (form-correctness visual review). It does not replace either.

## When this skill binds

Fire this skill when any of these are true:

- You are about to invoke FreeCADCmd.exe or IfcConvert.exe from WSL against a project repo.
- You are running any Windows .exe from WSL where the tool may log, write, or resolve paths relative to the repo working directory.
- You are checking the project working tree for untracked files after a WSL→Windows binary invocation.
- You are provisioning or asserting the canonical leak-guard `.gitignore` pattern set in a project repo.

Do NOT fire this skill for:

- Round-trip fidelity, element counting, platform-limitation classification, or NativeIFC decision trees → `freecad-headless-round-trip`.
- Running a full S.A.G.E. install sandbox validation → `sandbox-isolation-protocol`.
- FreeCADCmd.exe crash or unexpected exit → `systematic-debugging`.
- Visual form-correctness review of rendered panels → `bim-visual-verification`.
- Render scene configuration, camera/light-rig, or empty-frame diagnosis → `ifc-render-pipeline-discipline`.
- Generic pre-completion done-gate → `verification-before-completion`.

## Discipline

### Rule 1 — Temp-cwd isolation

Run EVERY WSL→Windows binary (FreeCADCmd.exe, IfcConvert.exe, any .exe) from a dedicated temp working directory created via `mkdtemp` under the WSL-visible Windows temp path. The project repo working directory is NEVER the cwd for any WSL→Windows binary invocation.

```bash
# Correct pattern
TEMP_CWD=$(mktemp -d /mnt/c/Users/<user>/AppData/Local/Temp/freecad-invoke-XXXXXX)
cd "$TEMP_CWD"
"/mnt/c/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe" /path/to/script.py
```

The project repo path is passed as an argument string to the binary — it is NEVER the cwd. The dedicated temp-cwd is the SOLE permitted write target for the invocation: `%TEMP%` translation routes the binary's own temp writes into that temp-cwd, which Rule 5 removes on cleanup. Any write the invocation makes OUTSIDE the temp-cwd (into `$HOME`, the project repo, or elsewhere on the filesystem) is a violation. The repo-scoped no-leak assertion (Rule 3) is necessary but not sufficient — it catches writes into the project tree but is blind to off-tree writes (into `$HOME` or the temp-cwd's siblings). Containment of off-tree writes is by construction: temp-cwd isolation plus `%TEMP%` translation eliminates the known leak paths. The residual (a binary that resolves paths against `$HOME` or another mount) is a named risk, not covered by Rule 3's repo-scoped assertion. Full `$HOME`-wide scanning is out of scope for this skill.

### Rule 2 — PATH TRANSLATION

Resolve `%TEMP%` and Windows temp paths ONLY via the WSL mount at `/mnt/c/Users/<user>/AppData/Local/Temp/` (where `<user>` is supplied by the per-project brief — never hardcoded). NEVER construct or open a literal `C:\...` backslash string from the Linux or Python side. A Windows path passed AS AN ARGUMENT to a `.exe` is an argument string for that binary — it is never opened, stat'd, or written by Linux-side code.

```python
# Correct: resolve via WSL mount (user from brief)
temp_dir = f"/mnt/c/Users/{wsl_user}/AppData/Local/Temp"

# VIOLATION: never open a literal Windows path from Linux
open(r"C:\Users\...\Temp\log.txt")  # banned
```

### Rule 3 — Fail-closed no-leak assertion

Before every WSL→Windows binary invocation, snapshot the project working tree:

```bash
git -C "$PROJECT_REPO" status --porcelain > /tmp/tree-before.txt
```

After the invocation, snapshot again:

```bash
git -C "$PROJECT_REPO" status --porcelain > /tmp/tree-after.txt
```

Diff the snapshots. Any new untracked path that is NOT in the brief-declared build-output whitelist (explicitly declared intended outputs: .ifc files, render PNGs, IfcConvert output) is a LEAK. Whitelist matching is by EXACT relative path (normalized) only — NOT by prefix or glob. A file landing UNDER a declared output directory (e.g., `output/foo.log` under declared `output/`) is NOT auto-whitelisted; a file matching a glob pattern is NOT auto-whitelisted. A path is "declared" only if it exactly equals a brief-declared output path. Any file that does not exactly match a declared path is a leak, regardless of the directory it landed in:

- **BUILD lane (freecad-architect):** fail closed → clean up the stray + surface `PAUSE: orchestrator must confirm leak-guard — undeclared file <path> appeared after the WSL invocation` + do not commit until resolved.
- **AUDIT lane (freecad-model-auditor):** fail closed → emit as a FINDING (cap verdict at REQUEST_CHANGES) + clean up the auditor's OWN stray temp artifact + do NOT provision or edit the project `.gitignore`.

### Rule 4 — Canonical .gitignore guard set (single source of truth)

The canonical leak-guard pattern set for any FreeCAD/WSL project repo is:

```
*.log
*.FCStd1
*.FCBak
*\\*
C:*
```

These five patterns are the single source of truth. Each traces to a real leak class:

- `*.log` — the observed leak: FreeCAD writes a session log relative to cwd or `%TEMP%`; without temp-cwd isolation this file lands in the project tree.
- `*\\*` — the literal-Windows-path leak mechanism: a backslash-named file (e.g., `C:\Users\...\Temp\freecad_session.log`) that a Windows binary creates relative to the repo cwd; any path containing a backslash is a Windows-path artifact.
- `C:*` — the same Windows-path mechanism via drive-letter prefix: a file whose name starts with a drive letter (e.g., `C:UsersTemp...`) if the binary constructs the path without the backslash separator.
- `*.FCStd1` — FreeCAD's own auto-backup file: FreeCAD writes a `.FCStd1` recovery backup adjacent to any open `.FCStd` model file; without cwd isolation this backup lands in the project tree.
- `*.FCBak` — FreeCAD's own crash-backup file: FreeCAD writes `.FCBak` on crash or at periodic intervals adjacent to the open model; same mechanism as `.FCStd1`.

**BUILD lane (freecad-architect):** MAY provision these patterns idempotently into the project `.gitignore` — append only missing patterns, never duplicate a line, never rewrite or remove unrelated `.gitignore` content.

**AUDIT lane (freecad-model-auditor):** ONLY asserts. Never provisions the project `.gitignore` (read-only register). If a pattern is absent, the auditor surfaces it as a finding — provisioning is freecad-architect's job.

### Rule 5 — Cleanup

After each invocation, remove the dedicated temp cwd and any temp copies made for the invocation:

```bash
rm -rf "$TEMP_CWD"
```

The no-leak assertion (Rule 3) and this cleanup run on the ERROR/INTERRUPT path too (finally-style): an artifact written before a crash — and not yet deleted by the binary itself — is still caught by the post-run `git status --porcelain` snapshot and removed here. A crash-interrupted invocation is not an excuse to skip the assertion or the cleanup.

This extends — and does not duplicate — the temp-file cleanup already required by `freecad-headless-round-trip`'s invocation rules. That rule governs the IFC temp copy; this rule governs the invocation cwd.

## Lane note

**BUILD consumer (freecad-architect)** can write the project tree — it provisions the `.gitignore` guard set idempotently and fails closed on any undeclared leak before commit.

**AUDIT consumer (freecad-model-auditor)** is read-only on the project tree — it asserts no-leak, cleans ONLY its own stray temp artifact (restoration, not model mutation), and does NOT provision the project `.gitignore`. Provisioning is the build lane's job.

## Output block

After every WSL→Windows binary invocation, the consuming agent emits:

```
@@WSL-BINARY-INVOCATION BEGIN
binary | temp-cwd (mkdtemp path) | tree-before (git status --porcelain) | tree-after | new-untracked-paths | declared-build-outputs (from brief) | leak (none | <stray-path>) | verdict (clean | leak-detected)
@@WSL-BINARY-INVOCATION END
```

## Anti-patterns

- **Running the WSL→Windows binary from the repo cwd.** The binary's relative-path and `%TEMP%` resolution land in the repo tree. Always use a dedicated mkdtemp cwd under Windows temp.
- **Opening a literal `C:\...` path from Linux or Python.** A Windows path is an argument string passed to the .exe; it is never opened, stat'd, or written by Linux-side code. Resolve via `/mnt/c/Users/<user>/AppData/Local/Temp/` instead.
- **Claiming "clean" without before/after snapshots.** The no-leak assertion requires `git status --porcelain` before AND after the invocation. A post-invocation snapshot alone cannot prove clean.
- **Read-only auditor provisioning the project `.gitignore`.** The audit lane is read-only on the project tree. Provisioning is a write operation; only freecad-architect (build lane) may perform it.
- **Treating a declared build output as a leak, or an undeclared stray as a build output.** The brief's explicit build-output whitelist (intended .ifc / render paths) is the boundary. Undeclared ≠ whitelisted; whitelisted ≠ undeclared.
- **Duplicating `freecad-headless-round-trip`'s round-trip fidelity trees.** This skill governs the invocation envelope only — not what the binary does inside.

## When NOT to use this skill

- Round-trip fidelity, element counting, platform-limitation classification → `freecad-headless-round-trip`.
- Visual form-correctness review → `bim-visual-verification`.
- Render scene, camera/light-rig, empty-frame diagnosis → `ifc-render-pipeline-discipline`.
- FreeCADCmd.exe crash or stack trace → `systematic-debugging`.
- Full S.A.G.E. install sandbox validation → `sandbox-isolation-protocol`.
- Generic pre-completion done-gate → `verification-before-completion`.
