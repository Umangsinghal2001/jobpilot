---
verified: 2026-10-03 (no git — drift = any covered file newer than this doc)
covers: [memory.py, memory.json, apply_agent.py, quickfill/quickfill.mjs]
---
# Data model — memory layer

Two kinds of data: **what is stored** (`memory.json`) and **what flows through** a run
(the agent's `FillRequest` in, `FilledReport` out). The report is the *only* source memory learns from.

```mermaid
erDiagram
    MEMORY_JSON ||--o{ QUESTION : "answers{}"
    QUESTION ||--|{ VARIANT : "list, newest first"
    MEMORY_JSON ||--o{ PLATFORM : "platforms{}"
    PLATFORM ||--|{ LESSON : "list, ≤20, seeds pinned"

    FILLED_REPORT ||--o{ FIELD : "fields[]"
    FILLED_REPORT ||--o{ REPORT_LESSON : "lessons[] (0-5)"
    FIELD }o..o| VARIANT : "learn(): if _keep()"
    REPORT_LESSON }o..o| LESSON : "learn(): if not company-specific"

    FILL_REQUEST ||--|{ FILL_ITEM : "fields[]"
    FILL_ITEM ||--|| FILL_RESULT : "one result per item"

    QUESTION {
        string key PK "norm(label): lowercase, no *, no trailing ?:."
    }
    VARIANT {
        string answer "exact value typed"
        string label "original wording, shown in prompt"
        list companies "last 5 that used this answer"
        date updated
    }
    PLATFORM {
        string ats PK "ats_key(): greenhouse|ashby|lever|workday|..."
    }
    LESSON {
        string text "one how-to tip for that ATS"
    }
    FILLED_REPORT {
        string ats
        string track
        list unfilled_required
        int steps_seen
    }
    FIELD {
        string label
        string answer "MUST be the exact value typed"
        enum confidence "sure | guess"
        string note
        bool reusable "agent's call: generic question?"
    }
    REPORT_LESSON {
        string text
    }
    FILL_REQUEST {
        string url "must be a Page URL seen this run"
    }
    FILL_ITEM {
        string label "visible label, no *"
        enum kind "text|combobox|select|checkbox|radio|file"
        string value
    }
    FILL_RESULT {
        enum status "ok|no_option|not_found|refused|lost|error"
        string detail
    }
```

## Rules that guard the store

| Rule | Where | Why |
|---|---|---|
| An answer enters memory only if **sure**, **reusable**, ≤200 chars, not a placeholder ("Custom essay…", "(displayed as…)"), doesn't **name the company**, and isn't a resume/CV/cover-letter field | [`memory.py:_keep`](../../memory.py) (line 76) | guesses and company-specific essays must never be replayed |
| Company-name test = whole name, or first word if ≥4 chars, **as a whole word** | `memory.py:_names` (line 67) | "AI Labs" must not block every "Email" |
| Same question, different answer → **new variant**, not overwrite; prompt shows up to 3 with companies | `learn` (line 84), `prompt_section` (line 113) | visa/relocation answers depend on the job's country |
| Lessons: case-insensitive dedup · reject ones naming the company · cap 20 · **evict oldest learned, never a seed** | `learn` | seeds were evicted after one eval before this rule |
| Every write is `tmp → replace` (atomic) | `_save` (line 61) | a crash never leaves half a file |
| Missing / corrupt file → start from `SEED_LESSONS`, empty answers | `load` (line 54) | memory is an optimisation; runs never fail on it |

## Current contents (2026-10-03)
35 questions (35 variants) · greenhouse 15 lessons · ashby 10 lessons.
Inspect what the agent sees: `python3 memory.py`.
