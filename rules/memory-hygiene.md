---
paths:
  - "**/hooks/scripts/**"
  - "**/hooks/**"
  - "**/src/sage_mcp/hooks_cli.py"
---

# Memory-hygiene conventions

These conventions apply when working on transcript-persisting hook scripts
(`hooks/scripts/_session_hook.py`, `src/sage_mcp/hooks_cli.py`) or any other code
path that writes session content into the nook (drawers, diary entries).

## The secret-leak surface

S.A.G.E.'s transcript-persisting hooks write session content into the nook:

- **Emergency drawer** (`hooks/scripts/_session_hook.py`): up to 4000 chars of
  recent transcript messages, filed on Stop and PreCompact hook fires when the
  Keeper has not run recently.
- **Diary auto-save** (`src/sage_mcp/hooks_cli.py`): periodic checkpoint entries
  derived from recent user messages, filed every `SAVE_INTERVAL` exchanges.

Unlike ECC (which processes only ephemeral in-context state), S.A.G.E. **persists**
this content to disk in the nook. A secret that appears in session transcript
content — API keys pasted into chat, credentials shown in tool output — can end
up verbatim in a nook drawer or diary entry, stored until the nook is wiped.

This is not a guarantee of a breach: the nook lives in `~/.sage/`, is not
synced by default, and drawer content is not exposed over the network except
via explicit MCP tool calls. But it is a surface that ECC does not have, and
operators should be aware of it when deciding whether to enable redaction.

## Opt-in secret-redaction (default OFF)

An optional, default-off secret-redaction pass is available on the
emergency-drawer write path. It is controlled by:

- **Config key**: `hooks.redact_secrets` (boolean, default `false`)
- **Env override**: `SAGE_REDACT_SECRETS=1` (overrides config)

When enabled, the drawer content is passed through
`sage_mcp.secret_scrub.scrub_secrets()` (the 13 high-confidence patterns at the
write boundary, NOT the aggressive hex≥40 Tier-0 pass — see `secret_scrub.py`
and ADR-0042) before being filed.

**Default is OFF so behavior is unchanged unless the operator explicitly opts
in.** Enabling redaction means legitimate content that matches a
credential-shaped pattern (e.g. a code example containing `password =
"example"`) may be redacted from drawer content. This is the accepted
tradeoff: operators who handle real credentials in session benefit from
redaction; those who do not can leave it off to preserve verbatim recall.

## What the scrubber does and does not cover

The high-confidence scrubber covers: Anthropic and OpenAI API keys, GitHub
tokens, AWS access key IDs, PEM private-key blocks, JWTs, bearer tokens,
password-in-URL, Slack tokens, Google API keys, AWS secret access keys
(contextual), and keyword-anchored `password=`/`secret:` assignments.

It does NOT cover: bare high-entropy secrets with no recognizable prefix or
keyword (e.g. a naked 40-char base64 token on its own line). Operators handling
such content should sanitize it out of session transcripts before the hook
fires, rather than relying on the scrubber as a sole control.

## Implementation rules

- Never change the default from OFF to ON without an ADR.
- Never apply the aggressive hex≥40 scrub (Tier-0 only) at the drawer/diary
  write boundary — that pattern redacts legitimate git SHAs and would make
  drawer content less useful for recall. Use `scrub_secrets()` only
  (default `aggressive=False`).
- When adding a new write path that persists session content to the nook,
  document it here and consider whether the redaction opt-in should extend to it.
- Tests for the redaction path must cover: default-off (content unchanged) and
  opt-in-on (credential-shaped content scrubbed). See
  `tests/test_session_hooks.py` for the pattern.
