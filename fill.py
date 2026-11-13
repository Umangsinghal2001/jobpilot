"""
fill.py — application harness. One fresh ApplyAgent per job (apply_agent.apply), one session in three parts:
find the form (browser), tailor the resume (no browser, TAILOR_SLOTS at once), fill the form (browser), then
stop before Submit. Browser parts share apply_agent.CHROME_SLOTS across every fill.py process. Status per job:
    ready-to-submit : whole form filled, tab left open + screenshot — you review & Submit
    needs-you       : blocked (login/captcha/account/email) or gaps left — reason says which
    skipped         : not eligible (onsite/hybrid outside India, US-only, pay under the floor)
    closed          : listing gone

Needs the agent browser: ./browser.sh start (log in once to LinkedIn/Wellfound/Google there).
Nothing is ever submitted — a deterministic guard blocks every Submit-style action.

    python3 fill.py                  # every job still in status 'new'
    python3 fill.py --job gitlab     # filter by company / title / id substring
    python3 fill.py --limit 5
    python3 fill.py --redo           # also re-run form-ready / needs-you jobs
    python3 fill.py --status needs-you
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import os
from datetime import date, datetime

import apply_agent
import store

TAILOR_SLOTS = 4  # resumes built at once (no browser): limited by cost. Chrome places: apply_agent.CHROME_SLOTS


def _to_store(res: dict) -> tuple[str, str]:
    """Map an ApplyAgent result -> (store status, reason)."""
    r = res.get("report") or {}
    if res["kind"] == "filled":
        gaps = r.get("unfilled_required") or []
        return ("needs-you", "unfilled: " + ", ".join(gaps)) if gaps else ("ready-to-submit", "")
    if res["kind"] == "blocked":
        kind = r.get("kind", "other")
        if kind == "not_eligible":                     # can't do it from India / under the pay floor
            return "skipped", f"not eligible: {r.get('reason', '')}"
        return ("closed" if kind in ("closed", "not_found") else "needs-you",
                f"{kind}: {r.get('reason', '')}")
    return "needs-you", f"agent error: {r.get('reason', '')}"


def spent_today() -> float:
    """API-equivalent $ of today's agent runs (subscription isn't billed per token; this is a usage gauge)."""
    today = date.today().isoformat()
    return sum(ap.get("cost_usd") or 0 for j in store.list_jobs()
               if (ap := store.get_extra(j, "apply") or {}).get("ran_at", "").startswith(today))


STARTED = datetime.now().isoformat(timespec="seconds")   # this batch's start: a job filled after it was done meanwhile


async def fill_one(job: dict, tailor: asyncio.Semaphore) -> tuple[str, str, int]:
    cap = store.settings()["daily_cap_usd"]
    async with tailor:   # ponytail: checked when the job's turn comes, so parallel runs can overshoot by a few jobs
        if cap and spent_today() >= cap:
            return job["company"], "paused (daily cap)", 0
    # fills and retries run side by side, so a job may also be in another task: never run it twice at once,
    # and don't redo one that another task filled after this batch started (the candidate's retry, for instance)
    lock = apply_agent.ARTIFACTS / "locks" / f"job-{job['id']}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return job["company"], "already running in another task", 0
        if ((store.get_extra(store.get_job(job["id"]) or job, "apply") or {}).get("ran_at") or "") > STARTED:
            return job["company"], "filled meanwhile by another task", 0
        try:
            res = await apply_agent.apply(job, tailor)
        except Exception as e:  # noqa: BLE001
            res = {"kind": "error", "report": {"reason": f"{type(e).__name__}: {e}"}}
    status, reason = _to_store(res)
    store.update_apply(job["id"], {**res, "reason": reason,
                                   "ran_at": datetime.now().isoformat(timespec="seconds")}, status)
    n = len((res.get("report") or {}).get("fields") or [])
    return job["company"], status, n


async def fill(job_filter: str | None = None, limit: int | None = None,
               statuses: tuple[str, ...] = ("new",), ids: list[str] | None = None) -> dict:
    if os.environ.get("JOBPILOT_STEALTH") == "1":
        print("fill: skipped — stealth mode (browser traffic is local/visible)")
        return {}
    if os.environ.get("JOBPILOT_SKIP_FILL") == "1":   # daily.sh: your visible agent Chrome is open — don't pop tabs into it
        print("fill: skipped — the visible agent Chrome is open; fill from the dashboard instead")
        return {}
    if not apply_agent.cdp_up():
        print("fill: agent browser not running — run: ./browser.sh start")
        return {}
    jobs = [j for j in store.list_jobs() if (j["id"] in ids if ids else j["status"] in statuses)]
    if job_filter:
        f = job_filter.lower()
        jobs = [j for j in jobs
                if f in j["company"].lower() or f in j["title"].lower() or f in j["id"]]
    if limit:
        jobs = jobs[:limit]
    if not jobs:
        print("fill: nothing to do")
        return {}

    tailor = asyncio.Semaphore(TAILOR_SLOTS)
    results = await asyncio.gather(*(fill_one(j, tailor) for j in jobs))
    summary: dict[str, int] = {}
    for company, status, n in results:
        summary[status] = summary.get(status, 0) + 1
        print(f"  {status:<16} {company[:28]:<30} {n} fields")
    print("fill summary:", summary)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", help="filter by company/title/id substring")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--redo", action="store_true", help="also re-run form-ready / needs-you jobs")
    ap.add_argument("--status", help="comma-separated statuses to process, e.g. needs-you")
    ap.add_argument("--ids", help="comma-separated job ids (any status) — the dashboard's Retry")
    a = ap.parse_args()
    if a.status:
        statuses = tuple(s.strip() for s in a.status.split(","))
    else:
        statuses = ("new", "form-ready", "needs-you") if a.redo else ("new",)
    asyncio.run(fill(a.job, a.limit, statuses, [i for i in (a.ids or "").split(",") if i] or None))
