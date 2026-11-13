import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { useApp } from './store'
import { getJSON, platformName, trackName, waited, days, byNewest, usd, type Job, type Task } from './api'

export function TopBar({ onSearch }: { onSearch: () => void }) {
  const { state } = useApp()
  const held = state?.jobs.filter((j) => ['needs-you', 'form-ready'].includes(j.status)).length ?? 0
  const pending = state ? Object.values(state.memory.pending).flat().length : 0
  const running = state?.tasks.some((t) => t.status === 'running' && t.agent)
  const ready = state?.jobs.filter((j) => j.status === 'ready-to-submit').length ?? 0
  const links: [string, string, React.ReactNode?][] = [
    ['/', 'Today'], ['/live', 'Live', running ? <span className="badge live">on</span> : null],
    ['/review', 'Review', ready ? <span className="badge" style={{ background: 'var(--surface-mid)', color: 'var(--ink-soft)' }}>{ready}</span> : null],
    ['/pipeline', 'Pipeline'], ['/held', 'Held', held ? <span className="badge">{held}</span> : null],
    ['/memory', 'Memory', pending ? <span className="badge">{pending}</span> : null], ['/insights', 'Insights'], ['/knowledge', 'Knowledge'],
  ]
  return (
    <header className="topbar">
      <Link to="/" className="brand">JobPilot</Link>
      <nav className="nav" aria-label="Main">
        {links.map(([to, label, extra]) => (
          <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>{label}{extra}</NavLink>
        ))}
      </nav>
      <div className="topbar-end">
        <button type="button" className="search-btn" onClick={onSearch}>
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          Search or run<span style={{ color: 'var(--outline)' }}>⌘K</span>
        </button>
        <NavLink to="/control" className="avatar" aria-label="Control: runs, browser, schedule">{(state?.me.name || 'U')[0]}</NavLink>
      </div>
    </header>
  )
}

export function Ring({ value, total, size = 168, label }: { value: number; total: number; size?: number; label?: string }) {
  const r = size / 2 - 12, c = 2 * Math.PI * r, f = total ? Math.min(value / total, 1) : 0
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label ?? `${value} of ${total}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-highest)" strokeWidth="5" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--primary)" strokeWidth="5" strokeLinecap="round"
        strokeDasharray={`${c * f} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: 'stroke-dasharray .6s ease' }} />
      <text x="50%" y="50%" dy="0.35em" textAnchor="middle" fontFamily="Manrope, sans-serif" fontWeight="700" fontSize={size / 5} fill="var(--primary)">
        {label ?? `${value} of ${total}`}
      </text>
    </svg>
  )
}

export const JobRow = memo(function JobRow({ job, lift, action = 'Review' }: { job: Job; lift?: boolean; action?: string }) {
  const d = days(job.filled_at) ?? 0
  return (
    <article className={`job-row${lift ? ' lift' : ''}`}>
      <div className="who"><div className="company">{job.company}</div><div className="role">{job.title}</div></div>
      <div className="pills">
        <span className={`pill${lift ? '' : ' white'}`}>{platformName(job)}</span>
        <span className={`pill${lift ? '' : ' white'}`}>{trackName(job.track)} resume</span>
        <span className={`pill${lift ? '' : ' white'}`}>{job.guesses ? `${job.guesses} answer${job.guesses > 1 ? 's' : ''} to check` : `${job.answers} answers`}</span>
        {job.posting.status === 'closed' && <span className="pill peach">Posting closed</span>}
      </div>
      <span className={`pill${d >= 5 ? ' peach' : ''}`}>{waited(job.filled_at)} waiting</span>
      <Link to={`/review/${job.id}`} className={`btn btn-sm ${lift ? 'btn-primary' : 'btn-secondary'}`}>{action}</Link>
    </article>
  )
})

export const readyJobs = (jobs: Job[]) => jobs.filter((j) => j.status === 'ready-to-submit').sort(byNewest)

