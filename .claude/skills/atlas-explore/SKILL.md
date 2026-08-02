---
name: atlas-explore
description: Understand an unfamiliar codebase area via the Atlas graph instead of blind grep. Use when asked "where does X live / how does Y work".
---

# atlas-explore

1. `atlas_communities` (workspace `plane-so`) for the architecture map + coupling insights (cycles, hubs).
2. `knowledge_recall` / `knowledge_search` for decisions, conventions, and bug patterns about the area.
3. `atlas_subgraph` centered on the relevant file/community to see structure + dependencies.
4. Only fall back to grep for literal strings the graph does not model.
