---
name: sandbox-isolation-protocol
description: Use when a S.A.G.E. test step will write package state, Nook data, a crontab entry, or Claude Code config and must NOT touch the operator's real environment. Triggers on "sandbox the install", "isolate the nook", "prove isolation before install.sh", "teardown and prove nothing real was touched", "shim the crontab". Do not use for verdict scoring (e2e-evidence-discipline) or for what counts as doctoring (rules/e2e-validation-conventions.md).
---

# Sandbox isolation protocol

This skill encodes prove-before-destroy for S.A.G.E. E2E runs. The operator's real package
install, Nook data, crontab, and Claude Code config are sacred. The discipline: build a
jail, **prove every boundary resolves inside it BEFORE the first destructive command**,
run the run, then prove at teardown that nothing real moved.

## The four boundaries S.A.G.E. install.sh crosses

`install.sh` writes to four places that must each be redirected into the jail. Knowing
all four is the whole game — miss one and the run leaks into the real environment:

1. **`~/.claude` payload** — CLAUDE.md spine, rules, docs, statusline, hooks, settings.
   Redirected by `export CLAUDE_DIR=$JAIL/.claude` (install.sh honors `${CLAUDE_DIR:-…}`).
2. **The Nook data** — `~/.sage/nook`. Redirected by `export SAGE_NOOK_PATH=$JAIL/.sage/nook`
   plus a jailed `HOME` (config.py resolves `~/.sage` via HOME).
3. **The crontab** — `crontab -` is **NOT** `$HOME`-scoped; a throwaway HOME alone does
   NOT protect it. Neutralize with a shim (below).
4. **Claude Code plugin config** — `claude plugin install` runs unconditionally in
   install.sh and writes to Claude Code's own config (`~/.claude.json` + a plugins cache),
   NOT necessarily `$CLAUDE_DIR`. `CLAUDE_CONFIG_DIR` is a bet on Claude Code's behavior,
   not something install.sh or S.A.G.E. control — so it must be **positively proven honored**
   before any real plugin install. If it cannot be proven (the default assumption), run
   `install.sh --dev-mode` (prints the plugin command instead of installing) and verify the
   plugin payload by inspection only. Never run a real `claude plugin install` on an
   unproven redirection.
5. **The Python environment (editable install)** — install.sh runs `uv pip install -e .`
   (or `pip install -e .`) from the **repo root**. Creating `$JAIL/venv` is NOT enough:
   `uv` discovers and targets the repo's own `.venv` if one exists, and a bare `pip`
   resolves to system / `~/.local`. The jail venv must be **activated**: `export
   VIRTUAL_ENV=$JAIL/venv` AND prepend `$JAIL/venv/bin` to PATH, and the proof gate must
   confirm where `python`/`pip`/`uv` actually write (in-jail), not merely that the venv dir
   exists. When in doubt, force the target: `uv pip install --python "$JAIL/venv/bin/python" -e .`.

`HOME`-derived paths the jailed `HOME` DOES cover (still verify at teardown):
`~/.local/bin/claude-wakeup`, `~/.cache/claude-wakeup`, `~/.config`. The plugin config
(boundary 4) is the one most at risk and is NOT covered by a jailed HOME.

## The crontab shim (higher fidelity than hiding cron)

Do not merely hide `crontab` from PATH — that skips the cron-wiring step and leaves it
untested. Instead, put a shim first on PATH that records calls to a jail file:

`$JAIL` itself must be **exported** (`export JAIL=...`) so the shim's `${JAIL:?}` resolves
inside the installer's subshells — an unexported `JAIL` makes the shim abort mid-install and
silently kills the cron-capture.

```bash
export JAIL                       # MUST be exported, not just set
mkdir -p "$JAIL/bin"
cat > "$JAIL/bin/crontab" <<'SHIM'
#!/usr/bin/env bash
# Captures crontab calls into the jail instead of touching the real crontab.
CAP="${JAIL:?}/captured-crontab"
case "$1" in
  -l) cat "$CAP" 2>/dev/null; exit 0 ;;       # list = read the capture (empty first run)
  -)  cat > "$CAP"; echo "[shim] captured crontab write -> $CAP" >&2; exit 0 ;;
  *)  echo "[shim] crontab $* (ignored)" >&2; exit 0 ;;
esac
SHIM
chmod +x "$JAIL/bin/crontab"
export PATH="$JAIL/bin:$PATH"
```

