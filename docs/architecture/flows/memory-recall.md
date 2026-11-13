---
verified: 2026-11-04 (no git — drift = any covered file newer than this doc)
covers: [apply_agent.py, memory.py, memory.json, fill.py]
---
# Flow 1 · Recall — the candidate's files and past answers, read on demand

Trigger: `fill.py` / `eval/run_eval.py` / `apply_agent.py <id>` calls `apply(job, tailor_slot)` ·
Entry point: `apply_agent.py` (`apply` → `_apply`)

One job is ONE SDK session in three parts: find the form (browser), the resume (no browser), fill (browser). The system prompt holds only instructions and rules (~17k chars);
everything about the candidate is a file the agent Reads, and past answers come from the `recall` tool.

```mermaid
sequenceDiagram
    autonumber
    participant H as fill.py
    participant A as _apply(job)
    participant X as artifacts/context/<job>/
    participant Q as Claude CLI (Opus)
    participant M as memory.py / memory.json
    participant C as Agent Chrome :9222

    H->>A: apply(job, tailor_slot (4 per process)); browser parts take chrome_slot() (3, shared by all processes)
    A->>X: make_context: posting, profile, standard answers, resume.yaml, pool, working knowledge,<br/>writing guide, approved platform notes, resume.md
    Note over A,C: Part 1 · find the form — Chrome slot
    A->>Q: query("Part 1 …", + Playwright, form_found, report_blocked)
    Q->>C: own tab → the real form (or report_blocked: closed, login, not eligible)
    A->>C: close the part's tabs (nothing filled yet)
    Note over A,Q: Part 2 · the resume — tailor slot, no browser
    A->>Q: query("Part 2 …", resume = session_id, tools = Read + tailor_resume + resume_pages)
    Q->>X: Read posting.txt, profile.md, resume.yaml, pool.yaml …  (Guard: Read only inside X)
    Q-->>A: tailor_resume(version) ⇄ page fill + what spilled · resume_pages → page images
    A->>A: keep the SDK session_id (init / ResultMessage)
    A->>Q: "/compact …" (still in the tailor slot): Parts 1-2 become a short summary, same session_id
    Note over A,C: Part 3 · fill — Chrome slot, same conversation
    A->>Q: query("Part 3 …", resume = session_id, + Playwright + fill tools)
    Q->>C: own tab, snapshot, refill, fill_fields …
    Q->>M: recall([every question on the page])
    M-->>Q: per question: up to 3 past answers (close / weak), with companies and dates
    Q-->>A: report_filled → recipe (no guesses) + pending lessons
```

What `recall` returns (real output for one Google Form, company names replaced):

```
"Expected Salary (in lakhs, per annum)"
  weak: "Expected CTC" → "…" (Company A; 2026-10-12)
"Given an offer, how soon can you join?"
  no past answer                      → the agent uses standard-answers.md (notice period)
"Will you require visa sponsorship?"
  close: "Will you now or in the future require visa sponsorship for employment?" → "Yes" (Company B; …)
  weak:  "… sponsorship for a visa to remain in your current location?" → "No" (Company C; …)
```

**Failure modes**
- **Word matching misses paraphrases** ("compensation" vs "CTC"). The prompt tells the agent to rephrase; a fact
  it still can't find is left empty and listed in `unfilled_required`, never guessed. Upgrade: embeddings.
- **The agent may skip a file or `recall`.** Nothing forces the read; watch it in the eval and the guard log
  (Read and recall calls are logged).
- **No saved posting** (no JD, no ATS API) → Part 1 reads it on the page and passes the requirements on in `form_found`.
- **Part 1 blocks the job** (closed, login, not eligible) → no resume is built.
- **Part 3 carries only a summary of Parts 1-2** (`/compact`). Without it, every browser turn re-read ~114k tokens
  of files and resume drafts (a 5-step Workday form: 67 calls, up to 219k tokens each, $7.24). If the compact
  fails, Part 3 runs on the full history; the PDF path, form URL and route are in its task message either way.
- **Memory is read when called**, so a lesson approved mid-run reaches the next `recall`; platform notes are
  copied into the context folder at the start of the run.
- **Lessons are written by the agent from web pages.** They wait in `pending` until the candidate approves them on the
  dashboard; only approved ones reach `platform-notes.md`.
