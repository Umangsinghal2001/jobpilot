"""
postings — is a job posting still open? Uses each ATS's public API (no browser, no agent).

    greenhouse  boards-api.greenhouse.io/v1/boards/<org>/jobs/<id>     200 live · 404 closed
    ashby       api.ashbyhq.com/posting-api/job-board/<org>             job id listed → live, else closed
    lever       api.lever.co/v0/postings/<org>/<id>                      200 live · 404 closed
    workable    apply.workable.com/api/v1/widget/accounts/<org>          job shortcode listed → live, else closed
    recruitee   <org>.recruitee.com/api/offers/<slug>                    200 + published → live · 404 closed
    anything else → "unknown" here, then claude_check() reads the page and lets Claude decide (below)

    python3 postings.py            # check every job you might still apply to; closes the dead ones

claude_check() — for every job the APIs can't answer: one invisible Chrome (its own, not the agent's — no
logins needed, no windows) renders each listing, ~8 at a time, and one small Haiku call per page, in parallel,
returns open / closed / unclear with the exact words that prove it. Closed jobs are closed automatically, with
that quote saved on the job (`posting.quote`); a quote that isn't really on the page counts as unclear.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta

import readers
import store

OPEN = ("ready-to-submit", "form-ready", "needs-you", "new")

GH = re.compile(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)")
ASHBY = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", re.I)
LEVER = re.compile(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{36})", re.I)
WORKABLE = re.compile(r"apply\.workable\.com/([^/?#]+)/j/([0-9A-Za-z]+)")
RECRUITEE = re.compile(r"([a-z0-9-]+)\.recruitee\.com/o/([^/?#]+)", re.I)


def status(url: str) -> str:
    """'live' | 'closed' | 'unknown'."""
    try:
        if m := GH.search(url):
            code, _ = readers._get_json(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}")
            return {200: "live", 404: "closed"}.get(code, "unknown")
        if m := ASHBY.search(url):
            code, d = readers._get_json(f"https://api.ashbyhq.com/posting-api/job-board/{m[1]}")
            if code != 200:
                return "closed" if code == 404 else "unknown"
            return "live" if any(j.get("id", "").lower() == m[2].lower() for j in d.get("jobs", [])) else "closed"
        if m := LEVER.search(url):
            code, _ = readers._get_json(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}")
            return {200: "live", 404: "closed"}.get(code, "unknown")
        if m := WORKABLE.search(url):        # its per-job endpoint 404s even for live jobs; the board list doesn't
            code, d = readers._get_json(f"https://apply.workable.com/api/v1/widget/accounts/{m[1]}")
            if code != 200:
                return "closed" if code == 404 else "unknown"
            return "live" if any(j.get("shortcode", "").upper() == m[2].upper() for j in d.get("jobs", [])) else "closed"
        if m := RECRUITEE.search(url):
            code, d = readers._get_json(f"https://{m[1]}.recruitee.com/api/offers/{m[2]}")
            if code == 200:
                return "live" if (d.get("offer") or {}).get("status", "published") == "published" else "closed"
            return "closed" if code == 404 else "unknown"
    except Exception:  # noqa: BLE001 — network trouble is "unknown", never "closed"
        return "unknown"
    return "unknown"


def check(statuses=OPEN) -> dict:
    """Check every job in `statuses`; mark the dead ones closed. Returns {checked, live, closed[], unknown}."""
    now = datetime.now().isoformat(timespec="seconds")
    out = {"checked": 0, "live": 0, "unknown": 0, "closed": []}
    for j in store.list_jobs():
        prev = store.get_extra(j, "posting") or {}
        recheck = j["status"] == "closed" and prev.get("how") == "claude"
        if j["status"] not in statuses and not recheck:
            continue
        # the listing may be a company careers page while the form lives on an ATS — try the form URLs too
        rec = store.get_extra(j, "recipe") or {}
        s = "unknown"
        for u in [j["url"]] + list(dict.fromkeys(f["url"] for f in rec.get("fields", []))):
            if (s := status(u)) != "unknown":
                break
        if recheck:
            if s == "live":                    # Claude read a dead page (often an aggregator); the real posting is open
                ap = store.get_extra(j, "apply")
                back = fill_status(ap) if ap else "new"
                with store.connect() as conn:
                    conn.execute("UPDATE jobs SET status=? WHERE id=?", (back, j["id"]))
                store.set_extra(j["id"], "posting", {"status": "live", "checked": now})
                out.setdefault("reopened", []).append(f"{j['company']} — {j['title']}")
            continue
        out["checked"] += 1
        if s != "unknown" or prev.get("how") not in ("claude", "you"):   # keep Claude's (or your) verdict where the APIs can't say
            store.set_extra(j["id"], "posting", {"status": s, "checked": now})
        if s == "closed":
            with store.connect() as conn:
                conn.execute("UPDATE jobs SET status='closed' WHERE id=?", (j["id"],))
            store.set_extra(j["id"], "closed_at", now)
            out["closed"].append(f"{j['company']} — {j['title']}")
        else:
            out[s] += 1
    return out


# ── Claude reads the page for everything the APIs can't answer ──

def fill_status(apply: dict) -> str:
    """The status a job had from its last agent run (ready-to-submit / needs-you …), to reopen it to."""
    import fill
    return fill._to_store(apply)[0]


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
VERDICT = {"type": "object", "additionalProperties": False, "required": ["verdict", "quote", "reason"], "properties": {
    "verdict": {"type": "string", "enum": ["open", "closed", "unclear"]},
    "quote": {"type": "string"}, "reason": {"type": "string"}}}
JUDGE = """You check whether one job posting is still accepting applications.
You get the job (company, title), the URL we opened, the URL it ended on, the HTTP status, the page title
and the page's visible text.
- open: this job's posting is on the page and still takes applications (an apply button or form, or the
  role described with no closing notice).
