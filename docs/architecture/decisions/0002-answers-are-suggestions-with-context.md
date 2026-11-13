---
verified: 2026-10-03 (no git)
covers: [memory.py, apply_agent.py]
status: accepted
date: 2026-09-29
---
# 0002 Remembered answers are suggestions with company context, not auto-fills

**Context:** Past reports showed the same question answered differently on purpose: visa sponsorship "Yes" for a US role (Job E), "No" for Job A. Reports also contained summaries ("Custom essay…") instead of typed values.
**Decision:** store each distinct answer as a *variant* tagged with the companies that used it; inject up to 3 variants into the prompt and let the agent decide per job. Only `sure` + `reusable` answers, filtered for placeholders, company names and resume fields. The agent must report the exact typed value.
**Alternatives:** deterministic auto-fill by normalized label (fast, but would silently replay a wrong-context answer on a real application); store everything and filter at read time (pollutes the prompt).
**Consequences:** correctness stays with the model's judgment; the prompt grows with distinct questions (capped at 120 shown); filters are blocklists and can leak — `python3 memory.py` is the audit.
