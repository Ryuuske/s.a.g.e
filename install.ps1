<#
.SYNOPSIS
    sage installer for native Windows Claude Code.

.DESCRIPTION
    Installs the sage Claude Code plugin and companion user-scope files
    (CLAUDE.md, rules\, docs\) into %USERPROFILE%\.claude\, patches settings.json
    to wire the statusline + SessionStart hook entries, and installs the sage
    Python package so the sage-mcp entry point is available.

    For WSL, use install.sh — Windows Claude Code and WSL Claude Code read
    different ~/.claude directories, so each environment is installed once.

.PARAMETER Force
    Overwrite existing files in ~/.claude/ (default: skip on collision).

.PARAMETER DryRun
    Print what would happen; change nothing.

.PARAMETER ClaudeMd
    'diff' | 'yes' | 'no' — control CLAUDE.md install. Default 'diff' (show diff, skip install).

.PARAMETER NoPerRepoClaudeMd
    Skip installing the per-repo .claude\CLAUDE.md stub at the destination (default: install if absent).

.PARAMETER DevMode
    Print the claude --plugin-dir command instead of installing the plugin.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    .\install.ps1 -Force
#>

#Requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$DryRun,
    [switch]$DevMode,
    [ValidateSet('diff','yes','no')][string]$ClaudeMd = 'diff',
    [switch]$NoPerRepoClaudeMd
)

$ErrorActionPreference = 'Stop'

$RepoRoot      = $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
# Normalize $env:USERPROFILE by trimming any trailing separator before any
# path-prefix operations ($ClaudeDir default, Backup-IfExists relpath logic).
# Closes F1↔F2 pattern-match finding P1.
$env:USERPROFILE = $env:USERPROFILE.TrimEnd('\', '/')
$ClaudeDir     = if ($env:CLAUDE_DIR) { $env:CLAUDE_DIR } else { Join-Path $env:USERPROFILE '.claude' }
$BackupDir     = Join-Path $ClaudeDir "backup\$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
$StatuslineDst = Join-Path $ClaudeDir 'statusline'
$HooksDst      = Join-Path $ClaudeDir 'hooks'
$DocsSpecsDst  = Join-Path $ClaudeDir 'docs\specs'
$SettingsDst   = Join-Path $ClaudeDir 'settings.json'
$ClaudeMdDst   = Join-Path $ClaudeDir 'CLAUDE.md'

function Say   ([string]$msg) { Write-Host $msg }
function Warn  ([string]$msg) { Write-Warning $msg }
function Errm  ([string]$msg) { Write-Host "ERROR: $msg" -ForegroundColor Red }
function WouldOrDoes { if ($DryRun) { 'would ' } else { '' } }

function Resolve-Python {
    foreach ($cmd in @('py','python3','python')) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            if ($cmd -eq 'py') { return @('py','-3') }
            return @($cmd)
        }
    }
    return $null
}

