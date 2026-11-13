"""
Memory for the ApplyAgent — what past applications taught us, fed into the next one.

  memory.json (human-editable — fix a wrong answer there and every future job uses the fix)
    answers   : normalized question label -> [{answer, companies, updated}]   (past VALUES, with context)
    platforms : ats -> [short how-to lessons]                                   ("Greenhouse: …")

Only `sure` + `reusable` answers are stored — never guesses, never company-specific essays.
Answers are SUGGESTIONS: the agent decides per job (visa sponsorship differs for a US vs India role).

    python3 memory.py            # print the platform notes the agent reads (answers come from recall())
    python3 memory.py --seed     # backfill from past ready-to-submit reports in the store
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

PATH = Path(__file__).parent / "memory.json"
MAX_LESSONS = 20       # per platform, newest kept
ATS = ("greenhouse", "ashby", "lever", "workday", "wellfound", "linkedin", "google-form", "smartrecruiters")
# report placeholders / annotations that aren't the literal typed value
NOT_A_VALUE = re.compile(r"\b(essay|custom|tailored|left blank|entered manually|letter)\b"
                         r"|\([^)]*\b(render|display|shown|format|greenhouse|ashby)\w*", re.I)   # "(displayed as …)" notes

SEED_LESSONS = {
    "greenhouse": [
        "Call greenhouse_questions first — it gives exact labels, required flags and options.",
        "Phone-country combobox is labelled \"Country\"; its option is \"India +91\".",
        "Every dropdown (country, yes/no, EEO) is a custom combobox — fill_fields kind=combobox handles them.",
        "Resume: fill_fields kind=file with label \"Resume/CV\" (no need to click Attach).",
        "Cover letter has no textbox until you click \"Enter manually\"; leave it unless required.",
    ],
    "ashby": [
        "Form lives at <job url>/application.",
        "Resume: fill_fields kind=file label \"Resume\" — never the \"Autofill from resume\" box.",
        "Text labels end with * when required; pass the label without the *.",
    ],
}


def norm(label: str) -> str:
    return re.sub(r"\s+", " ", label.replace("*", "")).strip().rstrip(":?.").strip().lower()


def ats_key(ats: str, url: str = "") -> str:
    s = f"{ats} {url}".lower().replace("_", " ").replace("google forms", "google-form").replace("google form", "google-form").replace("docs.google.com/forms", "google-form")
    return next((a for a in ATS if a in s or a.replace("-", "") in s), (ats or "other").lower().strip())


def load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except (OSError, ValueError):
        return {"answers": {}, "platforms": {k: list(v) for k, v in SEED_LESSONS.items()}}


MAX_SNAPS = 50


def _snaps() -> Path:
    return PATH.parent / "artifacts" / "memory-snapshots"


def _save(mem: dict) -> None:
    if PATH.exists():                      # snapshot before every change → the dashboard can undo any edit or run
        d = _snaps()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{datetime.now():%Y%m%d-%H%M%S-%f}.json").write_bytes(PATH.read_bytes())
        for old in sorted(d.glob("*.json"))[:-MAX_SNAPS]:
            old.unlink()
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, indent=2, ensure_ascii=False))
    tmp.replace(PATH)       # atomic: a crash never leaves half a file


def _names(company: str, text: str) -> bool:
    """Does text mention the company? Whole name, or its first word when that's distinctive (≥4 chars),
    as a whole word — so "AI Labs" doesn't match every "email"."""
    c = company.lower().strip()
    w = c.split()[0] if c else ""
    pats = [re.escape(c)] + ([rf"\b{re.escape(w)}\b"] if len(w) >= 4 else [])
    return bool(c) and any(re.search(p, text.lower()) for p in pats)


def _keep(company: str, f: dict) -> bool:
    a, lab = f.get("answer", "").strip(), f.get("label", "")
    return bool(a) and f.get("confidence") == "sure" and f.get("reusable", True) \
        and len(a) <= 200 and not NOT_A_VALUE.search(a) \
        and not _names(company, a + " " + lab) \
        and not re.search(r"\b(resume|cv|cover letter)\b", lab, re.I)   # files are chosen per job by track


