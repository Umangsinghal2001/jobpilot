---
verified: 2026-10-03 (no git)
covers: [memory.py]
status: accepted
date: 2026-09-29
---
# 0004 Platform lessons are capped, seeds pinned, company-specific ones rejected

**Context:** After one 5-job eval the agent had written 15 new Greenhouse lessons; a plain "keep newest 20" evicted every hand-written seed (the most valuable ones), and several lessons were about one company ("Job B asks about AI tools") or about a since-fixed bug.
**Decision:** cap 20 per platform; evict the oldest *learned* lesson, never a `SEED_LESSONS` entry; drop lessons that name the company; dedupe case-insensitively.
**Alternatives:** no cap (prompt bloat, stale tips forever); human approval queue for every lesson (safest, but adds a review step to each run); LLM-summarised consolidation (extra cost and another place to go wrong).
**Consequences:** good seeds survive; near-duplicates still accumulate up to the cap; lessons remain agent-written text injected as "follow them" — reviewed by eye, not by code.
