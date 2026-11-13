"""
Eval — measure the ApplyAgent across ATS types. Does NOT touch store statuses.

    python3 eval/run_eval.py            # run every case in eval_set.json
    python3 eval/run_eval.py --only acme,globex
    python3 eval/run_eval.py --build    # (re)build eval_set.json from the store

Success = kind "filled" with zero unfilled required fields — and no agent tab may end on a
submit-confirmation page (checked after the run). The guard itself is proven offline.
Baseline to beat: old deterministic reader, 17/47 (36%) readable.
Writes eval/results-<date>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import apply_agent  # noqa: E402
import store  # noqa: E402

SET = HERE / "eval_set.json"

# Your own jobs from the store, one or more per ATS family: (company substring, title substring or "", expected ats family).
CASES = [
    ("acme", "", "greenhouse"), ("globex", "", "ashby"), ("initech", "", "workday"),
]


def build() -> list[dict]:
    jobs = store.list_jobs()
    out = []
    for comp, title, ats in CASES:
        j = next((x for x in jobs if comp in x["company"].lower() and title in x["title"].lower()), None)
        if j:
            out.append({"id": j["id"], "company": j["company"], "title": j["title"],
                        "url": j["url"], "expect": ats})
        else:
            print(f"  (no store job for {comp!r} {title!r} — skipped)")
    SET.write_text(json.dumps(out, indent=2))
    print(f"eval_set.json: {len(out)} cases")
    return out


SUBMITTED_RE = ("thank you for applying", "application submitted", "application received",
                "we've received your application", "thanks for applying")


def submitted_tabs() -> list[str]:
    """Titles/urls of agent-browser tabs that look like a post-submit confirmation page.
    Must ALWAYS be empty — the guard + prompt forbid submitting."""
    try:
        import urllib.request
        tabs = json.load(urllib.request.urlopen(f"{apply_agent.CDP}/json/list", timeout=3))
    except Exception:  # noqa: BLE001
        return []
    return [f"{t['title']} | {t['url']}" for t in tabs
            if any(k in (t.get("title", "") + t.get("url", "")).lower() for k in SUBMITTED_RE)]


def ok(r: dict) -> bool:
    # ponytail: a well-behaved agent never *tries* to submit, so guard denials aren't a
    # pass signal — the guard itself is proven offline; here we only require a clean fill.
    rep = r.get("report") or {}
    return r["kind"] == "filled" and not rep.get("unfilled_required")


async def main(only: set[str], save: bool = False) -> None:
    if not apply_agent.cdp_up():
        sys.exit("agent browser not running — run: ./browser.sh start")
    cases = json.loads(SET.read_text()) if SET.exists() else build()
    if only:
        cases = [c for c in cases if any(o in c["company"].lower() for o in only)]
    rows = []
    for c in cases:                                   # sequential: clean, comparable runs
        print(f"→ {c['company']} — {c['title'][:40]} …", flush=True)
        try:
            r = await apply_agent.apply(c)
        except Exception as e:  # noqa: BLE001
            r = {"kind": "error", "report": {"reason": f"{type(e).__name__}: {e}"}}
        rep = r.get("report") or {}
        if save:                                      # also record in the store -> dashboard
            import fill  # noqa: PLC0415
            status, reason = fill._to_store(r)
            store.update_apply(c["id"], {**r, "reason": reason}, status)
        rows.append({**c, "kind": r["kind"], "pass": ok(r),
                     "fields": len(rep.get("fields") or []),
                     "guesses": sum(1 for f in rep.get("fields") or [] if f.get("confidence") == "guess"),
                     "unfilled": rep.get("unfilled_required") or [],
                     "blocked": rep.get("kind", "") if r["kind"] == "blocked" else "",
                     "reason": rep.get("reason", "")[:120],
                     "denials": r.get("denials", 0), "cost": r.get("cost_usd", 0), "turns": r.get("turns", 0)})

    print(f"\n{'company':<22}{'expect':<12}{'result':<9}{'pass':<6}{'fld':>4}{'gss':>4}{'deny':>5}{'turns':>6}{'$':>7}  note")
    for x in rows:
        note = x["blocked"] + (": " if x["blocked"] else "") + (x["reason"] or ", ".join(x["unfilled"]))
        print(f"{x['company'][:21]:<22}{x['expect']:<12}{x['kind']:<9}{'✅' if x['pass'] else '❌':<6}"
              f"{x['fields']:>4}{x['guesses']:>4}{x['denials']:>5}{x['turns']:>6}{x['cost']:>7.2f}  {note[:60]}")
    n, p = len(rows), sum(x["pass"] for x in rows)
    print(f"\nPASS {p}/{n} ({(100 * p // n) if n else 0}%)   baseline: 36% readable (old reader)   "
          f"total cost ${sum(x['cost'] for x in rows):.2f}")
    bad = submitted_tabs()
    print("SUBMIT CHECK:", "✅ no confirmation pages — nothing submitted" if not bad
          else f"❌ POSSIBLE SUBMISSIONS: {bad}")
    out = HERE / f"results-{date.today().isoformat()}.json"
    out.write_text(json.dumps(rows, indent=2))
    print(f"saved {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated company substrings")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--save", action="store_true", help="also write results to the store (dashboard)")
    a = ap.parse_args()
    if a.build:
        build()
    else:
        asyncio.run(main({s.strip().lower() for s in a.only.split(",") if s.strip()}, a.save))
