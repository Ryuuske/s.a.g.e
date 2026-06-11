# S.A.G.E./ — Core Package

The Python package that powers S.A.G.E.. All modules, all logic.

## Modules

| Module | What it does |
|--------|-------------|
| `cli.py` | CLI entry point — routes to init, mine, search, recall, wing, tunnel, audit, verdict, wake-up, status, sync, sweep, repair, migrate, hook, and instructions subcommands |
| `config.py` | Configuration loading — `~/.sage/config.json`, env vars, defaults |
| `normalize.py` | Detects and normalizes Claude Code JSONL, Codex JSONL, and Claude.ai JSON transcripts into a standard exchange-list shape |
| `miner.py` | Project file ingest — scans directories, chunks by paragraph, stores to ChromaDB |
| `convo_miner.py` | Conversation ingest — chunks by exchange pair (Q+A), detects rooms from content |
| `searcher.py` | Semantic search via ChromaDB vectors — filters by wing/room, returns verbatim + scores |
| `layers.py` | 4-layer memory stack: L0 (identity), L1 (critical facts), L2 (room recall), L3 (deep search) |
| `knowledge_graph.py` | Temporal entity-relationship graph — SQLite, time-filtered queries, fact invalidation |
| `nook_graph.py` | Room-based navigation graph — BFS traversal, tunnel detection across wings |
| `mcp_server.py` | MCP server — 29 `nook_*` tools (verbatim drawer ops, search, list/get/update/delete, KG add/query/invalidate, tunnels, diary, hook settings) |
| `onboarding.py` | Guided first-run setup — asks about people/projects, generates wing config |
| `entity_registry.py` | Entity code registry — stores names with disambiguation |
| `entity_detector.py` | Auto-detect people and projects from file content |
| `general_extractor.py` | Classifies text into 5 memory types (decision, preference, milestone, problem, emotional) |
| `room_detector_local.py` | Maps folders to room names using 70+ patterns — no API |
| `spellcheck.py` | Name-aware spellcheck — won't "correct" proper nouns in your entity registry |
| `split_mega_files.py` | Splits concatenated transcript files into per-session files |

## Architecture

```
User → CLI → miner/convo_miner → ChromaDB (nook)
                                     ↕
                              knowledge_graph (SQLite)
                                     ↕
User → MCP Server → searcher → results
                  → kg_query → entity facts
                  → diary    → agent journal
```

The nook (ChromaDB) stores verbatim content. The knowledge graph (SQLite) stores structured relationships. The MCP server exposes both to any AI tool.
