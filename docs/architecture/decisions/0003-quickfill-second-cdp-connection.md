---
verified: 2026-10-03 (no git)
covers: [quickfill/, apply_agent.py]
status: accepted
date: 2026-09-29
---
# 0003 Bulk filling via a Node quickfill on a second CDP connection

**Context:** Logs showed most tool calls went to custom dropdowns (Job A: 43 of 60 calls on 13 dropdowns, click → snapshot → click each). Playwright MCP has no bulk fill for custom comboboxes or file inputs.
**Decision:** an in-process tool `fill_fields` spawns `node quickfill.mjs`, which attaches to the same Chrome with `playwright-core 1.64.0-alpha` (the MCP's own build) via `connectOverCDP(..., {noDefaults: true})` and fills a whole page by visible label.
**Alternatives:** Python Playwright 1.63 — cannot attach to Chrome 153+ (`Browser.setDownloadBehavior` error); `browser_run_code_unsafe` — lets the model run arbitrary JS, defeats the submit guard; keep per-field MCP calls — the cost we were removing.
**Consequences:** −67% turns / −60% cost on the same 5 jobs. Safety for this path lives inside quickfill (no buttons/links/Enter, submit-word refusal, resume-only uploads, URL must be one the run visited), not in the PreToolUse guard. `playwright-core` is pinned and must move with the MCP version.
