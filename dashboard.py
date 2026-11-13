"""
L6 — Dashboard: the one place to review, submit and steer applications — no terminal needed.

    python3 dashboard.py serve            # live app → http://localhost:8765 (opens it in the agent Chrome)
    python3 dashboard.py serve --no-open  # what the always-on background service runs (com.jobpilot.dashboard)
    python3 dashboard.py                  # read-only snapshot → dashboard.html (what run.py writes)

Live mode is a stdlib HTTP server bound to 127.0.0.1 only. Every button calls existing code as a background
task (fill.py, refill.py, discover.py, postings.py, daily.sh, eval/run_eval.py, browser.sh): tasks stream their
output to artifacts/tasks/<id>.log, can be cancelled, and their history survives restarts (artifacts/tasks.json).
Fills and retries run side by side (shared Chrome places); a second daily run, discovery or eval waits for the first.
A watcher keeps one live connection to the agent Chrome: a job's tab showing a confirmation marks it submitted (a page
no phrase knows gets one Haiku read); a closed tab asks you "Did you submit?" instead of skipping.
Safety: Host must be localhost (blocks DNS rebinding); every POST needs the X-JobPilot header (a page
on another site can't send it without a CORS preflight, which this server never approves). File edits are limited
to a fixed list, each saved over a backup; uploads must be PDFs. Nothing here ever clicks Submit.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import apply_agent
import memory
import postings
import store

HERE = Path(__file__).parent
ROOT = apply_agent.ROOT
ART = apply_agent.ARTIFACTS
OUT = HERE / "dashboard.html"
UI = HERE / "dashboard_ui.html"
PORT = 8765
PY = sys.executable
_read = apply_agent._read

DAILY_LABEL, SERVER_LABEL = "com.jobpilot.daily", "com.jobpilot.dashboard"
LAUNCH = Path.home() / "Library" / "LaunchAgents"
MANAGED = os.environ.get("XPC_SERVICE_NAME") == SERVER_LABEL      # launchd sets this for its jobs
TASKS_FILE, TASK_LOGS, BACKUPS = ART / "tasks.json", ART / "tasks", ART / "backups"

HEARD = ("replied", "interview", "offer", "rejected")
DONE = ("applied",) + HEARD
STATUSES = ("new", "ready-to-submit", "needs-you", "skipped", "closed") + DONE
FILES = {   # the only files the dashboard may edit → (path, who reads it)
    "profile": (ROOT / "profile.md", "Your identity, targeting and rules — every agent reads it."),
    "answers": (apply_agent.COMMON, "Standard application answers — the ApplyAgent's answer kit."),
    "resume": (ROOT / "resume.md", "Resume text — the ApplyAgent writes custom answers from it."),
    "targets": (ROOT / "target-companies.md", "Target companies — discovery checks their careers pages."),
    "apply_prompt": (apply_agent.PROMPTS / "apply.py", "The ApplyAgent's instructions for filling a form."),
    "tailor_prompt": (apply_agent.PROMPTS / "tailor_resume.py", "The ApplyAgent's instructions for tailoring your resume."),
}
CATS = {   # needs-you reason → (group title, what you do about it)
    "login": ("Login wall", "Open the site, log in once in the agent's Chrome, then Retry."),
    "account_required": ("Account needed", "Create the account yourself (the agent never creates accounts), then Retry."),
    "captcha": ("Bot check", "Open it and apply yourself — the agent never solves CAPTCHAs."),
    "email_apply": ("Apply by email", "Copy the draft, attach your resume, send it yourself."),
    "unfilled": ("Fields left empty", "Answer the missing fields below, or Retry the agent."),
    "error": ("Agent error", "Usually a timeout or an expired Claude login. Retry."),
    "legacy": ("Old reader result", "Filled by the old non-agent reader. Retry with the agent."),
    "other": ("Other", "See the reason, then Retry or apply yourself."),
}


# ── state: everything the page shows ─────────────────────────────────────────

def _open_tabs() -> list[dict]:
    try:
        return [t for t in json.load(urllib.request.urlopen(f"{apply_agent.CDP}/json/list", timeout=2))
                if t.get("type") == "page"]
    except Exception:  # noqa: BLE001
        return []


def _keys(job: dict, recipe: dict | None) -> set[str]:
    return {apply_agent._urlkey(job["url"])} | {apply_agent._urlkey(f["url"]) for f in (recipe or {}).get("fields", [])}


def _tab_for(job: dict, recipe: dict | None, tabs: list[dict]) -> dict | None:
    keys = _keys(job, recipe)
    return next((t for t in reversed(tabs) if any(apply_agent._urlkey(t["url"]).startswith(k) for k in keys)), None)


SHARED = {"myworkdayjobs.com": "Workday", "greenhouse.io": "Greenhouse", "lever.co": "Lever", "ashbyhq.com": "Ashby",
          "smartrecruiters.com": "SmartRecruiters", "docs.google.com": "Google Form"}   # one host, many companies


def _site(url: str) -> tuple[str, str]:
    """(name, group key) of a URL's site: "instahyre.com", or "Workday · fractal" where one host serves many companies
    (a login there is per company)."""
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    base = ".".join(host.split(".")[-3 if re.search(r"\.(co|com|org|net)\.\w\w$", host) else -2:])
    for dom, name in SHARED.items():
        if host.endswith(dom):
            tenant = host.removesuffix(dom).split(".")[0] if host != dom else ""
            return (f"{name} · {tenant}" if tenant and name == "Workday" else name), host
    return base, base


def _where(j: dict, ap: dict, form: dict) -> dict:
    """Where the job is stuck (the page to act on), and the aggregator it came through, if any."""
    rep = ap.get("report") or {}
    in_reason = re.findall(r"https?://[^\s)\"',]+", ap.get("reason") or rep.get("reason") or form.get("reason") or "")
    url = (rep.get("apply_url") or (ap.get("form") or {}).get("url") or (in_reason[0] if in_reason else "")
           or rep.get("tab_url") or j["url"])
    name, key = _site(url)
    via, _ = _site(j["url"])
    return {"site": name, "key": key, "url": url, "via": via if via != name and _site(j["url"])[1] != key else ""}


def _when(ap: dict) -> str:
    if ap.get("ran_at"):
        return ap["ran_at"]
    log = Path(ap.get("log") or "")
    return datetime.fromtimestamp(log.stat().st_mtime).isoformat(timespec="seconds") if log.is_file() else ""


def _category(ap: dict, form: dict) -> str:
    rep = ap.get("report") or {}
    if not ap:                                   # old reader: its reason text says what blocked it
        r = (form.get("reason") or "").lower()
        for cat, words in (("login", ("login", "sign-in", "sign in", "easy apply")), ("captcha", ("cloudflare", "bot", "captcha")),
                           ("account_required", ("account",)), ("email_apply", ("email", "mailto"))):
            if any(w in r for w in words):
                return cat
        return "legacy" if form else "other"
    if ap.get("kind") == "blocked":
        return rep.get("kind") if rep.get("kind") in CATS else "other"
    if ap.get("kind") == "error":
        return "error"
    return "unfilled" if rep.get("unfilled_required") else "other"


_AUTH = {"t": 0.0, "ok": None}


def _logged_in() -> bool | None:
    if time.time() - _AUTH["t"] > 120:                      # `claude auth status` is free; cache 2 min
        try:
            out = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=15).stdout
            _AUTH["ok"] = bool(json.loads(out).get("loggedIn"))
        except Exception:  # noqa: BLE001
            _AUTH["ok"] = None
        _AUTH["t"] = time.time()
    return _AUTH["ok"]


def _chrome_mode() -> str:
    if not apply_agent.cdp_up():
        return "stopped"
    headless = subprocess.run(["pgrep", "-f", "--", "jobpilot-chrome.*--headless"], capture_output=True).returncode == 0
    return "invisible" if headless else "visible"


def _me() -> dict:
    p = _read(apply_agent.PROFILE)
    get = lambda k: (m.group(1).strip() if (m := re.search(rf"^- {k}:\s*(\S[^(\n]*)", p, re.M)) else "")  # noqa: E731
    return {"name": get("Name"), "phone": get("Phone"), "linkedin": get("LinkedIn")}


def _email_draft(j: dict, me: dict) -> str:
    sign = "\n".join(x for x in (me["name"], " · ".join(x for x in (me["phone"], me["linkedin"]) if x)) if x)
    return (f"Subject: Application for {j['title']} — {me['name']}\n\n"
            f"Hi {j['company']} team,\n\n"
            f"I'd like to apply for the {j['title']} role. My resume is attached — happy to share anything else "
            f"that helps.\n\nBest,\n{sign}\n")


def _resume_for(track: str) -> str:
    opts = apply_agent.resume_options()
    return opts.get(track) or str(apply_agent.master_resume() or "")


def _daily() -> dict:
    loaded = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{DAILY_LABEL}"], capture_output=True).returncode == 0
    when, _, why = _read(ART / "daily-status").strip().partition("|")
    log = _read(HERE / "run.log").splitlines()[-300:]
    i = max((n for n, l in enumerate(log) if "run start" in l), default=len(log))
    last = [re.sub(r"^\S+ \S+ (INFO|ERROR) ", "", l)[:260] for l in log[i:] if re.search(r" (INFO|ERROR) ", l)]
    return {"on": loaded, "last_date": _read(ART / "last-daily-run").strip(), "status_at": when, "status": why,
            "last_log": last[-8:], "last_log_at": log[i][:19] if i < len(log) else ""}


def _evals() -> dict:
    hist = []
    for f in sorted((HERE / "eval").glob("results-*.json"), reverse=True):
        try:
            rs = json.loads(f.read_text())
        except ValueError:
            continue
        hist.append({"date": f.stem[8:], "cases": len(rs), "pass": sum(bool(r.get("pass")) for r in rs),
                     "cost": round(sum(r.get("cost") or 0 for r in rs), 2), "turns": sum(r.get("turns") or 0 for r in rs),
                     "fails": [f"{r.get('company')}: {r.get('blocked') or r.get('reason') or 'unfilled ' + ', '.join(r.get('unfilled') or [])}"
                               for r in rs if not r.get("pass")][:10]})
    try:
        n = len(json.loads((HERE / "eval" / "eval_set.json").read_text()))
    except (OSError, ValueError):
        n = 0
    return {"cases": n, "history": hist[:10]}


def _usage(jobs: list[dict]) -> dict:
    """API-equivalent $ per day / track / job, from each job's latest agent run (a retry replaces the earlier figure)."""
    runs = [j for j in jobs if j["cost"]]
    day: dict[str, dict] = {}
    track: dict[str, dict] = {}
    for j in runs:
        for bucket, k in ((day, j["filled_at"][:10]), (track, j["track"] or "unknown")):
            b = bucket.setdefault(k, {"cost": 0.0, "runs": 0, "turns": 0})
            b["cost"] += j["cost"]
            b["runs"] += 1
            b["turns"] += j["turns"]
    week = (date.today() - timedelta(days=6)).isoformat()
    n = len(runs) or 1
    return {"days": [{"day": k, **v} for k, v in sorted(day.items())[-14:]],
            "tracks": [{"track": k, **v} for k, v in sorted(track.items(), key=lambda kv: -kv[1]["cost"])],
            "top": [{"company": j["company"], "title": j["title"], "cost": j["cost"], "turns": j["turns"]}
                    for j in sorted(runs, key=lambda j: -j["cost"])[:8]],
            "today": round(day.get(date.today().isoformat(), {}).get("cost", 0), 2),
            "week": round(sum(v["cost"] for k, v in day.items() if k >= week), 2),
            "avg_cost": round(sum(j["cost"] for j in runs) / n, 2), "avg_turns": round(sum(j["turns"] for j in runs) / n)}


