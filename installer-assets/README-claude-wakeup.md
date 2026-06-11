<!--
scope-owned: wakeup automation quirks + timing-constants record
audience: maintainer
source: hand
review-trigger: wakeup change
-->

# claude-wakeup

Auto-fires a greeting message ~60s after each Claude Max 5h budget reset, so
budget windows roll back-to-back even when the User isn't actively at the
keyboard. Maximizes daily budget runway when the User wakes up to a window
already in progress.

## What it does

Every minute (via cron), checks `~/.cache/claude-wakeup/state.json` for
`next_fire_at`. When the time has passed:

1. Spawns `claude --name auto-good-<yy-mm-dd_hh-mm-ss> --session-id <uuid>`
   in a throw-away tmux session.
2. Sends a time-of-day greeting (`Good morning|afternoon|evening|night`).
   This consumes 1 message and starts the new 5h budget window.
3. Kills the tmux session. The cloud-side chat persists in claude.ai's
   sidebar as an archived `auto-good-*` entry.
4. Reads the now-refreshed `~/.cache/sage/claude.json` for the new
   `primary.resets_at`.
5. Computes `next_fire_at = resets_at + 60s`.
6. Auto-deletes the local JSONL (`~/.claude/projects/-home-user-dev/<uuid>.jsonl`).
7. Sweeps any older `auto-good-*` JSONLs from prior runs.

Boot-up safety: a cold computer wakes, the first cron tick sees
`next_fire_at` in the past, and fires immediately — picking up the *current*
reset time from the response. No daisy-chain drift across reboots.

## What it does NOT do

- Cannot delete the `auto-good-*` entries from the claude.ai sidebar.
  Sidebar deletion requires manual click in claude.ai (no WSL-side API path
  per the User's "no cloud endpoints" constraint). User batch-deletes
  weekly/monthly.
- Cannot inject "Resume" into a paused/capped session. That feature was
  deferred (would require the cloud-events endpoint, out of scope here).

## Known quirks (load-bearing timing constants)

Three quirks shaped the timing constants at the top of the script. Do not
shorten them blindly — they were tuned against observed failures.

1. **Cache refresh race.** Under load (e.g. while another `claude` session
   is actively generating), the spawn can take longer than the original
   6-second `SPAWN_WAIT` and the greeting gets typed before the prompt is
   ready. Symptom: `~/.cache/sage/claude.json` does not update during
   the fire. Mitigations: `SPAWN_WAIT` raised to 10s, `RESPONSE_WAIT` raised
   to 25s, plus a post-kill poll (`wait_for_cache_refresh`) that waits up
   to 30s more for `claude.json.updated_at` to advance. The fire logs
   `FIRE ok` / `FIRE warn` / `FIRE err` depending on whether the cache
   refreshed and whether `resets_at` is usable.
2. **Post-kill JSONL flush race.** `tmux kill-session` sends SIGHUP, but
   `claude` keeps writing to its local JSONL for a few seconds after as
   it finishes streaming the response. If cleanup runs immediately,
   `unlink()` succeeds but the file reappears. Mitigation: 4-second
   `POST_KILL_SETTLE` sleep after kill, then cleanup retries up to 3 times
   with 1-second gaps. Final log line reports `attempts=N still_present=…`
   so a future race can be diagnosed from history alone.
3. **JSONL sometimes never written.** Early manual fires showed cases
   where the local JSONL was never created at all (likely an interaction
   with `--session-id` plus daemon load). The cleanup handles this
   gracefully: it logs `no local JSONL found` and moves on. No retry
   needed — if the file was never written, there's nothing to clean.

## Cost

~5 fires/day (one per 5h window, including overnight) = ~5 messages of
budget/day. On Claude Max this is negligible.

## Platform support

`claude-wakeup` is Linux-only at launch (WSL counts as Linux for this purpose). `install.sh` installs the script + cron entry on systems that have `crontab` available; on systems without `crontab`, the installer skips with a benign message.

Windows is deferred per ADR-0032 rationale (e): the existing Windows remote-control daemon (`claude-server` under Windows Task Scheduler) forwards interactive Claude sessions from Windows to WSL and occupies the session model that Windows cron-automation would need. Windows users with the remote-control daemon are already covered. Windows users without the remote-control daemon currently have no automation path on Windows; revisit when the daemon is replaced or extended. `install.ps1` emits a benign skip-message and takes no further action.

To disable claude-wakeup, edit your crontab (`crontab -e`) and remove the three lines bracketed by `# sage:claude-wakeup-begin` and `# sage:claude-wakeup-end` (inclusive). Optionally remove the script (`rm ~/.local/bin/claude-wakeup`) and the state directory (`rm -rf ~/.cache/claude-wakeup`). Re-running `install.sh` after disabling will re-install the block; this is safe and idempotent.

## Paths assumed

- `~/dev` — cwd matched to the daemon's; JSONL slug is derived from this path
- `~/.claude/projects/<slug>/` — local JSONL store where `<slug>` is derived by replacing `/` with `-` in the CWD path (e.g. `-home-yourname-dev`)
- `~/.cache/sage/claude.json` — rate-limit cache written by S.A.G.E.-statusline.py
- `~/.cache/claude-wakeup/` — script's own state + history + lock + log

If you run from a different cwd or a different user account, adjust the
`CWD` and `PROJECTS_DIR` constants at the top of the script.

## Why this design

- **Named sessions** (`auto-good-*`) → greppable in claude.ai sidebar for
  batch deletion.
- **Throw-away tmux** → graceful detach without `/exit` (which might mark
  the cloud session differently).
- **Reset time from cache, not stream-json** → simpler than parsing
  `rate_limit_event` from `--print --output-format=stream-json`, and the
  spawn already refreshes `claude.json` for free via the statusline.
- **Auto-cleanup of local JSONL only** → cloud-side deletion is out of
  scope per the User's "no cloud endpoints" rule. Local cleanup frees disk
  and keeps `/resume` picker tidy.
- **One-minute cron with idempotent fire-check** → simple, debuggable, and
  recovers cleanly from missed ticks or computer-off periods.