# Backup-IfExists PATH
#   If PATH exists (file or directory), copy it into the per-run timestamped
#   backup directory $BackupDir (set once at install start via UTC timestamp).
#   The backup directory is created lazily (only when something actually needs
#   backing up) to avoid littering empty timestamped dirs on no-op runs.
#   Destination mirrors PATH's hierarchy under $BackupDir: $USERPROFILE-relative
#   paths strip the $env:USERPROFILE prefix; other absolute paths strip the
#   leading separator. This preserves distinct backup destinations for paths
#   that share a basename (e.g. %USERPROFILE%\.claude\CLAUDE.md and
#   %USERPROFILE%\.claude\.claude\CLAUDE.md) — preventing the silent-skip /
#   data-loss collision that basename-keyed destinations produce.
#   Portable substring approach used (PS 5.1+ compatible); avoids
#   [System.IO.Path]::GetRelativePath which requires .NET Core 2.0+ and is
#   unavailable on Windows PowerShell 5.1 / .NET Framework 4.5.
#   In dry-run mode, prints what would happen and changes nothing.
function Backup-IfExists([string]$path) {
    if (Test-Path $path) {
        if ($path.StartsWith($env:USERPROFILE + '\') -or $path.StartsWith($env:USERPROFILE + '/')) {
            $rel = $path.Substring($env:USERPROFILE.Length + 1)
        } else {
            $rel = $path.TrimStart('\', '/')
            if ($rel -match '^[A-Za-z]:') {
                $rel = $rel.Substring(2).TrimStart('\', '/')
            }
        }
        $dest = Join-Path $BackupDir $rel
        if ($DryRun) {
            Say "would back up: $path -> $dest"
        } else {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
            Copy-Item -Recurse -Force $path $dest
            Say "back up: $path -> $dest"
        }
    }
}

function Copy-FileSafe([string]$src, [string]$dst) {
    if (Test-Path $dst) {
        if ($Force) {
            Backup-IfExists $dst
            # Remove before copy so a symlinked/junctioned $dst is REPLACED (not followed).
            # Copy-Item -Force without removal follows a symlink and overwrites the TARGET —
            # clobbering files outside the install dir (mirrors Copy-FileManaged hardening).
            if (-not $DryRun) {
                Remove-Item -Force $dst
                Copy-Item $src $dst
            }
            Say ("$(WouldOrDoes)overwrite: $dst")
        }
        else {
            Say "skip (exists): $dst  (use -Force to overwrite)"
        }
    }
    else {
        if (-not $DryRun) { Copy-Item $src $dst }
        Say ("$(WouldOrDoes)install: $dst")
    }
}

# Copy-FileManaged SRC DST
#   Content-aware always-refresh path for sage-OWNED hook files.
#
#   Unlike Copy-FileSafe (which skips existing files without -Force),
#   Copy-FileManaged refreshes the destination on reinstall when content has
#   changed — but SKIPS the backup+overwrite when $dst already exists AND is
#   byte-identical to $src (idempotent reinstall: no churn, no backup dir).
#
#   A symlinked/junctioned $dst is NEVER treated as identical to $src — it must
#   be replaced so the symlink itself is removed (not followed on write).
#   We check LinkType first, before content compare, so the identity fast-path
#   is never taken for symlinks.
#
#   Rationale: on upgrade, Copy-FileSafe's skip-if-exists would silently keep
#   OLD hook code running, making any hook change (including SAGE_HOOK_PROFILE=off
#   kill-switch) inert until -Force is explicitly passed.  (Codex PR#24 New-F2)
#   Content-aware idempotency mirrors the Codex PR#26 fix in install.sh.
#
#   -DryRun is respected: no writes occur, backup is simulated.
#   User-touchable files (CLAUDE.md) remain on Copy-FileSafe's cautious path.
function Copy-FileManaged([string]$src, [string]$dst) {
    if (Test-Path $dst) {
        # Identity check: skip entirely when dst is a regular file byte-identical
        # to src.  A symlink/junction (LinkType non-null/non-empty) is NEVER
        # considered identical — it must be replaced.
        $dstItem = Get-Item -LiteralPath $dst -ErrorAction SilentlyContinue
        $isLink = $dstItem -and ($dstItem.LinkType)
        if (-not $isLink) {
            $srcBytes = [System.IO.File]::ReadAllBytes($src)
            $dstBytes = [System.IO.File]::ReadAllBytes($dst)
            if ([System.Linq.Enumerable]::SequenceEqual($srcBytes, $dstBytes)) {
                Say "up-to-date (managed): $dst"
                return
            }
        }
        Backup-IfExists $dst
        # Remove before copy so a symlinked/junctioned $dst is REPLACED (not followed).
        # Copy-Item -Force without removal follows a symlink and overwrites the TARGET —
        # clobbering files outside the hooks dir (Codex PR#26 finding).
        if (-not $DryRun) {
            Remove-Item -Force $dst
            Copy-Item $src $dst
        }
        Say ("$(WouldOrDoes)refresh (managed): $dst")
    }
    else {
        if (-not $DryRun) { Copy-Item $src $dst }
        Say ("$(WouldOrDoes)install: $dst")
    }
}

function Copy-DirSafe([string]$src, [string]$dst, [string]$label) {
    if (Test-Path $dst) {
        if ($Force) {
            Backup-IfExists $dst
            if (-not $DryRun) {
                Remove-Item -Recurse -Force $dst
                Copy-Item -Recurse $src $dst
            }
            Say ("$(WouldOrDoes)overwrite ${label}: $dst")
        }
        else {
            Say "skip (exists): $dst  (use -Force to overwrite)"
        }
    }
    else {
        if (-not $DryRun) { Copy-Item -Recurse $src $dst }
        Say ("$(WouldOrDoes)install ${label}: $dst")
    }
}

Say 'sage installer (Windows)'
Say "source : $RepoRoot"
Say "target : $ClaudeDir"
if ($DryRun) { Say 'mode   : DRY-RUN (no changes will be made)' }
if ($Force)  { Say 'mode   : FORCE (will overwrite existing files)' }
Say ''

if (-not (Test-Path (Join-Path $RepoRoot 'statusline'))) {
    Errm "Cannot find statusline\ in $RepoRoot — run from repo root."
    exit 1
}

$Python = Resolve-Python
if (-not $Python) {
    Errm 'Python 3 not found (tried `py`, `python3`, `python`). Required for statusline + settings patch.'
    exit 1
}

# Create target dirs
foreach ($d in @($ClaudeDir, $HooksDst, (Join-Path $ClaudeDir 'docs'))) {
    if (-not (Test-Path $d)) {
        if (-not $DryRun) { New-Item -ItemType Directory -Path $d | Out-Null }
        Say ("$(WouldOrDoes)create dir: $d")
    }
}

Say ''
Say '==> statusline'
$statuslineSrc = Join-Path $RepoRoot 'statusline'
# Migration guard (parity with install.sh): a pre-rename install carries only the
# old entry-point script and a stale lib\ (the old cache namespace). The
# settings.json rewrite below points unconditionally at sage-statusline.py, so a
# plain non-force skip would leave settings.json executing a missing script.
# Refresh the statusline (backed up first) when the renamed entry point is absent.
# Restrict the migrate predicate to a DIRECTORY (-PathType Container): if the
# destination exists as a regular file, the installer's non-destructive
# skip-on-collision behavior must still apply rather than backup-and-remove it.
$StatuslineNeedsMigrate = (Test-Path $StatuslineDst -PathType Container) -and -not (Test-Path (Join-Path $StatuslineDst 'bin\sage-statusline.py'))
if ((Test-Path $StatuslineDst) -and -not $Force -and -not $StatuslineNeedsMigrate) {
    Say "skip (exists): $StatuslineDst  (use -Force to overwrite)"
}
else {
    if ($StatuslineNeedsMigrate -and -not $Force) { Say 'migrate: refreshing statusline for the rename (renamed entry point absent)' }
    if ($DryRun) {
        if (Test-Path $StatuslineDst) { Backup-IfExists $StatuslineDst }
    }
    else {
        Backup-IfExists $StatuslineDst
        if (Test-Path $StatuslineDst) { Remove-Item -Recurse -Force $StatuslineDst }
        New-Item -ItemType Directory -Path $StatuslineDst | Out-Null
        # Copy bin\, lib\, install\ — skip __pycache__.
        foreach ($sub in @('bin','lib','install')) {
            $srcSub = Join-Path $statuslineSrc $sub
            if (Test-Path $srcSub) {
                $dstSub = Join-Path $StatuslineDst $sub
                New-Item -ItemType Directory -Path $dstSub | Out-Null
                Robocopy "$srcSub" "$dstSub" /E /XD __pycache__ | Out-Null
                if ($LASTEXITCODE -ge 8) {
                    throw "Robocopy failed copying $srcSub to $dstSub (exit code $LASTEXITCODE)"
                }
            }
        }
        # README.md is a plain file, copy directly.
        $readmeSrc = Join-Path $statuslineSrc 'README.md'
        if (Test-Path $readmeSrc) {
            Copy-Item $readmeSrc (Join-Path $StatuslineDst 'README.md')
        }
    }
    Say ("$(WouldOrDoes)install: $StatuslineDst")
}

Say ''
Say '==> SessionStart hook'
Copy-FileManaged (Join-Path $RepoRoot 'installer-assets\inject-codex-budget.py') `
                 (Join-Path $HooksDst 'inject-codex-budget.py')
Copy-FileManaged (Join-Path $RepoRoot 'installer-assets\autonomy-continuation-sessionstart.py') `
                 (Join-Path $HooksDst 'autonomy-continuation-sessionstart.py')
# Copy wakeup SessionStart hook here (before patch_settings.py references it in settings.json)
# so a mid-run abort between the patch and the copy cannot leave settings.json pointing at
# an absent hook.  Mirrors install.sh which copies at line 339, before the patch at line 358.
Copy-FileManaged (Join-Path $RepoRoot 'installer-assets\claude-wakeup-sessionstart.py') `
                 (Join-Path $HooksDst 'claude-wakeup-sessionstart.py')

Say ''
Say '==> rules'
$rulesSrc = Join-Path $RepoRoot 'rules'
if (Test-Path $rulesSrc) {
    $rulesDst = Join-Path $ClaudeDir 'rules'
    if (-not (Test-Path $rulesDst)) {
        if (-not $DryRun) { New-Item -ItemType Directory -Path $rulesDst | Out-Null }
        Say ("$(WouldOrDoes)create dir: $rulesDst")
    }
    Get-ChildItem -Path $rulesSrc -Filter '*.md' | ForEach-Object {
        Copy-FileSafe $_.FullName (Join-Path $rulesDst $_.Name)
    }
} else {
    Warn 'rules\ not in repo — skipping.'
}

Say ''
Say '==> docs/specs'
# Reused Copy-DirSafe for docs/specs/ — directory-copy semantics are identical to skills.
Copy-DirSafe (Join-Path $RepoRoot 'docs\specs') $DocsSpecsDst 'docs/specs'

Say ''
Say '==> docs (framework spine)'
foreach ($doc in @('agent-roster.md')) {  # generated reference roster; matrix + registry-protocol ship inside docs\specs\
    $docSrc = Join-Path $RepoRoot "docs\reference\$doc"
    if (Test-Path $docSrc) {
        $refDir = Join-Path $ClaudeDir 'docs\reference'
        if (-not (Test-Path $refDir)) { if (-not $DryRun) { New-Item -ItemType Directory -Path $refDir | Out-Null } }
        $docDst = Join-Path $refDir $doc
        Copy-FileSafe $docSrc $docDst
    }
}

Say ''
Say '==> .development/decisions/0000-template.md'
$decisionsDir = Join-Path $ClaudeDir '.development\decisions'
if (-not (Test-Path $decisionsDir)) {
    if (-not $DryRun) { New-Item -ItemType Directory -Path $decisionsDir | Out-Null }
    Say ("$(WouldOrDoes)create dir: $decisionsDir")
}
Copy-FileSafe (Join-Path $RepoRoot 'installer-assets\0000-template.md') `
              (Join-Path $decisionsDir '0000-template.md')

Say ''
Say '==> docs/forbidden-patterns.md'
$forbiddenDst = Join-Path $ClaudeDir 'docs\forbidden-patterns.md'
$docsDir = Join-Path $ClaudeDir 'docs'
if (-not (Test-Path $docsDir)) {
    if (-not $DryRun) { New-Item -ItemType Directory -Path $docsDir | Out-Null }
    Say ("$(WouldOrDoes)create dir: $docsDir")
}
Copy-FileSafe (Join-Path $RepoRoot 'claude-md\forbidden-patterns-template.md') $forbiddenDst

Say ''
Say '==> agent-catalog.json'
$CatalogDst = Join-Path $ClaudeDir "agent-catalog.json"
if ($DryRun) {
    Say "would generate: $CatalogDst (from $RepoRoot\agents)"
} else {
    $genScript = Join-Path $RepoRoot 'installer-assets\gen-agent-catalog.py'
    $agentsDir = Join-Path $RepoRoot 'agents'
    if ($Python) {
        $pythonExeLocal = $Python[0]
        $pythonArgsLocal = if ($Python.Count -gt 1) { $Python[1..($Python.Count - 1)] } else { @() }
        & $pythonExeLocal @pythonArgsLocal $genScript $agentsDir $CatalogDst
        if ($LASTEXITCODE -eq 0) {
            Say "generated: $CatalogDst"
        } else {
            Warn "agent-catalog.json generation failed — install continues without an updated catalog"
        }
    } else {
        Warn "Python not found — skipping agent-catalog.json generation"
    }
}

Say ''
Say '==> per-repo .claude/CLAUDE.md stub'
if ($NoPerRepoClaudeMd) {
    Say 'skip: -NoPerRepoClaudeMd flag set'
}
else {
    $perRepoClaudeDir = Join-Path $ClaudeDir '.claude'
    $perRepoClaudeMdDst = Join-Path $perRepoClaudeDir 'CLAUDE.md'
    if (-not (Test-Path $perRepoClaudeDir)) {
        if (-not $DryRun) { New-Item -ItemType Directory -Path $perRepoClaudeDir | Out-Null }
        Say ("$(WouldOrDoes)create dir: $perRepoClaudeDir")
    }
    Copy-FileSafe (Join-Path $RepoRoot 'claude-md\per-repo-CLAUDE-stub.md') $perRepoClaudeMdDst
}

Say ''
Say '==> settings.json (statusLine + SessionStart hook entry)'
$pythonExe = $Python[0]
$pythonArgs = if ($Python.Count -gt 1) { $Python[1..($Python.Count - 1)] } else { @() }
# statusLine command — invoke through `py -3` / python so the .py file
# is executed by the right interpreter regardless of PATHEXT setup.
$StatuslineCmd  = "$($Python -join ' ') `"$StatuslineDst\bin\sage-statusline.py`""
$HookCmd        = "$($Python -join ' ') `"$HooksDst\inject-codex-budget.py`""
$WakeupHookCmd  = "$($Python -join ' ') `"$HooksDst\claude-wakeup-sessionstart.py`""
$ContinuationHookCmd = "$($Python -join ' ') `"$HooksDst\autonomy-continuation-sessionstart.py`""
$patcher        = Join-Path $RepoRoot 'statusline\install\patch_settings.py'
$patcherArgs    = @($patcher, $SettingsDst, $StatuslineCmd, $HookCmd, '--hook-wakeup', $WakeupHookCmd, '--hook-continuation', $ContinuationHookCmd)
if ($DryRun) { $patcherArgs += '--dry-run' }
& $pythonExe @pythonArgs @patcherArgs
if ($LASTEXITCODE -ne 0) {
    throw "patch_settings.py failed with exit code $LASTEXITCODE — settings.json may be in an inconsistent state at $SettingsDst"
}

Say ''
Say '==> CLAUDE.md'
$claudeMdSrc = Join-Path $RepoRoot 'claude-md\CLAUDE.md'
if (-not (Test-Path $claudeMdSrc)) {
    Warn 'claude-md\CLAUDE.md not in repo — skipping.'
}
elseif (Test-Path $ClaudeMdDst) {
    if ($ClaudeMd -eq 'yes' -or $Force) {
        Backup-IfExists $ClaudeMdDst
        if (-not $DryRun) { Copy-Item -Force $claudeMdSrc $ClaudeMdDst }
        Say ("$(WouldOrDoes)overwrite CLAUDE.md")
    }
    elseif ($ClaudeMd -eq 'no') {
        Say "skip: existing $ClaudeMdDst left in place"
    }
    else {
        Say "exists: $ClaudeMdDst"
        Say '       not overwriting. Re-run with -ClaudeMd yes (or -Force) to replace.'
    }
}
else {
    if ($ClaudeMd -eq 'no') {
        Say 'skip CLAUDE.md install (per -ClaudeMd no)'
    }
    else {
        if (-not $DryRun) { Copy-Item $claudeMdSrc $ClaudeMdDst }
        Say ("$(WouldOrDoes)install: $ClaudeMdDst")
    }
}

# --- sage Python package -------------------------------------------
# Install the Python package in editable mode so the sage-mcp entry
# point (declared in .mcp.json) is available when Claude Code loads the plugin.
Say ''
Say '==> sage package'
# With NO active venv, `uv pip install` aborts and a bare `pip install` may be
# externally-managed-blocked — the old code ran them unguarded and left sage-mcp
# absent while exiting 0. With no venv we use an isolated TOOL install (uv tool /
# pipx) that puts sage-mcp on PATH without activation. (ADR-0078)
$script:SagePkgOk = $false
if ($DryRun) {
    Say 'would: editable-install sage so sage-mcp lands on PATH (active venv -> uv/pip; else uv tool / pipx / pip --user)'
    $script:SagePkgOk = $true
}
else {
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    $pipxCmd = Get-Command pipx -ErrorAction SilentlyContinue
    $pipCmd = Get-Command pip -ErrorAction SilentlyContinue
    $inVenv = [bool]$env:VIRTUAL_ENV
    if ($inVenv -and $uvCmd) {
        Push-Location $RepoRoot; & uv pip install -e .; if ($LASTEXITCODE -eq 0) { $script:SagePkgOk = $true }; Pop-Location
    }
    elseif ($inVenv -and $pipCmd) {
        Push-Location $RepoRoot; & pip install -e .; if ($LASTEXITCODE -eq 0) { $script:SagePkgOk = $true }; Pop-Location
    }
    elseif ($uvCmd) {
        & uv tool install --editable $RepoRoot; if ($LASTEXITCODE -eq 0) { $script:SagePkgOk = $true }
    }
    elseif ($pipxCmd) {
        & pipx install --editable $RepoRoot; if ($LASTEXITCODE -eq 0) { $script:SagePkgOk = $true }
    }
    elseif ($pipCmd) {
        Push-Location $RepoRoot; & pip install --user -e .; if ($LASTEXITCODE -eq 0) { $script:SagePkgOk = $true }; Pop-Location
    }
    else {
        Errm 'no uv, pipx, or pip on PATH - cannot install the sage package.'
    }
    if (-not $script:SagePkgOk) { Errm 'sage package install did NOT succeed - sage-mcp will be missing (see post-check guidance below).' }
}

# --- Claude Code plugin ---------------------------------------------------
Say ''
Say '==> Claude Code plugin'
if ($DevMode) {
    Say "Dev mode - run Claude Code with:"
    Say "  claude --plugin-dir `"$RepoRoot`""
    Say '(or to install for all sessions: re-run without -DevMode)'
}
elseif (Get-Command claude -ErrorAction SilentlyContinue) {
    if ($DryRun) {
        Say "would: claude plugin marketplace add `"$RepoRoot`""
        Say "would: claude plugin install sage@sage"
    }
    else {
        # Two-step: register the repo's marketplace.json, then install by
        # plugin@marketplace name. `claude plugin install` expects a
        # plugin@marketplace name, NOT a filesystem path (there is no
        # --marketplace flag); handing it a path fails with "not found in any
        # configured marketplace". `marketplace add` is idempotent, so this is
        # safe under -Force re-runs. See ADR-0089.
        & claude plugin marketplace add "$RepoRoot" 2>&1 | Write-Host
        if ($LASTEXITCODE -ne 0) {
            Warn "claude plugin marketplace add returned non-zero — check output above"
        }
        & claude plugin install sage@sage 2>&1 | Write-Host
        if ($LASTEXITCODE -ne 0) {
            Warn "claude plugin install returned non-zero — check output above"
        }
    }
}
else {
    Warn "no 'claude' CLI on PATH - run manually:"
    Say "  claude plugin marketplace add `"$RepoRoot`""
    Say "  claude plugin install sage@sage"
    Say "  (or: claude --plugin-dir `"$RepoRoot`" for dev mode)"
}

# --- sage-mcp PATH post-check (ADR-0026) ---------------------------
# D.0.5 live sandbox verification exercises the absent-case behavior of this check.
Say ''
Say '==> sage-mcp PATH post-check'
$script:SageMcpMissing = $false
if ($DryRun) {
    Say "would check: sage-mcp present on PATH"
}
else {
    if (Get-Command sage-mcp -ErrorAction SilentlyContinue) {
        Say "sage-mcp present on PATH"
    }
    else {
        # FAIL LOUD (ADR-0078): record the miss, exit nonzero at the end.
        $script:SageMcpMissing = $true
        Errm "sage-mcp NOT on PATH after install — the MCP server will fail to start (command not found)."
        Errm "Fix one of, then re-run install.ps1:"
        Errm "  - install uv (https://docs.astral.sh/uv/) — it will 'uv tool install' sage-mcp onto PATH; or"
        Errm "  - activate a virtualenv first, then re-run; or"
        Errm "  - ensure the uv/pipx tool-bin dir (e.g. %USERPROFILE%\.local\bin) is on PATH."
    }
}

# --- claude-wakeup automation (ADR-0032) -----------------------------------
Say ''
Say '==> claude-wakeup automation (ADR-0032)'
Say 'claude-wakeup automation is Linux-only at launch; Windows users with the remote-control daemon are already covered, and Windows users without the daemon currently have no automation path. See installer-assets/README-claude-wakeup.md (Platform support section) for the deferral rationale.'


Say ''
Say 'Done.'
Say ''
Say 'Next:'
Say '  1. Open Claude Code in any project.'
Say '  2. Run /agents and /skills to confirm the roster and skills are visible.'
Say '  3. The bottom statusline should show folder, branch, model, Claude session %,'
Say '     and Codex window %. First render takes 1-2s; subsequent renders ~30ms.'
Say '  4. To inspect Codex budget directly, run:'
Say ("       $($Python -join ' ') `"$StatuslineDst\bin\sage-codex-budget.py`" --pretty")
Say '  5. Run "sage --version" to verify the Python package installed.'
Say '  6. Run "sage wing list" to inspect the 17-wing taxonomy.'
Say '  7. Run "sage bootstrap" to discover repos under ~/dev/, register wings,'
Say '     mine them into the nook, and build the registry in one step.'
Say '     Preview first with: sage bootstrap --dry-run'
Say '     Skip mining:        sage bootstrap --no-mine'
Say "  8. If anything is missing, re-run with -Force, or check $ClaudeDir."

# Fail loud (ADR-0078): a missing sage-mcp means the MCP server cannot start.
if ($script:SageMcpMissing) {
    Say ''
    Errm 'install.ps1 completed with errors: sage-mcp is not on PATH (see guidance above). Exit 1.'
    exit 1
}