def _outcomes(jobs: list[dict]) -> dict:
    sub = [j for j in jobs if j["status"] in DONE]

    def by(key: str) -> list[dict]:
        rows: dict[str, dict] = {}
        for j in sub:
            r = rows.setdefault(j[key] or "unknown", {"submitted": 0, "heard": 0, "interviews": 0, "offers": 0})
            r["submitted"] += 1
            r["heard"] += j["status"] in HEARD
            r["interviews"] += j["status"] in ("interview", "offer")
            r["offers"] += j["status"] == "offer"
        return [{"key": k, **v} for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["submitted"])]
    return {"by_track": by("track"), "by_platform": by("platform"), "submitted": len(sub)}


def _resumes(jobs: list[dict]) -> dict:
    master = apply_agent.master_resume()
    return {"master": master.name if master else "",
            "tracks": [{"track": t, "file": Path(p).name if p else "", "own": (apply_agent.RESUMES / f"{t}.pdf").exists(),
                        "jobs": sum(j["track"] == t for j in jobs)} for t, p in apply_agent.resume_options().items()]}


def _active_jobs() -> list[str]:
    """Jobs an agent is filling right now: an agent task is running and the job's log was written in the last 2 min."""
    if not any(t["status"] == "running" and t.get("agent") for t in TASKS.values()):
        return []
    now = time.time()
    return [f.stem for f in ART.glob("*.log") if JOB.match(f.stem) and now - f.stat().st_mtime < 120]


