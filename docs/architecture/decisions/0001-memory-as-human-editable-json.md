---
verified: 2026-10-03 (no git)
covers: [memory.py, memory.json]
status: accepted
date: 2026-09-29
---
# 0001 Memory is one human-editable JSON file, not a table in store.db

**Context:** Every job re-answered the same questions from scratch. We needed a store of past answers and per-ATS know-how that the agent reads before a run and the harness writes after.
**Decision:** `memory.json` next to the code: `{answers: {norm_label: [variants]}, platforms: {ats: [lessons]}}`, read/written only through `memory.py`, atomic `tmp → replace` writes.
**Alternatives:** a `memory` table in `store.db` (safe concurrent writes, queryable) — rejected because the user must be able to open, read and correct memory in any editor; an embeddings/vector store — overkill for ~100 short Q&A pairs the model can match by meaning itself.
**Consequences:** trivially inspectable (`python3 memory.py`) and editable; last-writer-wins across *separate processes*; no history unless the folder is put under git.
