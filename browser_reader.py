"""
Browser form reader (Playwright, headless Chromium).

For ATSes with no public API — Ashby, Lever, company career pages, embedded ATS
iframes. Loads the apply page, reveals the application form (clicks an "Apply"
button/tab if present), and extracts every visible field with its label, type,
required flag, and options (selects / radio / checkbox groups). Scans all frames.

Runs LOCALLY: the fetched URL leaves from this machine's network.
    JOBPILOT_STEALTH=1 skips the browser entirely.

    python3 browser_reader.py <apply-url>
"""
from __future__ import annotations

import json
import urllib.parse

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
APPLY_BUTTON = "a:has-text('Apply'), button:has-text('Apply'), [role=tab]:has-text('Application')"

# In-page extractor: every visible input/select/textarea -> {label,type,required,options}.
# Radio/checkbox inputs are grouped by name into one question with their labels as options.
JS_EXTRACT = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = el => (el && (el.innerText || el.textContent) || '').trim().replace(/\s+/g, ' ');
  const byFor = el => el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
  const labelFor = el => {
    const lf = byFor(el); if (lf) return txt(lf);
    const lb = el.closest('label'); if (lb) return txt(lb).replace(txt(el), '').trim();
    const al = el.getAttribute('aria-label'); if (al) return al.trim();
    const alb = el.getAttribute('aria-labelledby');
    if (alb) { const t = alb.split(' ').map(i => document.getElementById(i)).filter(Boolean).map(txt).join(' '); if (t) return t; }
    let node = el;
    for (let i = 0; i < 4 && node; i++) {
      node = node.parentElement; if (!node) break;
      const cand = node.querySelector('label, legend, [class*="abel"], h1,h2,h3,h4,h5,h6, p, span');
      if (cand && cand !== el && !cand.contains(el)) { const t = txt(cand); if (t && t.length < 300) return t; }
    }
    return el.getAttribute('placeholder') || el.name || '';
  };
  const out = [], seen = new Set(), groups = {};
  document.querySelectorAll('input, select, textarea').forEach(el => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type) || !vis(el)) return;
    if (type === 'radio' || type === 'checkbox') {
      const k = el.name || labelFor(el) || 'group';
      const g = (groups[k] ||= { label: '', type, opts: [], req: false });
      const own = el.closest('label') ? txt(el.closest('label')) : (byFor(el) ? txt(byFor(el)) : '');
      if (own) g.opts.push(own);
      g.req ||= el.required;
      const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend');
      if (lg && !g.label) g.label = txt(lg);
      if (!g.label) g.label = k;
      return;
    }
    const label = labelFor(el); if (!label) return;
    const key = label.toLowerCase(); if (seen.has(key)) return; seen.add(key);
    const req = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label);
    const options = type === 'select' || type === 'select-one'
      ? [...el.options].map(o => o.text.trim()).filter(Boolean) : [];
    out.push({ label: label.replace(/\s*\*$/, ''), type, required: !!req, options });
  });
  for (const g of Object.values(groups)) if (g.opts.length)
    out.push({ label: g.label.replace(/\s*\*$/, ''), type: g.type + '_group', required: g.req, options: g.opts });
  return out;
}
"""


# Aggregator / listing pages (Remotive, Techstars, recruiter boards) hold no form — the
# real one is behind an outbound "Apply" link. Find it: prefer mailto (so we can report it),
# then a link to a different host / known ATS, then any apply-ish link on the page.
JS_FIND_APPLY = r"""
() => {
  const isApply = el => /apply|application|submit (your )?(cv|resume)/i
      .test(((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')));
  const atsRe = /greenhouse|lever\.co|ashbyhq|workable|smartrecruiters|rippling|jobvite|bamboohr|docs\.google\.com\/forms|applytojob|breezy|recruitee|myworkdayjobs|icims|taleo|successfactors|zohorecruit|freshteam/i;
  const links = [...document.querySelectorAll('a[href]')];
  const cands = links.filter(a => isApply(a) || atsRe.test(a.href) || /\/apply/i.test(a.href));
  const mail = cands.find(a => a.href.startsWith('mailto:'));
  if (mail) return mail.href;
  const ext = cands.find(a => { try { return new URL(a.href).host !== location.host; } catch (e) { return false; } });
  return (ext || cands[0] || {}).href || '';
}
"""


def _ats(url: str) -> str:
    h = urllib.parse.urlparse(url).netloc.lower()
    for k in ("ashbyhq", "lever", "greenhouse", "workable", "smartrecruiters", "rippling", "jobvite", "bamboohr"):
        if k in h:
            return k
    return "company"


def _body_text(page) -> str:
    """Visible page text (the listing / JD) for the drafter — capped to keep prompts small."""
    try:
        return page.evaluate(
            "() => (document.body && document.body.innerText || '')"
            ".replace(/\\s+/g, ' ').trim().slice(0, 6000)"
        )
    except Exception:  # noqa: BLE001
        return ""


def _extract_all_frames(page) -> list[dict]:
    fields: list[dict] = []
    for fr in page.frames:                      # main frame + embedded ATS iframes
        try:
            fields += fr.evaluate(JS_EXTRACT)
        except Exception:  # noqa: BLE001 — cross-origin/detached frames
            continue
    # dedupe by label across frames
    seen, out = set(), []
    for f in fields:
        k = f["label"].lower()
        if k and k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _no_form_reason(final_url: str, title: str, text: str) -> str:
    """Precise, actionable reason when no form could be read (shown on the dashboard)."""
    t, ti, u = (text or "").lower(), (title or "").lower(), (final_url or "").lower()
    if "ray id" in t or "security verification" in t or "checking your browser" in t or "just a moment" in ti:
        return "bot-protected page (Cloudflare) — open it manually"
    if "docs.google.com" in u and ("sign-in" in ti or "sign in" in ti):
        return "Google Form requires your Google sign-in — open it in your browser"
    if "sign in" in ti or "log in" in ti or "/login" in u:
        return "login required — open it in your browser"
    if u.rstrip("/").endswith("/jobs") or u.rstrip("/").endswith("/careers"):
        return "job board root — open it and pick the role"
    return "no form found (JS-rendered / unusual page) — open it manually"


def read_with_browser(url: str, timeout_ms: int = 30000) -> dict:
    empty = {"ats": _ats(url), "status": "needs_you", "title": None, "questions": [],
             "reason": "", "jd": ""}
    jd = ""
    email_reason = ""
    final_url, final_text, title = "", "", ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA, viewport={"width": 1280, "height": 2000})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            jd = _body_text(page)                # the overview page carries the full JD
            fields = _extract_all_frames(page)
            if len(fields) < 3:                  # form probably behind an Apply button/tab
                try:
                    btn = page.locator(APPLY_BUTTON).first
                    if btn.count() and btn.is_visible():
                        btn.click(timeout=4000)
                        page.wait_for_timeout(2500)
                        fields = _extract_all_frames(page)
                except Exception:  # noqa: BLE001
                    pass
            if len(fields) < 3:                  # aggregator / listing page -> follow the outbound Apply link (1 hop)
                target = page.evaluate(JS_FIND_APPLY) or ""
                if target.startswith("mailto:"):
                    email_reason = f"apply by email: {target[7:].split('?')[0]}"
                elif target and target.split("#")[0] != url.split("#")[0]:
                    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(2500)
                    fields = _extract_all_frames(page)
                    url = target                  # label the REAL ats + enable the Ashby fallback below
            if len(fields) < 3 and "ashbyhq" in url and not email_reason:   # Ashby: /application route
                page.goto(url.rstrip("/") + "/application", wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2500)
                fields = _extract_all_frames(page)
            title = page.title()
            # after an Apply-click / /application nav the page may be form-only — keep the longer text
            later = _body_text(page)
            final_url, final_text = page.url, later      # for a precise needs-you reason
            if len(later) > len(jd):
                jd = later
        except PWTimeout:
            browser.close()
            return {**empty, "reason": "page load timed out"}
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
    if email_reason:
        return {**empty, "jd": jd, "reason": email_reason}
    if len(fields) < 3:
        return {**empty, "jd": jd, "reason": _no_form_reason(final_url, title, final_text)}
    return {"ats": _ats(url), "status": "ok", "title": title, "questions": fields,
            "reason": "", "jd": jd}


if __name__ == "__main__":
    import sys
    print(json.dumps(read_with_browser(sys.argv[1]), indent=2, ensure_ascii=False))