def _view(j: dict, me: dict, tabs: list[dict], live: bool, qa_too: bool = False) -> tuple[dict, dict]:
    """One job as the app shows it, and its raw apply record. The answers (qa) are left out unless qa_too:
    they're most of the payload and only the Review page reads them (GET /api/job/{id})."""
    ex = json.loads(j.get("extra") or "{}")
    ap, form, rec = ex.get("apply") or {}, ex.get("form") or {}, ex.get("recipe")
    rep = ap.get("report") or {}
    ran = _when(ap) if ap else ""
    if rep.get("fields"):
        qa = [{"q": f.get("label", ""), "a": f.get("answer", ""), "guess": f.get("confidence") == "guess",
               "note": f.get("note", "")} for f in rep["fields"]]
    else:
        qa = [{"q": q.get("label", ""), "a": q.get("answer", ""), "guess": False, "note": ""}
              for q in form.get("questions", [])]
    shot = ART / f"{j['id']}.png"
    tab = _tab_for(j, rec, tabs) if live else None
    cat = _category(ap, form)
    x = {
        "id": j["id"], "title": j["title"], "company": j["company"], "url": j["url"],
        "found": j.get("first_seen") or "", "status": j["status"], "source": j.get("source") or "",
        "ats": rep.get("ats") or form.get("ats", ""), "platform": memory.ats_key(rep.get("ats") or form.get("ats", ""), j["url"]),
        "track": rep.get("track", ""),
        "resume": Path(rep.get("resume_path", "")).name if rep.get("resume_uploaded") else "",
        "reason": ap.get("reason") or form.get("reason", ""), "cat": cat,
        "email": rep.get("email", ""), "unfilled": rep.get("unfilled_required") or [],
        "editable": bool(rep.get("fields")), "answers": len(qa), "guesses": sum(q["guess"] for q in qa),
        "shot": f"artifacts/{shot.name}" if shot.exists() and ap else "",
        "log": (ART / f"{j['id']}.log").exists(),
        "filled_at": ran, "cost": ap.get("cost_usd") or 0, "turns": ap.get("turns") or 0,
        "recipe": len(rec["fields"]) if rec else 0, "tab": bool(tab),
        "posting": ex.get("posting") or {}, "events": ex.get("events") or {},
        "note": ex.get("note") or "", "tags": ex.get("tags") or [],
        "where": _where(j, ap, form),
        "fit": ex.get("fit") or {},
        "ask": ex.get("ask") or {}, "detected": ex.get("detected") or {}, "inbox": ex.get("inbox") or [],
    }
    if cat == "email_apply" and j["status"] == "needs-you":
        x["draft"] = _email_draft(j, me)
    if qa_too:
        x["qa"] = qa
    return x, ap


def job_view(job_id: str) -> dict | None:
    j = store.get_job(job_id)
    return _view(j, _me(), _open_tabs(), True, qa_too=True)[0] if j else None


def state(live: bool = True) -> dict:
    tabs = _open_tabs() if live else []
    jobs, last_run, last_err = [], "", ""
    me = _me()
    for j in store.list_jobs():
        x, ap = _view(j, me, tabs, live)
        if x["filled_at"] > last_run:
            last_run, last_err = x["filled_at"], ap.get("reason", "") if ap.get("kind") == "error" else ""
        jobs.append(x)
    mem = memory.load()
    usage = _usage(jobs)
    return {
        "today": date.today().isoformat(), "live": live, "managed": MANAGED, "jobs": jobs, "me": me,
        "active_jobs": _active_jobs() if live else [],
        "funnel": {"found": len(jobs),
                   "filled": sum(x["status"] in ("ready-to-submit", "skipped") + DONE for x in jobs),
                   "submitted": sum(x["status"] in DONE for x in jobs),
                   "replies": sum(x["status"] in HEARD for x in jobs),
                   "interviews": sum(x["status"] in ("interview", "offer") for x in jobs)},
        "health": {"chrome": apply_agent.cdp_up() if live else None, "browser": _chrome_mode() if live else "",
                   "login": _logged_in() if live else None,
                   "login_error": "authenticate" in last_err.lower(),
                   "week_cost": usage["week"], "last_run": last_run,
                   "checked": max((x["posting"].get("checked", "") for x in jobs), default="")},
        "cats": {k: {"title": t, "hint": h} for k, (t, h) in CATS.items()},
        "memory": {
            "answers": [{"key": k, "label": vs[0]["label"].rstrip("*").strip(),
                         "variants": [{"answer": v["answer"], "companies": v["companies"]} for v in vs]}
                        for k, vs in sorted(mem["answers"].items(),
                                            key=lambda kv: -sum(len(v["companies"]) for v in kv[1]))],
            "platforms": mem.get("platforms", {}), "pending": mem.get("pending", {}),
            "snapshots": memory.snapshots()[:15] if live else [],
        },
        "usage": usage, "outcomes": _outcomes(jobs),
        "settings": store.settings(),
        "daily": _daily() if live else {},
        "evals": _evals(),
        "resumes": _resumes(jobs),
        "files": {k: d for k, (_, d) in FILES.items()},
        "tasks": sorted(TASKS.values(), key=lambda t: t["id"])[-40:],
    }


# ── background tasks: streamed log, cancel, history that survives restarts ───

TASKS: dict[int, dict] = {}
_LOCK = threading.Lock()


def _save_tasks() -> None:
    TASKS_FILE.write_text(json.dumps(sorted(TASKS.values(), key=lambda t: t["id"])[-200:]))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _tail(p: Path, n: int) -> str:
    return "\n".join(_read(p).splitlines()[-n:])


def _finish(t: dict, status: str) -> None:
    lines = [x for x in _read(TASK_LOGS / f"{t['id']}.log").splitlines() if x.strip()]
    t.update(status="cancelled" if t.get("cancelled") else status, ended=datetime.now().isoformat(timespec="seconds"),
             result="\n".join(lines[-6:]))
    with _LOCK:
        _save_tasks()


def _adopt() -> None:
    """Server restarted: a task whose process is still alive keeps running (tasks run in their own session)."""
    try:
        for t in json.loads(TASKS_FILE.read_text()):
            TASKS[t["id"]] = t
    except (OSError, ValueError):
        return
    for t in TASKS.values():
        if t["status"] != "running":
            continue
        if t.get("pid") and _alive(t["pid"]):
            def wait(t=t):
                while _alive(t["pid"]):
                    time.sleep(3)
                _finish(t, "done")
            threading.Thread(target=wait, daemon=True).start()
        else:
            t.update(status="interrupted", ended=datetime.now().isoformat(timespec="seconds"),
                     result="the dashboard server stopped while this ran")
    _save_tasks()


class Busy(ValueError):
    pass