This lets the installer's cron block EXECUTE so you can verify it *would* write the right
entry (read `$JAIL/captured-crontab`), while the real crontab is never touched.

## Prove-before-destroy (mandatory gate)

Before running install.sh, emit proof — echo each var and stat that paths resolve in-jail:

```bash
echo "JAIL=$JAIL"; echo "HOME=$HOME"; echo "CLAUDE_DIR=$CLAUDE_DIR"
echo "SAGE_NOOK_PATH=$SAGE_NOOK_PATH"; echo "CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR"
echo "VIRTUAL_ENV=$VIRTUAL_ENV"
command -v crontab python pip uv         # each must resolve under $JAIL
# where does the editable install actually land? (boundary 5 — the A1 leak)
uv pip install --dry-run -e . 2>&1 | head || pip install --dry-run -e . 2>&1 | head
python - <<'PY'
import os,sys; j=os.environ["JAIL"]
print("python in jail:", sys.executable.startswith(j))
PY
case "$HOME" in /tmp/*|/var/tmp/*) echo "HOME in tmp jail OK";; *) echo "HOME NOT JAILED — HALT";; esac
```

**Persisting the jail env across tool calls:** env-var exports do not survive between
separate shell invocations, so write a sourceable `$JAIL/env.sh` that re-enters the jail.
Quote **every** value, and especially `PATH` — on WSL the inherited `PATH` contains spaces
and parens (`Program Files (x86)`); an unquoted `export PATH=$PATH` writes a line that
fails to source (`syntax error near unexpected token (`), silently breaking re-entry.
Generate it with `printf '%q'` per value and `export PATH="$JAIL/bin:$JAIL/venv/bin:$PATH"`
(double-quoted), then prove a fresh `bash -c 'source env.sh; …'` re-enters cleanly.

Every line must resolve inside `$JAIL`. If any boundary resolves outside the jail — or if
`CLAUDE_CONFIG_DIR` honor by `claude plugin install` cannot be positively proven (default
assumption: it cannot, so use `--dev-mode`) — **HALT and surface to the orchestrator**; do
not run a leaking install. Before the shim is on PATH, snapshot the real crontab with the
REAL binary (`crontab -l > $SNAP`), and snapshot the real Claude plugin config surface
(`~/.claude.json` and any plugins cache) so teardown can diff both.

## Teardown proves non-interference

At the end: remove `$JAIL` entirely, then prove the real environment is intact. **First
strip `$JAIL/bin` from PATH** (or invoke `crontab` by its absolute real path) and assert
`command -v crontab` is no longer the shim — otherwise the crontab diff runs through the
shim's capture file and always reports a false "clean". Then:

- `diff` the real crontab (REAL binary) against the pre-run snapshot — expect identical.
- `diff` the real Claude plugin config (`~/.claude.json` + plugins cache) against its
  pre-run snapshot — expect identical (this is the boundary-4 leak surface; do not skip it).
- `stat` the real Nook dir (`~/.sage`) — expect unchanged or still-absent.
- `stat` `~/.local/bin/claude-wakeup` and `~/.cache/claude-wakeup` — expect unchanged.
- list the real `~/.claude/{agents,skills,rules}` and confirm the ONLY delta is the
  intentionally-authored crew files (name them).

Quote each proof verbatim. Any unexpected delta is a LEAK finding, not a footnote.

## Anti-patterns

- **Throwaway HOME alone.** It does not protect the crontab, the Claude plugin config, or
  the editable install. Enumerate all FIVE boundaries every time.
- **Creating a jail venv but not activating it.** `uv pip install -e .` from the repo root
  targets the repo's own `.venv`; bare `pip` hits system/`~/.local`. Export `VIRTUAL_ENV`
  and prepend `$JAIL/venv/bin` to PATH, and prove where the install actually lands.
- **Trusting CLAUDE_CONFIG_DIR without proof.** It is not honored by install.sh or S.A.G.E. —
  it is a bet on Claude Code. Prove it, or use `--dev-mode` and inspect the payload.
- **Hiding crontab instead of shimming it.** Skips testing the cron-wiring step. Shim and
  capture instead.
- **Running install.sh before the proof gate.** The proof is the gate, not a formality.
- **Teardown without a diff.** "I deleted the jail" is not proof the real state is intact.
  Diff against the snapshot and quote it.
