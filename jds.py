"""
jds — fetch and keep every job's description, so we can see what the market asks for (and tailor the resume).

    python3 jds.py           # every job that has no saved description yet (any status)
    python3 jds.py --redo    # fetch again, overwriting

Clean text from the ATS APIs where there is one (the same endpoints postings.py uses), else the page's
visible text from an invisible Chrome (postings._read: no logins, no windows, ~8 pages at a time).
Saved to artifacts/jd/<job id>.txt (a header, then the text); jobs.extra.jd = {how, chars, at}.
No Claude calls.
"""
from __future__ import annotations

import asyncio
import html
import re
import sys
from datetime import datetime

import postings
import readers
import store
from apply_agent import ARTIFACTS

JD = ARTIFACTS / "jd"
MIN_CHARS = 400        # less than this is a login wall, a bot check or an error page, not a description
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# a page is kept only if it reads like a job description — not a careers homepage, "no longer open" or an error
JD_WORDS = re.compile(r"responsibilit|requirement|qualification|experience (with|in)|you will|you'll|what you('ll| will) do|"
                      r"about the role|must have|nice to have|skills|we're looking for|we are looking for", re.I)
DEAD = re.compile(r"no longer (open|available|accepting)|job (is )?(closed|expired|not found)|page not found|"
                  r"unable to load this role|position has been filled|error=true", re.I)


def looks_like_jd(text: str) -> bool:
    return len(text) >= MIN_CHARS and len(JD_WORDS.findall(text)) >= 2 and not DEAD.search(text[:1500])


_boards: dict[str, list] = {}


def _ashby_board(org: str) -> list:
    if org not in _boards:
        code, d = readers._get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
        _boards[org] = d.get("jobs", []) if code == 200 else []
    return _boards[org]


def _ashby_text(j: dict) -> str:
    pay = (j.get("compensation") or {}).get("compensationTierSummary") or ""
    return (f"Location: {j.get('location', '')}\nPay: {pay}\n\n" if pay else "") + (j.get("descriptionPlain") or _plain(j.get("descriptionHtml", "")))


def _plain(h: str) -> str:
    """HTML → readable text: block tags become line breaks, the rest is dropped."""
    h = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h\d|/tr)\s*/?>", "\n", html.unescape(h or ""))
    h = re.sub(r"(?i)<\s*li[^>]*>", "• ", h)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", h))).strip()


def from_api(url: str) -> tuple[str, str]:
    """(text, how) from the job's ATS API, or ("", "") when there's no API or the job is gone."""
    P = postings
    if m := P.GH.search(url):
        code, d = readers._get_json(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}?content=true")
        return (_plain(d.get("content", "")), "greenhouse") if code == 200 else ("", "")
    if m := P.ASHBY.search(url):
        j = next((j for j in _ashby_board(m[1]) if j.get("id", "").lower() == m[2].lower()), None)
        return (_ashby_text(j), "ashby") if j else ("", "")
    if m := P.LEVER.search(url):
        code, d = readers._get_json(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}")
        if code != 200:
            return "", ""
        lists = "\n\n".join(f"{x.get('text', '')}\n{_plain(x.get('content', ''))}" for x in d.get("lists", []))
        return "\n\n".join(t for t in (d.get("descriptionPlain", ""), lists, d.get("additionalPlain", "")) if t), "lever"
    if m := P.WORKABLE.search(url):
        code, d = readers._get_json(f"https://apply.workable.com/api/v1/widget/accounts/{m[1]}?details=true")
        j = next((j for j in d.get("jobs", []) if j.get("shortcode", "").upper() == m[2].upper()), None) if code == 200 else None
        return (_plain(j.get("description", "")), "workable") if j and j.get("description") else ("", "")
    if m := P.RECRUITEE.search(url):
        code, d = readers._get_json(f"https://{m[1]}.recruitee.com/api/offers/{m[2]}")
        o = d.get("offer") or {} if code == 200 else {}
        return (_plain(o.get("description", "") + o.get("requirements", "")), "recruitee") if o else ("", "")
    if m := UUID.search(url):     # company sites that front Ashby (sarvam.ai/careers/jobs/<ashby id>)
        for org in _ashby_orgs():
            if j := next((j for j in _ashby_board(org) if j.get("id", "").lower() == m[0].lower()), None):
                return _ashby_text(j), "ashby"
    return "", ""


def _ashby_orgs() -> list[str]:
    return sorted({m[1] for j in store.list_jobs() if (m := re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/", j["url"]))})


def _save(j: dict, text: str, how: str) -> None:
    head = f"{j['company']} — {j['title']}\n{j['url']}\nstatus: {j['status']} · source: {how} · fetched {datetime.now():%Y-%m-%d}\n\n"
    (JD / f"{j['id']}.txt").write_text(head + text.strip() + "\n", encoding="utf-8")
    store.set_extra(j["id"], "jd", {"how": how, "chars": len(text), "at": datetime.now().isoformat(timespec="seconds")})


async def fetch(redo: bool = False, statuses: tuple[str, ...] | None = None) -> dict:
    from playwright.async_api import async_playwright
    JD.mkdir(parents=True, exist_ok=True)
    if redo:
        for f in JD.glob("*.txt"):
            f.unlink()
        for j in store.list_jobs():
            if store.get_extra(j, "jd"):
                store.set_extra(j["id"], "jd", None)
    jobs = [j for j in store.list_jobs() if j["url"].startswith("http") and (redo or not (JD / f"{j['id']}.txt").exists())
            and (statuses is None or j["status"] in statuses)]
    out = {"jobs": len(jobs), "api": 0, "page": 0, "none": 0}
    rest = []
    for j in jobs:                                   # APIs first: clean text, no browser
        rec = store.get_extra(j, "recipe") or {}
        text = how = ""
        for u in [j["url"]] + list(dict.fromkeys(f["url"] for f in rec.get("fields", []))):
            try:
                text, how = from_api(u)
            except Exception:  # noqa: BLE001 — network trouble: fall back to the page
                text = ""
            if len(text) >= MIN_CHARS:
                break
        if len(text) >= MIN_CHARS:
            _save(j, text, f"api:{how}")
            out["api"] += 1
        else:
            rest.append(j)
    print(f"APIs: {out['api']} descriptions; reading {len(rest)} pages…", flush=True)
    if rest:
        sem = asyncio.Semaphore(postings.PAGES)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)

            async def one(j: dict) -> None:
                p = await postings._read(browser, j["url"], sem)
                ok = not p.get("error") and p.get("status") not in (404, 410) and looks_like_jd(p.get("text", "")) \
                    and "error=true" not in p.get("final", "")
                if ok:
                    _save(j, f"Page title: {p['title']}\nEnded on: {p['final']}\n\n{p['text']}", "page")
                out["page" if ok else "none"] += 1
                print(f"  {'page' if ok else '—':<5} {j['company'][:28]:<30} {p.get('error') or p.get('status', '')}", flush=True)

            await asyncio.gather(*(one(j) for j in rest))
            await browser.close()
    return out


if __name__ == "__main__":
    assert _plain("<p>Build <b>agents</b></p><ul><li>Python</li></ul>") == "Build agents\n• Python"
    assert not looks_like_jd("Unable to load this role. " + "Products Developers " * 40)
    assert looks_like_jd("About the role. Responsibilities: build agents. Requirements: Python. " * 8)
    print(asyncio.run(fetch("--redo" in sys.argv)))
