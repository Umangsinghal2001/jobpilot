"""
ApplyAgent — a FRESH Claude agent per job that fills the application in a real browser
and STOPS before Submit. The human reviews the open tab (and a screenshot) and submits.

  three parts, ONE session (resume=<sdk session id>):
    1. find   : browser — follows the posting to the real form, stops on anything that blocks it (closed, login,
                not eligible), notes the form; its tab is then closed. Chrome slot.
    2. resume : no browser — tailors the resume. Tailor slot (holds no tab, window or Chrome slot).
    3. fill   : browser — reopens the form and fills it. Chrome slot.
  Chrome slots (CHROME_SLOTS) are shared across every fill.py process; tailor slots are per process.
  context : the candidate's files are in artifacts/context/<job>/ and read on demand (Read is limited to that folder);
            past answers come from the recall tool, not the prompt.

  browser : dedicated Chrome over CDP (browser.sh start) — your logins persist there,
            and tabs outlive the agent so the filled form stays open for you.
  tools   : Playwright MCP (mcp__playwright__*) + in-process jobpilot tools
            (greenhouse_questions, report_filled, report_blocked).
  safety  : deterministic PreToolUse guard — the agent must open its own tab first,
            can never close tabs, run JS, press Enter after filling, or click any
            Submit/Send/Finish/Complete-style control. Enforced in code, not the prompt.

    python3 apply_agent.py <job-id-or-company>      # run one job (debug)
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from pydantic import BaseModel

import memory
import readers
import store

HERE = Path(__file__).parent
# the candidate's own files (profile.md, resume/, kits/, …): $JOBPILOT_PROFILE, else the bundled example
ROOT = Path(os.environ.get("JOBPILOT_PROFILE") or HERE / "example-profile").expanduser()
ARTIFACTS = HERE / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)                     # a fresh clone has none yet
RESUMES = ROOT / "resumes"
PROFILE = ROOT / "profile.md"
RESUME_MD = ROOT / "resume.md"
COMMON = ROOT / "kits" / "_common-application-answers.md"
RESUME_DIR = ROOT / "resume"                        # resume.yaml + pool.yaml + versions/: one source, a version per job
WORKING = RESUME_DIR / "working-knowledge.yaml"     # stacks the candidate may claim only as "working knowledge of X"
PROMPTS = HERE / "prompts"                          # the agent's instructions, as plain text the candidate edits
CONTEXT = ARTIFACTS / "context"                     # per job: the files the agent may Read (and nothing else)
JD = ARTIFACTS / "jd"                               # saved postings (jds.py)
VOICE = ROOT / "voice"                              # optional writing guide (SKILL.md + references/corrections.md)

CDP = "http://localhost:9222"
PLAYWRIGHT_MCP = "@playwright/mcp@0.0.82"   # pinned: verified tool schemas (target=, paths=)
QUICKFILL = HERE / "quickfill" / "quickfill.mjs"   # bulk filler, same playwright-core as the MCP
CDP_PROXY = HERE / "quickfill" / "cdp-proxy.mjs"   # agent tabs open in background windows; never steal your focus
HELPER = HERE / "chrome-helper"                     # tiny extension: chrome.tabs.move, which CDP itself doesn't have
APP = "http://localhost:8765"                       # the window holding the app's tab is your review window
MODEL = "opus"
MAX_TURNS = 120
CHROME_SLOTS = 3   # agents in the shared Chrome at once, across EVERY fill.py process (lock files in artifacts/locks)
PRIORITY = os.environ.get("JOBPILOT_PRIORITY") == "1"   # the candidate's own retries: may also take one reserved place (a 4th)
TRACKS = ["ai_engineer", "senior_ai_engineer", "lead_ai_engineer",
          "fde_founding", "data_scientist", "ai_pm"]


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# the candidate's real job titles (profile.md "- Job titles: A · B"): a version may only pick one
_m = re.search(r"^- Job titles:\s*(.+)$", _read(PROFILE), re.M)
TITLES = tuple(t.strip() for t in (_m.group(1) if _m else "").split("·") if t.strip())


def cdp_up() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


@contextlib.asynccontextmanager
async def chrome_slot():
    """One of CHROME_SLOTS browser places, shared by every process (flock: released even if the process dies).
    A retry the candidate clicked (PRIORITY) tries the reserved place first, so it never queues behind a batch."""
    d = ARTIFACTS / "locks"
    d.mkdir(parents=True, exist_ok=True)
    names = (["chrome-priority"] if PRIORITY else []) + [f"chrome-{i}" for i in range(CHROME_SLOTS)]
    while True:
        for name in names:
            f = open(d / f"{name}.lock", "w")  # noqa: SIM115 — held for the whole part
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.close()
                continue
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
                f.close()
            return
        await asyncio.sleep(1)


def _answers(ws_url: str, wait: float) -> bool:
    """Does this tab's page run a trivial script within `wait` seconds? (A frozen page, e.g. one stuck behind a
    dialog, never does, and every Playwright call then waits on it until it times out.)"""
    from websockets.sync.client import connect
    try:
        with connect(ws_url, max_size=None, open_timeout=wait) as ws:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "1", "returnByValue": True}}))
            end = time.time() + wait
            while time.time() < end:
                if json.loads(ws.recv(timeout=max(0.1, end - time.time()))).get("id") == 1:
                    return True
    except Exception:  # noqa: BLE001 — no answer in time is the only thing we care about
        pass
    return False


def close_frozen_tabs() -> list[str]:
    """Before a browser part: close tabs that don't answer (3 s, then 5 s more). A tab holding a filled form that
    waits for the candidate's review is never closed: the part stops with an error naming it instead.
    ponytail: a page busy for 8 s straight would be taken for frozen; none seen so far."""
    try:
        tabs = [t for t in json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=3))
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl") and not t["url"].startswith((APP, "chrome"))]
    except Exception:  # noqa: BLE001
        return []
    frozen = [t for t in tabs if not _answers(t["webSocketDebuggerUrl"], 3) and not _answers(t["webSocketDebuggerUrl"], 5)]
    if not frozen:
        return []
    kept = {tid: j for j in store.list_jobs() if j["status"] in ("ready-to-submit", "needs-you")
            for tid in (store.get_extra(j, "parked") or {}).get("targets", [])}
    mine = [t for t in frozen if t["id"] in kept]
    if mine:
        raise RuntimeError("frozen tab with a filled form for you to review: " + ", ".join(
            f"{kept[t['id']]['company']} ({t['url'][:80]})" for t in mine) + " — reload or close it, then Retry")
    for t in frozen:
        try:
            urllib.request.urlopen(f"{CDP}/json/close/{t['id']}", timeout=3)
        except Exception:  # noqa: BLE001
            pass
    return [t["url"] for t in frozen]


def page_ids() -> set[str]:
    try:
        return {t["id"] for t in json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=3)) if t.get("type") == "page"}
    except Exception:  # noqa: BLE001
        return set()


def close_part_tabs(before: set[str], keys: set[str]) -> int:
    """After Part 1: close the tabs this agent opened (new since `before`, on a page it visited). Nothing was
    filled, so nothing is lost, and the job holds no tab while its resume is built."""
    try:
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=3))
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for t in tabs:
        if t.get("type") == "page" and t["id"] not in before and any(_urlkey(t.get("url", "")).startswith(k) for k in keys):
            try:
                urllib.request.urlopen(f"{CDP}/json/close/{t['id']}", timeout=3)
                n += 1
            except Exception:  # noqa: BLE001
                pass
    return n


def close_stale_tabs(url: str) -> int:
    """Close agent-browser tabs left by an EARLIER run of this same job (a re-run supersedes them),
    so the new agent's tab is the only one fill_fields can match."""
    try:
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=3))
    except Exception:  # noqa: BLE001
        return 0
    k, n = _urlkey(url), 0
    for t in tabs:
        if t.get("type") == "page" and _urlkey(t.get("url", "")).startswith(k):
            try:
                urllib.request.urlopen(f"{CDP}/json/close/{t['id']}", timeout=3)
                n += 1
            except Exception:  # noqa: BLE001
                pass
    return n