- closed: the page says this job is closed, filled, expired, paused, no longer available or not found; OR we
  were sent to a general careers, search or home page that does not show this role.
- unclear: a login wall, a bot check, an empty or unrelated page, or you can't tell. Also unclear when the
  role is merely absent from a partial list (search results, "showing 60 of 5,667", paginated boards): that
  proves nothing. Absence counts as closed only when the page shows the company's complete list of openings.
quote: the exact words from the page text that prove your verdict, copied character for character, under 200
characters. When the proof is that the role is missing (a redirect, or a list of openings without it), quote
the final URL or the page title instead. Leave it empty only for unclear.
reason: one short sentence."""
PAGES, CALLS = 8, 8          # pages rendered at once · Claude calls at once


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


async def _read(browser, url: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)    # let JS-built pages render
            except Exception:  # noqa: BLE001 — busy pages never go idle; what rendered is enough
                pass
            texts = []                                   # embedded job boards (Kula, Greenhouse embeds…) live in iframes
            for fr in page.frames:
                try:
                    texts.append(await fr.evaluate("() => document.body ? document.body.innerText : ''"))
                except Exception:  # noqa: BLE001 — a frame that's gone or cross-origin-dead just adds nothing
                    pass
            text = "\n".join(t for t in texts if t)
            return {"status": resp.status if resp else 0, "final": page.url, "title": await page.title(),
                    "text": re.sub(r"\n\s*\n+", "\n", text)[:12000]}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {str(e).splitlines()[0][:140]}"}
        finally:
            await ctx.close()


async def _judge(job: dict, page: dict, sem: asyncio.Semaphore) -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    if page.get("error"):
        return {"verdict": "unclear", "quote": "", "reason": f"the page didn't load ({page['error']})"}
    if page["status"] in (404, 410):
        return {"verdict": "closed", "quote": f"HTTP {page['status']}", "reason": "the page no longer exists"}
    prompt = (f"Job: {job['title']} at {job['company']}\nOpened: {job['url']}\nEnded on: {page['final']}\n"
              f"HTTP status: {page['status']}\nPage title: {page['title']}\n\nPage text:\n{page['text']}")
    opts = ClaudeAgentOptions(system_prompt=JUDGE, model="haiku", tools=[], max_turns=3, setting_sources=[],
                              strict_mcp_config=True, output_format={"type": "json_schema", "schema": VERDICT})
    out = None
    async with sem:
        try:
            async for m in query(prompt=prompt, options=opts):
                if isinstance(m, ResultMessage):
                    out = m.structured_output
        except Exception as e:  # noqa: BLE001
            return {"verdict": "unclear", "quote": "", "reason": f"Claude call failed ({type(e).__name__})"}
    if not isinstance(out, dict) or out.get("verdict") not in ("open", "closed", "unclear"):
        return {"verdict": "unclear", "quote": "", "reason": "no verdict came back"}
    q = _norm(out.get("quote", ""))
    if out["verdict"] == "closed" and not (q and (q in _norm(page["text"]) or q in _norm(page["final"]) or q in _norm(page["title"]))):
        return {"verdict": "unclear", "quote": "", "reason": f"said closed, but its quote isn't on the page: {out.get('reason', '')}"}
    return out


async def claude_check(statuses=OPEN, fresh_hours: int = 20) -> dict:
    """Jobs the APIs couldn't answer (and Claude hasn't checked today): render, judge, close the dead ones."""
    from playwright.async_api import async_playwright
    since = (datetime.now() - timedelta(hours=fresh_hours)).isoformat()
    jobs = []
    for j in store.list_jobs():
        p = store.get_extra(j, "posting") or {}
        if j["status"] in statuses and j["url"].startswith("http") and p.get("status") != "live" \
                and not (p.get("how") == "claude" and p.get("checked", "") > since):
            jobs.append(j)
    out = {"checked": len(jobs), "open": 0, "unclear": 0, "closed": []}
    if not jobs:
        return out
    pages, calls = asyncio.Semaphore(PAGES), asyncio.Semaphore(CALLS)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        async def one(j: dict) -> None:
            page = await _read(browser, j["url"], pages)
            v = await _judge(j, page, calls)
            now = datetime.now().isoformat(timespec="seconds")
            status_ = {"open": "live", "closed": "closed", "unclear": "unknown"}[v["verdict"]]
            store.set_extra(j["id"], "posting", {"status": status_, "checked": now, "how": "claude",
                                                 "quote": v.get("quote", "")[:300], "reason": v.get("reason", "")[:300],
                                                 "final_url": page.get("final", "")})
            if v["verdict"] == "closed":
                with store.connect() as conn:
                    conn.execute("UPDATE jobs SET status='closed' WHERE id=?", (j["id"],))
                store.set_extra(j["id"], "closed_at", now)
                out["closed"].append(f"{j['company']} — {j['title']}: “{v.get('quote', '')[:120]}”")
            else:
                out[v["verdict"]] += 1
            print(f"  {v['verdict']:<8} {j['company'][:28]:<30} {v.get('reason', '')[:90]}", flush=True)

        await asyncio.gather(*(one(j) for j in jobs))
        await browser.close()
    return out


if __name__ == "__main__":
    assert status("https://example.com/careers/123") == "unknown"
    assert _norm("  Job\n  CLOSED ") == "job closed"
    assert WORKABLE.search("https://apply.workable.com/huggingface/j/81B46579FE/apply/")[2] == "81B46579FE"
    assert RECRUITEE.search("https://prenosis.recruitee.com/o/sr-software-engineer-1/c/new")[2] == "sr-software-engineer-1"
    print(json.dumps(check(), indent=2))
    print("Claude reads the rest…", flush=True)
    print(json.dumps(asyncio.run(claude_check()), indent=2, ensure_ascii=False))
