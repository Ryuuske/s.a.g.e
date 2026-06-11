"""sage_mcp.cli_format — render search-result dicts for the CLI.

Two presentations over the ``ops.search`` / ``search_memories`` result dict
(ADR-0073): :func:`render_full` (rich, for ``sage search``) and
:func:`render_terse` (agent-keyed, for ``sage recall``). Both consume the same
hit shape; callers handle the error channel before rendering. Pure stdout
writers — no I/O beyond ``print``.
"""


def render_full(result, *, query, wing=None, room=None):
    """Rich rendering for ``sage search`` — wing/room, source, match, verbatim text."""
    hits = result.get("results") or []
    print(f"\n{'=' * 60}")
    print(f'  Results for: "{query}"')
    if wing:
        print(f"  Wing: {wing}")
    if room:
        print(f"  Room: {room}")
    if result.get("vector_disabled"):
        reason = result.get("vector_disabled_reason", "")
        print(f"  (vector index unavailable — BM25 fallback{f': {reason}' if reason else ''})")
    if result.get("metric_warning"):
        print(f"  NOTICE: {result['metric_warning']}")
    print(f"{'=' * 60}\n")

    if not hits:
        print(f'  No results found for: "{query}"\n')
        return

    for i, hit in enumerate(hits, 1):
        wing_name = hit.get("wing", "?")
        room_name = hit.get("room", "?")
        source = hit.get("source_file", "?")
        # BM25-fallback hits carry similarity=None (no vector score); show the
        # match type rather than a bare "cosine=None".
        sim = hit.get("similarity")
        match = f"cosine={sim}" if sim is not None else "bm25-only"
        strength_raw = hit.get("strength")
        strength = round(float(strength_raw), 3) if strength_raw is not None else "?"
        via = hit.get("matched_via", "drawer")

        print(f"  [{i}] {wing_name} / {room_name}")
        print(f"      Source: {source}")
        print(f"      Match:  {match}  strength={strength}  via={via}")
        print()
        for line in (hit.get("text") or "").strip().split("\n"):
            print(f"      {line}")
        print()
        print(f"  {'─' * 56}")

    print()


def render_terse(result, *, query, agent=None, wing=None):
    """Agent-keyed terse rendering for ``sage recall`` — drawer id, preview, agents."""
    hits = result.get("results") or []
    if not hits:
        filter_desc = f" tagged with agent={agent!r}" if agent else ""
        print(f"\n  No drawers matched {query!r}{filter_desc}.")
        return

    print(
        f"\n  {len(hits)} drawer(s) for {query!r}"
        + (f" — agent={agent}" if agent else "")
        + (f" — wing={wing}" if wing else "")
        + ":"
    )
    for hit in hits:
        drawer_id = hit.get("drawer_id") or "(unknown id)"
        preview = (hit.get("text") or "")[:200]
        hit_agents = hit.get("agents") or []
        print(f"\n  [{drawer_id}]  agents={hit_agents}")
        print(f"    {preview}")
