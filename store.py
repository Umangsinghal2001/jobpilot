"""
L1 — SQLite store. The handoff medium between agents.

Discovery writes jobs here; later stages (scorer, deep-agent) read/update rows.
Cross-run dedup: add_jobs() ignores jobs already seen (by a stable id derived
from the normalized URL, so tracking params like ?utm=... don't create dupes).

Displayed fields are title/company/url (your pick). We also keep source,
first_seen/last_seen, status, and a flexible `extra` JSON blob so later stages
can attach salary/location/confidence/answers WITHOUT a schema migration.

Pure stdlib (sqlite3) — no deps, runs offline. `python3 store.py` self-checks.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "store.db"
SETTINGS_PATH = Path(__file__).parent / "settings.json"   # dashboard-editable knobs; daily.sh reads it with plutil
DEFAULTS = {"min_battery": 50, "max_idle_min": 5, "daily_cap_usd": 0.0, "auto_detect": True}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id         TEXT PRIMARY KEY,          -- stable dedup hash
  title      TEXT NOT NULL,
  company    TEXT NOT NULL,
  url        TEXT NOT NULL,
  source     TEXT,                      -- which platform (ops, not displayed)
  status     TEXT NOT NULL DEFAULT 'new',  -- new|scored|skipped|applied...
  first_seen TEXT NOT NULL,             -- ISO date first discovered
  last_seen  TEXT NOT NULL,             -- ISO date last seen in a run
  extra      TEXT                       -- JSON: salary/location/score/answers later
);
"""


def _norm_url(url: str) -> str:
    """Normalize a URL for dedup: lowercase, drop query/fragment + trailing slash."""
    u = (url or "").strip().lower()
    u = re.sub(r"[#?].*$", "", u)   # drop ?query and #fragment (kills utm tracking)
    u = re.sub(r"/+$", "", u)       # drop trailing slash
    return u


def job_id(company: str, title: str, url: str) -> str:
    """Stable id: from normalized URL if present, else company|title."""
    key = _norm_url(url) or f"{(company or '').strip().lower()}|{(title or '').strip().lower()}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


class _Conn(sqlite3.Connection):
    """`with connect() as conn:` commits (or rolls back) AND closes. sqlite3's own `with` only commits, so every
    call leaked a file handle; a long-running server hit the 256-handle limit and could no longer open store.db."""
    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, factory=_Conn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def add_jobs(jobs: list[dict], path: Path = DB_PATH) -> dict:
    """Insert new jobs; dedup across runs by id. Returns {'new': n, 'dupe': m}."""
    today = date.today().isoformat()
    new = dupe = 0
    with connect(path) as conn:
        for j in jobs:
            jid = job_id(j.get("company", ""), j.get("title", ""), j.get("url", ""))
            extra = {k: v for k, v in j.items()
                     if k not in {"title", "company", "url", "source"}}
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (id, title, company, url, source, status, first_seen, last_seen, extra)
                   VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?)""",
                (jid, (j.get("title") or "").strip(), (j.get("company") or "").strip(),
                 (j.get("url") or "").strip(), j.get("source", ""),
                 today, today, json.dumps(extra) if extra else None),
            )
            if conn.total_changes > before:
                new += 1
            else:
                dupe += 1
                conn.execute("UPDATE jobs SET last_seen=? WHERE id=?", (today, jid))
    return {"new": new, "dupe": dupe}


def list_jobs(status: str | None = None, path: Path = DB_PATH) -> list[dict]:
    with connect(path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY first_seen DESC, rowid DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY first_seen DESC, rowid DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def counts(path: Path = DB_PATH) -> dict:
    with connect(path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        new = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='new'").fetchone()[0]
    return {"total": total, "new": new}


def get_job(job_id: str, path: Path = DB_PATH) -> dict | None:
    with connect(path) as conn:
        r = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def update_form(job_id: str, form: dict, status: str, path: Path = DB_PATH) -> None:
    """Attach the read+drafted form under extra['form'] and set the row's status
    (form-ready | needs-you | closed)."""
    with connect(path) as conn:
        row = conn.execute("SELECT extra FROM jobs WHERE id=?", (job_id,)).fetchone()
        extra = json.loads(row["extra"]) if row and row["extra"] else {}
        extra["form"] = form
        conn.execute("UPDATE jobs SET extra=?, status=? WHERE id=?",
                     (json.dumps(extra), status, job_id))


def update_apply(job_id: str, apply: dict, status: str, path: Path = DB_PATH) -> None:
    """Attach the ApplyAgent's report under extra['apply'] and set the row's status
    (ready-to-submit | needs-you | closed). extra['form'] (old drafter data) is kept."""
    with connect(path) as conn:
        row = conn.execute("SELECT extra FROM jobs WHERE id=?", (job_id,)).fetchone()
        extra = json.loads(row["extra"]) if row and row["extra"] else {}
        extra["apply"] = apply
        conn.execute("UPDATE jobs SET extra=?, status=? WHERE id=?",
                     (json.dumps(extra), status, job_id))


def set_extra(job_id: str, key: str, value, path: Path = DB_PATH) -> None:
    """Set one key of the row's extra JSON (e.g. 'recipe'), keeping every other key."""
    with connect(path) as conn:
        row = conn.execute("SELECT extra FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"no job {job_id}")
        extra = json.loads(row["extra"]) if row["extra"] else {}
        extra[key] = value
        conn.execute("UPDATE jobs SET extra=? WHERE id=?", (json.dumps(extra), job_id))


def get_extra(job: dict, key: str):
    """Read one key of a job row's extra JSON (None if absent)."""
    return json.loads(job.get("extra") or "{}").get(key)


def settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(**kv) -> dict:
    s = {**settings(), **{k: v for k, v in kv.items() if k in DEFAULTS}}
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))
    return s


if __name__ == "__main__":
    # Self-check in a throwaway DB: proves URL-normalization dedup + cross-run dedup.
    import os
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "t.db"
    sample = [
        {"title": "AI Engineer", "company": "Acme", "url": "https://x.com/jobs/1?utm=a", "source": "web"},
        {"title": "AI Engineer", "company": "Acme", "url": "https://x.com/jobs/1?utm=b", "source": "web"},  # same job, diff tracking
        {"title": "ML Eng", "company": "Beta", "url": "https://y.com/2", "source": "remoteok", "salary": "$120k"},
    ]
    r1 = add_jobs(sample, tmp)
    assert r1 == {"new": 2, "dupe": 1}, r1           # the two utm variants collapse to one
    r2 = add_jobs(sample, tmp)                         # re-run the same batch
    assert r2 == {"new": 0, "dupe": 3}, r2            # everything already seen
    assert counts(tmp)["total"] == 2, counts(tmp)
    # extra blob preserved for the ML Eng row
    ml = [j for j in list_jobs(path=tmp) if j["company"] == "Beta"][0]
    assert json.loads(ml["extra"])["salary"] == "$120k"
    before = len(os.listdir("/dev/fd"))
    for _ in range(300):                               # would leak 300 handles before the fix
        list_jobs(path=tmp)
    assert len(os.listdir("/dev/fd")) - before < 5, "store connections leak file handles"
    print("L1 store self-check PASSED:", r1, r2, counts(tmp))
    os.remove(tmp)