def run_task(label: str, cmd: list[str] | None = None, fn=None, job_id: str = "",
             agent: bool = False, env: dict | None = None, kind: str = "fill") -> dict:
    """agent=True: uses Claude. Fills and retries run side by side: their browser parts share the CHROME_SLOTS lock
    files (apply_agent.chrome_slot) across every process, and fill.py never runs one job twice at once. Only a
    second daily run, discovery or eval waits for the first (kind)."""
    with _LOCK:
        running = [t for t in TASKS.values() if t["status"] == "running"]
        if busy := next((t for t in running if job_id and t["job_id"] == job_id), None):
            return busy
        if agent and kind != "fill" and (other := next((t for t in running if t.get("kind") == kind), None)):
            raise Busy(f"“{other['label']}” is still running — wait for it or cancel it (bottom right).")
        t = {"id": max(TASKS, default=0) + 1, "label": label, "job_id": job_id, "agent": agent, "kind": kind, "status": "running",
             "started": datetime.now().isoformat(timespec="seconds"), "ended": "", "result": "", "pid": 0}
        TASKS[t["id"]] = t
        _save_tasks()
    TASK_LOGS.mkdir(parents=True, exist_ok=True)
    log = TASK_LOGS / f"{t['id']}.log"

    def work():
        try:
            if cmd:
                with log.open("w") as f:
                    p = subprocess.Popen(cmd, cwd=HERE, stdout=f, stderr=subprocess.STDOUT, start_new_session=True,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})})
                    t["pid"] = p.pid
                    with _LOCK:
                        _save_tasks()
                    rc = p.wait()
                _finish(t, "done" if rc == 0 else "failed")
            else:
                log.write_text(str(fn()))
                _finish(t, "done")
        except Exception as e:  # noqa: BLE001
            with log.open("a") as f:
                f.write(f"\n{type(e).__name__}: {e}\n")
            _finish(t, "failed")

    threading.Thread(target=work, daemon=True).start()
    return t


def act_cancel(tid: int) -> dict:
    t = TASKS.get(tid)
    if not t or t["status"] != "running" or not t.get("pid"):
        raise ValueError("That task isn't running.")
    t["cancelled"] = True
    os.killpg(t["pid"], signal.SIGTERM)     # the whole group: fill.py and the Claude / Playwright processes it started
    return {"message": f"Cancelled: {t['label']} — tabs already filled stay open"}


# ── actions ──────────────────────────────────────────────────────────────────

def _job(job_id: str) -> dict:
    j = store.get_job(job_id)
    if not j:
        raise KeyError(f"no job {job_id}")
    return j


def _event(job_id: str, name: str) -> None:
    ev = store.get_extra(_job(job_id), "events") or {}
    ev[name] = datetime.now().isoformat(timespec="seconds")
    store.set_extra(job_id, "events", ev)


def _front_agent_chrome() -> None:
    pid = subprocess.run(["pgrep", "-f", f"Contents/MacOS/Google Chrome --.*--user-data-dir={Path.home()}/.jobpilot-chrome"],
                         capture_output=True, text=True).stdout.split()
    if pid:
        subprocess.run(["osascript", "-e", f'tell application "System Events" to set frontmost of '
                        f'(first process whose unix id is {pid[0]}) to true'], capture_output=True)


def _open_in_agent(url: str) -> None:
    urllib.request.urlopen(urllib.request.Request(f"{apply_agent.CDP}/json/new?{url}", method="PUT"), timeout=5)
    _front_agent_chrome()


def _need_chrome() -> dict | None:
    mode = _chrome_mode()
    if mode == "stopped":
        return {"error": "The agent's Chrome isn't running — start it in Control → Agent Chrome."}
    if mode == "invisible":
        return {"error": "The daily run is using the invisible agent Chrome — try again when it finishes."}
    return None


def act_open(job_id: str) -> dict:
    """Bring the filled tab to the front — or refill it from the recipe if it's gone."""
    j = _job(job_id)
    if err := _need_chrome():
        return err
    rec = store.get_extra(j, "recipe")
    tab = _tab_for(j, rec, _open_tabs())
    if tab:
        urllib.request.urlopen(f"{apply_agent.CDP}/json/activate/{tab['id']}", timeout=3)
        _front_agent_chrome()
        return {"message": f"{j['company']}: tab brought to the front"}
    if rec:
        t = run_task(f"Refill · {j['company']}", [PY, "refill.py", job_id], job_id=job_id)
        threading.Timer(15, _front_agent_chrome).start()
        return {"task": t, "message": f"{j['company']}: tab was gone — refilling it from the recipe"}
    _open_in_agent(j["url"])
    return {"message": f"{j['company']}: no recipe yet — opened the listing"}


def act_site(job_id: str) -> dict:
    """Login-wall helper: open the job's site in the agent Chrome so you can log in there once."""
    j = _job(job_id)
    if err := _need_chrome():
        return err
    w = _where(j, store.get_extra(j, "apply") or {}, store.get_extra(j, "form") or {})
    _open_in_agent(w["url"])            # where it got stuck, not the listing it came from
    return {"message": f"Opened {w['site']} in the agent Chrome. Log in there once, then press Retry here."}


def act_reveal(job_id: str) -> dict:
    rep = (store.get_extra(_job(job_id), "apply") or {}).get("report") or {}
    p = _resume_for(rep.get("track", ""))
    if not p:
        return {"error": "No resume PDF found — add one in Knowledge → Resumes."}
    subprocess.run(["open", "-R", p])
    return {"message": f"Resume shown in Finder: {Path(p).name}"}


def act_status(job_id: str, status: str, auto: bool = False) -> dict:
    """I submitted / Skip / outcomes / Undo. Submitting is when memory learns your answers."""
    j = _job(job_id)
    if status not in STATUSES:
        raise ValueError("bad status")
    with store.connect() as conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    store.set_extra(job_id, "ask", None)                   # any answer settles "Did you submit?"
    _event(job_id, "auto_detected" if auto else status if status != "ready-to-submit" else "reopened")
    if j["status"] == "closed" and status != "closed":     # you say it's open: no posting check closes it again
        store.set_extra(job_id, "posting", {"status": "live", "how": "you", "checked": datetime.now().isoformat(timespec="seconds")})
    if status == "applied" and j["status"] not in DONE:
        rep = (store.get_extra(j, "apply") or {}).get("report") or {}
        if rep:
            memory.learn(j["company"], rep, j["url"], answers=True, lessons_to=None)
    words = {"applied": "marked submitted — memory learned these answers", "skipped": "skipped",
             "replied": "reply recorded", "interview": "interview recorded 🎉", "offer": "offer recorded 🎉",
             "rejected": "marked rejected", "ready-to-submit": "back in Today", "closed": "marked closed",
             "new": "back to new", "needs-you": "moved to Needs you"}
    return {"message": f"{j['company']}: {words[status]}"}


def act_bulk(ids: list[str], do: str, tag: str = "") -> dict:
    if do == "tag":
        tag = tag.strip()[:30]
        if not tag:
            raise ValueError("Type a tag first.")
        for i in ids:
            tags = store.get_extra(_job(i), "tags") or []
            store.set_extra(i, "tags", tags if tag in tags else tags + [tag])
        return {"message": f"Tagged {len(ids)} job(s) “{tag}”"}
    if do not in ("skipped", "closed", "new"):
        raise ValueError("bad bulk action")
    for i in ids:
        act_status(i, do)
    return {"message": f"{len(ids)} job(s) → {do}"}


