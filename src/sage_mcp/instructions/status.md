# S.A.G.E. Status

Display the current state of the user's memory nook.

## Step 1: Gather Nook Status

Check if MCP tools are available (look for nook_status in available tools).

- If MCP is available: Call the nook_status tool to retrieve nook state.
- If MCP is not available: Run the CLI command: S.A.G.E. status

## Step 2: Display Wing/Room/Drawer Counts

Present the nook structure counts clearly:
- Number of wings
- Number of rooms
- Number of drawers
- Total memories stored

Keep the output concise -- use a brief summary format, not verbose tables.

## Step 3: Knowledge Graph Stats (MCP only)

If MCP tools are available, also call:
- nook_kg_stats -- for a knowledge graph overview (triple count, entity
  count, relationship types)
- nook_graph_stats -- for connectivity information (connected components,
  average connections per entity)

Present these alongside the nook counts in a unified summary.

## Step 4: Suggest Next Actions

Based on the current state, suggest one relevant action:

- Empty nook (zero memories): Suggest "Try /S.A.G.E.:mine to add data from
  files, URLs, or text."
- Has data but no knowledge graph (memories exist but KG stats show zero
  triples): Suggest "Consider adding knowledge graph triples for richer
  queries."
- Healthy nook (has memories and KG data): Suggest "Use /S.A.G.E.:search to
  query your memories."

## Output Style

- Be concise and informative -- aim for a quick glance, not a report.
- Use short labels and numbers, not prose paragraphs.
- If any step fails or a tool is unavailable, note it briefly and continue
  with what is available.
