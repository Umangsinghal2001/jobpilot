// The dashboard API (dashboard.py today, FastAPI tomorrow — same contract).
// Every POST carries X-JobPilot: the server refuses POSTs without it (a page on another site can't send it).

export type QA = { q: string; a: string; guess: boolean; note: string }
export type Job = {
  id: string; title: string; company: string; url: string; found: string; status: string; source: string
  ats: string; platform: string; track: string; resume: string; reason: string; cat: string; email: string
  unfilled: string[]; editable: boolean; answers: number; guesses: number; shot: string; log: boolean
  filled_at: string; cost: number; turns: number; recipe: number; tab: boolean
  posting: { status?: string; checked?: string; how?: string; quote?: string; reason?: string }; events: Record<string, string>
  note: string; tags: string[]; draft?: string
  where: { site: string; key: string; url: string; via: string }
  fit: { verdict?: 'fit' | 'maybe' | 'no'; reason?: string; quote?: string; at?: string }
  ask: { q?: string; evidence?: string; source?: string; at?: string }   // "Did you submit?": a closed tab, or an unclear email
  detected: { how?: string; evidence?: string; at?: string }   // how JobPilot learned the status: page, haiku or email
  inbox: { email: string; date: string; subject: string; outcome: string; quote: string }[]   // fit.py: does the description match the candidate's targeting?   // the site it's stuck on, and the aggregator it came through
  qa?: QA[]   // only from GET /api/job/{id} (Review); the state carries the count
}
export type Task = { id: number; label: string; job_id: string; agent: boolean; status: string
  started: string; ended: string; result: string; pid: number }
export type Answer = { key: string; label: string; variants: { answer: string; companies: string[] }[] }
export type Row = { key: string; submitted: number; heard: number; interviews: number; offers: number }
export type State = {
  today: string; live: boolean; managed: boolean; jobs: Job[]; active_jobs: string[]
  me: { name: string; phone: string; linkedin: string }
  funnel: { found: number; filled: number; submitted: number; replies: number; interviews: number }
  health: { chrome: boolean; browser: string; login: boolean | null; login_error: boolean; week_cost: number; last_run: string; checked: string }
  cats: Record<string, { title: string; hint: string }>
  memory: { answers: Answer[]; platforms: Record<string, string[]>; pending: Record<string, string[]>
    snapshots: { name: string; when: string; answers: number; notes: number }[] }
  usage: { days: { day: string; cost: number; runs: number; turns: number }[]; tracks: { track: string; cost: number; runs: number; turns: number }[]
    top: { company: string; title: string; cost: number; turns: number }[]; today: number; week: number; avg_cost: number; avg_turns: number }
  outcomes: { by_track: Row[]; by_platform: Row[]; submitted: number }
  settings: { min_battery: number; max_idle_min: number; daily_cap_usd: number; auto_detect: boolean }
  daily: { on: boolean; last_date: string; status_at: string; status: string; last_log: string[]; last_log_at: string }
  evals: { cases: number; history: { date: string; cases: number; pass: number; cost: number; turns: number; fails: string[] }[] }
  resumes: { master: string; tracks: { track: string; file: string; own: boolean; jobs: number }[] }
  files: Record<string, string>
  tasks: Task[]
}
export type Step = { s: number; tool: string; deny: boolean; what: string; out: string }
export type Reply = { message?: string; error?: string; task?: Task }

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export async function post(path: string, body: unknown = {}, raw?: Blob): Promise<Reply> {
  const r = await fetch(`/api/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': raw ? 'application/pdf' : 'application/json', 'X-JobPilot': '1' },
    body: raw ?? JSON.stringify(body),
  })
  return r.json().catch(() => ({ error: `The server answered ${r.status}` }))
}

// ── small shared helpers ──
export const DONE = ['applied', 'replied', 'interview', 'offer', 'rejected']
export const HEARD = ['replied', 'interview', 'offer', 'rejected']
export const days = (iso: string) => (iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 864e5) : null)
export const waited = (iso: string) => { const d = days(iso); return d === null ? '' : d === 0 ? 'today' : d === 1 ? '1 day' : `${d} days` }
export const ago = (iso: string) => {
  if (!iso) return ''
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 6e4)
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`
}
export const usd = (n: number) => `$${(n || 0).toFixed(2)}`
export const nice = (s: string) => (s || '').replaceAll('_', ' ').replace(/^\w/, (c) => c.toUpperCase())
export const PLATFORM: Record<string, string> = { greenhouse: 'Greenhouse', ashby: 'Ashby', lever: 'Lever', workday: 'Workday',
  company: 'Careers page', wellfound: 'Wellfound', linkedin: 'LinkedIn', 'google-form': 'Google Form', smartrecruiters: 'SmartRecruiters', kula: 'Kula' }
export const platformName = (j: Job) => PLATFORM[j.platform] ?? (j.ats ? j.ats.split(' (')[0].replace(/^\w/, (c) => c.toUpperCase()) : 'Unknown site')
export const trackName = (t: string) => ({ ai_engineer: 'AI engineer', senior_ai_engineer: 'Senior AI', lead_ai_engineer: 'Lead AI',
  fde_founding: 'FDE', data_scientist: 'Data scientist', ai_pm: 'AI PM' } as Record<string, string>)[t] ?? 'Master'
// newest found first: applying early has the best odds. Same day: the server's order (newest added first), sort is stable
export const byNewest = (a: Job, b: Job) => (a.found < b.found ? 1 : a.found > b.found ? -1 : 0)

/** The agent's run as six phases (what the ring shows), read from its tool log. */
export function phase(steps: Step[]): number {
  let p = 0
  for (const s of steps) {
    const t = s.tool
    if (t.startsWith('report_')) p = Math.max(p, 6)
    else if (t === 'take_screenshot') p = Math.max(p, 5)
    else if (t === 'file_upload') p = Math.max(p, 4)
    else if (['fill_fields', 'type', 'select_option', 'fill_form', 'click'].includes(t)) p = Math.max(p, 3)
    else if (['snapshot', 'navigate', 'greenhouse_questions'].includes(t)) p = Math.max(p, 2)
    else if (t === 'tabs') p = Math.max(p, 1)
  }
  return p
}

/** A step in plain words, for people rather than logs. */
export function plain(s: Step): string {
  const w = s.what
  switch (s.tool) {
    case 'tabs': return w.startsWith('http') ? 'Opened the job page' : 'Switched to its tab'
    case 'navigate': return `Went to ${w.replace(/^https?:\/\//, '').split('/')[0]}`
    case 'snapshot': return 'Read the page'
    case 'greenhouse_questions': return "Read the form's questions from Greenhouse"
    case 'fill_fields': return `Filled ${w} in one go${s.out ? ` (${s.out.split('.')[0]})` : ''}`
    case 'type': return `Typed ${w.split(':')[0] || 'an answer'}`
    case 'click': return `Clicked ${w || 'something'}`
    case 'select_option': return `Picked an option for ${w}`
    case 'file_upload': return 'Uploaded your resume'
    case 'take_screenshot': return 'Took the final screenshot'
    case 'report_filled': return 'Handed the filled form to you'
    case 'report_blocked': return 'Stopped and explained why'
    case 'find': return `Looked for “${w}”`
    case 'wait_for': return 'Waited for the page'
    default: return `${nice(s.tool)} ${w}`.trim()
  }
}