def act_note(job_id: str, note: str, tags: list[str]) -> dict:
    _job(job_id)
    store.set_extra(job_id, "note", note.strip()[:4000])
    store.set_extra(job_id, "tags", list(dict.fromkeys(t.strip()[:30] for t in tags if t.strip()))[:10])
    return {"message": "Note saved"}


def _recipe_item(rec: dict, label: str) -> dict | None:
    k = memory.norm(label)
    return next((x for x in rec["fields"] if apply_agent._same(memory.norm(x["label"]), k)), None)


def act_answer(job_id: str, idx: int, answer: str) -> dict:
    """Edit one answer: fixes the stored report (what memory learns on submit) AND the recipe (what refill types)."""
    j = _job(job_id)
    ap = store.get_extra(j, "apply") or {}
    f = ap["report"]["fields"][idx]
    f.update(answer=answer, confidence="sure", note="edited by you")
    store.set_extra(job_id, "apply", ap)
    rec = store.get_extra(j, "recipe") or {"job_id": job_id, "company": j["company"], "title": j["title"],
                                             "created": time.strftime("%Y-%m-%d %H:%M"), "fields": []}
    item = _recipe_item(rec, f["label"])
    if item:
        item["value"] = answer
    else:
        url = rec["fields"][-1]["url"] if rec["fields"] else (ap["report"].get("tab_url") or j["url"])
        rec["fields"].append({"label": f["label"].rstrip("*").strip(), "kind": "auto", "value": answer, "url": url, "ok": None})
    store.set_extra(job_id, "recipe", rec)
    return {"message": "Saved — refill and memory will use the new answer."}


def act_push(job_id: str, idx: int) -> dict:
    """Type one edited answer into the open tab (never opens a new one, never submits)."""
    j = _job(job_id)
    f = (store.get_extra(j, "apply") or {})["report"]["fields"][idx]
    item = _recipe_item(store.get_extra(j, "recipe") or {"fields": []}, f["label"])
    if not item:
        return {"error": "Save the answer first."}
    payload = json.dumps({"url": item["url"], "fields": [{k: item[k] for k in ("label", "kind", "value")}],
                          "files_allowed": sorted(p for p in apply_agent.resume_options().values() if p)})
    p = subprocess.run(["node", str(apply_agent.QUICKFILL)], input=payload, capture_output=True, text=True, timeout=120)
    out = json.loads(p.stdout or "{}")
    if out.get("error"):
        return {"error": "The form's tab isn't open — use Open form (it refills with your edit)."}
    r = out["results"][0]
    return {"message": f"Typed into the open form ({r['detail']})"} if r["status"] == "ok" \
        else {"error": f"Couldn't place it ({r['status']}: {r['detail']}) — fix it in the tab."}


def act_memory(body: dict) -> dict:
    kind = body.get("kind")
    if kind == "lesson":
        memory.review_lesson(body["ats"], body["lesson"], body["action"])
        return {"message": {"approve": "Note approved — the agent follows it from the next run",
                            "reject": "Note rejected", "delete": "Note deleted"}[body["action"]]}
    if kind == "merge":
        memory.merge_answers(body["src"], body["dst"])
        return {"message": "Questions merged"}
    if kind == "rename":
        memory.rename_platform(body["old"], body["new"])
        return {"message": f"Platform renamed to {body['new'].strip().lower()}"}
    if kind == "restore":
        memory.restore(body["name"])
        return {"message": "Memory restored (the version you replaced is a snapshot too)"}
    memory.edit_answer(body["key"], body["old"], body.get("new"))
    return {"message": "Answer deleted" if body.get("new") is None else "Answer updated"}


def act_browser(do: str) -> dict:
    if do == "normal":                       # "my profiles disappeared": a 2nd Chrome on your usual profile folder
        subprocess.run(["open", "-na", "Google Chrome"])
        return {"message": "Opened your normal Chrome (your usual profiles)"}
    sh = str(HERE / "browser.sh")
    cmd = {"start": f"{sh} start", "stop": f"{sh} stop",
           "restart": f"{sh} stop; for i in $(seq 1 20); do {sh} status >/dev/null 2>&1 || break; sleep 0.5; done; {sh} start"}
    if do not in cmd:
        raise ValueError("bad browser action")
    if do != "stop" and _chrome_mode() == "invisible":
        return {"error": "The daily run is using the invisible agent Chrome — try again when it finishes."}
    return {"task": run_task(f"Agent Chrome · {do}", ["/bin/zsh", "-c", cmd[do]])}


def act_daily(body: dict) -> dict:
    dom, do = f"gui/{os.getuid()}", body.get("do")
    plist = LAUNCH / f"{DAILY_LABEL}.plist"
    if do == "on":
        if not plist.exists():
            shutil.copy(HERE / f"{DAILY_LABEL}.plist", plist)
        subprocess.run(["launchctl", "bootstrap", dom, str(plist)], capture_output=True)
        return {"message": "Daily run on — checks every 30 min while you use the Mac"}
    if do == "off":
        subprocess.run(["launchctl", "bootout", f"{dom}/{DAILY_LABEL}"], capture_output=True)
        return {"message": "Daily run off"}
    if do == "now":
        return {"task": run_task("Daily run (now)", ["/bin/zsh", str(HERE / "daily.sh")], agent=True,
                                 env={"JOBPILOT_FORCE": "1"}, kind="daily")}
    if do == "settings":
        s = body.get("settings") or {}
        clean = {"min_battery": max(0, min(100, int(s.get("min_battery", 50)))),
                 "max_idle_min": max(1, min(240, int(s.get("max_idle_min", 5)))),
                 "daily_cap_usd": max(0.0, float(s.get("daily_cap_usd", 0))),
                 "auto_detect": bool(s.get("auto_detect", True))}
        store.save_settings(**clean)
        return {"message": "Settings saved"}
    raise ValueError("bad daily action")


def act_login(do: str) -> dict:
    _AUTH["t"] = 0                             # recheck on the next refresh
    if do == "recheck":
        return {"message": "Checking the Claude login…"}
    f = ART / "claude-login.command"           # .command opens in Terminal without any automation permission
    f.write_text("#!/bin/zsh\necho 'Sign in to Claude for JobPilot (your Pro/Max account), then close this window.'\n"
                 "claude auth login\n")
    f.chmod(0o755)
    subprocess.run(["open", str(f)])
    return {"message": "Terminal opened with `claude auth login` — finish signing in there"}


