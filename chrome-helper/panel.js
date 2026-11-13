// The side panel: follows the active tab, asks the local JobPilot server which application it is, and shows
// that job's tailored resume against the base resume. It never reads or changes the page itself.
const API = "http://localhost:8765";
const app = document.getElementById("app");

// tiny DOM helper: text only, never HTML strings, so nothing from a job page can run here
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "onclick") e.onclick = v; else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}

function diffLine(ops) {
  return ops.map(([op, t], i) => {
    const sep = i ? " " : "";
    if (op === "del") return [sep, el("del", {}, t)];
    if (op === "ins") return [sep, el("ins", {}, t)];
    return sep + t;
  }).flat();
}

function show(...nodes) { app.replaceChildren(...nodes); }

function render(d) {
  if (!d.match) return show(el("p", { class: "muted" }, "This tab isn't a JobPilot application. Switch to an application tab in your review window."));
  const head = el("div", {},
    el("h1", {}, `${d.job.company} · ${d.job.title}`),
    d.none ? null : el("div", { class: "row" },
      el("button", { onclick: () => chrome.tabs.create({ url: `${API}/api/resume-pdf/${d.job.id}/tailored` }) }, "Open uploaded PDF"),
      el("button", { onclick: () => chrome.tabs.create({ url: `${API}/api/resume-pdf/${d.job.id}/base` }) }, "Open base PDF"),
      el("button", { onclick: () => chrome.tabs.create({ url: `${API}/api/resume-pdf/${d.job.id}/annotated` }) }, "Open annotated resume")));
  if (d.none) return show(head, el("p", { class: "muted" }, d.none));

  const parts = [head];
  const nums = d.facts.new_numbers;
  parts.push(nums.length
    ? el("div", { class: "warn" }, `Check these numbers: they appear in the tailored resume but not in your base resume: ${nums.join(", ")}`)
    : el("p", { class: "small ok" }, "Fact check: no numbers that aren't already in your base resume."));
  if (d.pdf.pages) parts.push(el("p", { class: "small muted" }, `${d.pdf.pages} pages · ${d.pdf.words} words · read back cleanly by the parser check`));

  parts.push(el("h2", {}, "Title"));
  parts.push(d.title.old === d.title.new ? el("p", { class: "muted" }, "Unchanged")
    : el("div", { class: "card" }, el("del", {}, d.title.old), el("br"), el("ins", {}, d.title.new)));

  parts.push(el("h2", {}, "Profile"));
  parts.push(d.profile ? el("div", { class: "card" }, ...diffLine(d.profile)) : el("p", { class: "muted" }, "Unchanged"));

  parts.push(el("h2", {}, "Projects"));
  for (const p of d.projects) {
    const counts = { kept: 0, reworded: 0, new: 0 };
    p.bullets.forEach(b => counts[b.kind]++);
    const card = el("div", { class: "card" },
      el("h3", {}, p.heading,
        p.heading !== p.name ? el("span", { class: "tag" }, "renamed") : null,
        p.moved_up ? el("span", { class: "tag" }, "moved up") : null),
      el("p", { class: "small muted" }, `${counts.reworded} reworded · ${counts.kept} as before · ${p.dropped.length} dropped`),
      el("ul", {}, p.bullets.map(b => {
        const li = b.kind === "reworded" ? el("li", {}, ...diffLine(b.diff))
          : el("li", { class: b.kind }, b.kind === "new" ? el("ins", {}, b.text) : b.text);
        if (b.note && b.note.src) li.append(el("div", { class: "small muted" },   // the agent's reason; never on the resume
          [b.note.why || "no reason given", b.note.src, b.note.status].filter(Boolean).join(" · ")));
        return li;
      })));
    if (p.dropped.length) card.append(el("details", {}, el("summary", {}, `${p.dropped.length} dropped bullet(s)`),
      el("ul", {}, p.dropped.map(t => el("li", {}, el("del", {}, t))))));
    parts.push(card);
  }
  if (d.left_out && d.left_out.length) parts.push(el("div", { class: "card" }, el("h3", {}, "Left out, and why"),
    el("ul", {}, d.left_out.map(x => el("li", {}, el("b", {}, x.what), `: ${x.why || "no reason given"}`)))));
  else if (d.hidden_projects.length) parts.push(el("p", { class: "small muted" }, `Left out: ${d.hidden_projects.join(", ")}`));

  parts.push(el("h2", {}, "Skills"));
  parts.push(el("div", { class: "card" },
    d.skills.added.length ? el("div", {}, d.skills.added.map(s => el("span", { class: "chip add" }, s))) : null,
    d.skills.removed.length ? el("div", {}, d.skills.removed.map(s => el("span", { class: "chip rm" }, s))) : null,
    el("p", { class: "small muted" }, `${d.skills.kept} kept as before`)));

  const other = Object.entries(d.other).filter(([, v]) => v !== "unchanged");
  parts.push(el("h2", {}, "Other sections"));
  parts.push(el("p", { class: "muted" }, other.length ? other.map(([k, v]) => `${k}: ${v}`).join(" · ") : "Achievements, education and certificates unchanged"));
  show(...parts);
}

