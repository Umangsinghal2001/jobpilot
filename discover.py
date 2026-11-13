"""
L3 — Discovery agent (v1).

ONE Claude Agent SDK agent that:
  - web-searches job boards (LinkedIn/Naukri/Indeed/Wellfound listings),
  - checks your target companies' careers,
  - fetches remote-job APIs (RemoteOK / Remotive / HN "Who's Hiring"),
  - dedups within its own context,
  - and emits the final list via a `submit_jobs` tool (the proven
    `submit_decision` pattern).

Python then writes the batch to the L1 SQLite store, which dedups across runs.

Runs on your PERSONAL Mac (subscription auth via `claude setup-token`).
    python3 discover.py
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from pydantic import BaseModel

import store
from apply_agent import ROOT

HERE = Path(__file__).parent
PROFILE = ROOT / "profile.md"
TARGETS = ROOT / "target-companies.md"

TARGET_JOB_COUNT = 40
MODEL = "sonnet"  # cheap worker; discovery is listing-level, no heavy reasoning

# Remote-job APIs the agent should fetch (JSON endpoints).
API_SOURCES = [
    "https://remoteok.com/api",
    "https://remotive.com/api/remote-jobs?search=AI%20engineer",
    "https://hnhiring.com/september-2026",  # HN 'Who is hiring' (swap month as needed)
]


# ── structured output: submit_jobs tool ────────────────────────────────────────

class Job(BaseModel):
    title: str
    company: str
    url: str
    source: str            # e.g. "web:linkedin", "remoteok", "target:acme"
    salary: str | None = None
    location: str | None = None


class JobBatch(BaseModel):
    jobs: list[Job]


def _build_submit_tool(sink: list[dict]):
    """One-shot tool whose input schema == JobBatch. Captures the agent's list."""
    @tool(
        "submit_jobs",
        "Submit the FINAL, de-duplicated list of jobs. Call this exactly once, "
        "at the end. Do not produce a text-only answer.",
        JobBatch.model_json_schema(),
    )
    async def submit(args: dict[str, Any]) -> dict[str, Any]:
        sink.append(args)
        return {"content": [{"type": "text", "text": f"received {len(args.get('jobs', []))} jobs"}]}

    return submit


def _read(path: Path, fallback: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return fallback


def _system_prompt(stealth: bool = False) -> str:
    profile = _read(PROFILE, "(profile.md missing)")
    targets = _read(TARGETS, "(target-companies.md missing)")
    if stealth:
        # WebFetch runs on the LOCAL machine (verified empirically), so every fetched URL
        # leaves from this network. Stealth = WebSearch only (server-side).
        api_section = (
            "3. WebFetch is DISABLED for this run — do NOT try to open pages or APIs. "
            "Rely on WebSearch results only (titles, companies, URLs from search snippets)."
        )
    else:
        api_list = "\n".join(f"  - {u}" for u in API_SOURCES)
        api_section = "3. Fetch these remote-job APIs and parse the JSON for matching roles:\n" + api_list
    return f"""You are a job-discovery agent. Find NEW job openings that match the
candidate profile below, then submit them with the `submit_jobs` tool.

# Candidate profile
{profile}

# Target companies (check their careers pages too)
{targets}

# Sources to cover
1. Web search the boards for the candidate's target roles in the places the profile allows, and remote
   (LinkedIn/Indeed/Wellfound listings surface via search).
2. The target companies' career pages.
{api_section}

# Rules
- Collect roughly {TARGET_JOB_COUNT} genuinely relevant openings. Quality over padding —
  respect the profile's seniority band and salary floor; skip clearly junior/senior-only
  or off-domain roles.
- Apply the profile's ELIGIBILITY hard rules to what the listing shows: drop onsite/hybrid
  roles where the candidate can't work, remote roles limited to a region that excludes them, and
  roles whose posted pay tops out under the floor. If the listing doesn't say, keep it.
- Capture ONLY listing-level data: title, company, apply URL, source, and salary/location
  if shown. DO NOT open/read full job descriptions — that's a later stage. Keep your
  context small.
- DEDUPLICATE within your results (same role from two sources = one entry; ignore
  tracking params in URLs).
- Prefer direct apply URLs (company/ATS) over aggregator redirects when you have both.
- When done, call `submit_jobs` EXACTLY ONCE with the full deduped list. No prose.
"""


async def discover() -> dict:
    sink: list[dict] = []
    submit_tool = _build_submit_tool(sink)
    server = "jobpilot_discovery"
    mcp = create_sdk_mcp_server(name=server, tools=[submit_tool])

    # JOBPILOT_STEALTH=1 -> WebSearch only (server-side). WebFetch is LOCAL: every fetched
    # URL leaves from this machine's network.
    stealth = os.environ.get("JOBPILOT_STEALTH") == "1"
    tools = ["WebSearch", f"mcp__{server}__submit_jobs"] + ([] if stealth else ["WebFetch"])
    if stealth:
        print("discovery: STEALTH mode — WebSearch only, no local fetches")
    options = ClaudeAgentOptions(
        system_prompt=_system_prompt(stealth),
        mcp_servers={server: mcp},
        allowed_tools=tools,
        disallowed_tools=["Task"],          # no runaway subagents in an unattended run
        permission_mode="acceptEdits",
        setting_sources=[],                  # hermetic: ignore local .claude config
        model=MODEL,
    )

    user = (
        f"Find ~{TARGET_JOB_COUNT} new, relevant jobs now across all the sources. "
        "Deduplicate, then call submit_jobs once with the full list."
    )
    nudge = user + (
        "\n\nREMINDER: your only valid final action is a single `submit_jobs` tool call "
        "with the job list. Do not answer in text."
    )

    for attempt in range(2):
        prompt = user if attempt == 0 else nudge
        async for msg in query(prompt=prompt, options=options):
            for block in getattr(msg, "content", None) or []:
                is_tool = isinstance(block, ToolUseBlock) or getattr(block, "type", None) == "tool_use"
                name = getattr(block, "name", "")
                if is_tool and name.endswith("submit_jobs"):
                    sink.append(getattr(block, "input", {}) or {})
        if sink:
            break
        print(f"attempt {attempt + 1}: agent didn't submit_jobs; retrying with nudge...")

    if not sink:
        raise RuntimeError("Discovery agent never called submit_jobs after 2 attempts.")

    # Validate + flatten to store dicts (salary/location go into `extra`).
    batch = JobBatch.model_validate(sink[0])
    rows = [j.model_dump(exclude_none=True) for j in batch.jobs]
    result = store.add_jobs(rows)
    print(f"discovery: found {len(rows)} (submitted) → store new={result['new']} dupe={result['dupe']}")
    print(f"store totals: {store.counts()}")
    return result


if __name__ == "__main__":
    asyncio.run(discover())