def act_add(body: dict) -> dict:
    url = str(body.get("url", "")).strip()
    if not re.fullmatch(r"https?://\S+", url):
        raise ValueError("Paste the full job link (https://…)")
    slug = next((m.group(1) for rx in (postings.GH, postings.ASHBY, postings.LEVER) if (m := rx.search(url))), "")
    host = re.sub(r"^www\.", "", url.split("/")[2])
    company = str(body.get("company", "")).strip() or (slug or host.split(".")[0]).replace("-", " ").title()
    title = str(body.get("title", "")).strip() or "(added by you)"
    r = store.add_jobs([{"title": title, "company": company, "url": url, "source": "manual"}])
    jid = store.job_id(company, title, url)
    msg = f"Added {company}" if r["new"] else f"{company} was already in your list"
    if body.get("fill"):
        return {"message": msg + " — filling it now",
                "task": run_task(f"Fill · {company}", [PY, "fill.py", "--ids", jid], job_id=jid, agent=True,
                                 env={"JOBPILOT_PRIORITY": "1"})}
    return {"message": msg}


def act_file(name: str, text: str) -> dict:
    path = FILES[name][0]
    if len(text) > 300_000:
        raise ValueError("File too large")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy(path, BACKUPS / f"{name}-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    path.write_text(text, encoding="utf-8")
    return {"message": f"Saved {path.name} — the next agent run uses it (old version in artifacts/backups)"}


def act_resume(track: str, data: bytes, remove: bool = False) -> dict:
    if track not in apply_agent.TRACKS:
        raise ValueError("unknown track")
    if not remove and (not data.startswith(b"%PDF-") or len(data) > 10_000_000):
        raise ValueError("Upload a PDF under 10 MB")
    dest = apply_agent.RESUMES / f"{track}.pdf"
    if dest.exists():
        BACKUPS.mkdir(parents=True, exist_ok=True)
        shutil.move(dest, BACKUPS / f"{track}-{datetime.now():%Y%m%d-%H%M%S}.pdf")
    if remove:
        return {"message": f"{track}: removed — jobs on this track use your master resume"}
    dest.write_bytes(data)
    return {"message": f"{track}: resume saved — the agent uploads it for {track.replace('_', ' ')} jobs"}


def act_restart() -> dict:
    if not MANAGED:
        return {"error": "This server was started from a terminal — stop it with Ctrl+C and start it again."}
    threading.Timer(0.5, os._exit, [3]).start()     # launchd restarts it (KeepAlive on non-zero exit)
    return {"message": "Restarting the dashboard server — the page reconnects in a few seconds"}


def _timeline(job_id: str) -> dict:
    """The agent's run, step by step, from artifacts/<id>.log (written by apply_agent.Guard)."""
    rows, t0 = [], None
    for line in _read(ART / f"{job_id}.log").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t0 = t0 if t0 is not None else r.get("t", 0)
        a, tool = r.get("input") or {}, r.get("tool", "")
        if isinstance(a, str):           # in-process tools log their input as text
            n = len(re.findall(r"'label':", a))
            what = f"{n} field(s)" if tool == "fill_fields" else a[:200]
        elif tool == "browser_type":
            what = f"{a.get('element', '')}: “{str(a.get('text', ''))[:80]}”"
        else:
            what = a.get("element") or a.get("url") or a.get("action") or a.get("filename") or a.get("key") or ""
            if a.get("paths"):
                what = "upload " + ", ".join(Path(p).name for p in a["paths"])
        rows.append({"s": round(r.get("t", t0) - t0), "tool": tool.replace("browser_", ""), "deny": r.get("ev") == "DENY",
                     "what": str(r.get("why") if r.get("ev") == "DENY" else what)[:240],
                     "out": str(r.get("out", "")).split("\n")[0][:200]})
    shot = ART / f"{job_id}.png"
    return {"steps": rows, "shot": f"artifacts/{shot.name}" if shot.exists() else ""}


# ── submission watcher ───────────────────────────────────────────────────────
# One live CDP connection to the agent Chrome (while it's visible): every tab's page is read when it changes and every
# 3 s while it's on screen. A confirmation in a job's own tab (by tab id, from park(); else by URL) marks it submitted.
# A job's tab that moved to a new page without a known phrase gets one cheap Haiku read. A closed tab never means
# "skipped" any more: the job goes into "Did you submit?" (extra.ask) for you to answer.

CONFIRM = re.compile(
    r"thanks? (you )?for (applying|your application|submitting)|application (has been |was )?(successfully )?"
    r"(submitted|received|sent)|we('ve| have) received your application|your application is (in|complete)|"
    r"successfully (submitted|applied)|you('ve| have) (successfully )?applied|your response has been recorded|"
    r"thanks for completing|form (has been )?submitted|application sent", re.I)   # not "thanks for your interest": postings say it
CONFIRM_URL = re.compile(r"/(confirmation|thank-?you|thanks|submitted|application-submitted|formresponse)\b", re.I)
READ_JS = """(() => JSON.stringify({vis: document.visibilityState, url: location.href,
  text: document.visibilityState === 'visible' || window.__jpForce ? ([...document.querySelectorAll('[role=dialog], dialog[open]')]
    .map(d => d.innerText).join('\\n') + '\\n' + (document.body ? document.body.innerText : '').slice(0, 6000)) : ''}))()"""
JUDGE_PAGE = """A job candidate filled an application form in this browser tab, and the tab then moved to the page below.
Does this page say the application was sent (a confirmation, thank-you or "we received it" page)? An error page, a
validation message, the form itself, a login page or an unrelated page is "no". `quote`: the page's own words that
decide it, copied exactly ("" when no)."""


class _Cdp:
    """A tiny synchronous CDP client: calls wait for their own reply; events in between are queued."""
    def __init__(self, ws):
        self.ws, self.n, self.events = ws, 0, []

    def call(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 5) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}, **({"sessionId": session} if session else {})}))
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(self.ws.recv(timeout=max(0.05, end - time.time())))
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m["result"]
            if "method" in m:
                self.events.append(m)
        raise TimeoutError(method)

    def wait_events(self, timeout: float) -> list[dict]:
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = json.loads(self.ws.recv(timeout=max(0.05, end - time.time())))
            except TimeoutError:
                break
            if "method" in m:
                self.events.append(m)
        out, self.events = self.events, []
        return out


def _job_tabs() -> tuple[dict[str, str], dict[str, set[str]]]:
    """{tab id: job id} for the tabs park() moved to your review window, and every open job's URL keys (the fallback
    for a tab opened another way)."""
    tabs, keys = {}, {}
    for j in store.list_jobs():
        if j["status"] in ("ready-to-submit", "needs-you", "form-ready") and "reopened" not in (store.get_extra(j, "events") or {}):
            for tid in (store.get_extra(j, "parked") or {}).get("targets", []):
                tabs[tid] = j["id"]
            keys[j["id"]] = _keys(j, store.get_extra(j, "recipe"))
    return tabs, keys