def learn(company: str, report: dict, url: str = "", mem: dict | None = None,
          answers: bool = True, lessons_to: str | None = "platforms") -> dict:
    """Fold one FilledReport into memory. Sync + single write, so concurrent asyncio jobs can't interleave.
    The live flow splits it: after an agent run → lessons_to="pending", answers=False (you approve notes);
    when you mark the job submitted → answers=True, lessons_to=None (only answers you actually sent).
    --seed keeps the old all-in-one behaviour."""
    mem = mem or load()
    today = date.today().isoformat()
    for f in (report.get("fields") or []) if answers else []:
        if not _keep(company, f):
            continue
        variants = mem["answers"].setdefault(norm(f["label"]), [])
        v = next((x for x in variants if x["answer"].lower() == f["answer"].strip().lower()), None)
        if v is None:
            variants.insert(0, v := {"answer": f["answer"].strip(), "label": f["label"], "companies": []})
        if company not in v["companies"]:
            v["companies"] = (v["companies"] + [company])[-5:]
        v["updated"] = today
    # platform lessons must hold for ANY company on that ATS — a lesson naming this company is job-specific
    lessons = [s.strip() for s in report.get("lessons") or [] if s.strip() and not _names(company, s)]
    if lessons and lessons_to:
        key = ats_key(report.get("ats", ""), url)
        known = {c.lower() for c in mem["platforms"].get(key, []) + mem.setdefault("pending", {}).get(key, [])}
        cur = mem.setdefault(lessons_to, {}).setdefault(key, [])
        for s in lessons:
            if s.lower() not in known:
                cur.append(s)
                known.add(s.lower())
        _cap(mem, lessons_to, key)
    _save(mem)
    return mem


def _cap(mem: dict, where: str, key: str) -> None:
    cur, seeds = mem[where][key], set(SEED_LESSONS.get(key, []))
    while len(cur) > MAX_LESSONS:              # evict the oldest LEARNED lesson; seeds are pinned
        cur.remove(next((c for c in cur if c not in seeds), cur[0]))


# ── edits from the dashboard (each one load → change → atomic save) ─────────────

def review_lesson(ats: str, lesson: str, action: str) -> dict:
    """approve (pending → platforms) · reject (drop from pending) · delete (drop from platforms)."""
    mem = load()
    pend, plat = mem.setdefault("pending", {}).setdefault(ats, []), mem["platforms"].setdefault(ats, [])
    if action in ("approve", "reject") and lesson in pend:
        pend.remove(lesson)
        if action == "approve" and lesson not in plat:
            plat.append(lesson)
            _cap(mem, "platforms", ats)
    elif action == "delete" and lesson in plat:
        plat.remove(lesson)
    else:
        raise KeyError("no such lesson")
    _save(mem)
    return mem


def edit_answer(key: str, old: str, new: str | None) -> dict:
    """Change one remembered answer (new=None deletes it; an empty question disappears)."""
    mem = load()
    variants = mem["answers"].get(key) or []
    v = next((x for x in variants if x["answer"] == old), None)
    if v is None:
        raise KeyError("no such answer")
    if new is None or not new.strip():
        variants.remove(v)
        if not variants:
            del mem["answers"][key]
    else:
        v["answer"], v["updated"] = new.strip(), date.today().isoformat()
    _save(mem)
    return mem


def snapshots() -> list[dict]:
    """Newest first: [{name, when, answers, notes}] — what memory looked like BEFORE each change."""
    out = []
    for f in sorted(_snaps().glob("*.json"), reverse=True):
        try:
            m = json.loads(f.read_text())
        except ValueError:
            continue
        out.append({"name": f.stem, "when": datetime.strptime(f.stem, "%Y%m%d-%H%M%S-%f").isoformat(timespec="seconds"),
                    "answers": len(m.get("answers", {})), "notes": sum(map(len, m.get("platforms", {}).values()))})
    return out


