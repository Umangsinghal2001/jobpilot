# JobPilot

JobPilot is the job-search agent I built and use for my own search. Every day it finds openings that match a
profile, reads each posting, tailors a resume for the job, and fills the application form in a real Chrome
window. Then it stops. I review the open tab and click Submit myself.

It runs on a Claude subscription through the Claude Agent SDK (no API key), with Playwright driving the browser
and a React dashboard for reviewing the results.

## What happens to one job

1. **Discover** (`discover.py`): one agent searches the job boards, the target companies' career pages and a few
   remote-job APIs, and saves about 40 matching openings to SQLite. Duplicates are dropped by normalized URL.
2. **Read the posting** (`jds.py`): the description comes from the ATS's public API when there is one, else from
   the page.
3. **Fit check** (`fit.py`): one cheap Haiku call per job compares the description with the targeting in
   `profile.md`. A "no" moves the job to skipped, with the reason, and Undo on the dashboard puts it back.
4. **Apply** (`apply_agent.py`, run by `fill.py`): a fresh agent per job, in three parts of one session.
   - Find: follow the posting to the real form. Stop if the job is closed, behind a login, or breaks one of the
     profile's eligibility rules.
   - Resume: write a version file over the base `resume.yaml` (reorder, reword, pick bullets from `pool.yaml`),
     build the PDF and check it the way an ATS parser reads it. No browser in this part.
   - Fill: reopen the form, fill every field, upload the tailored PDF and report each answer as "sure" or
     "guess".
5. **Check postings** (`postings.py`): jobs that closed since they were found get marked before anyone spends
   time on them.
6. **Inbox** (`inbox.py`, optional): reads job email through a read-only Gmail connection and updates each job:
   submitted, replied, rejected, interview. When it can't tell, it asks on the dashboard.
7. **Review** (`server.py` + `web/`): the dashboard at http://localhost:8765 shows each filled form with its
   answers, the resume diff against the base, and the open tab to submit from.

`run.py` runs steps 1 to 6. `daily.sh` runs it once a day from launchd, only while the Mac is in use and on
power or above 50% battery.

## Safety

The agent never submits an application. This is enforced in code by a `PreToolUse` guard in `apply_agent.py`,
not by the prompt. The guard blocks:
- clicks on any Submit, Send, Finish or Complete style control, and pressing Enter after filling;
- running JavaScript on the page, or closing tabs (a filled tab stays open for review);
- reading files outside the job's own context folder (`artifacts/context/<job>/`).

`python apply_agent.py --selftest` checks the guard offline. Other limits:
- Gmail access is the `gmail.readonly` scope, so Google itself refuses any send, delete or change. What the inbox
  agent may read (one label plus job-board mail) is fixed in `gmail.py`.
- The agent never types passwords, creates accounts or attempts a CAPTCHA. It stops and reports the job instead.
- Answers marked "guess" are never replayed into a later form. You confirm them first.
- Agent status changes (fit "no", inbox outcomes, skips) can be undone from the dashboard.
- Platform lessons the agent writes from web pages wait as "pending" until you approve them.

## Memory and cost

Past answers live in `memory.json`, a plain JSON file you can edit. A `recall` tool gives the agent the closest
past answers to each question on the page, with the company and date they went to, as suggestions. Approved
per-ATS lessons ("Greenhouse: call greenhouse_questions first") go into every run on that platform.

Filling a whole page in one call (`quickfill/`, a Node script on a second CDP connection) instead of a click and
a snapshot per dropdown took the same five test jobs from 195 agent turns and $7.82 to 64 turns and $3.13, all
five filled and none submitted. The design notes and diagrams are in `docs/architecture/` and `architecture.d2`.

## Setup

Built and run on macOS with Google Chrome. Needs Python 3.11+, Node 20+, poppler (`brew install poppler`) and the
Claude Code CLI logged in with a Pro or Max plan.

```bash
npm install -g @anthropic-ai/claude-code && claude      # log in once
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
npm --prefix quickfill install
npm --prefix web install && npm --prefix web run build
```

Run it:

```bash
./browser.sh start                    # the agent's own Chrome profile; log in to job sites here once
.venv/bin/python server.py            # dashboard on http://localhost:8765
.venv/bin/python run.py               # one daily run: discover, fit, fill, postings, inbox
.venv/bin/python fill.py --job acme   # or fill one company's job
```

Gmail is optional. Put an OAuth desktop client from Google Cloud at `secrets/gmail-client.json`, then run
`.venv/bin/python gmail.py --auth` once.

## Your profile folder

Everything about the candidate lives outside the code, in one folder. `JOBPILOT_PROFILE` points at it; without
it, JobPilot uses `example-profile/`, a made-up candidate.

```
profile.md                          identity, job titles, targeting, eligibility rules, pitch
resume.md                           plain-text resume, for forms that want it pasted
target-companies.md                 career pages discovery checks
kits/_common-application-answers.md notice period, salary ask, short bio, and so on
resume/resume.yaml                  the base resume (python resume/build.py turns it into a PDF)
resume/pool.yaml                    more work as ready bullets, for tailored versions to pick from
resume/working-knowledge.yaml       skills the agent may claim beyond the resume
voice/SKILL.md                      optional writing guide for free-text answers
```

Copy `example-profile/`, fill it in, then `export JOBPILOT_PROFILE=~/my-profile`.

## Layout

| Path | What it does |
|---|---|
| `apply_agent.py` | the per-job agent: three parts, the submit guard, resume building, tools |
| `prompts/` | the agent's instructions as plain text |
| `discover.py`, `jds.py`, `fit.py`, `postings.py` | finding jobs, reading descriptions, fit check, closed postings |
| `fill.py`, `refill.py` | run the apply agent over the queue; replay a saved form without the agent |
| `memory.py` | past answers and per-ATS lessons |
| `inbox.py`, `gmail.py` | read-only email tracking |
| `store.py` | SQLite job store |
| `server.py`, `dashboard.py`, `web/` | FastAPI backend and React dashboard |
| `quickfill/`, `chrome-helper/` | bulk form filling over CDP; a small extension that moves tabs between windows |
| `resume/build.py` | resume YAML to PDF, plus an ATS-style parse check |
| `eval/run_eval.py` | runs the apply agent on a fixed set of jobs across ATS types and scores it |

## Notes

- Automating job sites may break their terms of service. Read them before pointing this at a site, and use it for
  your own applications only.
- Every agent run writes a transcript under `~/.claude/projects/`. Delete that folder to remove them.
- `JOBPILOT_STEALTH=1` turns off every local fetch and the browser reader: discovery then uses only web search,
  which runs on Anthropic's side.
- No licence is granted. You're welcome to read the code; ask me before reusing it.
