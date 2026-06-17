<!-- GENERATED — DO NOT EDIT. Regenerate with `uv run python scripts/gen_docs.py`. -->

# Agent dependencies — reference

Generated from `agents/*.md` frontmatter `requires` fields. Lists every agent
that declares at least one external dependency not shipped with S.A.G.E. core.
Governed by ADR-0121.

| Agent | Dependency | Kind | Install | Why |
|---|---|---|---|---|
| `dev-browser-operator` | Playwright MCP plugin | `mcp-plugin` | claude plugin install (Playwright MCP) — registered at user scope | the one-off interactive browser tools (navigate/click/snapshot/evaluate) are reachable only via this MCP server |
| `dev-browser-operator` | playwright runtime + browser binaries | `package` | pip install playwright (in the project venv) && playwright install | committed Playwright scripts (the deliverable for recurring flows) need the runtime + browser binaries to execute |
| `media-manual-author` | pandoc | `system` | apt-get install pandoc (or brew install pandoc) | renders the composed markdown to pdf or docx; md output needs no render tool but pdf/docx steps call pandoc directly |
| `media-manual-author` | wkhtmltopdf | `system` | apt-get install wkhtmltopdf (or brew install wkhtmltopdf) | Step 5 renders PDF via `pandoc --pdf-engine=wkhtmltopdf`; required only for PDF output (md/docx need only pandoc) |
| `media-manual-author` | ~/.venvs/docgen | `venv` | ~/.venvs/docgen/bin/pip install python-docx openpyxl (see docgen toolkit notes) | alternative render path when the brief names docgen as the render tool; absent if only pandoc render is used |
| `media-transcriber` | ffmpeg | `system` | scripts/media/setup.sh (installs ffmpeg + all media pipeline deps) | audio extraction and frame capture stages invoke ffmpeg directly; without it the pipeline fails at the audio stage |
| `media-transcriber` | ~/.venvs/media | `venv` | scripts/media/setup.sh | faster-whisper, scenedetect, Pillow, imagehash and the rest of the transcription/packaging deps live in this venv (hash-locked in scripts/media/requirements-media.txt); doctor.py reports missing venv if absent |