def restore(name: str) -> dict:
    """Go back to a snapshot. The current memory is snapshotted first, so a restore can be undone too."""
    if not re.fullmatch(r"\d{8}-\d{6}-\d{6}", name):
        raise ValueError("bad snapshot name")
    mem = json.loads((_snaps() / f"{name}.json").read_text())
    _save(mem)
    return mem


def merge_answers(src: str, dst: str) -> dict:
    """Two labels for the same question ("Phone" / "Phone number"): fold src's answers into dst."""
    mem = load()
    if src == dst or src not in mem["answers"] or dst not in mem["answers"]:
        raise KeyError("pick two different questions")
    into = mem["answers"][dst]
    for v in mem["answers"].pop(src):
        same = next((x for x in into if x["answer"].lower() == v["answer"].lower()), None)
        if same:
            same["companies"] = list(dict.fromkeys(same["companies"] + v["companies"]))[-5:]
        else:
            into.append(v)
    _save(mem)
    return mem


def rename_platform(old: str, new: str) -> dict:
    """Fix a platform name (google_forms → google-form): its notes and pending notes move, merged without dupes."""
    mem = load()
    new = new.strip().lower()
    if not new or old == new:
        raise ValueError("give a different name")
    for where in ("platforms", "pending"):
        moved = mem.setdefault(where, {}).pop(old, [])
        cur = mem[where].setdefault(new, [])
        cur += [x for x in moved if x not in cur]
        if not cur:
            del mem[where][new]
    _save(mem)
    return mem


STOP = set("the a an and or of to for in on at your you is are be do does did what which how will would can could "
           "please this that with from as by any have has our we us it its if there per".split())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


def recall(questions: list[str], mem: dict | None = None, k: int = 3) -> str:
    """The agent's `recall` tool: for each form question, the candidate's answers to the most similar past questions.
    ponytail: shared words (Jaccard) miss paraphrases ("compensation" vs "CTC"); embeddings if the eval shows misses."""
    mem = mem or load()
    keys = [(key, _words(key)) for key in mem["answers"]]
    out = []
    for q in questions:
        qw = _words(q)
        hits = sorted(((len(qw & kw) / len(qw | kw), key) for key, kw in keys if qw & kw), reverse=True)[:k]
        lines = [f'"{q}"'] + [
            f'  {"close" if s >= .5 else "weak"}: "{mem["answers"][key][0]["label"].rstrip("*").strip()}" → ' + " | ".join(
                f'"{v["answer"]}" ({", ".join(v["companies"][-3:])}; {v.get("updated", "")})' for v in mem["answers"][key][:3])
            for s, key in hits]
        out.append("\n".join(lines if hits else lines + ["  no past answer"]))
    return "\n".join(out)


def platform_notes(mem: dict | None = None) -> str:
    """The approved how-tos per ATS (pending ones wait for the candidate's approval on the dashboard)."""
    mem = mem or load()
    return "\n\n".join(f"## {a}\n" + "\n".join(f"- {s}" for s in ls) for a, ls in mem["platforms"].items() if ls)