def _page_says_sent(text: str) -> dict:
    """One Haiku read of a page a job's tab moved to: {sent: bool, quote}. Only when no known phrase matched."""
    import asyncio
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    schema = {"type": "object", "additionalProperties": False, "required": ["sent", "quote"],
              "properties": {"sent": {"type": "boolean"}, "quote": {"type": "string"}}}
    opts = ClaudeAgentOptions(system_prompt=JUDGE_PAGE, model="haiku", tools=[], max_turns=3, setting_sources=[],
                              strict_mcp_config=True, output_format={"type": "json_schema", "schema": schema})

    async def run() -> dict:
        async for m in query(prompt=text[:6000], options=opts):
            if isinstance(m, ResultMessage) and isinstance(m.structured_output, dict):
                return m.structured_output
        return {"sent": False, "quote": ""}
    out = asyncio.run(run())
    q = " ".join(str(out.get("quote", "")).split()).lower()
    return out if not out.get("sent") or (q and q in " ".join(text.split()).lower()) else {"sent": False, "quote": ""}


def _submitted(jid: str, how: str, evidence: str) -> None:
    act_status(jid, "applied", auto=True)
    store.set_extra(jid, "detected", {"how": how, "evidence": evidence[:300], "at": datetime.now().isoformat(timespec="seconds")})
    print(f"detected submission ({how}): {jid} — {evidence[:100]}", flush=True)


def _watch_session() -> None:
    """Watch until Chrome goes away or detection is switched off."""
    from websockets.sync.client import connect
    ver = json.load(urllib.request.urlopen(f"{apply_agent.CDP}/json/version", timeout=3))
    with connect(ver["webSocketDebuggerUrl"], max_size=None, open_timeout=5) as ws:
        cdp = _Cdp(ws)
        cdp.call("Target.setDiscoverTargets", {"discover": True})
        sessions: dict[str, str] = {}       # tab id -> our CDP session on it
        first_url: dict[str, str] = {}      # tab id -> the page it showed when we first saw it
        judged: set[tuple[str, str]] = set()
        changed: set[str] = set()
        last_closed_check = 0.0
        while store.settings()["auto_detect"] and _chrome_mode() == "visible":
            for ev in cdp.wait_events(3):
                tid = (ev.get("params", {}).get("targetInfo") or {}).get("targetId") or ev.get("params", {}).get("targetId")
                if ev["method"] == "Target.targetInfoChanged" and tid:
                    changed.add(tid)        # a new page or title: read it now, even if it isn't on screen
                elif ev["method"] == "Target.targetDestroyed" and tid:
                    sessions.pop(tid, None)
                    first_url.pop(tid, None)
            job_of, keys = _job_tabs()
            pages = [t for t in _open_tabs() if t["url"].startswith("http") and not t["url"].startswith(apply_agent.APP)]
            for t in pages:
                tid = t["id"]
                try:
                    if tid not in sessions:
                        sessions[tid] = cdp.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
                    expr = ("window.__jpForce = true;" if tid in changed else "window.__jpForce = false;") + READ_JS
                    r = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, sessions[tid], timeout=3)
                    page = json.loads(r["result"]["value"])
                except Exception:  # noqa: BLE001 — a busy, frozen or closing tab: try it next pass
                    sessions.pop(tid, None)
                    continue
                url, text = page["url"], page["text"]
                first_url.setdefault(tid, url)
                jid = job_of.get(tid)
                if not text and tid not in changed:
                    continue
                hit = CONFIRM.search(text)
                if hit or CONFIRM_URL.search(url):
                    if not jid:     # a tab opened another way: the most specific job URL it sits under
                        hk = apply_agent._urlkey(url)
                        jid = (max(((len(k), j) for j, ks in keys.items() for k in ks if hk.startswith(k)), default=(0, None)))[1]
                    if jid:
                        _submitted(jid, "page", hit.group(0) if hit else url)
                elif jid and url != first_url[tid] and (tid, url) not in judged:
                    judged.add((tid, url))  # the job's tab moved to a page no phrase knows: one cheap read
                    try:
                        v = _page_says_sent(f"Page URL: {url}\n\n{text}")
                    except Exception as e:  # noqa: BLE001
                        print(f"watcher: page read failed: {type(e).__name__}: {e}", flush=True)
                        v = {"sent": False}
                    if v.get("sent"):
                        _submitted(jid, "haiku", v.get("quote", ""))
                changed.discard(tid)
            if time.time() - last_closed_check > 10:
                last_closed_check = time.time()
                for jid in detect_closed():
                    print(f"tab closed -> asking: {jid}", flush=True)


def detect_closed() -> list[str]:
    """Jobs whose tabs YOU closed after they reached the review window → "Did you submit?" (extra.ask), never a silent
    skip: you answer Submitted or Skipped. Runs after the page reads, so a confirmation seen before you closed it counts."""
    try:
        ver = json.load(urllib.request.urlopen(f"{apply_agent.CDP}/json/version", timeout=2))
    except Exception:  # noqa: BLE001 — Chrome down: nothing to judge
        return []
    browser, open_ids = ver["webSocketDebuggerUrl"].rsplit("/", 1)[-1], {t["id"] for t in _open_tabs()}
    if not open_ids:            # an empty list means a bad read, not that you closed everything
        return []
    done = []
    for j in store.list_jobs():
        p = store.get_extra(j, "parked")
        if not p:
            continue
        if j["status"] not in ("ready-to-submit", "needs-you") or p["browser"] != browser:
            store.set_extra(j["id"], "parked", None)       # handled, or Chrome restarted: stop tracking
        elif not open_ids & set(p["targets"]):
            store.set_extra(j["id"], "parked", None)
            ask(j["id"], "You closed its tab. Did you submit it?")
            _event(j["id"], "tab_closed")
            done.append(j["id"])
    return done


def ask(job_id: str, question: str, evidence: str = "", source: str = "tab") -> None:
    """Put a job in "Did you submit?" (Today and Held): you answer with Submitted or Skipped."""
    store.set_extra(job_id, "ask", {"q": question, "evidence": evidence[:300], "source": source,
                                    "at": datetime.now().isoformat(timespec="seconds")})


def _watch_loop() -> None:
    while True:
        try:
            if store.settings()["auto_detect"] and _chrome_mode() == "visible":
                _watch_session()
        except Exception as e:  # noqa: BLE001 — Chrome restarted or went away: connect again shortly
            print(f"watcher: {type(e).__name__}: {e}", flush=True)
        time.sleep(5)


# ── HTTP ─────────────────────────────────────────────────────────────────────

ART_RE = re.compile(r"^/artifacts/([0-9a-f]{16})\.png$")
JOB = re.compile(r"^[0-9a-f]{16}$")
JOB_ACTS = {"open", "status", "answer", "push", "site", "reveal", "note"}