def load_helper() -> str:
    """Load chrome-helper into the agent Chrome (loading over CDP lasts until Chrome quits, so browser.sh calls
    this at every start). Returns the extension id, which is the same every time (derived from the path)."""
    from websockets.sync.client import connect
    ver = json.load(urllib.request.urlopen(f"{CDP}/json/version", timeout=3))
    with connect(ver["webSocketDebuggerUrl"], max_size=None, open_timeout=5) as ws:
        ws.send(json.dumps({"id": 1, "method": "Extensions.loadUnpacked", "params": {"path": str(HELPER)}}))
        while True:
            m = json.loads(ws.recv(timeout=15))
            if m.get("id") == 1:
                if "error" in m:
                    raise RuntimeError(m["error"].get("message"))
                return m["result"]["id"]


def park(keys: set[str], close: bool = False, job_id: str = "") -> str:
    """A job is done: move every window its agent opened into your review window (the one with the app's
    tab) as background tabs — no focus change, the page isn't reloaded, and Chrome lets background tabs
    sleep. close=True (closed postings) closes them instead. keys = _urlkey()s of the pages the run visited."""
    from websockets.sync.client import connect
    ver = json.load(urllib.request.urlopen(f"{CDP}/json/version", timeout=3))
    with connect(ver["webSocketDebuggerUrl"], max_size=None, open_timeout=5) as ws:
        n = 0

        def call(method: str, params: dict | None = None, session: str | None = None) -> dict:
            nonlocal n
            n += 1
            ws.send(json.dumps({"id": n, "method": method, "params": params or {},
                                **({"sessionId": session} if session else {})}))
            while True:
                m = json.loads(ws.recv(timeout=15))
                if m.get("id") == n:
                    if "error" in m:
                        raise RuntimeError(f"{method}: {m['error'].get('message')}")
                    return m["result"]

        def window(tid: str) -> int:
            return call("Browser.getWindowForTarget", {"targetId": tid})["windowId"]

        pages = [t for t in call("Target.getTargets")["targetInfos"] if t["type"] == "page"]
        mine = [t for t in pages if not t["url"].startswith(APP) and any(_urlkey(t["url"]).startswith(k) for k in keys)]
        if not mine:
            return "no tabs left"
        if close:
            for t in mine:
                call("Target.closeTarget", {"targetId": t["targetId"]})
            return f"closed {len(mine)} tab(s)"
        app = next((t for t in pages if t["url"].startswith(APP)), None)
        review = window(app["targetId"] if app else call("Target.createTarget", {
            "url": APP + "/", "newWindow": True, "background": True, "focus": False})["targetId"])
        # remember which tabs went to your review window: if YOU close them all, the job is skipped
        # (dashboard.detect_closed). `browser` is this Chrome launch — a restart isn't you closing tabs.
        if job_id:
            store.set_extra(job_id, "parked", {"targets": [t["targetId"] for t in mine],
                                               "browser": ver["webSocketDebuggerUrl"].rsplit("/", 1)[-1]})
        wins = sorted({window(t["targetId"]) for t in mine} - {review})
        if not wins:
            return "already in the review window"
        ext = call("Extensions.loadUnpacked", {"path": str(HELPER)})["id"]   # same id every time (from the path)
        tid = call("Target.createTarget", {"url": f"chrome-extension://{ext}/blank.html", "hidden": True})["targetId"]
        try:
            s = call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
            # every tab of the agent's windows (its listing + the form it went on to); the emptied windows close
            js = f"""(async () => {{
                for (let i = 0; i < 50 && !(globalThis.chrome && chrome.tabs); i++) await new Promise(r => setTimeout(r, 100));
                const ids = (await chrome.tabs.query({{}})).filter(t => {json.dumps(wins)}.includes(t.windowId)).map(t => t.id);
                if (ids.length) await chrome.tabs.move(ids, {{windowId: {review}, index: -1}});
                return ids.length }})()"""
            why = ""
            for _ in range(20):     # the hidden page may still be loading (about:blank, or mid-navigation)
                try:
                    r = call("Runtime.evaluate", {"expression": js, "awaitPromise": True, "returnByValue": True}, s)
                except RuntimeError as e:
                    r = {"exceptionDetails": {"text": str(e)}}
                if "exceptionDetails" not in r:
                    return f"moved {r['result']['value']} tab(s) to the review window"
                why = json.dumps(r["exceptionDetails"])[:200]
                time.sleep(0.2)
            raise RuntimeError(why)
        finally:
            call("Target.closeTarget", {"targetId": tid})


def voice_guide() -> str:
    """The candidate's writing guide (frontmatter stripped) plus logged corrections, if any. Optional."""
    guide = re.sub(r"\A---.*?---\s*", "", _read(VOICE / "SKILL.md"), flags=re.S)
    fixes = _read(VOICE / "references" / "corrections.md")
    if "**Before:**" in fixes:
        guide += "\n\n# Corrections (newest first; these outrank every rule above)\n" + fixes
    return guide or "(No writing guide given: write plainly, in the first person, with no buzzwords.)\n"


def make_context(job: dict) -> Path:
    """artifacts/context/<job>/: everything the agent may Read for this job, fresh each run."""
    d = CONTEXT / job["id"]
    d.mkdir(parents=True, exist_ok=True)
    posting = _read(JD / f"{job['id']}.txt")
    if not posting and job["url"].startswith("http"):
        try:                                        # no saved posting yet: try the ATS's public API (no browser)
            import jds
            text, how = jds.from_api(job["url"])
            if len(text) >= jds.MIN_CHARS:
                jds._save(job, text, f"api:{how}")
                posting = _read(JD / f"{job['id']}.txt")
        except Exception:  # noqa: BLE001 — Part 2 then reads the posting on the page
            pass
    files = {
        "posting.txt": posting or "NO SAVED POSTING for this job. Read it on the page in Part 2.\n",
        "profile.md": _read(PROFILE),
        "standard-answers.md": _read(COMMON),
        "resume.yaml": _read(RESUME_DIR / "resume.yaml"),
        "pool.yaml": _read(RESUME_DIR / "pool.yaml"),
        "working-knowledge.yaml": _read(WORKING),
        "writing-guide.md": voice_guide(),
        "platform-notes.md": memory.platform_notes() or "(none yet)\n",
        "resume.md": _read(RESUME_MD),
    }
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def _length(lay: list[dict]) -> str:
    """What the agent needs to fit 2 pages: how full each page is, and what spilled onto the last one."""
    fill = ", ".join(f"page {p['page']} {round(p['reaches'] * 100)}% full ({p['lines']} lines)" for p in lay)
    last = lay[-1] if lay else None
    over = f" Page {last['page']} starts with: " + " / ".join(last["first"][:6]) if last and last["page"] > 2 else ""
    return fill + "." + over


