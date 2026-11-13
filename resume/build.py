"""
build — resume.yaml → resume.html → resume.pdf, then reads the PDF back the way a resume parser (ATS) does.

    python resume/build.py                 # writes resume.html, resume.pdf, resume.parsed.txt
    python resume/build.py other.pdf       # also show what a parser reads from another PDF
    python resume/build.py --for acme      # a version for one job: versions/acme.yaml → versions/out/acme.pdf

The data (resume.yaml, pool.yaml, versions/) lives in <profile folder>/resume/: $JOBPILOT_PROFILE, else example-profile/.

A version file changes only what it names (facts stay in resume.yaml, so every version tells the same story):
    title / profile / skills / achievements / …   replace that section
    projects: {"<project name>": {hide: true}}           drop a project
    projects: {"<project name>": {bullets: [2, 0, "new wording"]}}   pick, reorder (base index) or reword bullets
    lead: ["<project name>", …]                          move these to the top of their role, in this order
    projects: {"<project>": {bullets: [{pool: triage.routing}, {pool: triage.tracing, text: "reworded"}]}}   bullets from pool.yaml
    projects: {"<pool project id>": {bullets: [...]}}    add a new project from pool.yaml to the current job
    projects: {"<project>": {name: "heading"}}           show a different heading (the key stays the same)
    a bullet as {base: 2, why: …} / {pool: <id>, text: …, why: …}   why = the reason, shown only on the review copy
    {pool: <id>, hide: true, why: …} / {hide: true, why: …} on a project / left_out: {certificates: why}   cut, reason kept
    certificates: [] (or any list section)               leave that section out
A project name that doesn't exist in resume.yaml stops the build (a typo never silently does nothing).

resume.parsed.txt is exactly the text a parser gets (pdftotext, reading order). The check after each build fails
loudly on what breaks ATS parsing: bullets detached from their line, missing links, more than 2 pages.
Needs: PyYAML + Playwright (both in requirements.txt), poppler (pdftotext, pdfinfo).
"""
from __future__ import annotations

import html
import re
import os
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(os.environ.get("JOBPILOT_PROFILE") or Path(__file__).parents[1] / "example-profile").expanduser() / "resume"
FONTS = Path(__file__).parent / "fonts"
SRC, HTML, PDF, PARSED = HERE / "resume.yaml", HERE / "resume.html", HERE / "resume.pdf", HERE / "resume.parsed.txt"

# small inline icons (no text inside, so a parser never sees them)
_I = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
ICON = {
    "mail": f'<svg {_I}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
    "phone": f'<svg {_I}><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>',
    "linkedin": f'<svg {_I}><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 11v5M8 8v.01M12 16v-5M16 16v-3a2 2 0 0 0-4 0"/></svg>',
    "gfg": f'<svg {_I}><path d="M3 12h7a4 4 0 1 1-1-3M21 12h-7a4 4 0 1 0 1-3"/></svg>',
    "hackerrank": f'<svg {_I}><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 8v8M15 8v8M9 12h6"/></svg>',
    "codechef": f'<svg {_I}><path d="M7 11a5 5 0 0 1 10 0v6H7z"/><path d="M9 21h6M10 7a2 2 0 1 1 4 0"/></svg>',
    "ext": f'<svg {_I}><path d="M12 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6M11 13l9-9M15 4h5v5"/></svg>',
    "profile": f'<svg {_I}><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M15 8h2M15 12h2M7 16h10"/></svg>',
    "work": f'<svg {_I}><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/></svg>',
    "award": f'<svg {_I}><circle cx="12" cy="9" r="6"/><path d="M9 14.5 7 21l5-3 5 3-2-6.5"/></svg>',
    "skills": f'<svg {_I}><path d="M4 7h3a1 1 0 0 0 1-1V5a2 2 0 1 1 4 0v1a1 1 0 0 0 1 1h3v3a1 1 0 0 0 1 1h1a2 2 0 1 1 0 4h-1a1 1 0 0 0-1 1v3H4z"/></svg>',
    "education": f'<svg {_I}><path d="M22 9 12 4 2 9l10 5 10-5zM6 11v5c3 2 9 2 12 0v-5"/></svg>',
    "cert": f'<svg {_I}><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z"/><path d="M9 13h6M9 17h4"/></svg>',
}