if __name__ == "__main__":
    import sys
    if "--seed" in sys.argv:
        import store
        mem = load()
        n = 0
        for j in store.list_jobs("ready-to-submit"):
            rep = json.loads(j.get("extra") or "{}").get("apply", {}).get("report") or {}
            if rep:
                mem = learn(j["company"], rep, j["url"], mem)
                n += 1
        _save(mem)
        print(f"seeded from {n} reports → {len(mem['answers'])} questions")
    # self-check: the filters that keep bad data out of memory
    assert norm("Email*") == norm("email") == "email"
    assert ats_key("", "https://job-boards.greenhouse.io/x/jobs/1") == "greenhouse"
    assert not _keep("Acme", {"label": "Why Acme?", "answer": "Because", "confidence": "sure"})
    assert not _keep("X Corp", {"label": "Code sample", "answer": "https://a.b", "confidence": "guess"})
    assert not _keep("X Corp", {"label": "Cover", "answer": "Custom essay: …", "confidence": "sure"})
    assert not _keep("X Corp", {"label": "Resume/CV*", "answer": "a.pdf", "confidence": "sure"})
    print(platform_notes())
    demo = {"answers": {"expected ctc": [{"answer": "X", "label": "Expected CTC", "companies": ["A"]}],
                        "last name": [{"answer": "S", "label": "Last Name", "companies": ["A"]}]}, "platforms": {}}
    r = recall(["Expected CTC (LPA)", "How soon can you join?"], demo)
    assert 'close: "Expected CTC" → "X"' in r and "no past answer" in r, r
    import tempfile
    PATH = Path(tempfile.mkdtemp()) / "memory.json"      # never touch the real memory in the self-check
    m = {"answers": {}, "platforms": {"ashby": list(SEED_LESSONS["ashby"]) + [f"tip {i}" for i in range(30)]}}
    m = learn("Acme Inc", {"ats": "ashby", "lessons": ["new tip", "Acme's form is short"]}, "", m)
    ash = m["platforms"]["ashby"]
    assert len(ash) == MAX_LESSONS and all(x in ash for x in SEED_LESSONS["ashby"]) and "new tip" in ash
    assert not any("Acme" in x for x in ash)
    assert _keep("AI Labs", {"label": "Email", "answer": "a@b.c", "confidence": "sure"})      # no "ai" ⊂ "email"
    assert not _keep("X", {"label": "Phone", "answer": "98765 (displayed as 9 by the intl formatter)", "confidence": "sure"})
    assert not _keep("Globex AI", {"label": "Why Globex?", "answer": "x", "confidence": "sure"})
    m = {"answers": {}, "platforms": {}}
    m = learn("Acme Inc", {"ats": "ashby", "lessons": ["pending tip"], "fields": [
        {"label": "Visa?", "answer": "No", "confidence": "sure"}]}, "", m, answers=False, lessons_to="pending")
    assert m["answers"] == {} and m["pending"]["ashby"] == ["pending tip"] and not m["platforms"].get("ashby")
    m = learn("Acme Inc", {"ats": "ashby", "lessons": ["pending tip"], "fields": [
        {"label": "Visa?", "answer": "No", "confidence": "sure"}]}, "", m, answers=True, lessons_to=None)
    assert m["answers"]["visa"][0]["answer"] == "No" and m["pending"]["ashby"] == ["pending tip"]
    _save(m)
    m = review_lesson("ashby", "pending tip", "approve")
    assert m["pending"]["ashby"] == [] and m["platforms"]["ashby"] == ["pending tip"]
    m = edit_answer("visa", "No", "Yes"); assert m["answers"]["visa"][0]["answer"] == "Yes"
    m = edit_answer("visa", "Yes", None); assert "visa" not in m["answers"]
    assert _keep("X Corp", {"label": "Visa sponsorship?", "answer": "No", "confidence": "sure", "reusable": True})
    assert ats_key("google_forms") == ats_key("Google Forms") == "google-form"
    m = {"answers": {"phone": [{"answer": "95", "label": "Phone", "companies": ["A"]}],
                     "phone number": [{"answer": "95", "label": "Phone number", "companies": ["B"]},
                                      {"answer": "+91 95", "label": "Phone number", "companies": ["C"]}]},
         "platforms": {"google_forms": ["t1"], "google-form": ["t2"]}, "pending": {"google_forms": ["p"]}}
    _save(m)
    m = merge_answers("phone number", "phone")
    assert "phone number" not in m["answers"] and m["answers"]["phone"][0]["companies"] == ["A", "B"] and len(m["answers"]["phone"]) == 2
    m = rename_platform("google_forms", "google-form")
    assert m["platforms"] == {"google-form": ["t2", "t1"]} and m["pending"] == {"google-form": ["p"]}
    before = snapshots()[1]          # [0] = just before the rename
    assert before["answers"] == 2 and restore(before["name"])["answers"].get("phone number")
    print("self-check ok")
