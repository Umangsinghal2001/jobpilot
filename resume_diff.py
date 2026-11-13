"""
resume_diff — what a job's tailored resume changed against the base resume, for the review side panel.

    job_for_url(url)  -> the job whose application lives at that URL (most specific match), or None
    diff_for_job(job) -> {title, profile, projects, skills, other, facts, pdf} or {"none": reason}

Works on the resume data (resume.yaml + the job's version file), not on PDFs, so every change is exact.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

import store
from apply_agent import ARTIFACTS, HERE, RESUME_DIR, _urlkey

if str(HERE / "resume") not in sys.path:
    sys.path.insert(0, str(HERE / "resume"))
import build as rb  # noqa: E402  (resume/build.py)


def _md_plain(s: str) -> str:
    """resume.yaml markup → plain text: **bold** and [text](url) dropped to their text."""
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", str(s or "")).replace("**", "")


def words_diff(old: str, new: str) -> list[list[str]]:
    """[[op, text], …] with op in eq / del / ins, word by word."""
    a, b = _md_plain(old).split(), _md_plain(new).split()
    out: list[list[str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(["eq", " ".join(a[i1:i2])])
        else:
            if i2 > i1:
                out.append(["del", " ".join(a[i1:i2])])
            if j2 > j1:
                out.append(["ins", " ".join(b[j1:j2])])
    return out


def _resume_version(job: dict) -> dict | None:
    """Where the run recorded which version file it built (the store, or a single-job test run's result file)."""
    rv = (store.get_extra(job, "apply") or {}).get("resume_version")
    if not rv:
        f = ARTIFACTS / f"{job['id']}.result.json"
        if f.exists():
            rv = json.loads(f.read_text()).get("resume_version")
    return rv or None


def job_for_url(url: str) -> dict | None:
    """The job whose listing or form pages match this URL; the longest (most specific) match wins."""
    k = _urlkey(url)
    if not k or k.startswith(("localhost", "127.0.0.1")):
        return None
    best: tuple[int, dict] | None = None
    for j in store.list_jobs():
        keys = {_urlkey(j["url"])} | {_urlkey(f["url"]) for f in (store.get_extra(j, "recipe") or {}).get("fields", [])}
        for key in keys:
            if key and k.startswith(key) and (best is None or len(key) > best[0]):
                best = (len(key), j)
    return best[1] if best else None


def _projects(r: dict) -> list[dict]:
    return [p for co in r["experience"] for ro in co["roles"] for p in ro["projects"]]


def _skills(r: dict) -> list[str]:
    return [s.strip() for g in r.get("skills", []) for s in re.split(r",(?![^(]*\))", _md_plain(g["items"])) if s.strip()]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def diff_for_job(job: dict) -> dict:
    rv = _resume_version(job)
    if not rv or not rv.get("version"):
        return {"none": "No tailored resume for this job yet: it went out with the base resume, or the agent hasn't filled it."}
    name = re.sub(r"^resume/versions/|\.yaml$", "", rv["version"])
    base, ver = rb.load(""), rb.load(name)

    base_bullets = {p["name"]: [_md_plain(b) for b in p["bullets"]] for p in _projects(base) if p.get("name")}
    base_order = [p.get("name") for p in _projects(base)]
    projects = []
    for i, p in enumerate(_projects(ver)):
        if not p.get("name"):                       # unnamed groups (an internship role) are never changed by versions
            continue
        olds = base_bullets.get(p["name"], [])
        used: set[int] = set()
        bullets = []
        for b, note in zip(p["bullets"], p.get("_notes") or [{}] * len(p["bullets"])):
            nb = _md_plain(b)
            if nb in olds:
                used.add(olds.index(nb))
                bullets.append({"kind": "kept", "text": nb, "note": note})
                continue
            ratios = [difflib.SequenceMatcher(a=o, b=nb).ratio() for o in olds]
            k = max(range(len(olds)), key=ratios.__getitem__) if olds else -1
            if k >= 0 and ratios[k] > 0.35:
                used.add(k)
                bullets.append({"kind": "reworded", "diff": words_diff(olds[k], nb), "note": note})
            else:
                bullets.append({"kind": "new", "text": nb, "note": note})
        moved = base_order.index(p["name"]) if p["name"] in base_order else i
        projects.append({"name": p["name"], "heading": p.get("display") or p["name"], "moved_up": moved > i,
                         "bullets": bullets, "dropped": [o for k, o in enumerate(olds) if k not in used]})
    shown = {p["name"] for p in projects}
    hidden = [n for n in base_bullets if n not in shown]

    bs, vs = _skills(base), _skills(ver)
    other = {sec: ("unchanged" if base.get(sec) == ver.get(sec) else "removed" if not ver.get(sec) else "changed")
             for sec in ("achievements", "education", "certificates")}
    flat = lambda r: json.dumps(r, ensure_ascii=False)  # noqa: E731
    new_numbers = sorted(_numbers(_md_plain(flat(ver))) - _numbers(_md_plain(flat(base) + flat(rb.pool()))),  # pool facts aren't new
                         key=lambda x: float(x))

    out_dir = ARTIFACTS / "resumes" / job["id"]
    return {
        "job": {"id": job["id"], "company": job["company"], "title": job["title"], "version": rv["version"]},
        "title": {"old": base["title"], "new": ver["title"]},
        "profile": words_diff(base["profile"], ver["profile"]) if base["profile"] != ver["profile"] else None,
        "projects": projects,
        "hidden_projects": hidden,
        "left_out": ver.get("_left_out", []),
        "skills": {"added": [s for s in vs if s not in bs], "removed": [s for s in bs if s not in vs],
                   "kept": len([s for s in vs if s in bs])},
        "other": other,
        "facts": {"new_numbers": new_numbers},
        "pdf": {"tailored": (out_dir / rv["pdf"].rsplit("/", 1)[-1]).exists() if rv.get("pdf") else False,
                "pages": rv.get("pages"), "words": rv.get("words")},
    }


def tailored_pdf(job: dict) -> Path | None:
    rv = _resume_version(job) or {}
    p = Path(rv.get("pdf", ""))
    return p if p.is_file() and ARTIFACTS in p.resolve().parents else None


def annotated(job: dict) -> Path | None:
    """The review copy of the tailored resume: the same page with each choice's reason beside it."""
    p = tailored_pdf(job)
    return p.parent / "resume.review.html" if p and (p.parent / "resume.review.html").is_file() else None


if __name__ == "__main__":
    assert words_diff("Built a **copilot** today", "Built a multi-agent copilot today") == \
        [["eq", "Built a"], ["ins", "multi-agent"], ["eq", "copilot today"]]
    assert job_for_url("http://localhost:8765/review") is None
    j = store.get_job("89fdeb6864382a9c")          # Steps AI: has a tailored version from the test run
    if j and _resume_version(j):
        assert job_for_url(j["url"])["id"] == j["id"]
        d = diff_for_job(j)
        print(f"{d['job']['company']}: {len(d['projects'])} projects, "
              f"{sum(b['kind'] == 'reworded' for p in d['projects'] for b in p['bullets'])} reworded bullets, "
              f"{len(d['skills']['added'])} skills added, new numbers {d['facts']['new_numbers']}")
    print("resume_diff self-test ok")