def dispatch(act: str, arg: str, raw: bytes) -> tuple[int, dict]:
    """Every dashboard action: POST /api/<act>[/<arg>], JSON body (raw PDF bytes for resume). Both servers use it."""
    try:
        if act in JOB_ACTS and not JOB.match(arg):
            raise ValueError("bad job id")
        if act == "resume":                  # raw PDF bytes, not JSON
            return 200, act_resume(arg, raw)
        body = json.loads(raw or b"{}")
        ids = [i for i in body.get("ids", []) if isinstance(i, str) and JOB.match(i)]
        if act == "open":
            out = act_open(arg)
        elif act == "site":
            out = act_site(arg)
        elif act == "reveal":
            out = act_reveal(arg)
        elif act == "status":
            out = act_status(arg, body["status"])
        elif act == "note":
            out = act_note(arg, str(body.get("note", "")), [str(t) for t in body.get("tags", [])])
        elif act == "answer":
            out = act_answer(arg, int(body["idx"]), str(body["answer"]))
        elif act == "push":
            out = act_push(arg, int(body["idx"]))
        elif act == "retry":
            if not ids:
                raise ValueError("no jobs")
            verb = body.get("verb", "Retry")
            out = {"task": run_task(f"{verb} · {len(ids)} job{'s' * (len(ids) != 1)}",
                                    [PY, "fill.py", "--ids", ",".join(ids)], job_id=ids[0] if len(ids) == 1 else "",
                                    agent=True,     # your retries take the reserved Chrome place: no wait behind a batch
                                    env={"JOBPILOT_PRIORITY": "1"} if verb == "Retry" or len(ids) == 1 else None)}
        elif act == "bulk":
            out = act_bulk(ids, str(body.get("do")), str(body.get("tag", "")))
        elif act == "fill":
            n = int(body.get("limit") or 0)
            out = {"task": run_task(f"Fill new jobs{f' (up to {n})' if n else ''}",
                                    [PY, "fill.py"] + (["--limit", str(n)] if n else []), agent=True)}
        elif act == "discover":
            out = {"task": run_task("Find jobs", [PY, "discover.py"], agent=True, kind="discover")}
        elif act == "check":
            out = {"task": run_task("Check postings", [PY, "postings.py"])}
        elif act == "eval":
            out = {"task": run_task("Eval run", [PY, "eval/run_eval.py"], agent=True, kind="eval")}
        elif act == "cancel":
            out = act_cancel(int(arg))
        elif act == "browser":
            out = act_browser(arg)
        elif act == "daily":
            out = act_daily(body)
        elif act == "login":
            out = act_login(arg)
        elif act == "add":
            out = act_add(body)
        elif act == "file":
            if arg not in FILES:
                raise ValueError("not an editable file")
            out = act_file(arg, str(body["text"]))
        elif act == "resume-remove":
            out = act_resume(arg, b"", remove=True)
        elif act == "inbox":
            out = {"task": run_task("Check my inbox", [PY, "inbox.py"], agent=True, kind="inbox")}
        elif act == "gmail-auth":           # opens Google's approval page in your browser (Testing mode: every 7 days)
            out = {"task": run_task("Reconnect Gmail", [PY, "gmail.py", "--auth"])}
        elif act == "detect":           # the watcher reads tabs live; this only settles closed tabs right away
            n = detect_closed()
            out = {"message": "Submissions are detected live, within seconds" + (f"; {len(n)} closed tab(s) asked" if n else "")}
        elif act == "memory":
            out = act_memory(body)
        elif act == "restart":
            out = act_restart()
        else:
            return 404, {"error": "unknown action"}
        return (400 if "error" in out else 200), out
    except (KeyError, ValueError, IndexError, TypeError, OSError) as e:
        return 400, {"error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in (f"localhost:{PORT}", f"127.0.0.1:{PORT}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, UI.read_text(encoding="utf-8").replace("__STATE__", "null").encode(),
                              "text/html; charset=utf-8")
        if path == "/api/state":
            return self._json(state())
        if m := ART_RE.match(path):
            f = ART / f"{m[1]}.png"
            return self._send(200, f.read_bytes(), "image/png") if f.exists() else self._send(404, b"", "text/plain")
        if (m := re.fullmatch(r"/api/task/(\d+)", path)) and int(m[1]) in TASKS:
            return self._json({"task": TASKS[int(m[1])], "log": _tail(TASK_LOGS / f"{m[1]}.log", 400)})
        if m := re.fullmatch(r"/api/log/([0-9a-f]{16})", path):
            return self._json(_timeline(m[1]))
        if (m := re.fullmatch(r"/api/file/(\w+)", path)) and m[1] in FILES:
            return self._json({"text": _read(FILES[m[1]][0]), "name": FILES[m[1]][0].name})
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-JobPilot") != "1":
            return self._send(403, b"forbidden", "text/plain")
        raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 12_000_000))
        act, arg = (self.path.strip("/").split("/") + ["", ""])[1:3]   # /api/<action>[/<arg>]
        code, out = dispatch(act, arg, raw)
        self._json(out, code)

def build(path=store.DB_PATH, out: Path = OUT) -> Path:
    """Read-only snapshot (run.py). Same page; action buttons hidden, data inlined."""
    s = state(live=False)
    out.write_text(UI.read_text(encoding="utf-8").replace("__STATE__", json.dumps(s).replace("</", "<\\/")),
                   encoding="utf-8")
    print(f"dashboard snapshot: {s['funnel']} -> {out}")
    return out


def _show_in_agent_chrome() -> None:
    """Open (or switch to) the dashboard in the agent's Chrome — same profile and window as the form tabs."""
    url = f"http://localhost:{PORT}/"
    if _chrome_mode() == "invisible":
        print("the daily run is using the invisible agent Chrome right now — run ./browser.sh start when it finishes")
        return
    if not apply_agent.cdp_up():
        subprocess.run([str(HERE / "browser.sh"), "start"])          # opens the dashboard as its first page
        return
    tab = next((t for t in _open_tabs() if t["url"].startswith(url)), None)
    if tab:
        urllib.request.urlopen(f"{apply_agent.CDP}/json/activate/{tab['id']}", timeout=3)
        _front_agent_chrome()
    else:
        _open_in_agent(url)


def serve(open_page: bool = True) -> None:
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)   # bound first, so the page can load right away
    except OSError:                                                # the background service already serves it
        print(f"dashboard already running → http://localhost:{PORT}")
        if open_page:
            _show_in_agent_chrome()
        return
    _adopt()
    threading.Thread(target=_watch_loop, daemon=True).start()
    print(f"JobPilot dashboard → http://localhost:{PORT}" + ("  (background service)" if MANAGED else "  (Ctrl+C to stop)"), flush=True)
    if open_page:
        _show_in_agent_chrome()
    srv.serve_forever()


if __name__ == "__main__":
    serve("--no-open" not in sys.argv) if sys.argv[1:2] == ["serve"] else build()