let lastUrl = "";
async function refresh() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab && tab.url || "";
  if (url === lastUrl) return;
  lastUrl = url;
  if (!/^https?:/.test(url) || url.startsWith(API)) return render({ match: false });
  show(el("p", { class: "muted" }, "Loading…"));
  try {
    const r = await fetch(`${API}/api/resume-diff?url=${encodeURIComponent(url)}`);
    render(await r.json());
  } catch (e) {
    show(el("p", { class: "muted" }, "Can't reach the JobPilot server on localhost:8765. Is it running?"));
  }
}

chrome.tabs.onActivated.addListener(refresh);
chrome.tabs.onUpdated.addListener((_id, info, tab) => { if (tab.active && (info.url || info.status === "complete")) refresh(); });
chrome.windows.onFocusChanged.addListener(refresh);
refresh();

// Prompt editor: the ApplyAgent's two prompt files, through the dashboard's own file API (it keeps a backup per save).
// It sits outside #app so following the tabs never wipes an unsaved edit.
const PROMPTS = { apply_prompt: "Filling the form", tailor_prompt: "Tailoring the resume" };
const box = document.getElementById("prompts");
let open = "", dirty = false;

async function editPrompt(name) {
  if (dirty && !confirm("Discard your unsaved edit?")) return;
  open = open === name ? "" : name; dirty = false;
  if (!open) return drawPrompts();
  try {
    const f = await (await fetch(`${API}/api/file/${name}`)).json();
    drawPrompts(f.name, f.text);
  } catch (e) { drawPrompts("", "", "Can't reach the JobPilot server."); }
}

async function savePrompt(text, status) {
  status.textContent = "Saving…";
  try {
    const r = await fetch(`${API}/api/file/${open}`, { method: "POST",
      headers: { "Content-Type": "application/json", "X-JobPilot": "1" }, body: JSON.stringify({ text }) });
    const out = await r.json();
    status.textContent = out.message || out.error || `Save failed (${r.status})`;
    if (r.ok) dirty = false;
  } catch (e) { status.textContent = "Can't reach the JobPilot server; not saved."; }
}

function drawPrompts(file = "", text = "", err = "") {
  const status = el("p", { class: "small muted" }, err || (file && `prompts/${file} · the next agent run reads it`));
  const kids = [el("h2", {}, "Agent prompts"),
    el("div", { class: "row" }, Object.entries(PROMPTS).map(([k, label]) =>
      el("button", { class: open === k ? "on" : "", onclick: () => editPrompt(k) }, label)))];
  if (open && file) {
    const ta = el("textarea", { spellcheck: "false" });
    ta.value = text;
    ta.oninput = () => { dirty = true; };
    kids.push(ta, el("div", { class: "row" }, el("button", { onclick: () => savePrompt(ta.value, status) }, "Save")), status);
  } else if (err) kids.push(status);
  box.replaceChildren(...kids);
}
drawPrompts();