def left_out(v: dict, base: dict) -> list[str]:
    """What a version drops from the candidate's base resume: skills, projects, base bullets, section lines."""
    out = []
    if "skills" in v:
        have = " ".join(str(g.get("items", "")) for g in v.get("skills") or [] if isinstance(g, dict)).lower()
        gone = [x.strip() for g in base.get("skills") or [] for x in re.split(r",(?![^(]*\))", str(g.get("items", "")))
                if x.strip() and re.sub(r"\(.*?\)", "", x).strip().lower() not in have]   # "MongoDB (NoSQL)" ~ "MongoDB"
        if gone:
            out.append(f"{len(gone)} skills ({', '.join(gone[:15])}{' …' if len(gone) > 15 else ''})")
    projects = v.get("projects") or {}
    hidden = [k for k, x in projects.items() if isinstance(x, dict) and x.get("hide")]
    if hidden:
        out.append(f"{len(hidden)} projects ({', '.join(hidden)})")
    sizes = {p["name"]: len(p.get("bullets") or []) for co in base.get("experience") or [] for ro in co["roles"]
             for p in ro["projects"] if p.get("name")}
    dropped = 0
    for k, x in projects.items():
        if isinstance(x, dict) and not x.get("hide") and isinstance(x.get("bullets"), list) and k in sizes:
            used = {b if isinstance(b, int) else b.get("base") for b in x["bullets"]
                    if isinstance(b, int) or (isinstance(b, dict) and not b.get("hide"))}
            dropped += len(set(range(sizes[k])) - used)
    if dropped:
        out.append(f"{dropped} base bullets")
    for sec in ("achievements", "education", "certificates"):
        if sec in v and len(v[sec] or []) < len(base.get(sec) or []):
            out.append(f"{len(base.get(sec) or []) - len(v[sec] or [])} {sec} lines")
    return out


def build_version(job: dict, version: str, text: str) -> dict | str:
    """Write versions/<version>.yaml, build it with resume/build.py, and run its parser checks.
    The checked PDF's details, or the problem for the agent to fix."""
    import sys
    import yaml
    if str(HERE / "resume") not in sys.path:
        sys.path.insert(0, str(HERE / "resume"))
    import build as rb
    try:
        if not isinstance(yaml.safe_load(text), dict):
            return "the version must be a YAML mapping (title:, profile:, lead:, projects:, skills: …)"
    except yaml.YAMLError as e:
        return f"not valid YAML: {str(e)[:300]}"
    extra = set(yaml.safe_load(text)) - {"title", "profile", "skills", "projects", "lead", "achievements",
                                         "education", "certificates", "left_out"}
    if extra:          # name, contact and experience (dates, employers, role titles) are facts: never in a version
        return f"a version can't change {sorted(extra)}"
    title = yaml.safe_load(text).get("title")
    if title is not None and title not in TITLES:
        return f"title must be exactly one of {list(TITLES)} (the candidate's real job title), not {title!r}"
    vf = RESUME_DIR / "versions" / f"{version}.yaml"
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(text, encoding="utf-8")
    try:
        r = rb.load(version)
    except SystemExit as e:                      # a project name that isn't in resume.yaml
        return str(e)
    except IndexError:
        return "a bullet number is past the end of that project's list in resume.yaml (numbers start at 0)"
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        return f"the version doesn't fit the format: {type(e).__name__}: {e}"
    out = ARTIFACTS / "resumes" / job["id"]
    out.mkdir(parents=True, exist_ok=True)
    pdf = out / f"{r['name'].replace(' ', '_')}_Resume.pdf"   # the file name a recruiter sees
    rb.build(r, out / "resume.html", pdf)
    c = rb.check(pdf, out / "resume.parsed.txt", rb.source_words(r))
    length = _length(rb.layout(pdf))
    problems = ([f"{c['pages']} pages, the limit is 2. {length} Make the smallest cut that fits (Rule E; "
                 "resume_pages shows the pages), then call tailor_resume again"] if c["pages"] > 2 else []) + \
        ([f"review notes leaked into the PDF: {rb.leaked(r, (out / 'resume.parsed.txt').read_text())[:2]}"]
         if rb.leaked(r, (out / "resume.parsed.txt").read_text()) else []) + \
        ([f"words a parser can't read back: {', '.join(c['lost'][:8])}"] if c["lost"] else []) + \
        (["bullet marks detached from their lines"] if c["lone_bullets"] else []) + \
        (["the resume fonts didn't load"] if not c["fonts_ok"] else []) + \
        ([f"only {c['links']} of {len(rb.urls(r))} links made it in"] if c["links"] < len(rb.urls(r)) else [])
    if problems:
        return "checks failed: " + "; ".join(problems)
    lay = rb.layout(pdf)
    gone = left_out(yaml.safe_load(text), yaml.safe_load(_read(RESUME_DIR / "resume.yaml")))
    if gone and lay and lay[-1]["reaches"] < 0.9:      # room left, yet things are missing: tell the agent
        length += (f" But this version leaves out {'; '.join(gone)} from the candidate's resume, and page {lay[-1]['page']} still "
                   "has room. Put back what fits (start from the candidate's whole resume; cut only to fit 2 pages), then build again.")
    return {"pdf": str(pdf), "version": str(vf.relative_to(ROOT)), "pages": c["pages"], "words": c["words"], "length": length}


def master_resume() -> Path | None:
    m = re.search(r"Master resume:\s*(\S+\.pdf)", _read(PROFILE))
    p = ROOT / Path(m.group(1)).expanduser() if m else None   # relative paths are inside the profile folder
    return p if p and p.exists() else None


def resume_options() -> dict[str, str]:
    """track -> absolute PDF path. Tracks without an exported PDF fall back to the master."""
    master = master_resume()
    out = {}
    for t in TRACKS:
        p = RESUMES / f"{t}.pdf"
        out[t] = str(p) if p.exists() else (str(master) if master else "")
    return out


# ── terminal report schemas ────────────────────────────────────────────────────

class Field(BaseModel):
    label: str
    answer: str
    confidence: Literal["sure", "guess"]
    note: str        # why it's a guess ("" when sure)
    reusable: bool   # generic question any company could ask (not "Why us?" essays) -> goes to memory


