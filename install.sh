#!/usr/bin/env bash
# sage installer
#
# Installs the sage Claude Code plugin and companion user-scope files
# (CLAUDE.md, rules/, docs/) into ~/.claude/.
# Non-destructive by default: refuses to overwrite existing files unless --force.
#
# Usage:
#   ./install.sh             # safe install, skip anything already present
#   ./install.sh --force     # overwrite existing files (destructive)
#   ./install.sh --dry-run   # print what would happen, change nothing
#   ./install.sh --dev-mode  # print plugin-dir command instead of installing
#   ./install.sh --help

set -euo pipefail

# ---- args ----------------------------------------------------------------
FORCE=false
DRY_RUN=false
DEV_MODE=false
INSTALL_CLAUDE_MD=diff  # diff | yes | no
NO_PER_REPO_CLAUDE_MD=false

usage() {
  cat <<EOF
sage installer

Usage: $0 [options]

Options:
  --force                    Overwrite existing files in ~/.claude/ (default: skip)
  --dry-run                  Show what would be done; change nothing
  --dev-mode                 Print the claude --plugin-dir command instead of installing the plugin
  --claude-md=yes            Install CLAUDE.md without prompting
  --claude-md=no             Skip CLAUDE.md install
  --claude-md=diff           Show diff and skip (default)
  --no-per-repo-claude-md    Skip per-repo .claude/CLAUDE.md stub install at destination
  -h, --help                 Show this message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)        FORCE=true; shift ;;
    --dry-run)      DRY_RUN=true; shift ;;
    --dev-mode)     DEV_MODE=true; shift ;;
    --claude-md=yes)  INSTALL_CLAUDE_MD=yes; shift ;;
    --claude-md=no)   INSTALL_CLAUDE_MD=no; shift ;;
    --claude-md=diff) INSTALL_CLAUDE_MD=diff; shift ;;
    --no-per-repo-claude-md) NO_PER_REPO_CLAUDE_MD=true; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

# ---- paths ---------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Normalize HOME by stripping ALL trailing separators before any
# path-prefix operations (CLAUDE_DIR default, backup_if_exists relpath logic).
# Strip-all parity with install.ps1's $env:USERPROFILE.TrimEnd, scoped to each
# platform's separator: '/' on Unix here, '\' and '/' on Windows there.
# Closes F1↔F2 pattern-match finding P1 and the F7→F7b strip-one-vs-strip-all
# asymmetry surfaced by F7 audit pair.
while [[ "$HOME" == */ ]]; do HOME="${HOME%/}"; done
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
BACKUP_DIR="$CLAUDE_DIR/backup/$(date -u +%Y%m%dT%H%M%SZ)"
STATUSLINE_DST="$CLAUDE_DIR/statusline"
HOOKS_DST="$CLAUDE_DIR/hooks"
DOCS_SPECS_DST="$CLAUDE_DIR/docs/specs"
SETTINGS_DST="$CLAUDE_DIR/settings.json"
CLAUDE_MD_DST="$CLAUDE_DIR/CLAUDE.md"

# ---- helpers -------------------------------------------------------------
say()   { printf "%s\n" "$*"; }
warn()  { printf "WARN: %s\n" "$*" >&2; }
err()   { printf "ERROR: %s\n" "$*" >&2; }

would_or_does() {
  if $DRY_RUN; then printf "would "; fi
}

