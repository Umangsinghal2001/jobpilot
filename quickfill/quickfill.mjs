// quickfill — fill a whole application form in ONE call, by visible label.
//   stdin : {"url": "<tab url>", "fields": [{"label", "kind", "value"}], "files_allowed": [abs paths]}
//   stdout: {"ok": n, "results": [{label, status, detail}]}
// kind: text | select | combobox | checkbox | radio | file
// Never clicks buttons or links, never presses Enter — it only types, picks options, checks boxes
// and sets file inputs. Final submission stays with the human (and the guard).
// Uses the same playwright-core build as @playwright/mcp 0.0.82 (works with Chrome 153 over CDP).
import { chromium } from 'playwright-core';

const SUBMIT_RE = /\b(submit|send)\b|\bfinish\b|\bcomplete (my |your |the )?application\b|\bconfirm (and|&) (apply|submit)\b/i;
const T = { timeout: 5000 };
const norm = s => (s || '').replace(/\*/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
const key = u => { try { const x = new URL(u); return x.host + x.pathname.replace(/\/$/, ''); } catch { return u; } };

const input = JSON.parse(await new Promise(r => { let d = ''; process.stdin.on('data', c => d += c); process.stdin.on('end', () => r(d)); }));
const browser = await chromium.connectOverCDP(process.env.CDP || 'http://localhost:9222', { noDefaults: true });
const pages = browser.contexts().flatMap(c => c.pages());
const want = key(input.url);
// newest match wins: the agent's tab is the one it just opened (the harness also closes stale re-run tabs)
let page = pages.findLast(p => key(p.url()) === want) || pages.findLast(p => key(p.url()).startsWith(want));   // only deeper pages of the form, never a parent page
if (!page && input.open) {                     // refill.py: the tab is gone — open the form again
  page = await (browser.contexts()[0] || await browser.newContext()).newPage();
  await page.goto(input.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  const now = key(page.url());
  if (!(now === want || now.startsWith(want))) {   // ATS bounced us: posting gone
    console.log(JSON.stringify({ ok: 0, error: `the form page redirected to ${page.url()} — the posting is probably closed` }));
    process.exit(0);
  }
}
if (!page) {
  console.log(JSON.stringify({ ok: 0, error: `no open tab for ${input.url}`, tabs: pages.map(p => p.url()) }));
  process.exit(0);
}

await page.bringToFront();   // background tabs are rAF-throttled -> clicks time out

async function first(loc) {
  const n = await loc.count();
  for (let i = 0; i < n; i++) if (await loc.nth(i).isVisible().catch(() => false)) return loc.nth(i);
  return n ? loc.first() : null;
}

async function find(label, role) {
  const tries = [page.getByLabel(label, { exact: true })];
  if (role) tries.push(page.getByRole(role, { name: label, exact: true }), page.getByRole(role, { name: label }));
  tries.push(page.getByLabel(label));
  for (const t of tries) { const el = await first(t); if (el) return el; }
  return null;
}

function pick(names, value) {
  const v = norm(value);
  const ns = names.map(n => [n, norm(n)]).filter(([n]) => !SUBMIT_RE.test(n));
  return (ns.find(([, x]) => x === v) || ns.find(([, x]) => x.startsWith(v))
    || ns.filter(([, x]) => x.includes(v)).sort((a, b) => a[1].length - b[1].length)[0] || [])[0];
}

async function combobox(el, value) {
  if (await el.evaluate(e => e.tagName === 'SELECT')) return select(el, value);
  // only ever click a real combobox/input — a button whose aria-label matches must never be clicked
  if (!(await el.evaluate(e => e.tagName === 'INPUT' || /combobox|listbox/.test(e.getAttribute('role') || ''))))
    return ['refused', 'element with that label is not a combobox'];
  await el.click(T);
  const editable = await el.isEditable().catch(() => false);
  for (const typed of editable ? [value.slice(0, 30), value.split(/[\s(+]/)[0], ''] : ['']) {
    if (editable) { await el.fill(''); if (typed) await el.pressSequentially(typed, { delay: 15 }); }
    await page.waitForTimeout(350);
    const opts = page.getByRole('option');
    const names = (await opts.allInnerTexts()).map(s => s.trim());
    const hit = pick(names, value);
    if (hit) {
      await opts.nth(names.indexOf(hit)).click(T);
      return ['ok', `picked "${hit}"`];
    }
    if (!typed) {
      await page.keyboard.press('Escape');
      return ['no_option', `no option matching "${value}". Options: ${names.slice(0, 20).join(' | ')}`];
    }
  }
}

async function select(el, value) {
  const labels = await el.evaluate(e => [...e.options].map(o => o.label));
  const hit = pick(labels, value);
  if (!hit) return ['no_option', `options: ${labels.slice(0, 20).join(' | ')}`];
  await el.selectOption({ label: hit }, T);
  return ['ok', `selected "${hit}"`];
}

async function radio(label, value) {
  for (const role of ['radiogroup', 'group']) {
    const g = await first(page.getByRole(role, { name: label }));
    if (g) {
      const names = (await g.getByRole('radio').evaluateAll(rs => rs.map(r => r.labels?.[0]?.innerText || r.getAttribute('aria-label') || r.value))).map(s => s.trim());
      const hit = pick(names, value);
      if (hit) { await g.getByRole('radio', { name: hit }).first().check(T); return ['ok', `chose "${hit}"`]; }
      return ['no_option', `radios: ${names.join(' | ')}`];
    }
  }
  // no group found: only safe if exactly ONE radio on the page has this name ("Yes" usually appears many times)
  const rs = page.getByRole('radio', { name: value, exact: true });
  if (await rs.count() === 1) { await rs.check(T); return ['ok', `chose "${value}" (the only radio with that name)`]; }
  return ['not_found', 'no radio group with that label'];
}

// kind "auto" (refill of a field the agent filled by hand): decide by what the label points at
async function auto(label, value) {
  for (const role of ['radiogroup', 'group']) {
    const g = await first(page.getByRole(role, { name: label }));
    if (g && await g.getByRole('radio').count()) return radio(label, value);
  }
  // report labels can be reworded ("Phone - Country", "Gender (Voluntary …)") — also try the core of the label
  const alts = [...new Set([label, label.replace(/\s*\([^)]*\)\s*/g, ' ').trim(), label.split(' - ').pop().trim()])].filter(Boolean);
  let el = null;
  for (const l of alts) if ((el = await find(l))) break;
  if (!el) return ['not_found', 'no element with that label'];
  const t = await el.evaluate(e => ({ tag: e.tagName, type: e.type, role: e.getAttribute('role') || '' }));
  if (t.tag === 'SELECT' || t.role === 'combobox') return combobox(el, value);
  if (t.type === 'checkbox') { await el.setChecked(!/^(no|false|0|unchecked)$/i.test(value), T); return ['ok', 'set']; }
  if (t.tag === 'INPUT' || t.tag === 'TEXTAREA' || t.role === 'textbox') { await el.fill(value, T); return ['ok', 'typed']; }
  return ['not_found', `label points at a ${t.tag.toLowerCase()}, not a form field`];
}

async function file(label, value) {
  if (!(input.files_allowed || []).includes(value)) return ['refused', 'only the provided resume paths may be uploaded'];
  const inputs = page.locator('input[type=file]');
  const n = await inputs.count();
  const want = norm(label).split(/[\s/]/)[0];               // "resume/cv" -> "resume"
  const info = [];
  for (let i = 0; i < n; i++) info.push(await inputs.nth(i).evaluate(e => {
    let near = '', p = e;
    for (let k = 0; k < 4 && p; k++, p = p.parentElement) near += ' ' + (p.innerText || '').slice(0, 200);
    const own = `${e.id} ${e.name} ${e.getAttribute('aria-label') || ''}`;
    // Ashby's "Autofill from resume" input re-parses the PDF and overwrites the form — never use it
    return { own, near, autofill: /autofill/i.test(own + ' ' + (e.closest('[class*=autofill]') ? 'autofill' : '')) };
  }));
  const order = [...info.keys()].filter(i => !info[i].autofill);
  const idx = order.find(i => norm(info[i].own).includes(want)) ?? order.find(i => norm(info[i].near).includes(want))
    ?? (order.length === 1 ? order[0] : undefined);
  if (idx === undefined) return ['not_found', `no file input near "${label}" (${n} file inputs on page)`];
  await inputs.nth(idx).setInputFiles(value);
  await page.waitForTimeout(800);
  const name = value.split('/').pop();
  const shown = await page.getByText(name, { exact: false }).count();
  return ['ok', shown ? `attached ${name}` : `set ${name} (filename not yet visible — verify)`];
}

const results = [];
// files first: some ATSes (Ashby "Autofill from resume") re-render the form after an upload,
// wiping anything typed before it.
const fields = [...(input.fields || [])].sort((a, b) => (b.kind === 'file') - (a.kind === 'file'));
let uploaded = false;
for (const f of fields) {
  if (uploaded && f.kind !== 'file') { await page.waitForLoadState('networkidle', T).catch(() => {}); await page.waitForTimeout(1500); uploaded = false; }
  const { label, kind = 'text' } = f;
  const value = String(f.value ?? '');
  let r;
  try {
    if (SUBMIT_RE.test(label)) r = ['refused', 'looks like a submit control'];
    else if (kind === 'file') r = await file(label, value);
    else if (kind === 'radio') r = await radio(label, value);
    else if (kind === 'auto') r = await auto(label, value);
    else {
      const el = await find(label, { text: 'textbox', combobox: 'combobox', select: 'combobox', checkbox: 'checkbox' }[kind]);
      if (!el) r = ['not_found', 'no element with that label'];
      else if (kind === 'combobox') r = await combobox(el, value);
      else if (kind === 'select') r = await select(el, value);
      else if (kind === 'checkbox') { await el.setChecked(!/^(no|false|0|unchecked)$/i.test(value), T); r = ['ok', 'set']; }
      else if (await el.evaluate(e => e.tagName === 'SELECT' || e.getAttribute('role') === 'combobox' && e.tagName !== 'TEXTAREA')) r = await combobox(el, value);
      else { await el.fill(value, T); r = ['ok', 'typed']; }
    }
  } catch (e) {
    r = ['error', String(e.message || e).split('\n')[0].slice(0, 200)];
  }
  if (kind === 'file' && r[0] === 'ok') uploaded = true;
  results.push({ label, kind, status: r[0], detail: r[1], value });
}
// read-back: a text value that didn't stick (re-render, controlled input) is reported, not trusted
await page.waitForTimeout(500);
for (const r of results) {
  if (r.status !== 'ok' || !(r.kind === 'text' || (r.kind === 'auto' && r.detail === 'typed'))) continue;
  const el = await find(r.label, 'textbox');
  const got = el ? await el.inputValue().catch(() => null) : null;
  const digits = s => s.replace(/\D/g, '');                 // phones get reformatted: 98765 43210
  const same = norm(got) === norm(r.value) || (/\d{6,}/.test(r.value) && digits(got || '').endsWith(digits(r.value).slice(-10)));
  if (got !== null && !same)
    Object.assign(r, { status: 'lost', detail: `value did not stick (now "${(got || '').slice(0, 60)}") — refill it` });
}
for (const r of results) delete r.value;
console.log(JSON.stringify({ ok: results.filter(r => r.status === 'ok').length, results }));
process.exit(0);   // ponytail: exit without browser.close() — the tab must stay open for the human