class FilledReport(BaseModel):
    ats: str
    track: str
    resume_uploaded: bool
    resume_path: str
    tab_url: str
    screenshot: str
    fields: list[Field]
    unfilled_required: list[str]
    steps_seen: int
    lessons: list[str]   # 0-5 platform tips that would have saved tool calls -> platform memory


class BlockedReport(BaseModel):
    kind: Literal["login", "captcha", "account_required", "email_apply",
                  "closed", "not_found", "not_eligible", "other"]
    reason: str
    apply_url: str   # "" if none
    email: str       # "" unless kind == email_apply
    screenshot: str  # "" if none


class FillItem(BaseModel):
    label: str
    kind: Literal["text", "combobox", "select", "checkbox", "radio", "file"]
    value: str


class FillRequest(BaseModel):
    url: str               # the current tab's Page URL (from the snapshot)
    fields: list[FillItem]


def _resp_text(resp: Any) -> str:
    """Tool responses arrive as str | {content:[{text}]} | [{text}] — flatten to the real text
    (json.dumps would escape newlines and glue them onto URLs)."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        return "\n".join(_resp_text(v) for k, v in resp.items() if k in ("content", "text", "result")) or json.dumps(resp, default=str)
    if isinstance(resp, list):
        return "\n".join(_resp_text(x) for x in resp)
    return str(resp)


def _urlkey(u: str) -> str:
    """host+path — the same tab identity quickfill.mjs matches on (query/fragment ignored)."""
    from urllib.parse import urlsplit
    x = urlsplit(u.strip())
    return (x.netloc + x.path).rstrip("/").lower()


def _text(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


def _tight(model: type[BaseModel]) -> dict:
    """All top-level props required + no extras, so a malformed report is rejected
    by MCP validation and the model retries."""
    s = model.model_json_schema()
    s["required"] = list(s.get("properties", {}))
    s["additionalProperties"] = False
    return s


# ── the submit guard (deterministic safety) ────────────────────────────────────

SUBMIT_RE = re.compile(
    r"\b(submit|send)\b"                       # Submit application / Send / Submit
    r"|\bfinish\b|\bcomplete (my |your |the )?application\b"
    r"|\bconfirm (and|&) (apply|submit)\b",
    re.I,
)
APPLY_RE = re.compile(r"^\W*(easy |quick )?apply\b", re.I)   # "Apply", "Apply now", "Easy Apply"
FILL_TOOLS = {"browser_type", "browser_fill_form", "browser_select_option", "browser_file_upload", "fill_fields"}


class Guard:
    """Per-run state + PreToolUse/PostToolUse hooks. One instance per job."""

    def __init__(self, job_id: str, url: str, uploads: set[str] = frozenset(), context: Path | None = None):
        self.url = url
        self.context = context        # the only folder Read may open
        self.uploads = set(uploads)   # the ONLY local files the agent may put into a web form
        self.urls: set[str] = set()   # pages this run has visited — fill_fields may only target these
        self.recipe: dict[str, dict] = {}   # norm(label) -> what fill_fields put there (for refill.py)
        self.log = ARTIFACTS / f"{job_id}.log"
        self.log.write_text("")
        self.tab_ready = False
        self.filled = 0
        self.denials = 0
        self.snapshot = ""      # text of the latest tool response that contained [ref=...]

    def _write(self, **rec) -> None:
        rec["t"] = round(time.time(), 1)
        line = json.dumps(rec, default=str)
        if len(line) > 4000:   # shorten the long values, never cut the JSON (a cut line can't be parsed)
            rec = {k: (v if len(json.dumps(v, default=str)) < 600 else str(v)[:600] + "…") for k, v in rec.items()}
            line = json.dumps(rec, default=str)
        with self.log.open("a") as f:
            f.write(line + "\n")

    def _label_for(self, target: str) -> str:
        """Resolve a snapshot ref (e.g. e12) to its line text, for submit-word matching."""
        if not target or not self.snapshot:
            return ""
        m = re.search(r"^\s*-\s*(.*?)\s*\[ref=" + re.escape(target) + r"\]", self.snapshot, re.M)
        return m.group(1) if m else ""

    def _deny(self, tool: str, why: str) -> dict:
        self.denials += 1
        self._write(ev="DENY", tool=tool, why=why)
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": why,
        }}

    async def pre(self, inp: dict[str, Any], tool_use_id: str | None, ctx: Any) -> dict:
        name = inp.get("tool_name", "")
        tool = name.rsplit("__", 1)[-1]
        a = inp.get("tool_input") or {}

        if name == "Read":
            p = Path(str(a.get("file_path", ""))).expanduser().resolve()
            if not self.context or not p.is_relative_to(self.context.resolve()):
                return self._deny(tool, f"Read only the files in {self.context}.")
            return {}
        if tool in ("browser_evaluate", "browser_run_code_unsafe", "browser_close"):
            return self._deny(tool, f"{tool} is not permitted in this run.")

        # a data:/javascript:/blob: page is just browser_evaluate by another name (seen: the agent wrote JS into
        # data: URLs to solve a coding puzzle on Close's form) — only real web pages may be opened
        if tool in ("browser_navigate", "browser_tabs") and a.get("url") and \
                not re.match(r"https?://", str(a["url"]).strip(), re.I):
            return self._deny(tool, "Only http(s) pages may be opened. Never run code — if a question needs a "
                                    "coding puzzle or computation, leave it empty and list it in unfilled_required.")

        if tool == "browser_tabs":
            act = a.get("action")
            if act == "close":
                return self._deny(tool, "Never close tabs — the human reviews them.")
            if act in ("new", "select"):
                self.tab_ready = True
            return {}
        if not self.tab_ready:
            return self._deny(tool, f'First open your own tab: browser_tabs {{"action":"new","url":"{self.url}"}}. '
                                    "Do not act in any other tab.")

        if tool == "browser_file_upload" and any(x not in self.uploads for x in a.get("paths") or []):
            return self._deny(tool, "Only the candidate's resume PDFs listed in the instructions may be uploaded.")
        if tool == "browser_type" and a.get("submit"):
            return self._deny(tool, "Do not press Enter/submit while typing. Type without submit=true.")
        if tool == "browser_press_key" and str(a.get("key", "")).lower() == "enter" and self.filled:
            return self._deny(tool, "Enter can submit the form — not allowed once fields are filled.")
        if tool == "browser_click":
            text = f"{a.get('element', '')} | {self._label_for(str(a.get('target', '')))}"
            if SUBMIT_RE.search(text) or (self.filled and APPLY_RE.search(text.split("|")[0].strip() or text.split("|")[-1].strip())):
                return self._deny(tool, "Final submission is reserved for the human. Do NOT click it. "
                                        "Take the full-page screenshot and call report_filled now.")
        return {}

    async def post(self, inp: dict[str, Any], tool_use_id: str | None, ctx: Any) -> dict:
        name = inp.get("tool_name", "")
        tool = name.rsplit("__", 1)[-1]
        resp = inp.get("tool_response")
        text = _resp_text(resp)
        if "[ref=" in text:
            self.snapshot = text
        self.urls.update(_urlkey(u) for u in re.findall(r"Page URL: (\S+)", text))
        if tool in FILL_TOOLS:
            self.filled += 1
        self._write(ev="ok", tool=tool, input=inp.get("tool_input"),
                    **({"out": text[:800]} if name.startswith("mcp__jobpilot__") else {}))
        return {}


# ── system prompt: how a careful human applies ────────────────────────────────

def _system(job: dict, shot: Path, resumes: dict[str, str]) -> str:
    """The agent's instructions: prompts/apply.py then prompts/tailor_resume.py (the candidate's to edit), filled in for this job.
    Only instructions and rules: the candidate's files are read on demand from the context folder."""
    import runpy
    values = {
        "job_url": job["url"],
        "base_resumes": "\n".join(f"  - {t}: {p or '(none available)'}" for t, p in resumes.items()),
        "screenshot_path": str(shot),
        "titles": " or ".join(repr(t) for t in TITLES),
        "context_dir": str(CONTEXT / job["id"]),
    }
    out = []
    for name, var in (("apply", "APPLY"), ("tailor_resume", "TAILOR_RESUME")):
        try:                                                              # read fresh: an edit applies to the next run
            text = runpy.run_path(str(PROMPTS / f"{name}.py"))[var]
        except Exception as e:  # noqa: BLE001 — a broken edit: say where, don't run on a half prompt
            raise SystemExit(f"prompts/{name}.py can't be read ({type(e).__name__}: {e}). Keep the text inside {var} = r\"\"\" … \"\"\"")
        unknown = set(re.findall(r"\{\{(\w+)\}\}", text)) - set(values)
        if unknown:
            raise SystemExit(f"prompts/{name}.py: unknown {{{{...}}}} name(s) {sorted(unknown)}; known: {sorted(values)}")
        out.append(re.sub(r"\{\{(\w+)\}\}", lambda m: values[m[1]], text.removesuffix("\n")))   # the newline before the closing quotes
    return "\n".join(out) + "\n"


