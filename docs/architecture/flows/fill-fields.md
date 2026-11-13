---
verified: 2026-10-03 (no git — drift = any covered file newer than this doc)
covers: [apply_agent.py, quickfill/quickfill.mjs, quickfill/package.json]
---
# Flow 2 · fill_fields — a whole page in ONE tool call

Trigger: the agent, after one snapshot · Entry points:
[`apply_agent.py:381`](../../../apply_agent.py) (`fill_fields` tool) →
[`quickfill/quickfill.mjs`](../../../quickfill/quickfill.mjs)

```mermaid
sequenceDiagram
    autonumber
    participant O as Opus agent
    participant P as Playwright MCP
    participant G as Guard
    participant T as fill_fields (Python)
    participant N as quickfill.mjs (Node)
    participant C as Chrome tab

    O->>P: browser_tabs new (job url)
    P-->>G: PostToolUse: record "Page URL: …" → guard.urls
    O->>P: browser_snapshot (+ greenhouse_questions on Greenhouse)
    Note over O: build ONE request:<br/>[{label, kind, value}] for every field,<br/>values from answer bank / profile / JD
    O->>T: fill_fields{url, fields}
    T->>T: check tab_ready
    T->>T: check host+path(url) ∈ guard.urls
    T->>N: spawn node · stdin {url, fields, files_allowed = resume PDFs}
    N->>C: connectOverCDP(:9222, noDefaults) → newest tab matching url
    N->>C: bringToFront (unthrottle)
    loop files first, then each field (wait for network idle after an upload)
        alt label matches SUBMIT_RE
            N-->>N: refused
        else text
            N->>C: fill
        else combobox
            N->>C: only if INPUT / role=combobox: click → type → click option (exact › prefix › shortest contains)
        else select / checkbox
            N->>C: selectOption / setChecked
        else radio
            N->>C: group by question → check option (fallback only if exactly ONE radio has that name)
        else file
            N->>C: value ∈ files_allowed? setInputFiles on input nearest label (skips "Autofill from resume")
        end
    end
    N->>C: read-back every text value (phones compared by digits) → "lost" if it didn't stick
    N-->>T: {ok, results[]} (exits WITHOUT closing the tab)
    T-->>O: "14/14 fields ok." or "- NOT OK [status] label: detail"
    T-->>G: PostToolUse: filled++ · log input + output to artifacts/<id>.log
    O->>P: ONE browser_snapshot to verify
    O->>P: fix only NOT-OK fields by hand (click → snapshot → click option)
```

Measured (same 5 jobs, before → after): **195 → 64 turns, $7.82 → $3.13**; Job A 70 → 11 turns.

**Failure modes**
- **No tab matches the url** → `{error, tabs}`; the agent sees open URLs and retries / falls back to manual.
- **Duplicate tabs for one job** → newest wins; `close_stale_tabs` (flow 1) prevents duplicates from re-runs.
- **Node hangs** (page frozen) → 180 s timeout, process killed, agent told to fill the rest by hand.
- **Option not found** → `no_option` + first 20 options listed so the agent can pick the right one manually.
- **Value overwritten by a re-render** (Ashby autofill) → files go first + settle wait; read-back reports `lost`.
- **Yes/No as buttons** (Ashby `aria-pressed` pairs) → `not_found` by design (quickfill never clicks buttons); agent clicks them (+2-4 calls).
- **Tab in background** → `bringToFront`; without it clicks time out (rAF throttling).
- **fill_fields is NOT a Playwright tool**, so the PreToolUse guard doesn't see its internals — safety lives inside quickfill: never clicks buttons/links, never presses Enter, refuses submit-like labels and options, uploads only the resume PDFs passed by Python.
- **Chrome auto-update relaunch** → all tabs gone; re-run the job (~$0.50).
