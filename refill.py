"""
refill — bring back a filled application from its recipe, WITHOUT the agent (no Claude, ~10 s, $0).
For when the tab got closed, Chrome restarted, or the session expired before you could review it.

    python3 refill.py acme                 # re-open + re-fill (company / title / id substring)
    python3 refill.py acme --force         # re-fill even if the tab is still open (closes it first)
    python3 refill.py --backfill            # build recipes for jobs filled before recipes existed

Recipes live in store.db, jobs.extra["recipe"] = {fields: [{label, kind, value, url}], created, …} —
written by apply_agent after every filled run. Replay goes through quickfill.mjs, so it never clicks buttons or Submit.
Needs the agent browser (./browser.sh start). Anything it can't place is listed for you to finish.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request

import apply_agent
import store

A = apply_agent.ARTIFACTS




def _find(q: str) -> list[dict]:
    q = q.lower()
    return [j for j in store.list_jobs()
            if q == j["id"] or q in j["company"].lower() or q in j["title"].lower()]


def backfill() -> None:
    """Older runs left no fill_fields record — rebuild from the stored report (kind=auto by label)."""
    n = 0
    for j in store.list_jobs("ready-to-submit"):
        rep = json.loads(j.get("extra") or "{}").get("apply", {}).get("report") or {}
        if rep and not store.get_extra(j, "recipe"):
            r = apply_agent.build_recipe(j, {}, rep)
            store.set_extra(j["id"], "recipe", r)
            print(f"  {j['company'][:24]:<25} {len(r['fields'])} fields (from report)")
            n += 1
    for res in A.glob("*.result.json"):          # single-job runs (apply_agent.py <id>) never touch the store
        jid = res.name.split(".")[0]
        r = json.loads(res.read_text())
        job = store.get_job(jid)
        if r.get("kind") == "filled" and job and not store.get_extra(job, "recipe"):
            rec = apply_agent.build_recipe(job, {}, r["report"])
            store.set_extra(jid, "recipe", rec)
            print(f"  {job['company'][:24]:<25} {len(rec['fields'])} fields (from {res.name})")
            n += 1
    print(f"backfilled {n} recipe(s)")


def _open_tabs(url: str) -> list[dict]:
    k = apply_agent._urlkey(url)
    tabs = json.load(urllib.request.urlopen(f"{apply_agent.CDP}/json/list", timeout=3))
    return [t for t in tabs if t.get("type") == "page" and apply_agent._urlkey(t.get("url", "")).startswith(k)]


def refill(job: dict, force: bool = False) -> int:
    rec = store.get_extra(job, "recipe")
    if not rec:
        print(f"no recipe for {job['company']} — run the agent once, or `refill.py --backfill`")
        return 1
    form_urls = list(dict.fromkeys(f["url"] for f in rec["fields"]))
    if not force and any(_open_tabs(u) for u in form_urls):
        print(f"{job['company']}: the filled tab is still open — nothing to do (use --force to re-fill)")
        return 0
    store.set_extra(job["id"], "parked", None)     # we're replacing its tab: closing it isn't a skip
    apply_agent.close_stale_tabs(job["url"])
    allowed = sorted(p for p in apply_agent.resume_options().values() if p)
    results = []
    for url in form_urls:                      # one quickfill per page of the form, in order
        fields = [{k: f[k] for k in ("label", "kind", "value")} for f in rec["fields"] if f["url"] == url]
        payload = {"url": url, "fields": fields, "files_allowed": allowed, "open": True}
        p = subprocess.run(["node", str(apply_agent.QUICKFILL)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=240)
        out = json.loads(p.stdout or "{}")
        if out.get("error") or "results" not in out:
            print(f"{job['company']}: {out.get('error') or p.stderr[-300:]}")
            if "probably closed" in (out.get("error") or ""):
                with store.connect() as conn:        # keep the dashboard honest
                    conn.execute("UPDATE jobs SET status='closed' WHERE id=?", (job["id"],))
                print("  marked closed in the store")
            return 1
        results += out["results"]
    ok = [r for r in results if r["status"] == "ok"]
    print(f"{job['company']} — {job['title'][:50]}: {len(ok)}/{len(rec['fields'])} fields restored "
          f"(recipe from {rec['created']})")
    for r in results:
        if r["status"] != "ok":
            print(f"  finish by hand · {r['label'][:60]} — {r['status']}: {r['detail'][:90]}")
    print("Review the tab in the agent browser, then Submit yourself.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("job", nargs="?", help="company / title / id substring")
    ap.add_argument("--force", action="store_true", help="re-fill even if the tab is still open")
    ap.add_argument("--backfill", action="store_true", help="build recipes from stored reports")
    a = ap.parse_args()
    if a.backfill:
        backfill()
        sys.exit(0)
    if not a.job:
        ap.error("give a job (company / title / id) or --backfill")
    if not apply_agent.cdp_up():
        sys.exit("agent browser not running — run: ./browser.sh start")
    jobs = [j for j in _find(a.job) if store.get_extra(j, "recipe")] or _find(a.job)
    if len(jobs) != 1:
        sys.exit("match exactly one job:\n" + "\n".join(f"  {j['id']}  {j['company']} — {j['title']}" for j in jobs[:15]))
    sys.exit(refill(jobs[0], a.force))