def md(s: str) -> str:
    """Escape, then **bold** and [text](url)."""
    s = html.escape(str(s), quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)


def _mark(n: dict) -> str:
    """Review overlay: the bullet's colour mark: where it came from, and red-ish if that work isn't in production."""
    kind = "pool" if "pool" in n["src"] else "base" if n["src"].startswith("base") else "edit"
    return f'm-{kind}{" reworded" if n["src"].startswith("reworded") else ""}{" notprod" if n["status"] in ("prototype", "design") else ""}'


def _note(n: dict) -> str:
    bits = [html.escape(n["why"]) if n["why"] else '<i>no reason given</i>', html.escape(n["src"])]
    return " · ".join(bits + ([f'<b>{n["status"]}</b>'] if n["status"] else []))


def section(icon: str, title: str, body: str) -> str:
    return f'<section><h2>{ICON[icon]}{title}</h2>{body}</section>'


def render(r: dict, review: bool = False) -> str:
    contact = "".join(f'<a href="{html.escape(c["url"])}">{ICON[c["icon"]]}{html.escape(c["label"])}</a>' for c in r["contact"])
    exp = []
    for co in r["experience"]:
        roles, one = co["roles"], len(co["roles"]) == 1
        block = [f'<div class="company"><b>{md(co["company"])}</b></div>'] if not one else []
        for ro in roles:
            where = " | ".join(x for x in (ro.get("location"), f'<span class="blue">{ro["dates"]}</span>') if x)
            head = f'<b>{md(co["company"])},</b> <i class="blue">{md(ro["title"])}</i>' if one else f'<b class="blue">{md(ro["title"])}</b>'
            block.append(f'<div class="role{"" if one else " sub"}"><span>{head}</span><span>{where}</span></div>')
            for p in ro["projects"]:
                if p.get("name"):
                    link = f' <a class="see" href="{html.escape(p["link"])}">(See visuals &amp; full details here)</a>{ICON["ext"]}' if p.get("link") else ""
                    block.append(f'<div class="project{"" if one else " sub"}"><b>{md(p.get("display") or p["name"])}</b>{link}</div>')
                notes = p.get("_notes") if review else None
                lis = (f'<li class="{_mark(n)}"><span class="note">{_note(n)}</span>{md(b)}</li>' if n else f"<li>{md(b)}</li>"
                       for b, n in zip(p["bullets"], notes or [None] * len(p["bullets"])))
                block.append(f'<ul class="{"" if one else "sub"}">' + "".join(lis) + "</ul>")
        exp.append(f'<div class="co">{"".join(block)}</div>')
    ach = "".join(
        f'<p><a class="strong" href="{html.escape(a["url"])}">{md(a["text"])}</a>{ICON["ext"]}, <i class="blue">{md(a["source"])}</i></p>'
        if a.get("url") else f"<p>{md(a['text'])}</p>" for a in r["achievements"])
    skills = "".join(f'<p><b>{md(s["group"])}</b> — {md(s["items"])}</p>' for s in r["skills"])
    edu = "".join(f'<div class="role"><span><b>{md(e["school"])},</b> <i class="blue">{md(e["degree"])}</i></span><span class="blue">{e["dates"]}</span></div>'
                  for e in r["education"])
    certs = "".join(
        f'<div><a class="strong" href="{html.escape(c["url"])}">{md(c["name"])}</a>{ICON["ext"]}<br><span>{md(c["issuer"])}</span></div>'
        if c.get("url") else f'<div><b>{md(c["name"])}</b><br><span>{md(c["issuer"])}</span></div>' for c in r["certificates"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(r['name'])} — Resume</title>
<style>{(FONTS / "fonts.css").read_text().replace("url(fonts/", f"url({FONTS.as_uri()}/")}{CSS}</style>{REVIEW_CSS if review else ""}</head><body{' class="review"' if review else ""}>
<header><h1>{html.escape(r['name'])}<span class="title">{md(r['title'])}</span></h1><nav>{contact}</nav></header>
<main>
{section("profile", "Profile", f"<p>{md(r['profile'])}</p>")}
{section("work", "Work Experience", "".join(exp))}
{section("award", "Achievements", ach) if ach else ""}
{section("skills", "Technical Skills and Tools", skills) if skills else ""}
{section("education", "Education", edu) if edu else ""}
{section("cert", "Certificates", f'<div class="certs">{certs}</div>') if certs else ""}
{_left_out_box(r) if review else ""}
</main></body></html>"""


def _left_out_box(r: dict) -> str:
    items = "".join(f'<li><b>{md(x["what"])}</b>: {html.escape(x["why"]) or "<i>no reason given</i>"}</li>' for x in r.get("_left_out", []))
    return (f'<div class="leftout"><h3>Left out, and why</h3><ul>{items or "<li>Nothing</li>"}</ul>'
            '<p>Marks: <span class="m-base">base resume</span> <span class="m-base reworded">base, reworded</span> '
            '<span class="m-pool">from the pool</span> <span class="m-pool reworded">pool, reworded</span> '
            '<span class="m-edit">new wording</span> <span class="notprod">not in production</span></p></div>')


REVIEW_CSS = """<style>
/* review copy only: A4 page plus a margin for the notes. The uploaded PDF is built without any of this. */
body.review { width: 300mm; background: #f4f4f1; }
body.review header, body.review main { width: 210mm; background: #fff; }
body.review header { background: #1f4bd3; }
/* the note floats into the margin; flow-root makes the bullet as tall as its note, so notes never overlap */
body.review li { display: flow-root; border-left: 2.5px solid transparent; padding-left: 1.5mm; margin-left: -2mm; margin-bottom: 1mm; }
body.review .note { float: right; width: 76mm; margin-right: -86mm; font-size: 7pt; line-height: 1.2; color: #333; }
.m-base { border-left-color: #9a9a92 !important; } .m-pool { border-left-color: #1d9a5b !important; }
.m-edit { border-left-color: #c47a00 !important; } .reworded { border-left-style: dashed !important; }
.notprod { background: #fde8e6; } .notprod .note b { color: #b42318; }
.leftout { margin: 6mm 0; padding: 3mm 4mm; border: 1px solid #ccc; border-radius: 2mm; background: #fafaf7; }
.leftout h3 { margin: 0 0 1.5mm; font-size: 10pt; } .leftout span { border-left: 2.5px solid transparent; padding: 0 2mm; margin-right: 2mm; }
</style>"""


CSS = """
@page { size: A4; margin: 7mm 0 8mm 0; }
@page :first { margin-top: 0; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; font: 9.3pt/1.18 'Source Sans Pro', Arial, sans-serif;   /* Arial: symbols like → that Source Sans Pro lacks; Lucida Grande (the default fallback) loses the following space in the PDF text */ color: #111; }
a { color: inherit; text-decoration: none; }
.blue, a.see, a.strong { color: #1d44c8; }
svg { width: 1em; height: 1em; vertical-align: -0.14em; }
header { background: #1f4bd3; color: #fff; padding: 8mm 10mm 7mm; }
h1 { margin: 0; font: 700 22pt 'Comfortaa', sans-serif; letter-spacing: .06em; }
h1 .title { font: italic 400 15pt 'Source Sans Pro', sans-serif; letter-spacing: 0; margin-left: 10px; }
nav { margin-top: 2.5mm; display: flex; gap: 14px; font-size: 10pt; }
nav a svg { margin-right: 5px; }
main { padding: 2mm 10mm 0; }
section { margin-top: 3.4mm; }
h2 { font-size: 12pt; color: #1d44c8; text-transform: uppercase; letter-spacing: .05em; margin: 0 0 2mm;
     padding-bottom: 1.2mm; border-bottom: 1.4px solid #1d44c8; break-after: avoid; }
h2 svg { width: 1.15em; height: 1.15em; margin-right: 8px; vertical-align: -0.18em; }
p { margin: 0 0 1.6mm; }
.co { margin-bottom: 3mm; }
.company { margin-bottom: .6mm; }
.role { display: flex; justify-content: space-between; gap: 12px; margin-bottom: .8mm; break-after: avoid; }
.role span:last-child { white-space: nowrap; }
.role.sub { padding-left: 4mm; }
.project { margin: 1.4mm 0 .3mm 3mm; break-after: avoid; }
.project.sub { margin-left: 7mm; }
a.see { font-style: italic; font-weight: 700; text-decoration: underline; }
.project svg, p svg, .certs svg { color: #1d44c8; width: .85em; height: .85em; margin-left: 3px; }
ul { margin: 0 0 .8mm; padding-left: 9mm; }
ul.sub { padding-left: 13mm; }
li { margin: 0 0 .15mm; break-inside: avoid; }
a.strong { font-weight: 700; }
.certs { display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: auto auto; grid-auto-flow: column; gap: 3mm 8mm; }
.certs span { padding-left: 3mm; }
"""


def pool() -> dict:
    """pool.yaml: {"projects": {id: project}, "bullets": {id: bullet}}, empty if there's no pool."""
    f = HERE / "pool.yaml"
    ps = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("projects", []) if f.exists() else []
    return {"projects": {p["id"]: p for p in ps}, "bullets": {b["id"]: b for p in ps for b in p["bullets"]}}


def load(version: str = "") -> dict:
    """resume.yaml, with versions/<version>.yaml applied on top."""
    r = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    if not version:
        return r
    v = yaml.safe_load((HERE / "versions" / f"{version}.yaml").read_text(encoding="utf-8")) or {}
    pl = pool()
    projects = {p["name"]: p for co in r["experience"] for ro in co["roles"] for p in ro["projects"] if p.get("name")}
    for name in list(v.get("projects", {})) + list(v.get("lead", [])):
        if name not in projects and name in pl["projects"] and not pl["projects"][name].get("extends"):
            # ponytail: a new pool project joins the first role (the current job), the only place pool work lives today
            projects[name] = {"name": name, "display": pl["projects"][name]["name"], "bullets": []}
            pl["projects"][name].setdefault("status", None)
            r["experience"][0]["roles"][0]["projects"].append(projects[name])
        elif name not in projects:
            raise SystemExit(f"versions/{version}.yaml: no project called {name!r} in resume.yaml, nor a new one in pool.yaml")
    status_of = {pp.get("extends") or pid: pp.get("status") for pid, pp in pl["projects"].items()}
    left_out = [{"what": k, "why": w} for k, w in (v.get("left_out") or {}).items()]
    for name, change in (v.get("projects") or {}).items():
        p = projects[name]
        if change.get("hide"):
            p["hide"] = True
            left_out.append({"what": p.get("display") or name, "why": change.get("why", "")})
        if change.get("name"):
            p["display"] = change["name"]
        if "bullets" in change:
            base, out, notes = p["bullets"], [], []
            for b in change["bullets"]:
                # 2 | "text" | {base: 2} | {pool: <id>} | {text: "..."}; a dict may add text (a rewording) and why
                d = b if isinstance(b, dict) else {"base": b} if isinstance(b, int) else {"text": b}
                if set(d) - {"base", "pool", "text", "why", "hide"}:
                    raise SystemExit(f"versions/{version}.yaml: a bullet can't have {sorted(set(d) - {'base', 'pool', 'text', 'why', 'hide'})}")
                if d.get("hide"):                  # considered and cut: not printed, but its reason is kept
                    left_out.append({"what": f"bullet {d.get('pool') or d.get('base')} of {p.get('display') or name}", "why": d.get("why", "")})
                    continue
                if "pool" in d and d["pool"] not in pl["bullets"]:
                    raise SystemExit(f"versions/{version}.yaml: no bullet {d['pool']!r} in pool.yaml")
                pb = pl["bullets"].get(d.get("pool"), {})
                orig = pb.get("text") if pb else base[d["base"]] if "base" in d else None
                out.append(d.get("text") or orig)
                src = f"pool {d['pool']}" if pb else f"base bullet {d['base']}" if "base" in d else "new wording"
                notes.append({"src": ("reworded " if d.get("text") and orig else "") + src, "why": d.get("why", ""),
                              "status": pb.get("status") or status_of.get(name)})
            p["bullets"], p["_notes"] = out, notes
    lead = v.get("lead") or []
    for co in r["experience"]:
        for ro in co["roles"]:
            ro["projects"] = sorted((p for p in ro["projects"] if not p.get("hide")),
                                    key=lambda p: lead.index(p["name"]) if p.get("name") in lead else len(lead))
    for k, val in v.items():
        if k not in ("projects", "lead", "left_out"):
            r[k] = val
    r["_left_out"] = left_out
    return r


def build(r: dict, html_path: Path, pdf_path: Path) -> None:
    html_path.write_text(render(r), encoding="utf-8")
    html_path.with_suffix(".review.html").write_text(render(r, review=True), encoding="utf-8")   # for the candidate, never uploaded
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.pdf(path=str(pdf_path), format="A4", print_background=True, prefer_css_page_size=True)
        b.close()


def parsed(pdf: Path) -> str:
    """What a resume parser gets: the PDF's text in reading order."""
    return subprocess.run(["pdftotext", "-raw", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def links(pdf: Path) -> int:
    import zlib
    raw = pdf.read_bytes()
    found = set(re.findall(rb"/URI\s*\((.*?)\)", raw))
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            found |= set(re.findall(rb"/URI\s*\((.*?)\)", zlib.decompress(m[1])))
        except Exception:  # noqa: BLE001 — not a zlib stream
            pass
    return len(found)


def _words(text: str) -> set[str]:
    text = re.sub(r"-\n", "-", text.replace("\u2060", ""))         # a line broken at a hyphen is still one word
    return {w.strip(".,;:()|'’\"").lower() for w in text.split()} - {""}


def source_words(r) -> set[str]:
    """Every word the YAML puts on the page (markup and URLs removed)."""
    out: list[str] = []
    def walk(x, key=""):
        if isinstance(x, dict):
            for k, v in x.items():
                if k not in ("url", "link", "icon") and not k.startswith("_") and not (k == "name" and x.get("display")):   # a key, not printed
                    walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif x is not None:
            out.append(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", str(x)).replace("**", ""))
    walk(r)
    return _words(" ".join(out))


def check(pdf: Path, out: Path, expect: set[str] | None = None) -> dict:
    text = parsed(pdf)
    out.write_text(text, encoding="utf-8")
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout)[1])
    lone = sum(1 for line in text.splitlines() if line.strip() in ("•", "●", "▪"))
    lost = sorted(expect - _words(text)) if expect else []       # glued or dropped words: "productengineering"
    fonts = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True).stdout
    return {"pages": pages, "lone_bullets": lone, "links": links(pdf), "words": len(text.split()), "lost": lost,
            "fonts_ok": all(f in fonts for f in ("Comfortaa-Bold", "SourceSansPro-Regular", "SourceSansPro-Bold"))}


def layout(pdf: Path) -> list[dict]:
    """Per page: how far down the text reaches (share of the page height), its line count and first lines."""
    bbox = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True).stdout
    out = []
    for i, (h, body) in enumerate(re.findall(r'<page width="[\d.]+" height="([\d.]+)">(.*?)</page>', bbox, re.S), 1):
        text = subprocess.run(["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(pdf), "-"], capture_output=True, text=True).stdout
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        out.append({"page": i, "reaches": round(max(map(float, re.findall(r'yMax="([\d.]+)"', body)), default=0) / float(h), 2),
                    "lines": len(lines), "first": lines[:8]})
    return out


def leaked(r: dict, text: str) -> list[str]:
    """Review notes (the reasons) that made it into the PDF's text: must always be none."""
    flat = " ".join(text.split())
    whys = [n["why"] for co in r["experience"] for ro in co["roles"] for p in ro["projects"] for n in p.get("_notes", [])]
    whys += [x["why"] for x in r.get("_left_out", [])]
    return [w for w in whys if w and " ".join(w.split())[:40] in flat]


def compare(other: Path, out: Path, pdf: Path = PDF) -> None:
    """The text a parser reads from each PDF, side by side; detached bullets in red."""
    def col(pdf: Path) -> str:
        lines = parsed(pdf).replace("\u2060", "").splitlines()
        return "\n".join(f'<span class="bad">{html.escape(l)}</span>' if l.strip() in ("•", "●", "▪") else html.escape(l)
                         for l in lines)
    out.write_text(f"""<!doctype html><meta charset="utf-8"><title>What a parser reads</title>
<style>body{{font:13px/1.45 ui-monospace,Menlo,monospace;margin:16px;background:#fff;color:#111}}
.g{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}h2{{font:600 15px system-ui;margin:0 0 8px}}
pre{{white-space:pre-wrap;margin:0;padding:12px;background:#f6f6f3;border-radius:8px}}.bad{{background:#ffd9d6;color:#a01010}}</style>
<div class="g"><div><h2>{html.escape(other.name)}</h2><pre>{col(other)}</pre></div>
<div><h2>{pdf.name} (built from resume.yaml)</h2><pre>{col(pdf)}</pre></div></div>""", encoding="utf-8")


def urls(r) -> set[str]:
    if isinstance(r, dict):
        return set().union(*(({v} if k in ("url", "link") and v else urls(v)) for k, v in r.items())) if r else set()
    if isinstance(r, list):
        return set().union(*(urls(v) for v in r)) if r else set()
    return set()


if __name__ == "__main__":
    args = sys.argv[1:]
    version = args[args.index("--for") + 1] if "--for" in args else ""
    others = [a for a in args if a.endswith(".pdf")]
    r = load(version)
    out = HERE / "versions" / "out" if version else HERE
    out.mkdir(parents=True, exist_ok=True)
    stem = version or "resume"
    pdf, parsed_txt = out / f"{stem}.pdf", out / f"{stem}.parsed.txt"
    build(r, out / f"{stem}.html", pdf)
    c = check(pdf, parsed_txt, source_words(r))
    print(f"{pdf.name}: {c['pages']} pages · {c['words']} words · {c['links']} links · {c['lone_bullets']} bullets detached from their line"
          f" · {len(c['lost'])} words a parser can't read{': ' + ', '.join(c['lost'][:12]) if c['lost'] else ''}")
    for other in others:
        o = check(Path(other), out / (Path(other).stem + ".parsed.txt"))
        print(f"{Path(other).name}: {o['pages']} pages · {o['words']} words · {o['links']} links · {o['lone_bullets']} bullets detached from their line")
        compare(Path(other), out / "parse-compare.html", pdf)
        print(f"side by side: {(out / 'parse-compare.html').relative_to(HERE)}")
    assert not leaked(r, parsed_txt.read_text()), "a review note leaked into the PDF"
    assert c["fonts_ok"], "the resume fonts didn't load (fell back to Arial) — check fonts/fonts.css paths"
    assert c["pages"] <= 2, "more than 2 pages"
    assert c["lone_bullets"] == 0, "a bullet is separated from its text — parsers will read broken lines"
    assert not c["lost"], "words from the resume don't come out of the PDF intact — see above"
    assert c["links"] >= len(urls(r)), f"only {c['links']} of {len(urls(r))} links made it into the PDF"