# backup_if_exists PATH
#   If PATH exists (file or directory), copy it into the per-run timestamped
#   backup directory $BACKUP_DIR (set once at install start via UTC timestamp).
#   The backup directory is created lazily (only when something actually needs
#   backing up) to avoid littering empty timestamped dirs on no-op runs.
#   Destination mirrors PATH's hierarchy under $BACKUP_DIR: $HOME-relative paths
#   strip the $HOME prefix; other absolute paths strip the leading slash.
#   This preserves distinct backup destinations for paths that share a basename
#   (e.g. ~/.claude/CLAUDE.md and ~/.claude/.claude/CLAUDE.md) — preventing the
#   silent-skip / data-loss collision that basename-keyed destinations produce.
#   In dry-run mode, prints what would happen and changes nothing.
backup_if_exists() {
  local path="$1"
  if [[ -e "$path" || -d "$path" ]]; then
    local rel
    if [[ "$path" == "$HOME"/* ]]; then
      rel="${path#$HOME/}"
    else
      rel="${path#/}"
    fi
    local dest="$BACKUP_DIR/$rel"
    if $DRY_RUN; then
      say "would back up: $path → $dest"
    else
      mkdir -p "$(dirname "$dest")"
      cp -a "$path" "$dest"
      say "back up: $path → $dest"
    fi
  fi
}

# prompt_user QUESTION
#   Non-interactive / dry-run: prints what it *would* prompt and returns "skip".
#   Interactive (TTY): prints QUESTION, reads one line from stdin.
#     y / yes            → returns "replace"
#     n / no (or empty)  → returns "skip"
#     d / diff           → returns "diff"
#   Any other input is treated as "skip".
prompt_user() {
  local question="$1"
  if $DRY_RUN || [[ ! -t 0 ]]; then
    # Informational line goes to stderr so it is not captured by $() at the call site.
    printf "would prompt: %s [y/n/d]\n" "$question" >&2
    printf "skip"
    return 0
  fi
  printf "%s [y/n/d] " "$question" >&2
  local answer
  read -r answer
  case "${answer,,}" in
    y|yes)      printf "replace" ;;
    d|diff)     printf "diff" ;;
    *)          printf "skip" ;;
  esac
}

# do_git_commit
#   Called only when $CLAUDE_DIR/.git exists (ADR-0010 gate) and not in
#   dry-run mode (caller already handles dry-run branch).
#   Stages sage-owned paths (skipping any that don't exist yet), then checks
#   whether anything is actually staged before committing — so a no-op reinstall
#   (content-identical files, nothing new) does NOT create an empty commit and
#   does NOT abort under set -e.
#   Defense-in-depth for the Codex PR#26 root-cause fix: even if some untracked
#   file exists (e.g. backup/), an empty staged set must never reach git commit.
#   Real git add/commit failures (e.g. lock contention, corrupt index) still
#   propagate via set -euo pipefail — only missing-path false-positives are
#   suppressed by the existence check.
do_git_commit() {
  local target_dir="$1"
  (
    cd "$target_dir"
    # Stage only paths that exist — avoids `git add` aborting on pathspecs that
    # do not yet exist in the working tree (e.g. CLAUDE.md was skipped via
    # --claude-md=no).  git add on a non-existent path exits nonzero with
    # "pathspec ... did not match any files", which propagates as a fatal under
    # set -e even though nothing is wrong with the install.
    local _paths=()
    for _p in statusline/ hooks/ \
               docs/specs/ \
               docs/reference/agent-roster.md docs/specs/audit-pairing-matrix.md \
               docs/specs/agent-registry-protocol.md \
               .development/decisions/0000-template.md docs/forbidden-patterns.md \
               rules/ \
               agent-catalog.json \
               settings.json CLAUDE.md; do
      [[ -e "$_p" ]] && _paths+=("$_p")
    done
    if [[ ${#_paths[@]} -gt 0 ]]; then
      git add -- "${_paths[@]}"
    fi
    # Check what is STAGED (index vs HEAD) before committing.
    # `git diff --cached --quiet` exits 0 when nothing is staged, 1 when staged
    # changes exist.  We invert the condition: if nothing staged, log and return.
    if git diff --cached --quiet 2>/dev/null; then
      say "no changes to commit in $target_dir"
      return 0
    fi
    git commit -m "feat: install sage framework (spine + rules + docs + statusline + hooks + generated agent-catalog)"
    say "committed in $target_dir"
  )
}

copy_file() {
  local src="$1" dst="$2"
  if [[ -e "$dst" ]]; then
    if $FORCE; then
      backup_if_exists "$dst"
      # Remove before copy so a symlinked $dst is REPLACED (not followed).
      # cp without rm -f dereferences a symlink and overwrites the symlink TARGET
      # outside ~/.claude — the same clobber class copy_managed guards against.
      $DRY_RUN || { rm -f "$dst"; cp "$src" "$dst"; }
      say "$(would_or_does)overwrite: $dst"
    else
      say "skip (exists): $dst  (use --force to overwrite)"
    fi
  else
    $DRY_RUN || cp "$src" "$dst"
    say "$(would_or_does)install: $dst"
  fi
}

# copy_managed SRC DST
#   Content-aware always-refresh path for sage-OWNED hook files.
#
#   Unlike copy_file (which skips existing files without --force), copy_managed
#   refreshes the destination on reinstall when content has changed — but SKIPS
#   the backup+overwrite when $dst already exists AND is byte-identical to $src
#   (idempotent reinstall: no churn, no backup dir, no git noise).
#
#   A symlinked $dst is NEVER treated as identical to $src (a symlink is not a
#   regular file; it must be replaced).  We check -L first, before cmp, so the
#   identity fast-path is never taken for symlinks — the replace path runs.
#
#   Rationale: on upgrade, copy_file's skip-if-exists would silently keep OLD
#   hook code running, making any hook change (including the SAGE_HOOK_PROFILE=off
#   kill-switch) inert until --force is explicitly passed.  (Codex PR#24 New-F2)
#   Content-aware idempotency was added to fix Codex PR#26 finding: identical
#   reinstall left untracked backup/ files → git status dirty → do_git_commit
#   staged nothing → git commit failed → aborted installer under set -e.
#
#   --dry-run is respected: no writes occur, backup is simulated.
#   user-touchable files (CLAUDE.md) remain on copy_file's cautious path.
copy_managed() {
  local src="$1" dst="$2"
  if [[ -e "$dst" || -L "$dst" ]]; then
    # Identity check: skip entirely when dst is a regular file byte-identical
    # to src.  A symlink (-L true) is NEVER considered identical — it must be
    # replaced so the symlink itself is removed (not followed on write).
    if [[ ! -L "$dst" ]] && cmp -s "$src" "$dst"; then
      say "up-to-date (managed): $dst"
      return 0
    fi
    backup_if_exists "$dst"
    # Remove before copy so a symlinked $dst is REPLACED (not followed).
    # cp "$src" "$dst" without removal would dereference a symlink and overwrite
    # the symlink's TARGET — clobbering files outside ~/.claude/hooks.
    # Plain `rm -f` + `cp` is portable (macOS/BSD cp lacks --remove-destination).
    $DRY_RUN || { rm -f "$dst"; cp "$src" "$dst"; }
    say "$(would_or_does)refresh (managed): $dst"
  else
    $DRY_RUN || cp "$src" "$dst"
    say "$(would_or_does)install: $dst"
  fi
}

copy_skill_dir() {
  local src="$1" dst="$2"
  if [[ -d "$dst" ]]; then
    if $FORCE; then
      backup_if_exists "$dst"
      $DRY_RUN || { rm -rf "$dst"; cp -R "$src" "$dst"; }
      say "$(would_or_does)overwrite skill: $dst"
    else
      say "skip (exists): $dst  (use --force to overwrite)"
    fi
  else
    $DRY_RUN || cp -R "$src" "$dst"
    say "$(would_or_does)install skill: $dst"
  fi
}

# ---- preflight -----------------------------------------------------------
say "sage installer"
say "source : $REPO_ROOT"
say "target : $CLAUDE_DIR"
$DRY_RUN && say "mode   : DRY-RUN (no changes will be made)"
$FORCE   && say "mode   : FORCE (will overwrite existing files)"
say ""

if [[ ! -d "$REPO_ROOT/statusline" ]]; then
  err "Cannot find statusline/ in $REPO_ROOT — are you running this from the repo root?"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found on PATH — required for statusline + settings.json patch."
  exit 1
fi

# Create target dirs
for d in "$CLAUDE_DIR" "$HOOKS_DST" "$CLAUDE_DIR/docs"; do
  if [[ ! -d "$d" ]]; then
    $DRY_RUN || mkdir -p "$d"
    say "$(would_or_does)create dir: $d"
  fi
done

# ---- statusline + Codex budget hook -------------------------------------
say ""
say "==> statusline"
# Migration guard: an existing install from before the statusline rename carries
# only the pre-rename entry-point scripts and a stale lib/ (the old cache
# namespace). The settings.json rewrite below unconditionally points at
# sage-statusline.py, so a plain non-force skip would leave settings.json
# executing a script that does not exist. When the renamed entry point is absent
# at the destination, REFRESH the statusline (framework code, backed up first)
# even without --force so the two stay in sync.
_statusline_needs_migrate=false
if [[ -d "$STATUSLINE_DST" ]] && [[ ! -f "$STATUSLINE_DST/bin/sage-statusline.py" ]]; then
  _statusline_needs_migrate=true
fi
if [[ -d "$STATUSLINE_DST" ]] && ! $FORCE && ! $_statusline_needs_migrate; then
  say "skip (exists): $STATUSLINE_DST  (use --force to overwrite)"
else
  $_statusline_needs_migrate && ! $FORCE && say "migrate: refreshing statusline for the sage-* rename (renamed entry point absent)"
  if $DRY_RUN; then
    [[ -d "$STATUSLINE_DST" ]] && backup_if_exists "$STATUSLINE_DST"
    say "would install: $STATUSLINE_DST"
  else
    backup_if_exists "$STATUSLINE_DST"
    rm -rf "$STATUSLINE_DST"
    mkdir -p "$STATUSLINE_DST"
    # Copy bin/, lib/, install/ — skip __pycache__.
    for sub in bin lib install; do
      src="$REPO_ROOT/statusline/$sub"
      if [[ -d "$src" ]]; then
        mkdir -p "$STATUSLINE_DST/$sub"
        tar --exclude='__pycache__' -C "$src" -cf - . | tar -C "$STATUSLINE_DST/$sub" -xf -
      fi
    done
    # README.md is a plain file, copy directly.
    [[ -f "$REPO_ROOT/statusline/README.md" ]] && cp "$REPO_ROOT/statusline/README.md" "$STATUSLINE_DST/"
    chmod +x "$STATUSLINE_DST/bin/sage-statusline.py" "$STATUSLINE_DST/bin/sage-codex-budget.py" 2>/dev/null || true
    say "install: $STATUSLINE_DST"
  fi
fi

say ""
say "==> SessionStart hook"
copy_managed "$REPO_ROOT/installer-assets/inject-codex-budget.py" "$HOOKS_DST/inject-codex-budget.py"
$DRY_RUN || chmod +x "$HOOKS_DST/inject-codex-budget.py" 2>/dev/null || true
copy_managed "$REPO_ROOT/installer-assets/autonomy-continuation-sessionstart.py" "$HOOKS_DST/autonomy-continuation-sessionstart.py"
$DRY_RUN || chmod +x "$HOOKS_DST/autonomy-continuation-sessionstart.py" 2>/dev/null || true
# claude-wakeup-sessionstart.py is grouped here (with the other two managed SessionStart hooks)
# so ALL three are refreshed BEFORE do_git_commit runs.  Moving this copy up ensures
# git-backed ~/.claude installs capture the wakeup hook refresh in the same auto-commit
# that captures inject-codex-budget.py and autonomy-continuation-sessionstart.py.
# (Codex PR#26 P2 fix — issue #25 follow-up; ADR-0032 cron/wakeup-binary wiring is later
# and does NOT depend on this copy step.)
copy_managed "$REPO_ROOT/installer-assets/claude-wakeup-sessionstart.py" "$HOOKS_DST/claude-wakeup-sessionstart.py"
$DRY_RUN || chmod +x "$HOOKS_DST/claude-wakeup-sessionstart.py" 2>/dev/null || true

say ""
say "==> settings.json (statusLine + SessionStart hook entry)"
# Single-quote the path inside the command string so a $CLAUDE_DIR that
# contains spaces (e.g. `C:/Users/Foo Bar/.claude` on WSL) still resolves
# to one argv element when Claude Code shell-executes the statusLine /
# SessionStart commands. install.ps1 already does the equivalent via
# backtick-escaped double-quotes (line 255-256).
STATUSLINE_CMD="'$STATUSLINE_DST/bin/sage-statusline.py'"
HOOK_CMD="python3 '$HOOKS_DST/inject-codex-budget.py'"
WAKEUP_HOOK_CMD="python3 '$HOOKS_DST/claude-wakeup-sessionstart.py'"
CONTINUATION_HOOK_CMD="python3 '$HOOKS_DST/autonomy-continuation-sessionstart.py'"
if $DRY_RUN; then
  python3 "$REPO_ROOT/statusline/install/patch_settings.py" --dry-run \
    "$SETTINGS_DST" "$STATUSLINE_CMD" "$HOOK_CMD" --hook-wakeup "$WAKEUP_HOOK_CMD" \
    --hook-continuation "$CONTINUATION_HOOK_CMD"
else
  python3 "$REPO_ROOT/statusline/install/patch_settings.py" \
    "$SETTINGS_DST" "$STATUSLINE_CMD" "$HOOK_CMD" --hook-wakeup "$WAKEUP_HOOK_CMD" \
    --hook-continuation "$CONTINUATION_HOOK_CMD"
fi

# ---- CLAUDE.md (handled with care) --------------------------------------
# Merge model (ADR-0083): the repo source claude-md/CLAUDE.md wraps the sage
# framework spine in <!-- BEGIN SAGE --> / <!-- END SAGE --> markers.
#
# On (re)install:
#   • Markers present in dst  → awk replaces ONLY the fenced block, preserving
#     everything the user wrote outside the markers (lossless upgrade).
#     Re-running is idempotent: no duplicate blocks are created.
#   • No markers in dst       → fall back to the existing diff-prompt path so
#     the user explicitly opts into the fenced layout. No silent wrap/overwrite.
#
# Timestamped backup + --dry-run machinery is reused unchanged (ADR-0083).
#
# Marker well-formedness contract (P3 fold):
#   • Markers are WHOLE-LINE only: ^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$
#     and the END equivalent. A prose line that merely *contains* the marker
#     text is NOT treated as a fence boundary (preserved verbatim).
#   • sage's own emitted markers are always whole-line (no surrounding prose).
#   • Before merging, the destination is validated: exactly one BEGIN and one
#     END, in the correct order. Any other shape (zero, multiple, unterminated,
#     or END-before-BEGIN) is refused — dst is left UNTOUCHED.
#
# fence_replace_claude_md SRC DST BACKUP_PATH
#   Reads the spine from SRC (between BEGIN SAGE / END SAGE markers in the
#   source), then replaces the fenced block in DST with the new spine.
#   Returns 0 on success, 1 on error (e.g. malformed fence in dst, markers
#   missing from src).
#   BACKUP_PATH is used in error messages so the user knows where the
#   timestamped backup was written before the error was detected.
fence_replace_claude_md() {
  local src="$1" dst="$2" backup_path="${3:-}"

  # ---- Validate the SOURCE fence before extracting -----------------------
  # Source is sage's OWN claude-md/CLAUDE.md — a bad fence there is a sage
  # packaging defect.  Require EXACTLY one whole-line BEGIN and one whole-line
  # END, in the correct order.  If malformed, refuse loudly and leave dst
  # UNTOUCHED (mirrors the dest-side validation block below).
  local src_begin_count src_end_count src_begin_line src_end_line
  src_begin_count="$(grep -c '^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$' "$src" 2>/dev/null || true)"
  src_end_count="$(grep -c '^[[:space:]]*<!-- END SAGE -->[[:space:]]*$' "$src" 2>/dev/null || true)"

  if [[ "$src_begin_count" -ne 1 ]] || [[ "$src_end_count" -ne 1 ]]; then
    if [[ "$src_begin_count" -eq 0 ]] && [[ "$src_end_count" -eq 0 ]]; then
      warn "fence_replace_claude_md: SOURCE has no SAGE markers — sage packaging defect, dst untouched."
    elif [[ "$src_begin_count" -gt 1 ]]; then
      warn "fence_replace_claude_md: SOURCE has $src_begin_count <!-- BEGIN SAGE --> markers — sage packaging defect, dst untouched."
    elif [[ "$src_end_count" -gt 1 ]]; then
      warn "fence_replace_claude_md: SOURCE has $src_end_count <!-- END SAGE --> markers — sage packaging defect, dst untouched."
    elif [[ "$src_begin_count" -eq 1 ]] && [[ "$src_end_count" -eq 0 ]]; then
      warn "fence_replace_claude_md: SOURCE has <!-- BEGIN SAGE --> but no <!-- END SAGE --> (unterminated source fence) — sage packaging defect, dst untouched."
    elif [[ "$src_begin_count" -eq 0 ]] && [[ "$src_end_count" -eq 1 ]]; then
      warn "fence_replace_claude_md: SOURCE has <!-- END SAGE --> but no <!-- BEGIN SAGE --> — sage packaging defect, dst untouched."
    else
      warn "fence_replace_claude_md: SOURCE has unexpected marker counts (BEGIN=$src_begin_count END=$src_end_count) — sage packaging defect, dst untouched."
    fi
    return 1
  fi

  # Order check on source: BEGIN must appear before END.
  src_begin_line="$(grep -n '^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$' "$src" | head -1 | cut -d: -f1)"
  src_end_line="$(grep -n '^[[:space:]]*<!-- END SAGE -->[[:space:]]*$' "$src" | head -1 | cut -d: -f1)"
  if [[ -z "$src_begin_line" ]] || [[ -z "$src_end_line" ]] || [[ "$src_end_line" -le "$src_begin_line" ]]; then
    warn "fence_replace_claude_md: SOURCE <!-- END SAGE --> appears before or at same line as <!-- BEGIN SAGE --> — sage packaging defect, dst untouched."
    return 1
  fi

  # ---- Extract the fenced spine from the source file ----------------------
  # We need everything from BEGIN SAGE through END SAGE (inclusive of markers).
  # Source must be well-formed (created by sage itself); abort if not.
  local src_spine
  src_spine="$(awk '/^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$/{found=1} found{print} /^[[:space:]]*<!-- END SAGE -->[[:space:]]*$/{if(found)exit}' "$src")"
  if [[ -z "$src_spine" ]]; then
    warn "CLAUDE.md source does not contain <!-- BEGIN SAGE --> / <!-- END SAGE --> markers — cannot fence-merge."
    return 1
  fi

  # ---- Well-formedness validation on destination --------------------------
  # Require EXACTLY one BEGIN and one END, BEGIN before END, no unterminated
  # fence (in_fence open at EOF).  Any other shape is data-loss territory —
  # refuse the merge, leave dst UNTOUCHED, direct user to the backup.
  #
  # Markers are matched whole-line only (^[[:space:]]*...[[:space:]]*$) so a
  # prose line that merely contains the marker text is NOT counted.
  local begin_count end_count begin_line end_line
  begin_count="$(grep -c '^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$' "$dst" 2>/dev/null || true)"
  end_count="$(grep -c '^[[:space:]]*<!-- END SAGE -->[[:space:]]*$' "$dst" 2>/dev/null || true)"

  # Count check: must be exactly 1 of each.
  if [[ "$begin_count" -ne 1 ]] || [[ "$end_count" -ne 1 ]]; then
    local backup_hint=""
    if [[ -n "$backup_path" ]]; then
      backup_hint=" (backup saved to $backup_path)"
    fi
    if [[ "$begin_count" -eq 0 ]] && [[ "$end_count" -eq 0 ]]; then
      warn "fence_replace_claude_md: destination has no SAGE markers — refusing merge$backup_hint. Fix manually."
    elif [[ "$begin_count" -gt 1 ]]; then
      warn "fence_replace_claude_md: destination has $begin_count <!-- BEGIN SAGE --> markers — refusing merge (non-deterministic)$backup_hint. Fix manually."
    elif [[ "$end_count" -gt 1 ]]; then
      warn "fence_replace_claude_md: destination has $end_count <!-- END SAGE --> markers — refusing merge (non-deterministic)$backup_hint. Fix manually."
    elif [[ "$begin_count" -eq 1 ]] && [[ "$end_count" -eq 0 ]]; then
      warn "fence_replace_claude_md: destination has <!-- BEGIN SAGE --> but no <!-- END SAGE --> (unterminated fence — user content after BEGIN would be silently dropped)$backup_hint. Fix manually."
    elif [[ "$begin_count" -eq 0 ]] && [[ "$end_count" -eq 1 ]]; then
      warn "fence_replace_claude_md: destination has <!-- END SAGE --> but no <!-- BEGIN SAGE --> — refusing merge$backup_hint. Fix manually."
    else
      warn "fence_replace_claude_md: destination has unexpected marker counts (BEGIN=$begin_count END=$end_count) — refusing merge$backup_hint. Fix manually."
    fi
    return 1
  fi

  # Order check: BEGIN must appear before END.
  begin_line="$(grep -n '^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$' "$dst" | head -1 | cut -d: -f1)"
  end_line="$(grep -n '^[[:space:]]*<!-- END SAGE -->[[:space:]]*$' "$dst" | head -1 | cut -d: -f1)"
  if [[ -z "$begin_line" ]] || [[ -z "$end_line" ]] || [[ "$end_line" -le "$begin_line" ]]; then
    local backup_hint=""
    if [[ -n "$backup_path" ]]; then
      backup_hint=" (backup saved to $backup_path)"
    fi
    warn "fence_replace_claude_md: <!-- END SAGE --> appears before or at same line as <!-- BEGIN SAGE --> — refusing merge$backup_hint. Fix manually."
    return 1
  fi

  # ---- Perform the in-place fence replacement via awk --------------------
  # Strategy: emit lines outside the fence verbatim; when we enter the
  # fenced region (whole-line BEGIN marker only), emit the new spine (once)
  # instead; skip lines until whole-line END marker, then resume normal emit.
  #
  # Spine is injected by reading from a temp file (not via awk -v) so that
  # backslash sequences in the spine (e.g. Windows paths like C:\Users) are
  # never interpreted as awk escape sequences.  awk -v treats backslash
  # specially (\n → newline, \t → tab, etc.); file-based injection is literal.
  local tmp_out tmp_spine
  tmp_out="$(mktemp)"
  tmp_spine="$(mktemp)"
  printf '%s\n' "$src_spine" > "$tmp_spine"

  awk -v spine_file="$tmp_spine" '
    BEGIN { in_fence=0; emitted=0 }
    /^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$/ {
      in_fence=1
      if (!emitted) {
        while ((getline line < spine_file) > 0) {
          print line
        }
        close(spine_file)
        emitted=1
      }
      next
    }
    /^[[:space:]]*<!-- END SAGE -->[[:space:]]*$/ {
      in_fence=0
      next
    }
    !in_fence { print }
  ' "$dst" > "$tmp_out"

  rm -f "$tmp_spine"

  # Verify the output contains the markers (sanity check)
  if ! grep -q '^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$' "$tmp_out"; then
    local backup_hint=""
    if [[ -n "$backup_path" ]]; then
      backup_hint=" (backup saved to $backup_path)"
    fi
    warn "fence_replace_claude_md: awk output missing <!-- BEGIN SAGE --> — aborting, dst unchanged$backup_hint."
    rm -f "$tmp_out"
    return 1
  fi

  mv "$tmp_out" "$dst"
  return 0
}

say ""
say "==> CLAUDE.md"
if [[ ! -f "$REPO_ROOT/claude-md/CLAUDE.md" ]]; then
  warn "claude-md/CLAUDE.md not in repo — skipping."
else
  if [[ -f "$CLAUDE_MD_DST" ]]; then
    # Check whether the destination already has SAGE markers (fenced install)
    _dst_has_fence=false
    if grep -q '^[[:space:]]*<!-- BEGIN SAGE -->[[:space:]]*$' "$CLAUDE_MD_DST" 2>/dev/null; then
      _dst_has_fence=true
    fi

    if $_dst_has_fence && $FORCE; then
      # ---- Force + fenced: full replace (ADR-0083 refinement, P3 fold) -----
      # When --force is set AND the destination already has SAGE fence markers,
      # perform a FULL overwrite of CLAUDE.md (backup first) rather than the
      # selective fence-merge.  This lets the operator completely reset the file
      # — including any user content outside the markers — which is exactly what
      # "force" means.  The fence-merge path silently merges even under --force,
      # which was surprising and misaligned with the --force contract.
      # Re-installs without --force still use the selective fence-merge path below.
      backup_if_exists "$CLAUDE_MD_DST"
      $DRY_RUN || cp "$REPO_ROOT/claude-md/CLAUDE.md" "$CLAUDE_MD_DST"
      say "$(would_or_does)overwrite CLAUDE.md (--force full replace; fenced layout retained)"
    elif $_dst_has_fence; then
      # ---- Marker-fenced upgrade path (ADR-0083) -------------------------
      # Replace only the sage-managed block; user content outside the fence
      # is preserved. Reuse timestamped backup + dry-run machinery.
      #
      # Honor --claude-md=no: skip even when the dst is fenced, unless
      # --force is set (force is handled above by the $_dst_has_fence&&$FORCE
      # branch, so $FORCE is always false here).
      if [[ "$INSTALL_CLAUDE_MD" == "no" ]]; then
        say "skip: existing $CLAUDE_MD_DST left in place (--claude-md=no)"
      else
      # Compute the backup path so fence_replace_claude_md can include it in
      # error messages (the backup is taken *before* the merge attempt, so the
      # user always has a safe copy to restore from if validation fails).
      _fence_backup_path=""
      if [[ "$CLAUDE_MD_DST" == "$HOME"/* ]]; then
        _fence_backup_path="$BACKUP_DIR/${CLAUDE_MD_DST#$HOME/}"
      else
        _fence_backup_path="$BACKUP_DIR/${CLAUDE_MD_DST#/}"
      fi
      if $DRY_RUN; then
        backup_if_exists "$CLAUDE_MD_DST"
        say "would fence-merge: $CLAUDE_MD_DST (preserving content outside <!-- BEGIN SAGE --> / <!-- END SAGE -->)"
      else
        backup_if_exists "$CLAUDE_MD_DST"
        if fence_replace_claude_md "$REPO_ROOT/claude-md/CLAUDE.md" "$CLAUDE_MD_DST" "$_fence_backup_path"; then
          say "fence-merged: $CLAUDE_MD_DST (sage spine updated; user content outside markers preserved)"
        else
          warn "fence-merge failed — $CLAUDE_MD_DST left in place."
        fi
      fi
      fi  # end: --claude-md=no skip guard
    elif [[ "$INSTALL_CLAUDE_MD" == "yes" ]] || $FORCE; then
      # ---- Force/yes + no markers: full overwrite -------------------------
      backup_if_exists "$CLAUDE_MD_DST"
      $DRY_RUN || cp "$REPO_ROOT/claude-md/CLAUDE.md" "$CLAUDE_MD_DST"
      say "$(would_or_does)overwrite CLAUDE.md"
    elif [[ "$INSTALL_CLAUDE_MD" == "no" ]]; then
      say "skip: existing $CLAUDE_MD_DST left in place"
    else
      # ---- No markers in dst: fall back to diff-prompt path ---------------
      # The user has not yet adopted the fenced layout. Show the diff and
      # ask; do not silently wrap or overwrite (ADR-0083).
      say "exists: $CLAUDE_MD_DST (no <!-- BEGIN SAGE --> markers — fence-merge not available)"
      if command -v diff >/dev/null 2>&1; then
        say "Diff (existing vs repo):"
        say ""
        diff -u "$CLAUDE_MD_DST" "$REPO_ROOT/claude-md/CLAUDE.md" || true
        say ""
      fi
      say "Tip: accepting this install will write the SAGE-fenced version."
      say "     Future re-installs will then preserve your content outside the markers."
      _claude_md_choice="$(prompt_user "Overwrite $CLAUDE_MD_DST with fenced version?")"
      if [[ "$_claude_md_choice" == "replace" ]]; then
        backup_if_exists "$CLAUDE_MD_DST"
        $DRY_RUN || cp "$REPO_ROOT/claude-md/CLAUDE.md" "$CLAUDE_MD_DST"
        say "$(would_or_does)overwrite CLAUDE.md (fenced layout adopted)"
      else
        say "skip: existing $CLAUDE_MD_DST left in place. Re-run with --claude-md=yes (or --force) to adopt fenced layout,"
        say "       or --claude-md=no to silence this message."
      fi
    fi
  else
    if [[ "$INSTALL_CLAUDE_MD" == "no" ]]; then
      say "skip CLAUDE.md install (per --claude-md=no)"
    else
      $DRY_RUN || cp "$REPO_ROOT/claude-md/CLAUDE.md" "$CLAUDE_MD_DST"
      say "$(would_or_does)install: $CLAUDE_MD_DST"
    fi
  fi
fi

# ---- install rules/ (work-type conventions) ------------------------------
say ""
say "==> rules"
if [[ -d "$REPO_ROOT/rules" ]]; then
  $DRY_RUN || mkdir -p "$CLAUDE_DIR/rules"
  shopt -s nullglob
  for f in "$REPO_ROOT/rules"/*.md; do
    [[ -e "$f" ]] || continue
    copy_file "$f" "$CLAUDE_DIR/rules/$(basename "$f")"
  done
  shopt -u nullglob
else
  warn "rules/ not in repo — skipping."
fi

# ---- install docs/specs (framework spec contracts) -----------------------
say ""
say "==> docs/specs"
# Reused copy_skill_dir for docs/specs/ — directory-copy semantics are identical
copy_skill_dir "$REPO_ROOT/docs/specs" "$DOCS_SPECS_DST"

# ---- install docs (framework spine) -------------------------------------
say ""
say "==> docs (framework spine)"
for doc in agent-roster.md; do  # generated reference roster; matrix + registry-protocol ship inside docs/specs/
  $DRY_RUN || mkdir -p "$CLAUDE_DIR/docs/reference"
  if [[ -f "$REPO_ROOT/docs/reference/$doc" ]]; then
    copy_file "$REPO_ROOT/docs/reference/$doc" "$CLAUDE_DIR/docs/reference/$doc"
  fi
done

# ---- install .development/decisions/0000-template.md (starter ADR template) -----
say ""
say "==> .development/decisions/0000-template.md"
$DRY_RUN || mkdir -p "$CLAUDE_DIR/.development/decisions"
copy_file "$REPO_ROOT/installer-assets/0000-template.md" "$CLAUDE_DIR/.development/decisions/0000-template.md"

# ---- install docs/forbidden-patterns.md (destination bootstrap template) -
say ""
say "==> docs/forbidden-patterns.md"
$DRY_RUN || mkdir -p "$CLAUDE_DIR/docs"
copy_file "$REPO_ROOT/claude-md/forbidden-patterns-template.md" "$CLAUDE_DIR/docs/forbidden-patterns.md"

# ---- generate agent-catalog.json (every run, roster is source of truth) -----
say ""
say "==> agent-catalog.json"
CATALOG_DST="$CLAUDE_DIR/agent-catalog.json"
if $DRY_RUN; then
  say "would generate: $CATALOG_DST (from $REPO_ROOT/agents)"
else
  if python3 "$REPO_ROOT/installer-assets/gen-agent-catalog.py" "$REPO_ROOT/agents" "$CATALOG_DST" 2>&1; then
    say "generated: $CATALOG_DST"
  else
    warn "agent-catalog.json generation failed — install continues without an updated catalog"
  fi
fi

# ---- install .claude/CLAUDE.md per-repo stub ----------------------------
say ""
say "==> per-repo .claude/CLAUDE.md stub"
if $NO_PER_REPO_CLAUDE_MD; then
  say "skip: --no-per-repo-claude-md flag set"
else
  $DRY_RUN || mkdir -p "$CLAUDE_DIR/.claude"
  copy_file "$REPO_ROOT/claude-md/per-repo-CLAUDE-stub.md" "$CLAUDE_DIR/.claude/CLAUDE.md"
fi

# ---- optional git commit in ~/.claude/ ----------------------------------
say ""
say "==> git"
if [[ -d "$CLAUDE_DIR/.git" ]]; then
  if $DRY_RUN; then
    say "would: stage and commit in $CLAUDE_DIR"
  else
    do_git_commit "$CLAUDE_DIR"
  fi
else
  say "($CLAUDE_DIR is not a git repo — skipping auto-commit)"
fi

# ---- sage Python package ------------------------------------------
# Install the Python package in editable mode so the sage-mcp entry
# point (declared in .mcp.json) is available when Claude Code loads the plugin.
say ""
say "==> sage package"
# Editable install so the sage-mcp entry point is on PATH for Claude Code.
# An active venv is the user's chosen target (uv/pip pip-install into it). With
# NO active venv, `uv pip install` aborts ("No virtual environment found") and a
# bare `pip install` hits PEP 668 on modern distros — the old code swallowed
# both with `|| warn` and exited 0 with sage-mcp absent (silent broken install).
# Instead, with no venv we use an isolated TOOL install (uv tool / pipx) that
# puts sage + sage-mcp on PATH (~/.local/bin) without requiring activation —
# the mechanism the README calls "preferred". (ADR-0078)
SAGE_PKG_OK=false
if $DRY_RUN; then
  say "would: editable-install sage so sage-mcp lands on PATH (active venv → uv/pip; else uv tool / pipx / pip --user)"
  SAGE_PKG_OK=true
elif [[ -n "${VIRTUAL_ENV:-}" ]] && command -v uv >/dev/null 2>&1; then
  (cd "$REPO_ROOT" && uv pip install -e . 2>&1) && SAGE_PKG_OK=true
elif [[ -n "${VIRTUAL_ENV:-}" ]] && command -v pip >/dev/null 2>&1; then
  (cd "$REPO_ROOT" && pip install -e . 2>&1) && SAGE_PKG_OK=true
elif command -v uv >/dev/null 2>&1; then
  uv tool install --editable "$REPO_ROOT" 2>&1 && SAGE_PKG_OK=true
elif command -v pipx >/dev/null 2>&1; then
  pipx install --editable "$REPO_ROOT" 2>&1 && SAGE_PKG_OK=true
elif command -v pip >/dev/null 2>&1; then
  (cd "$REPO_ROOT" && pip install --user -e . 2>&1) && SAGE_PKG_OK=true
else
  err "no uv, pipx, or pip on PATH — cannot install the sage package."
fi
$SAGE_PKG_OK || $DRY_RUN || err "sage package install did NOT succeed — sage-mcp will be missing (see the post-check guidance below)."

# ---- Claude Code plugin --------------------------------------------------
say ""
say "==> Claude Code plugin"
if $DEV_MODE; then
  say "Dev mode — run Claude Code with:"
  say "  claude --plugin-dir \"$REPO_ROOT\""
  say "(or to install for all sessions: re-run without --dev-mode)"
elif command -v claude >/dev/null 2>&1; then
  if $DRY_RUN; then
    say "would: claude plugin marketplace add \"$REPO_ROOT\""
    say "would: claude plugin install sage@sage"
  else
    # Two-step: register the repo's marketplace.json, then install by
    # plugin@marketplace name. `claude plugin install` expects a
    # plugin@marketplace name, NOT a filesystem path (there is no
    # --marketplace flag); handing it a path fails with "not found in any
    # configured marketplace". `marketplace add` is idempotent, so this is
    # safe under --force re-runs. See ADR-0089.
    claude plugin marketplace add "$REPO_ROOT" 2>&1 || \
      warn "claude plugin marketplace add returned non-zero — check output above"
    claude plugin install sage@sage 2>&1 || \
      warn "claude plugin install returned non-zero — check output above"
  fi
else
  warn "no 'claude' CLI on PATH — run manually:"
  say "  claude plugin marketplace add \"$REPO_ROOT\""
  say "  claude plugin install sage@sage"
  say "  (or: claude --plugin-dir \"$REPO_ROOT\" for dev mode)"
fi

# ---- sage-mcp PATH post-check (ADR-0026) --------------------------
# D.0.5 live sandbox verification exercises the absent-case behavior of this check.
say ""
say "==> sage-mcp PATH post-check"
SAGE_MCP_MISSING=false
if $DRY_RUN; then
  say "would check: sage-mcp present on PATH"
else
  if command -v sage-mcp >/dev/null 2>&1; then
    say "sage-mcp present on PATH: $(command -v sage-mcp)"
  else
    # FAIL LOUD (ADR-0078): the install is not usable without sage-mcp, and a
    # silent exit-0 here is the bug the E2E validation caught. Record the miss
    # and exit nonzero at the end (after best-effort steps below still run).
    SAGE_MCP_MISSING=true
    err "sage-mcp NOT on PATH after install — the MCP server will fail to start (command not found)."
    err "Fix one of, then re-run install.sh:"
    err "  • install uv (https://docs.astral.sh/uv/) — install.sh will 'uv tool install' sage-mcp onto PATH; or"
    err "  • activate a virtualenv first:  python3 -m venv .venv && . .venv/bin/activate ; or"
    err "  • if you used a --user/tool install, ensure ~/.local/bin is on your PATH."
  fi
fi

# ---- claude-wakeup automation (ADR-0032) ----------------------------------
say ""
say "==> claude-wakeup automation (ADR-0032)"
if ! command -v crontab >/dev/null 2>&1; then
  say "claude-wakeup skipped: crontab not present (install cron via your package manager — e.g., 'apt install cron' on Debian/Ubuntu — and re-run install to enable)"
elif [[ ! -x "$REPO_ROOT/installer-assets/claude-wakeup" ]]; then
  warn "claude-wakeup skipped: installer-assets/claude-wakeup not found or not executable"
else
  if $DRY_RUN; then
    say "would install: $HOME/.local/bin/claude-wakeup"
  else
    mkdir -p "$HOME/.local/bin"
    install -m 755 "$REPO_ROOT/installer-assets/claude-wakeup" "$HOME/.local/bin/claude-wakeup"
    say "installed: $HOME/.local/bin/claude-wakeup"
  fi
  if $DRY_RUN; then
    say "would create dir: $HOME/.cache/claude-wakeup"
  else
    mkdir -p "$HOME/.cache/claude-wakeup"
    say "cache dir ready: $HOME/.cache/claude-wakeup"
  fi
  # Cron install: idempotent begin/end bracket + rc-safe read + inline CLAUDE_BIN.
  #
  # Design:
  #   • Always rebuild the sage cron block from the current 'claude' binary path
  #     so a moved binary is re-pointed on re-run (no early-return short-circuit).
  #   • Use begin/end markers so the strip-and-rewrite is surgical and never
  #     touches the user's own cron jobs.
  #   • Check crontab -l rc before using its output — a transient read failure
  #     (locked file, LDAP/NIS hiccup) yields empty stdout and would otherwise
  #     silently overwrite the user's entire crontab.
  #   • Normalize trailing newline before appending so a non-newline-terminated
  #     crontab cannot merge the sage block onto the user's last cron line.
  #   • Scope CLAUDE_BIN inline on the sage line (not as a global env var) so
  #     unrelated cron jobs don't inherit it.
  if $DRY_RUN; then
    say "would install cron entry: * * * * * CLAUDE_BIN=<path> $HOME/.local/bin/claude-wakeup  # sage:claude-wakeup"
  else
    CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
    if [[ -z "$CLAUDE_BIN" ]]; then
      warn "claude-wakeup cron entry NOT installed: 'claude' not found on PATH at install time. Add claude to PATH and re-run install.sh to enable the cron entry."
    else
      # Read the existing crontab. A non-zero rc is AMBIGUOUS: it is the normal
      # "no crontab for <user>" first-install case (empty stdout) AND a transient
      # read failure (locked file, LDAP/NIS hiccup — also empty stdout). Treating
      # the latter as "no crontab" would replace a crontab we simply could not
      # read with ONLY the sage block, destroying the user's jobs (T2 codex sev80).
      # Disambiguate on stderr: only the genuine "no crontab" message is safe to
      # proceed past; any other error aborts.
      _cron_errf="$(mktemp 2>/dev/null || echo "/tmp/sage-cron-err.$$")"
      _existing="$(crontab -l 2>"$_cron_errf")"
      _cron_rc=$?
      _cron_err="$(cat "$_cron_errf" 2>/dev/null)"; rm -f "$_cron_errf"
      if [[ $_cron_rc -ne 0 ]] && ! printf '%s' "$_cron_err" | grep -qiE 'no crontab'; then
        # Non-zero rc that is NOT the benign "no crontab" case — abort rather than
        # risk overwriting an unreadable-but-present crontab.
        warn "claude-wakeup cron entry NOT installed: 'crontab -l' exited $_cron_rc (${_cron_err:-no stderr}). Re-run install.sh to retry."
      elif printf '%s' "$_existing" | grep -q '# sage:claude-wakeup-begin' \
        && [[ "$(printf '%s' "$_existing" | grep -c '# sage:claude-wakeup-begin')" \
            != "$(printf '%s' "$_existing" | grep -c '# sage:claude-wakeup-end')" ]]; then
        # Unbalanced begin/end markers (a crashed prior write): the range-delete
        # below would strip from the orphan begin marker through EOF, taking the
        # user's own jobs with it (T2 codex sev85). Refuse to touch it.
        warn "claude-wakeup cron entry NOT installed: crontab has an unbalanced sage marker block (begin without end). Remove the stray '# sage:claude-wakeup-begin' line manually, then re-run install.sh."
      else
        # Strip any prior sage block (idempotent; no-op on first install).
        # Two passes: (1) the begin/end bracket (current format); (2) any legacy
        # bare single-line entry ending in `# sage:claude-wakeup` written by a
        # pre-bracket install — without pass 2 an upgrade re-run would leave the
        # legacy line in place and append the new block, double-firing wakeup.
        # Pass 1 removes the bracket's inner line first; the begin/end markers
        # end in `-begin`/`-end` so pass 2 skips them — only a legacy line matches.
        _stripped="$(printf '%s' "$_existing" \
          | sed -e '/# sage:claude-wakeup-begin/,/# sage:claude-wakeup-end/d' \
                -e '/# sage:claude-wakeup$/d')"
        # Build and install fresh cron with normalized trailing newline + sage block.
        {
          if [[ -n "$_stripped" ]]; then
            # Re-add stripped content, ensuring it ends with exactly one newline.
            printf '%s\n' "${_stripped%$'\n'}"
          fi
          printf '# sage:claude-wakeup-begin\n* * * * * CLAUDE_BIN="%s" "%s/.local/bin/claude-wakeup"  # sage:claude-wakeup\n# sage:claude-wakeup-end\n' \
            "$CLAUDE_BIN" "$HOME"
        } | crontab -
        say "claude-wakeup cron entry installed (CLAUDE_BIN=$CLAUDE_BIN)"
      fi
    fi
  fi
fi

# ---- done ----------------------------------------------------------------
say ""
say "Done."
say ""
say "Next:"
say "  1. Open Claude Code in any project."
say "  2. Run /agents and /skills to confirm the roster and skills are visible."
say "  3. The bottom statusline should show folder, branch, model, Claude session %,"
say "     and Codex window %. First render takes 1–2s; subsequent renders ~30ms."
say "  4. To inspect Codex budget directly, run:"
say "       $STATUSLINE_DST/bin/sage-codex-budget.py --pretty"
say "  5. Run 'sage --version' to verify the Python package installed."
say "  6. Run 'sage wing list' to inspect the 17-wing taxonomy."
say "  7. Run 'sage bootstrap' to discover repos under ~/sage/, register wings,"
say "     mine them into the nook, and build the registry in one step."
say "     Preview first with: sage bootstrap --dry-run"
say "     Skip mining:        sage bootstrap --no-mine"
say "  8. If anything is missing, re-run with --force to overwrite, or check $CLAUDE_DIR."

# Fail loud (ADR-0078): a missing sage-mcp means the MCP server cannot start.
# Exit nonzero AFTER the best-effort steps above so the failure is unmissable
# to a human and to any script that gates on install.sh's exit code.
if [[ "${SAGE_MCP_MISSING:-false}" == "true" ]]; then
  say ""
  err "install.sh completed with errors: sage-mcp is not on PATH (see guidance above). Exit 1."
  exit 1
fi
