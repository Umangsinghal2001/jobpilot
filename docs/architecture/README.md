# Architecture — JobPilot memory layer

Read **top-down**: context → containers → data → flows (in run order) → decisions.

No git in this folder, so `verified` is a date. **Drift check:** a doc may be stale if any file it
`covers` is newer than it — `find .. ../.. -maxdepth 2 -newer <doc> -name '*.py' -o -newer <doc> -name '*.mjs'`.

| # | Doc | Covers | Verified |
|---|---|---|---|
| L1 | [context.d2](context.d2) | memory layer in its world: you, agent, Claude, ATS sites | 2026-10-03 |
| L2 | [containers.d2](containers.d2) | processes + stores + protocols (Python, CLI, MCP, Node quickfill, Chrome, memory.json) | 2026-10-03 |
| data | [data-model.md](data-model.md) | memory.json schema, FillRequest / FilledReport, the filter rules | 2026-10-03 |
| flow 1 | [flows/memory-recall.md](flows/memory-recall.md) | memory → prompt, before the agent starts | 2026-10-03 |
| flow 2 | [flows/fill-fields.md](flows/fill-fields.md) | one tool call fills the whole page | 2026-10-03 |
| flow 3 | [flows/memory-learn.md](flows/memory-learn.md) | filled report → memory.json | 2026-10-03 |
| flow 4 | [diagrams/09-flow-refill.d2](diagrams/09-flow-refill.d2) | recipes recorded per run · `refill.py` replays them without the agent | 2026-10-09 |
| ADR | [decisions/0001](decisions/0001-memory-as-human-editable-json.md) | JSON file, not a DB table | accepted |
| ADR | [decisions/0002](decisions/0002-answers-are-suggestions-with-context.md) | answers are context-tagged suggestions | accepted |
| ADR | [decisions/0003](decisions/0003-quickfill-second-cdp-connection.md) | Node quickfill over a 2nd CDP connection | accepted |
| ADR | [decisions/0004](decisions/0004-pinned-seed-lessons.md) | lesson cap, pinned seeds, no company-specific lessons | accepted |

The whole-system diagrams (all of JobPilot) stay in `../../architecture.d2`.

## Dashboard (2026-10-09)
`python3 dashboard.py serve` → http://localhost:8765 (stdlib server, 127.0.0.1 only; POSTs need the
`X-JobPilot` header + localhost Host). Statuses added: `applied`, `skipped`, `replied`.
Memory now learns **answers only when you mark a job submitted**; agent-written platform notes go to
`memory.json → pending` until approved in the Memory tab. Posting liveness: `postings.py` (also run daily by run.py).
