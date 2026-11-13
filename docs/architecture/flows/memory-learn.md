---
verified: 2026-10-03 (no git — drift = any covered file newer than this doc)
covers: [apply_agent.py, memory.py, memory.json]
---
# Flow 3 · Learn — a filled run teaches memory

Trigger: the agent calls `report_filled` · Entry point:
[`apply_agent.py:479`](../../../apply_agent.py) → [`memory.py:84`](../../../memory.py) (`learn`)

```mermaid
sequenceDiagram
    autonumber
    participant O as Opus agent
    participant A as apply()
    participant L as memory.learn
    participant K as _keep / _names
    participant F as memory.json
    participant Y as You

    O->>A: report_filled{fields[], lessons[], ats, …}
    Note over O: answer = EXACT value typed<br/>reusable = generic question?<br/>lessons = 0-5 platform tips
    A->>L: learn(company, report, url)  [only when kind == filled]
    L->>F: load()
    loop each field
        L->>K: _keep(company, field)?
        alt sure ∧ reusable ∧ ≤200 chars ∧ not placeholder ∧ not naming company ∧ not resume/CV/cover
            L->>L: key = norm(label)
            alt same answer exists
                L->>L: add company (keep last 5) · updated = today
            else
                L->>L: insert NEW variant first (old answers kept)
            end
        else
            L-->>L: skip
        end
    end
    L->>L: lessons: drop ones naming the company · dedup (case-insensitive)
    L->>L: platform = ats_key(report.ats, url) · append
    L->>L: while > 20: evict oldest LEARNED lesson (seeds pinned)
    L->>F: _save: write .tmp → atomic replace
    A-->>A: memory error? → base.memory_error, run still succeeds
    Y->>F: (any time) edit/correct by hand → used from the next job on
```

Other writers:
- **Seed** (one-off): `python3 memory.py --seed` folds every `ready-to-submit` report in `store.db` through the same `learn()`.
- **You**: edit `memory.json` directly. It is plain JSON; keep the shape in `data-model.md`.

**Failure modes**
- **Learns what the agent typed, before you review.** If you change an answer in the tab before submitting, memory doesn't see it — correct `memory.json` too.
- **Eval runs write to memory** (they call the same `apply()`), so eval results drift as memory grows. Intentional for now; copy `memory.json` aside before an A/B eval.
- **Two separate processes** (e.g. `fill.py` and an eval at once) can lose one's update (last writer wins). Within one process, `learn` is synchronous, so concurrent jobs can't interleave.
- **Agent marks `reusable` wrong** → company-name + placeholder filters catch the common cases; anything else is visible in `python3 memory.py`.
- **Near-duplicate lessons** (same tip, different words) aren't merged — the 20-cap bounds them.
- **No history** (folder isn't a git repo): a bad edit can't be rolled back. `git init` the jobpilot folder (local only) if you want history.
