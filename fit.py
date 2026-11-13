"""
fit — before any apply run, does the job's description match what the candidate targets? One cheap Haiku call per job.

    python3 fit.py           # every 'new' job with a saved description and no verdict yet
    python3 fit.py --redo    # judge them again (after you change the targeting in profile.md)

Reads the "Targeting" and "Pitch" sections of profile.md and the saved description (artifacts/jd, from jds.py).
Verdicts: fit · maybe · no. "no" moves the job to skipped with the reason (Undo on the dashboard puts it back).
Saved as jobs.extra.fit = {verdict, reason, quote, at}. Eligibility (location, pay) is NOT judged here:
discovery and the apply agent's Part 1 do that.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime

import store
from apply_agent import JD, PROFILE, _read

CALLS = 8              # Haiku calls at once
JUDGE = """You decide whether a job matches what this candidate targets. You get the candidate's targeting and pitch, then
the job's description.
- fit: the job's core work is in the candidate's target roles.
- maybe: partly the candidate's (some of the core work is, some isn't), or the description is too thin to tell.
- no: the core work is outside the candidate's targets.
Judge the core work the description lays out, not the title: an "ML Engineer" building LLM agents is a fit, an
"AI Engineer" doing model-training infrastructure isn't. Don't judge location, pay or seniority: other checks do.
`quote`: the posting's own words that decide it (copied exactly, under 200 characters).
`reason`: one plain sentence, e.g. "core work is GPU inference serving, no LLM application or agent work"."""
VERDICT = {"type": "object", "additionalProperties": False, "required": ["verdict", "reason", "quote"],
           "properties": {"verdict": {"type": "string", "enum": ["fit", "maybe", "no"]},
                          "reason": {"type": "string"}, "quote": {"type": "string"}}}


def targeting() -> str:
    """The parts of profile.md that say what the candidate wants: Targeting and Pitch."""
    p = _read(PROFILE)
    return "\n\n".join(m.group(0).strip() for h in ("Targeting", "Pitch")
                       if (m := re.search(rf"^## {h}.*?(?=^## |\Z)", p, re.S | re.M)))


async def _judge(job: dict, jd: str, sem: asyncio.Semaphore) -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    opts = ClaudeAgentOptions(system_prompt=JUDGE, model="haiku", tools=[], max_turns=3, setting_sources=[],
                              strict_mcp_config=True, output_format={"type": "json_schema", "schema": VERDICT})
    prompt = f"# The candidate's targeting\n{targeting()}\n\n# The job: {job['title']} at {job['company']}\n{jd[:8000]}"
    out, cost = None, 0.0
    async with sem:
        try:
            async for m in query(prompt=prompt, options=opts):
                if isinstance(m, ResultMessage):
                    out, cost = m.structured_output, m.total_cost_usd or 0.0
        except Exception as e:  # noqa: BLE001 — no verdict: the job stays new and gets filled as before
            return {"verdict": "", "reason": f"Claude call failed ({type(e).__name__})", "quote": "", "cost": 0.0}
    if not isinstance(out, dict) or out.get("verdict") not in ("fit", "maybe", "no"):
        return {"verdict": "", "reason": "no verdict came back", "quote": "", "cost": cost}
    return {**out, "cost": cost}


async def check(redo: bool = False) -> dict:
    jobs = [j for j in store.list_jobs("new") if (JD / f"{j['id']}.txt").exists() and (redo or not store.get_extra(j, "fit"))]
    out = {"checked": len(jobs), "fit": 0, "maybe": 0, "no": 0, "failed": 0, "cost_usd": 0.0}
    sem = asyncio.Semaphore(CALLS)

    async def one(j: dict) -> None:
        v = await _judge(j, _read(JD / f"{j['id']}.txt"), sem)
        out["cost_usd"] += v.pop("cost")
        if not v["verdict"]:
            out["failed"] += 1
            return
        out[v["verdict"]] += 1
        store.set_extra(j["id"], "fit", {**v, "quote": v["quote"][:300], "reason": v["reason"][:300],
                                         "at": datetime.now().isoformat(timespec="seconds")})
        if v["verdict"] == "no":            # not the candidate's kind of job: out of the fill queue (Undo puts it back)
            with store.connect() as conn:
                conn.execute("UPDATE jobs SET status='skipped' WHERE id=? AND status='new'", (j["id"],))
        print(f"  {v['verdict']:<6} {j['company'][:26]:<28} {j['title'][:40]:<42} {v['reason'][:90]}", flush=True)

    await asyncio.gather(*(one(j) for j in jobs))
    out["cost_usd"] = round(out["cost_usd"], 4)
    return out


if __name__ == "__main__":
    t = targeting()
    assert "## Targeting" in t and "## Pitch" in t and "Eligibility" not in t, t[:300]
    print(json.dumps(asyncio.run(check("--redo" in sys.argv)), indent=2))
