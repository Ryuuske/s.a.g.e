"""sage_mcp.estate — The Estate Dashboard package.

Ships as part of sage (ADR-0007). Provides a local, read-only web dashboard
that renders the live Sage environment as a 2.5D isometric Roman villa with
4-level semantic zoom.

Code location ratified at Phase 0 (plan-detailed.md §Step 0.1).
Tech-stack split: Python for the adapter + server (this package),
TypeScript for the layout engine + renderer (src/sage_mcp/estate/web/).
"""
