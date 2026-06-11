# S.A.G.E.

AI memory system. Store everything, find anything. Local, free, no API key.

---

## Slash Commands

| Command              | Description                    |
|----------------------|--------------------------------|
| /S.A.G.E.:init      | Install and set up S.A.G.E. |
| /S.A.G.E.:search    | Search your memories           |
| /S.A.G.E.:mine      | Mine projects and conversations|
| /S.A.G.E.:status    | Nook overview and stats      |
| /S.A.G.E.:help      | This help message              |

---

## MCP Tools (29)

### Nook (read)
- nook_status -- Nook status and stats
- nook_list_wings -- List all wings
- nook_list_rooms -- List rooms in a wing
- nook_get_taxonomy -- Get the full taxonomy tree
- nook_search -- Search memories by query
- nook_check_duplicate -- Check if a memory already exists

### Nook (write)
- nook_add_drawer -- Add a new memory (drawer)
- nook_delete_drawer -- Delete a memory (drawer)

### Knowledge Graph
- nook_kg_query -- Query the knowledge graph
- nook_kg_add -- Add a knowledge graph entry
- nook_kg_invalidate -- Invalidate a knowledge graph entry
- nook_kg_timeline -- View knowledge graph timeline
- nook_kg_stats -- Knowledge graph statistics

### Navigation
- nook_traverse -- Traverse the nook structure
- nook_find_tunnels -- Find cross-wing connections
- nook_graph_stats -- Graph connectivity statistics

### Agent Diary
- nook_diary_write -- Write a diary entry
- nook_diary_read -- Read diary entries

---

## CLI Commands

    S.A.G.E. init <dir>                  Initialize a new nook
    S.A.G.E. mine <dir>                  Mine a project (default mode)
    S.A.G.E. mine <dir> --mode convos    Mine conversation exports
    S.A.G.E. search "query"              Search your memories
    S.A.G.E. split <dir>                 Split large transcript files
    S.A.G.E. wake-up                     Load nook into context
    S.A.G.E. status                      Show nook status
    S.A.G.E. repair                      Rebuild vector index
    S.A.G.E. mcp                         Show MCP setup command
    S.A.G.E. hook run                    Run hook logic (for harness integration)
    S.A.G.E. instructions <name>         Output skill instructions

---

## Session Hooks (orchestrator-gated emergency fallback)

S.A.G.E. ships two session hooks (Stop and PreCompact) at
`hooks/scripts/`. Both are emergency-only fallbacks that fire ONLY
when the orchestrator has not dispatched the Keeper agent within the
last 30 minutes (see `~/.sage/last_keeper_dispatch`). When
they do fire, they file one drawer with the last ~4000 chars of the
session transcript into `<current-wing>/handoff` (Stop) or
`<current-wing>/handoff-precompact` (PreCompact).

Neither hook fires periodically. Neither attempts semantic-importance
selection. The Keeper (`aidev-keeper`) is the primary path for
preserving session content; the hooks exist to backstop the case where
the orchestrator forgets to dispatch the Keeper on session end.

Both hooks read a JSON envelope from stdin per the Claude Code hook
protocol: `{"session_id": ..., "transcript_path": ..., "stop_hook_active": ...}`.
They are wired into Claude Code via `hooks/hooks.json`.

---

## Architecture

    Wings (projects/people)
      +-- Rooms (topics)
            +-- Closets (summaries)
                  +-- Drawers (verbatim memories)

    Halls connect rooms within a wing.
    Tunnels connect rooms across wings.

The nook is stored locally using ChromaDB for vector search and SQLite for
metadata. No cloud services or API keys required.

---

## Getting Started

1. /S.A.G.E.:init -- Set up your nook
2. /S.A.G.E.:mine -- Mine a project or conversation
3. /S.A.G.E.:search -- Find what you stored