# ── the recipe: everything needed to re-fill this exact form without the agent ──

def _same(a: str, b: str) -> bool:
    """Same question? Equal, or one contains the other when the shorter is ≥6 chars
    ("country" ~ "country (phone)", but "name" !~ "first name")."""
    return a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a))


def build_recipe(job: dict, filled: dict[str, dict], report: dict) -> dict:
    """fill_fields items (real labels + kinds, proven on this form) + the agent's final answers for
    anything it fixed or filled by hand (kind=auto). Values are exact — including job-specific essays."""
    answers = {memory.norm(f["label"]): f for f in report.get("fields") or []}
    guesses = [a for a, f in answers.items() if f.get("confidence") == "guess"]   # never replayed: the candidate must confirm them
    answers = {a: f for a, f in answers.items() if a not in guesses}
    items = {k: dict(v) for k, v in filled.items() if not any(_same(k, g) for g in guesses)}
    for k, it in items.items():                       # fixed by hand after fill_fields → final answer
        if not it["ok"] and it["kind"] != "file":
            f = next((f for a, f in answers.items() if _same(a, k)), None)
            if f:
                it["value"] = f["answer"]
    url = next(reversed([it["url"] for it in items.values()]), report.get("tab_url") or job["url"])
    for k, f in answers.items():                      # filled only by hand → replay by label, kind=auto
        a = (f.get("answer") or "").strip()
        if a and not any(_same(k, i) for i in items) and not memory.NOT_A_VALUE.search(a) \
                and not re.search(r"\b(resume|cv|cover letter)\b", f["label"], re.I):
            items[k] = {"label": f["label"].rstrip("*").strip(), "kind": "auto", "value": a, "url": url, "ok": None}
    if report.get("resume_uploaded") and report.get("resume_path") and not any(i["kind"] == "file" for i in items.values()):
        items["resume"] = {"label": "Resume", "kind": "file", "value": report["resume_path"], "url": url, "ok": None}
    return {"job_id": job["id"], "company": job["company"], "title": job["title"],
            "created": time.strftime("%Y-%m-%d %H:%M"), "fields": list(items.values())}


# ── the run ────────────────────────────────────────────────────────────────────

async def apply(job: dict, tailor_slot: asyncio.Semaphore | None = None) -> dict:
    """Run one ApplyAgent in one session: find the form (Chrome slot), tailor the resume (tailor slot), fill the
    form (Chrome slot). Returns {kind: filled|blocked|error, report, cost_usd, turns, denials, filled_actions,
    log, session_id, phases, form}."""
    ARTIFACTS.mkdir(exist_ok=True)
    if job["url"].startswith("mailto:"):        # e.g. HN "Who's Hiring" posts: nothing to fill, no agent needed
        email = job["url"][7:].split("?")[0]
        return {"kind": "blocked", "cost_usd": 0.0, "turns": 0, "denials": 0, "filled_actions": 0, "log": "",
                "report": {"kind": "email_apply", "reason": f"apply by email to {email}", "apply_url": "",
                           "email": email, "screenshot": ""}}
    return await _apply(job, tailor_slot or asyncio.Semaphore(1))


async def _part(prompt: str, opts: ClaudeAgentOptions, sink: dict, run: dict) -> str:
    """One query in the job's session. Adds cost/turns to run, keeps the SDK's session id; returns an error or ""."""
    err = ""
    try:
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                run["session"] = msg.data.get("session_id") or run["session"]
                bad = [s for s in msg.data.get("mcp_servers", [])
                       if s.get("name") in ("playwright", "jobpilot") and s.get("status") in ("failed", "needs-auth")]
                if bad:
                    return f"MCP server failed to start: {bad}"
            for b in getattr(msg, "content", None) or []:
                if isinstance(b, ToolUseBlock):
                    n = b.name.rsplit("__", 1)[-1]
                    if n == "report_filled":
                        sink.setdefault("filled", b.input)
                    elif n == "report_blocked":
                        sink.setdefault("blocked", b.input)
            if isinstance(msg, ResultMessage):
                run["cost"] = max(run["cost"], msg.total_cost_usd or 0.0)   # a resumed session reports its running total
                run["turns"] += msg.num_turns or 0
                run["session"] = msg.session_id or run["session"]
                if msg.is_error:
                    err = f"{msg.subtype}: {msg.result or ''}"[:300]
    except Exception as e:  # noqa: BLE001 — CLI exits non-zero after error results
        err = err or f"{type(e).__name__}: {e}"[:300]
    return err


