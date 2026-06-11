# S.A.G.E. Mine

When the user invokes this skill, follow these steps:

## 1. Ask what to mine

Ask the user what they want to mine and where the source data is located.
Clarify:
- Is it a project directory (code, docs, notes)?
- Is it conversation exports (Claude, ChatGPT, Slack)?
- Do they want auto-classification (decisions, milestones, problems)?

## 2. Choose the mining mode

There are three mining modes:

### Project mining

    S.A.G.E. mine <dir>

Mines code files, documentation, and notes from a project directory.

### Conversation mining

    S.A.G.E. mine <dir> --mode convos

Mines conversation exports from Claude, ChatGPT, or Slack into the nook.

### General extraction (auto-classify)

    S.A.G.E. mine <dir> --mode convos --extract general

Auto-classifies mined content into decisions, milestones, and problems.

## 3. Optionally split mega-files first

If the source directory contains very large files, suggest splitting them
before mining:

    S.A.G.E. split <dir> [--dry-run]

Use --dry-run first to preview what will be split without making changes.

## 4. Optionally tag with a wing

If the user wants to organize mined content under a specific wing, add the
--wing flag:

    S.A.G.E. mine <dir> --wing <name>

## 5. Show progress and results

Run the selected mining command and display progress as it executes. After
completion, summarize the results including:
- Number of items mined
- Categories or classifications applied
- Any warnings or skipped files

## 6. Suggest next steps

After mining completes, suggest the user try:
- /S.A.G.E.:search -- search the newly mined content
- /S.A.G.E.:status -- check the current state of their nook
- Mine more data from additional sources
