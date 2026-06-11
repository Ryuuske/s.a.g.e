# S.A.G.E. Init

Guide the user through a complete S.A.G.E. setup. Follow each step in order,
stopping to report errors and attempt remediation before proceeding.

## Step 1: Check Python version

Run `python3 --version` (or `python --version` on Windows) and confirm the
version is 3.9 or higher. If Python is not found or the version is too old,
tell the user they need Python 3.9+ installed and stop.

## Step 2: Check if S.A.G.E. is already installed

Run `sage --version`. If it succeeds, the CLI is on PATH — report
the installed version and skip to Step 4.

If `sage --version` fails, **do not** skip to Step 4 just because
`pip show sage` or `uv tool list` reports the package as installed:
the package may live inside a venv that isn't activated, in which case
Step 5 (`sage init ...`) will fail with `command not found`. Treat
that case as not-installed and continue to Step 3, which will (re)install
into a PATH-visible location via `uv tool install` or `pip`.

## Step 3: Install S.A.G.E.

Prefer [`uv`](https://docs.astral.sh/uv/) — it isolates the CLI from system
Python and avoids most environment-related failures:

Install from the local clone (S.A.G.E. is NOT on PyPI — the names `sage` and
`sage-mcp` are held by unrelated packages): from the repo root run
`bash install.sh` (it performs `pip install -e .`). If only the package is
needed: `pip install -e .` from the repo root.

### Error handling -- install failures

If the install command fails, try these fallbacks in order:

1. Try `pip install -e .` directly from the repo root (or `pip3 install -e .` /
   `python3 -m pip install -e .`).
2. If the error mentions missing build tools or compilation failures (commonly
   from chromadb or its native dependencies):
   - On Linux/macOS: suggest `sudo apt-get install build-essential python3-dev`
     (Debian/Ubuntu) or `xcode-select --install` (macOS)
   - On Windows: suggest installing Microsoft C++ Build Tools from
     https://visualstudio.microsoft.com/visual-cpp-build-tools/
   - Then retry the install command
5. If all attempts fail, report the error clearly and stop.

## Step 4: Ask for project directory

Ask the user which project directory they want to initialize with S.A.G.E..
Offer the current working directory as the default. Wait for their response
before continuing.

## Step 5: Initialize the nook

Run `sage init --yes <dir>` where `<dir>` is the directory from Step 4.

If this fails, report the error and stop.

## Step 6: Configure MCP server

Run the command for the AI client the user is configuring:

    # Claude Code
    claude mcp add S.A.G.E. -- sage-mcp

    # Codex CLI
    codex mcp add S.A.G.E. -- sage-mcp

If this fails, report the error but continue to the next step (MCP
configuration can be done manually later).

## Step 7: Verify installation

Run `sage status` and confirm the output shows a healthy nook.

If the command fails or reports errors, walk the user through troubleshooting
based on the output.

## Step 8: Show next steps

Tell the user setup is complete and suggest these next actions:

- Use /S.A.G.E.:mine to start adding data to their nook
- Use /S.A.G.E.:search to query their nook and retrieve stored knowledge
