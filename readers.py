"""
Form readers — turn an apply URL into the EXACT list of application questions,
plus the listing text (`jd`) so the drafter can tailor "why us" answers.

One adapter per ATS:
  - Greenhouse  -> public JSON API (no browser; exact required flags + select options + full JD)
  - LinkedIn / Wellfound / Workday-family -> 'needs_you' (login walls; don't automate)
  - everything else -> Playwright browser reader (browser_reader.py) when installed

Always returns:
  {"ats": str, "status": "ok" | "needs_you" | "closed", "title": str | None,
   "questions": [{"label", "type", "required", "options"}], "reason": str, "jd": str}

    python3 readers.py <apply-url>      # prints the parsed form
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

GH_RE = re.compile(r"greenhouse\.io/([^/]+)/jobs/(\d+)")

# hosts behind a login wall — the agent must not (and cannot) fill these
NEEDS_YOU = {
    "linkedin.com": "LinkedIn Easy Apply — your login; don't automate",
    "wellfound.com": "Wellfound apply needs your login",
    "myworkdayjobs.com": "Workday needs an account",
    "unitedhealthgroup.com": "UHG careers portal needs an account",
    "deloitte.com": "Deloitte (Workday) needs an account",
}


def _strip_html(s: str) -> str:
    s = _html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _get_json(url: str) -> tuple[int, dict]:
    def _open(ctx=None):
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "jobpilot/0.1"}
        )
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return r.status, json.loads(r.read().decode())

    try:
        return _open()
    except urllib.error.HTTPError as e:            # 404 etc. — Greenhouse returns JSON bodies
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError) or "CERTIFICATE" in str(e.reason).upper():
            # ponytail: a TLS-intercepting proxy breaks certifi. Public job-board APIs only;
            # verifies normally on an ordinary network.
            return _open(ssl._create_unverified_context())
        raise


def _empty(ats: str, status: str, reason: str) -> dict:
    return {"ats": ats, "status": status, "title": None, "questions": [],
            "reason": reason, "jd": ""}


def read_greenhouse(url: str) -> dict | None:
    m = GH_RE.search(url)
    if not m:
        return None
    board, jid = m.groups()
    code, d = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}?questions=true"
    )
    if code != 200 or "questions" not in d:
        return _empty("greenhouse", "closed", d.get("error") or f"http {code}")
    qs = []
    for q in d.get("questions", []):
        f = (q.get("fields") or [{}])[0]
        qs.append({
            "label": (q.get("label") or "").strip(),
            "type": f.get("type", "input_text"),
            "required": bool(q.get("required")),
            "options": [v.get("label") for v in (f.get("values") or []) if v.get("label")][:60],
        })
    return {"ats": "greenhouse", "status": "ok", "title": d.get("title"),
            "questions": qs, "reason": "",
            "jd": _strip_html(d.get("content") or "")[:6000]}


def read_form(url: str) -> dict:
    r = read_greenhouse(url)
    if r:
        return r
    host = urllib.parse.urlparse(url).netloc.lower()
    for k, why in NEEDS_YOU.items():
        if k in host:
            return _empty(k.split(".")[0], "needs_you", why)
    if os.environ.get("JOBPILOT_STEALTH") == "1":
        # browser fetches are LOCAL and visible to network monitoring — off in stealth
        return _empty("unknown", "needs_you", "stealth mode: browser reader disabled")
    try:
        from browser_reader import read_with_browser  # Playwright; optional until installed
    except ImportError:
        return _empty("unknown", "needs_you", "browser reader not installed yet")
    return read_with_browser(url)


if __name__ == "__main__":
    import sys
    print(json.dumps(read_form(sys.argv[1]), indent=2, ensure_ascii=False))