async def _apply(job: dict, tailor_slot: asyncio.Semaphore) -> dict:
    shot = ARTIFACTS / f"{job['id']}.png"
    resumes = resume_options()
    context = make_context(job)
    guard = Guard(job["id"], job["url"], {p for p in resumes.values() if p}, context)
    sink: dict[str, dict] = {}
    run = {"cdp": CDP, "session": None, "cost": 0.0, "turns": 0}

    @tool("report_filled", "Call ONCE when the whole form is filled and you're on the final "
          "review screen (after the full-page screenshot).", _tight(FilledReport))
    async def report_filled(args: dict[str, Any]) -> dict[str, Any]:
        sink["filled"] = args
        return {"content": [{"type": "text", "text": "Report received. You are done — stop now."}]}

    @tool("report_blocked", "Call ONCE if you cannot fill this application (login wall, "
          "CAPTCHA, account required, email-only, closed/not found).", _tight(BlockedReport))
    async def report_blocked(args: dict[str, Any]) -> dict[str, Any]:
        sink["blocked"] = args
        return {"content": [{"type": "text", "text": "Report received. You are done — stop now."}]}

    @tool("form_found", "Part 1 only: call ONCE when you've reached the application form and nothing blocks it. "
          "Fill nothing.", {"type": "object", "properties": {
              "url": {"type": "string", "description": "the form's Page URL"},
              "ats": {"type": "string"},
              "route": {"type": "string", "description": "how you got there, if the URL alone won't reopen it"},
              "questions": {"type": "array", "items": {"type": "string"}, "description": "the visible questions"},
              "for_the_resume": {"type": "string", "description": "anything on the form or page the resume should answer"},
              "resume_upload": {"type": "string", "enum": ["yes", "no", "unsure"],
                                "description": "does this form take a resume file? no = it sends the resume saved on the candidate's profile"}},
              "required": ["url", "ats", "route", "questions", "for_the_resume", "resume_upload"]})
    async def form_found(args: dict[str, Any]) -> dict[str, Any]:
        sink["form"] = args
        return _text("Noted. Part 1 is done — stop now.")

    version = f"agent/{re.sub(r'[^a-z0-9]+', '-', job['company'].lower()).strip('-')[:30]}-{job['id'][:6]}"

    @tool("tailor_resume", "Build this job's tailored resume from a version file (YAML over resume.yaml), and "
          "check it the way a resume parser reads it. Returns the PDF path to upload, or the problem to fix.",
          {"version_yaml": str})
    async def tailor_resume(args: dict[str, Any]) -> dict[str, Any]:
        res = await asyncio.to_thread(build_version, job, version, str(args.get("version_yaml", "")))
        if isinstance(res, str):
            return _text(f"tailor_resume: {res}. Fix the version and call tailor_resume again.")
        guard.uploads.add(res["pdf"])            # the only new file the agent may upload
        sink["resume_version"] = res
        return _text(f"Built and checked ({res['pages']} pages, every word and link reads back). {res['length']} "
                     f"Upload this file: {res['pdf']}")

    @tool("resume_pages", "See the resume as built by your last tailor_resume call: one image per page, "
          "so you can judge what to cut or add back.", {})
    async def resume_pages(args: dict[str, Any]) -> dict[str, Any]:
        pdf = next((ARTIFACTS / "resumes" / job["id"]).glob("*_Resume.pdf"), None)
        if not pdf:
            return _text("No resume built yet: call tailor_resume first.")
        import base64
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            await asyncio.to_thread(subprocess.run, ["pdftoppm", "-r", "80", "-png", str(pdf), f"{tmp}/p"], check=True)
            pages = sorted(Path(tmp).glob("p-*.png"))
            imgs = [{"type": "image", "data": base64.b64encode(f.read_bytes()).decode(), "mimeType": "image/png"} for f in pages]
        return {"content": [{"type": "text", "text": f"{len(imgs)} page(s), as last built:"}] + imgs}

    @tool("recall", "The candidate's answers to similar questions on earlier applications. Pass EVERY question on the "
          "current form page in one call. Matches are suggestions: judge whether each fits this job.",
          {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
           "required": ["questions"]})
    async def recall(args: dict[str, Any]) -> dict[str, Any]:
        return _text(memory.recall([str(q) for q in args.get("questions") or []][:60]))

    @tool("greenhouse_questions", "For Greenhouse job URLs only: the exact application questions "
          "(labels, required, options) and the full job description.", {"url": str})
    async def greenhouse_questions(args: dict[str, Any]) -> dict[str, Any]:
        r = await asyncio.to_thread(readers.read_greenhouse, args.get("url", ""))
        text = json.dumps(r, ensure_ascii=False)[:20000] if r else "Not a Greenhouse job URL."
        return {"content": [{"type": "text", "text": text}]}

    async def _quickfill(url: str, fields: list[dict]) -> dict | str:
        """Fill fields on the agent's current page via quickfill.mjs (never clicks buttons). The result
        dict, or an error text for the agent. Every value placed is remembered for the recipe."""
        if not guard.tab_ready:
            return "Open your own tab first (browser_tabs new)."
        if _urlkey(url) not in guard.urls:
            return f"url must be the Page URL of your current tab. Seen this run: {sorted(guard.urls)}"
        payload = json.dumps({"url": url, "fields": fields, "files_allowed": sorted(guard.uploads)})
        proc = await asyncio.create_subprocess_exec(
            "node", str(QUICKFILL), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={**os.environ, "CDP": run["cdp"]})
        try:
            out, err = await asyncio.wait_for(proc.communicate(payload.encode()), timeout=180)
        except TimeoutError:
            proc.kill()
            return "timed out — fall back to manual filling for the rest."
        try:
            res = json.loads(out.decode() or "{}")
        except ValueError:
            return f"failed: {err.decode()[-400:]}"
        if res.get("error"):
            return f"{res['error']}. Open tabs: {res.get('tabs')}"
        sent = {f["label"]: f for f in fields}
        for r in res["results"]:                       # remember it for refill.py (last write wins)
            if r["label"] in sent:
                f = sent[r["label"]]
                guard.recipe[memory.norm(f["label"])] = {"label": f["label"], "kind": f["kind"],
                                                          "value": f["value"], "url": url, "ok": r["status"] == "ok"}
        return res

    @tool("fill_fields", "Fill MANY fields of the current page in ONE call, by visible label: text, "
          "combobox (custom or native dropdowns), select, checkbox, radio, file (resume). Never clicks "
          "buttons or submits. Returns per-field status; re-snapshot afterwards (refs go stale).",
          _tight(FillRequest))
    async def fill_fields(args: dict[str, Any]) -> dict[str, Any]:
        res = await _quickfill(str(args.get("url", "")), args.get("fields") or [])
        if isinstance(res, str):
            return _text(f"fill_fields: {res}")
        bad = [r for r in res["results"] if r["status"] != "ok"]
        lines = [f"{res['ok']}/{len(res['results'])} fields ok."] + \
                [f"- NOT OK [{r['status']}] {r['label']}: {r['detail']}" for r in bad] + \
                ["Take ONE browser_snapshot to verify, then fix only the NOT OK fields by hand."]
        return _text("\n".join(lines))

    # what this job's form was filled with last time (your edits included) — replayed by `refill`
    saved = (store.get_extra(job, "recipe") or {}).get("fields") or []
    last = {memory.norm(f.get("label", "")): f for f in ((store.get_extra(job, "apply") or {}).get("report") or {}).get("fields") or []}
    replayed: set[str] = set()

    @tool("refill", "Put back the values this job's form was filled with LAST time (the candidate's "
          "corrections included), on the current page, in ONE call. Never clicks buttons or submits. "
          "Call it FIRST on every page of the form, before fill_fields.", {"url": str})
    async def refill(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", ""))
        here = [f for f in saved if _urlkey(f["url"]) == _urlkey(url)]
        # ponytail: the form's URL can change between runs (tokens in the path) — then try every value not yet placed
        todo = [f for f in (here or saved) if f["label"] not in replayed]
        guessed = [f for f in todo if (last.get(memory.norm(f["label"])) or {}).get("confidence") == "guess"]
        todo = [f for f in todo if f not in guessed]    # a guess is never typed again: the candidate confirms it first
        if not todo:
            return _text("No saved values left for this page — fill it with fill_fields."
                         + "".join(f"\n- NOT PLACED {f['label']}: last time's value was a guess." for f in guessed))
        res = await _quickfill(url, [{k: f[k] for k in ("label", "kind", "value")} for f in todo])
        if isinstance(res, str):
            return _text(f"refill: {res}")
        lines = []
        for r in res["results"]:
            if r["status"] == "ok":
                replayed.add(r["label"])
                f = next(f for f in todo if f["label"] == r["label"])
                was = last.get(memory.norm(f["label"])) or {}
                lines.append(f"- OK {f['label']} = {str(f['value'])[:200]}")
        bad = [r for r in res["results"] if r["status"] != "ok"]
        head = f"{len(lines)}/{len(todo)} saved values placed." + (
            " The NOT OK ones may be on another step of the form — refill again there." if bad else "")
        tail = ([f"- NOT PLACED {f['label']}: last time's value was a guess. Answer it from the candidate's files or recall; "
                 "if they don't have it, leave it empty and list it in unfilled_required." for f in guessed] +
                ["Report the OK ones in report_filled as they are. Take ONE browser_snapshot, then fill_fields only "
                 "what is still empty on this page."])
        return _text("\n".join([head] + lines + [f"- NOT OK {r['label']}: {r['detail']}" for r in bad] + tail))

    jp = create_sdk_mcp_server(name="jobpilot", tools=[report_filled, report_blocked, form_found, greenhouse_questions,
                                                       fill_fields, refill, tailor_resume, resume_pages, recall])
    never = ["Task", "Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch"]
    no_js = ["mcp__playwright__browser_evaluate", "mcp__playwright__browser_run_code_unsafe", "mcp__playwright__browser_close"]
    opts = ClaudeAgentOptions(
        system_prompt=_system(job, shot, resumes),
        mcp_servers={"jobpilot": jp},
        disallowed_tools=never,
        hooks={
            "PreToolUse": [HookMatcher(matcher="Read|mcp__playwright__.*", hooks=[guard.pre], timeout=10)],
            "PostToolUse": [HookMatcher(matcher="Read|mcp__.*", hooks=[guard.post], timeout=10)],
        },
        permission_mode="acceptEdits",
        setting_sources=[],
        strict_mcp_config=True,      # only OUR servers — no claude.ai account connectors
        model=MODEL,
        max_turns=MAX_TURNS,
        cwd=str(HERE),
        env={"MCP_TOOL_TIMEOUT": "120000"},
    )
    phases: dict[str, dict] = {}
    done = lambda: "filled" in sink or "blocked" in sink or err.startswith("MCP server failed")  # noqa: E731

    async def browser_part(name: str, prompts: list[str], tools: list[str]) -> str:
        """A part with the browser: a Chrome slot, a cdp-proxy and the Playwright MCP for its length only."""
        nonlocal err
        cost0, t = run["cost"], time.time()
        async with chrome_slot():
            try:
                if closed := await asyncio.to_thread(close_frozen_tabs):
                    phases.setdefault("frozen_tabs_closed", []).extend(closed)
            except RuntimeError as e:          # one of the candidate's own review tabs: never closed
                err = str(e)
                phases[name] = {"secs": 0, "cost_usd": 0.0, "error": err}
                return err
            # the agent talks to Chrome through cdp-proxy, so its tabs never take focus from the form you're correcting
            proxy = await asyncio.create_subprocess_exec("node", str(CDP_PROXY), stdin=asyncio.subprocess.PIPE,
                                                         stdout=asyncio.subprocess.PIPE, env={**os.environ, "CDP": CDP})
            try:
                run["cdp"] = f"http://127.0.0.1:{(await asyncio.wait_for(proxy.stdout.readline(), 15)).decode().strip()}"
                opts.mcp_servers = {"jobpilot": jp, "playwright": {"type": "stdio", "command": "npx", "args": [
                    "-y", PLAYWRIGHT_MCP, "--cdp-endpoint", run["cdp"],
                    "--output-dir", str(ARTIFACTS), "--allow-unrestricted-file-access"]}}
                opts.allowed_tools, opts.disallowed_tools = ["Read", "mcp__playwright__*"] + tools, never + no_js
                for prompt in prompts:                  # the second one only nudges a run that ended without reporting
                    opts.resume = run["session"]        # the SDK's id for this job's conversation (None: a new one)
                    err = await _part(prompt, opts, sink, run)
                    if done() or name == "find":
                        break
            finally:
                proxy.kill()
                opts.mcp_servers = {"jobpilot": jp}
        phases[name] = {"secs": round(time.time() - t), "cost_usd": round(run["cost"] - cost0, 4), "error": err}
        return err

    # ── Part 1: find the form (browser). A closed posting or a login wall costs a minute, not a resume ──
    err = ""
    store.set_extra(job["id"], "parked", None)     # we're replacing its tab: closing it isn't a skip
    close_stale_tabs(job["url"])
    before = page_ids()
    await browser_part("find", [f"Part 1: find the form, for {job['title']} at {job['company']}.\nURL: {job['url']}\n"
                                "Read posting.txt and profile.md, open your tab, and follow the posting to the "
                                "application form. Fill nothing: call form_found or report_blocked."],
                       ["mcp__jobpilot__form_found", "mcp__jobpilot__report_blocked", "mcp__jobpilot__greenhouse_questions"])
    form = sink.get("form") or {}
    if "blocked" not in sink and not err:
        phases["find"]["tabs_closed"] = close_part_tabs(before, guard.urls | {_urlkey(job["url"])})
        guard.tab_ready, guard.url = False, form.get("url") or job["url"]   # Part 3 opens its own tab again

    # ── Part 2: the resume (no browser: holds no tab, window or Chrome slot). Skipped when the form takes no
    #    resume file (Wellfound sends the resume saved on the candidate's profile): a tailored PDF would never reach them ──
    no_upload = form.get("resume_upload") == "no"
    if no_upload:
        phases["resume"] = {"secs": 0, "cost_usd": 0.0, "error": "", "skipped": "the form takes no resume upload"}
    elif "blocked" not in sink and run["session"] and not err:          # a part that errored ends the run (e.g. expired login)
        cost0, t = run["cost"], time.time()
        async with tailor_slot:
            opts.resume = run["session"]
            opts.allowed_tools = ["Read", "mcp__jobpilot__tailor_resume", "mcp__jobpilot__resume_pages",
                                  "mcp__jobpilot__report_blocked"]
            err = await _part("Part 2: the resume. No browser in this part. Tailor the resume until tailor_resume "
                              "accepts it.", opts, sink, run)
            if not err and "blocked" not in sink:
                # same session, small context: Part 3 would otherwise carry every file and draft of Parts 1-2 on each of
                # its many browser turns. Done here, in the tailor slot, so it never holds a Chrome slot.
                c0 = run["cost"]
                cerr = await _part("/compact Keep only what Part 3 (filling the form) needs: the tailored PDF path, the "
                                   "form URL and how to reach it, the form's questions, anything learned about this job or "
                                   "its eligibility, and the facts about the candidate you used in the resume.", opts, sink, run)
                phases["compact"] = {"cost_usd": round(run["cost"] - c0, 4), "error": cerr}   # a failed compact isn't fatal
        phases["resume"] = {"secs": round(time.time() - t), "cost_usd": round(run["cost"] - cost0, 4), "error": err}

    # ── Part 3: fill the form (browser), in the same conversation ──
    if "blocked" not in sink and run["session"] and not err:          # a part that errored ends the run (e.g. expired login)
        saved = (store.get_extra(job, "recipe") or {}).get("fields") or []
        made = sink.get("resume_version")
        first = ("Part 3: fill the form.\n"
                 + f"Open your tab at the form: {guard.url}\n"
                 + (f"How you reached it in Part 1: {form['route']}\n" if form.get("route") else "")
                 + ("This form takes no resume upload (Part 1): upload nothing, and write the note to the hiring "
                    "team as Part 3, step 6 says.\n" if no_upload else
                    f"Upload the tailored resume you built: {made['pdf']}\n" if made else
                    "Part 2 didn't produce a tailored resume: tailor it now (Part 2), then fill the form.\n")
                 + (f"This job was filled before: {len(saved)} saved values. Call refill first on each page of the form.\n"
                    if saved else "")
                 + "Fill the whole application, then screenshot and report.")
        nudge = ("Finish now. If your tab isn't current: browser_tabs action=list, then select the tab "
                 f"showing {urlsplit(guard.url).netloc or guard.url}. Take the full-page screenshot, "
                 "then call report_filled or report_blocked.")
        await browser_part("fill", [first, nudge], ["mcp__jobpilot__*"])

    base = {"cost_usd": round(run["cost"], 4), "turns": run["turns"], "denials": guard.denials,
            "filled_actions": guard.filled, "log": str(guard.log), "session_id": run["session"], "phases": phases,
            "form": sink.get("form"),
            "resume_version": sink.get("resume_version")}
    closed = sink.get("blocked", {}).get("kind") in ("closed", "not_found", "not_eligible")
    if guard.urls:
        try:     # out of your way: into the review window as background tabs (closed postings: just closed)
            base["window"] = await asyncio.to_thread(park, guard.urls | {_urlkey(job["url"])}, closed, job["id"])
        except Exception as e:  # noqa: BLE001 — window tidying never fails the run
            base["window"] = f"not moved: {type(e).__name__}: {e}"[:200]
    if "filled" in sink:
        try:
            store.set_extra(job["id"], "recipe", build_recipe(job, guard.recipe, sink["filled"]))
            base["recipe"] = "store.db jobs.extra.recipe"
        except Exception as e:  # noqa: BLE001 — the recipe is a convenience, never fail the run on it
            base["recipe_error"] = f"{type(e).__name__}: {e}"
        try:
            # answers are learned when YOU mark the job submitted (dashboard); new platform notes wait
            # in memory.json "pending" until you approve them
            memory.learn(job["company"], sink["filled"], job["url"], answers=False, lessons_to="pending")
        except Exception as e:  # noqa: BLE001 — memory is an optimisation, never fail the run on it
            base["memory_error"] = f"{type(e).__name__}: {e}"
        return {"kind": "filled", "report": sink["filled"], **base}
    if "blocked" in sink:
        return {"kind": "blocked", "report": sink["blocked"], **base}
    return {"kind": "error", "report": {"reason": err or "agent ended without reporting"}, **base}


def _selftest() -> None:
    """python apply_agent.py --selftest — the guard rules that need no browser."""
    g = Guard("selftest", "https://jobs.example.com/x")
    g.tab_ready, g.filled = True, 3
    deny = lambda tool, inp: bool(asyncio.run(g.pre(  # noqa: E731
        {"tool_name": f"mcp__playwright__{tool}", "tool_input": inp}, None, None)))
    assert deny("browser_navigate", {"url": "data:text/html,<script>fetch('x')</script>"})
    assert deny("browser_navigate", {"url": " javascript:alert(1)"})
    assert deny("browser_tabs", {"action": "new", "url": "blob:https://x/1"})
    assert deny("browser_evaluate", {"function": "() => 1"})
    assert deny("browser_press_key", {"key": "Enter"})
    assert deny("browser_click", {"element": "Submit application", "target": "e9"})
    assert not deny("browser_navigate", {"url": "https://jobs.ashbyhq.com/close/1/application"})
    assert not deny("browser_tabs", {"action": "list"})
    g.context = CONTEXT / "selftest"                    # Read: only this job's folder
    read = lambda path: bool(asyncio.run(g.pre({"tool_name": "Read", "tool_input": {"file_path": path}}, None, None)))  # noqa: E731
    assert read(str(HERE / "store.db")) and read(str(g.context / ".." / "other" / "profile.md")) and read("~/.ssh/id_rsa")
    assert not read(str(g.context / "pool.yaml"))
    g.log.unlink(missing_ok=True)
    import yaml                                         # left_out: a dropped skill is reported, the candidate's own resume isn't
    base = yaml.safe_load(_read(RESUME_DIR / "resume.yaml"))
    assert left_out({"skills": base["skills"]}, base) == []
    first, second = [x.strip() for x in base["skills"][0]["items"].split(",")][:2]
    fewer = [{**base["skills"][0], "items": first}] + base["skills"][1:]
    assert f"skills ({second}" in left_out({"skills": fewer}, base)[0], left_out({"skills": fewer}, base)
    print("guard self-test ok")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(_selftest())
    q = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    job = next((j for j in store.list_jobs()
                if q and (q == j["id"] or q in j["company"].lower())), None)
    if not job:
        sys.exit(f"no job matching {q!r}")
    if not cdp_up():
        sys.exit("agent browser not running — run: ./browser.sh start")
    res = asyncio.run(apply(job))
    out = ARTIFACTS / f"{job['id']}.result.json"
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False))
    rep = res.get("report") or {}
    print(f"{res['kind']} · fields={len(rep.get('fields') or [])} · unfilled={rep.get('unfilled_required')} · "
          f"denials={res.get('denials')} · turns={res.get('turns')} · ${res.get('cost_usd')}  -> {out}")
