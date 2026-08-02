---
name: atlas-impact-analysis
description: Assess the blast radius of a code change before making it — which callers/processes break. Use before editing or refactoring a function, class, or file.
---

# atlas-impact-analysis

1. Identify the symbol(s) you are about to change.
2. Call `atlas_blast_radius` (workspace `plane-so`, direction `upstream`) for each — d1 = WILL BREAK, d2 = LIKELY, d3 = TEST.
3. For a whole file, use `atlas_subgraph` centered on its `code-file:` node to see dependents.
4. Report the impacted set to the user and cover d1 callers with tests/edits before committing.