/** "Did you submit?": jobs whose tab you closed, or an email the inbox agent couldn't place. One click settles each. */
export function AskList({ jobs }: { jobs: Job[] }) {
  const { act } = useApp()
  const asks = jobs.filter((j) => j.ask?.q).sort((a, b) => ((a.ask.at ?? '') < (b.ask.at ?? '') ? 1 : -1))
  if (!asks.length) return null
  return (
    <section className="section" aria-labelledby="ask-h">
      <div className="section-head"><h2 id="ask-h" className="title-lg">Did you submit?</h2><span className="pill peach">{asks.length}</span></div>
      <p className="muted" style={{ margin: 0, maxWidth: 720 }}>JobPilot couldn't tell on its own. Your answer is the record.</p>
      <div className="list">
        {asks.map((j) => (
          <div key={j.id} className="line">
            <div className="who" title={`${j.company} · ${j.title}`}><b>{j.company}</b> <span className="muted">· {j.title}</span></div>
            <div className="todo" title={j.ask.evidence ? `“${j.ask.evidence}”` : ''}>{j.ask.q}{j.ask.evidence ? ` “${j.ask.evidence.slice(0, 80)}”` : ''}</div>
            <span className="tag" style={{ cursor: 'default' }}>{j.ask.source === 'email' ? 'from an email' : 'tab closed'}</span>
            <div className="acts">
              <button className="btn btn-sm btn-primary" onClick={() => act(`status/${j.id}`, { status: 'applied' })}>Submitted</button>
              <button className="btn btn-sm btn-secondary" onClick={() => act(`status/${j.id}`, { status: 'ready-to-submit' })}>Not yet</button>
              <button className="btn btn-sm btn-quiet" onClick={() => act(`status/${j.id}`, { status: 'skipped' })}>Skipped</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

/** Confirm before anything that spends agent usage. */
export function confirmRuns(what: string, n: number, avgCost: number, avgTurns: number) {
  return window.confirm(`${what}\n\n${n} agent run${n === 1 ? '' : 's'}, about ${Math.round((avgTurns || 20) * n)} turns, ≈ ${usd((avgCost || 1) * n)} API-equivalent.\nYour Claude plan isn't billed per token, but this counts toward its limits.`)
}

export function LogDialog({ task, onClose }: { task: Task | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const [log, setLog] = useState('')
  const [t, setT] = useState<Task | null>(task)
  const { act } = useApp()
  useEffect(() => {
    if (!task) return ref.current?.close()
    ref.current?.showModal()
    let stop = false
    const tick = async () => {
      const r = await getJSON<{ task: Task; log: string }>(`/api/task/${task.id}`).catch(() => null)
      if (stop || !r) return
      setLog(r.log); setT(r.task)
      if (r.task.status === 'running') window.setTimeout(tick, 2000)
    }
    tick()
    return () => { stop = true }
  }, [task])
  return (
    <dialog ref={ref} className="sheet-dialog" onClose={onClose}>
      <div className="dialog-body">
        <div className="section-head"><h2 className="title-md">{t?.label}</h2><span className="muted">{t?.status}</span>
          <span style={{ marginLeft: 'auto' }} className="row-actions">
            {t?.status === 'running' && t.pid ? <button className="btn btn-sm btn-danger" onClick={() => window.confirm('Stop this task? Forms already filled stay open.') && act(`cancel/${t.id}`)}>Stop</button> : null}
            <button className="btn btn-sm btn-secondary" onClick={() => ref.current?.close()}>Close</button>
          </span></div>
        <pre className="log">{log || 'No output yet.'}</pre>
      </div>
    </dialog>
  )
}

type Cmd = { label: string; hint?: string; run: () => void }
export function Palette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { state, act } = useApp()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  useEffect(() => { if (open) { setQ(''); setI(0) } }, [open])
  const items = useMemo<Cmd[]>(() => {
    if (!state) return []
    const go = (to: string) => () => { nav(to); onClose() }
    const run = (path: string, body?: unknown) => () => { act(path, body); onClose() }
    const actions: Cmd[] = [
      { label: 'Find new jobs', hint: 'discovery agent', run: run('discover') },
      { label: 'Fill new jobs', hint: `${state.jobs.filter((j) => j.status === 'new').length} new`, run: run('fill', {}) },
      { label: 'Check postings', hint: 'free', run: run('check') },
      { label: 'Detect submissions now', run: run('detect') },
      { label: 'Check my inbox', hint: 'reads your job emails', run: run('inbox') },
      { label: 'Reconnect Gmail', hint: 'opens Google’s approval page', run: run('gmail-auth') },
      { label: 'Restart the agent Chrome', run: run('browser/restart') },
      { label: 'Open my normal Chrome', run: run('browser/normal') },
      { label: 'Watch the agents', run: go('/live') }, { label: 'Held for you', run: go('/held') },
      { label: 'Memory', run: go('/memory') }, { label: 'Insights', run: go('/insights') }, { label: 'Control', run: go('/control') },
    ]
    const s = q.trim().toLowerCase()
    const jobs: Cmd[] = state.jobs.filter((j) => s && `${j.company} ${j.title}`.toLowerCase().includes(s)).slice(0, 8)
      .map((j) => ({ label: `${j.company}, ${j.title}`, hint: j.status.replaceAll('-', ' '), run: go(`/review/${j.id}`) }))
    return [...jobs, ...actions.filter((a) => !s || a.label.toLowerCase().includes(s))]
  }, [state, q, act, nav, onClose])
  if (!open) return null
  return (
    <div className="palette" onClick={onClose}>
      <div className="palette-box" role="dialog" aria-label="Search and commands" onClick={(e) => e.stopPropagation()}>
        <input autoFocus aria-label="Search jobs or commands" placeholder="Search jobs, or type a command" value={q}
          onChange={(e) => { setQ(e.target.value); setI(0) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setI((x) => Math.min(x + 1, items.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setI((x) => Math.max(x - 1, 0)) }
            if (e.key === 'Enter') items[i]?.run()
            if (e.key === 'Escape') onClose()
          }} />
        {items.map((c, k) => (
          <button key={c.label} className={`palette-item${k === i ? ' on' : ''}`} onMouseEnter={() => setI(k)} onClick={c.run}>
            <span style={{ flexGrow: 1 }}>{c.label}</span>{c.hint && <span className="small muted">{c.hint}</span>}
          </button>
        ))}
        {!items.length && <div className="empty">Nothing matches “{q}”.</div>}
      </div>
    </div>
  )
}
