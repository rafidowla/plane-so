---
name: atlas-schema-change
description: Make a database/schema change safely and keep migration churn minimal. Use when editing *.sql, *.prisma, *.graphql, or migrations.
---

# atlas-schema-change

1. `atlas_schema_drift` — compare the live-DB dump against declared schema files to see the true delta.
2. `knowledge_recall` for prior schema decisions so you do not re-litigate or contradict them.
3. Make the minimal change; prefer additive/back-compatible migrations.
4. `schema_confirm` — record the change title, file, and the WHY into workspace `plane-so` for the next engineer.
