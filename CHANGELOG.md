# Changelog

All notable changes to sage are documented here. Format:
[Keep a Changelog](https://keepachangelog.com/) · versioning: semver.

## [1.2.0] — 2026-06-17

### Removed
- The estate-dashboard feature entirely — the `sage estate` CLI command and the `nook_estate` MCP tool (ADR-0116). **Breaking** if you used them.

### Changed
- Local working artifacts consolidated under a single gitignored `.development/` tree; the plan/ADR/audit conventions move from `docs/*` to `.development/*` for all repos (ADR-0117). **Reinstall (`install.sh` / `install.ps1`) required** to pick up the rewritten spine.
- CLAUDE.md §19 (Playwright routing) retired from the resident spine in favour of the `dev-browser-operator` agent.

### Added
- Media-* agent family + `media-to-manual` skill + `scripts/media/` ingestion pipeline (probe → transcribe → frames → manifest → index) with a hash-locked dependency lockfile (ADR-0118).
- `dev-browser-operator` agent + `browser-automation-discipline` skill for Playwright-based browser automation (ADR-0119, ADR-0120, ADR-0122).
- Agent external-dependency tracking: a `requires` manifest field + generated `docs/reference/agent-dependencies.md` (ADR-0121).

## [1.1.0] — 2026-06-16

Initial public release.
