import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../store'
import { confirmRuns } from '../components'
import { platformName, waited, byNewest, type Job } from '../api'

type Col = { key: string; title: string; statuses: string[]; drop?: string; hint: string }
const COLS: Col[] = [
  { key: 'new', title: 'New', statuses: ['new'], drop: 'new', hint: 'Found, not filled yet' },
  { key: 'held', title: 'Held for you', statuses: ['needs-you', 'form-ready'], hint: 'Waiting on something only you can do' },
  { key: 'ready', title: 'Ready to submit', statuses: ['ready-to-submit'], drop: 'ready-to-submit', hint: 'Filled, waiting for your review' },
  { key: 'applied', title: 'Submitted', statuses: ['applied'], drop: 'applied', hint: 'Drop here when you submit' },
  { key: 'replied', title: 'Heard back', statuses: ['replied'], drop: 'replied', hint: 'Drop here when they reply' },
  { key: 'interview', title: 'Interview and offer', statuses: ['interview', 'offer'], drop: 'interview', hint: 'Drop here for an interview' },
]
const SIDE: [string, string][] = [['skipped', 'Skipped'], ['rejected', 'Rejected'], ['closed', 'Closed postings']]
/** New jobs, best first: the fit check's verdict (fit.py), then newest. */
const FIT: { key: string; title: string; pill: string }[] = [
  { key: 'fit', title: 'Good fit', pill: 'mint' }, { key: 'maybe', title: 'Maybe', pill: '' }, { key: '', title: 'Not checked yet', pill: '' }]
const fitOf = (j: Job) => (j.fit.verdict === 'fit' || j.fit.verdict === 'maybe' ? j.fit.verdict : '')

export default function Pipeline() {
  const { state, act } = useApp()
  const s = state!
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [over, setOver] = useState('')
  const [tag, setTag] = useState('')
  const [showSide, setShowSide] = useState('')
  const match = (j: Job) => !q || `${j.company} ${j.title} ${j.note} ${j.tags.join(' ')}`.toLowerCase().includes(q.toLowerCase())
  const jobs = s.jobs.filter(match)
  const active = new Set(s.active_jobs)
  const toggle = (id: string) => setSel((x) => { const n = new Set(x); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const drop = (status: string) => async (e: React.DragEvent) => {
    e.preventDefault(); setOver('')
    const id = e.dataTransfer.getData('text/plain')
    if (id) await act(`status/${id}`, { status })
  }
  const bulk = async (what: string) => { await act('bulk', { ids: [...sel], do: what, tag }); if (what !== 'tag') setSel(new Set()) }

  const card = (j: Job) => (
    <article key={j.id} className={`card${sel.has(j.id) ? ' selected' : ''}`} draggable
      onDragStart={(e) => e.dataTransfer.setData('text/plain', j.id)}>
      <label className="check" style={{ fontSize: 14 }}>
        <input type="checkbox" checked={sel.has(j.id)} onChange={() => toggle(j.id)} aria-label={`Select ${j.company}`} />
        <span className="company" style={{ fontSize: 17 }}>{j.company}</span>
      </label>
      <Link to={`/review/${j.id}`} className="role" style={{ textDecoration: 'none' }}>{j.title}</Link>
      <div className="pills" style={{ fontSize: 12.5 }}>
        {active.has(j.id) && <span className="pill mint">Filling now</span>}
        <span className="pill">{platformName(j)}</span>
        {j.status === 'ready-to-submit' && <span className={`pill${(j.filled_at && (Date.now() - new Date(j.filled_at).getTime()) / 864e5 >= 5) ? ' peach' : ''}`}>{waited(j.filled_at)}</span>}
        {j.events.auto_detected && <span className="pill">noticed automatically</span>}
        {j.tags.map((t) => <span key={t} className="pill mint">{t}</span>)}
        {j.status === 'new' && j.fit.verdict && <span className={`pill ${j.fit.verdict === 'fit' ? 'mint' : ''}`} title={j.fit.quote ? `“${j.fit.quote}”` : ''}>{j.fit.verdict === 'fit' ? 'Good fit' : 'Maybe'}</span>}
      </div>
      {j.fit.reason && (j.status === 'new' || (j.status === 'skipped' && j.fit.verdict === 'no')) &&
        <span className="small muted" title={j.fit.quote ? `“${j.fit.quote}”` : ''}>{j.fit.verdict === 'no' ? 'Not a fit: ' : ''}{j.fit.reason}</span>}
      {j.status === 'closed' && j.posting.quote && <span className="small muted">“{j.posting.quote.slice(0, 110)}”</span>}
    </article>
  )

  return (
    <main className="main" style={{ maxWidth: 'none', gap: 32 }}>
      <section style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '16px 32px' }}>
        <div className="head" style={{ flex: '1 1 480px' }}>
          <span className="label">Pipeline</span>
          <h1 className="display" style={{ fontSize: 48 }}>Every application, every stage.</h1>
          <p className="lead">Drag a card to record what happened. Select several to act on them together.</p>
        </div>
        <label className="field" style={{ minWidth: 280 }}>Find a job<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Company, title, note or tag" /></label>
      </section>

      <div className="board">
        {COLS.map((c) => {
          const list = jobs.filter((j) => c.statuses.includes(j.status)).sort(byNewest)
          return (
            <section key={c.key} aria-label={c.title} className={`column${over === c.key ? ' over' : ''}`}
              onDragOver={c.drop ? (e) => { e.preventDefault(); setOver(c.key) } : undefined}
              onDragLeave={() => setOver('')} onDrop={c.drop ? drop(c.drop) : undefined}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}><h2 style={{ fontSize: 18 }}>{c.title}</h2><span className="muted">{list.length}</span></div>
              <span className="small muted">{c.hint}</span>
              {c.key === 'new' ? FIT.map((f) => {
                const part = list.filter((j) => fitOf(j) === f.key)
                return part.length ? (
                  <div key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
                      <b style={{ fontSize: 14 }}>{f.title}</b><span className="muted small">{part.length}</span>
                      {f.key && <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto', height: 32, padding: '0 12px', fontSize: 13 }}
                        onClick={() => confirmRuns(`Fill the ${part.length} ${f.title.toLowerCase()} jobs?`, part.length, s.usage.avg_cost, s.usage.avg_turns) && act('retry', { ids: part.map((j) => j.id), verb: 'Fill selected' })}>Fill {part.length}</button>}
                    </div>
                    {part.slice(0, 40).map(card)}
                    {part.length > 40 && <span className="small muted">and {part.length - 40} more — narrow it with search</span>}
                  </div>
                ) : null
              }) : <>
                {list.slice(0, 40).map(card)}
                {list.length > 40 && <span className="small muted">and {list.length - 40} more — narrow it with search</span>}
              </>}
            </section>
          )
        })}
      </div>

      <div className="row-actions">
        {SIDE.map(([st, label]) => {
          const n = jobs.filter((j) => j.status === st).length
          return (
            <button key={st} className={`btn btn-sm ${showSide === st ? 'btn-primary' : 'btn-secondary'}`}
              onDragOver={(e) => e.preventDefault()} onDrop={drop(st)} onClick={() => setShowSide(showSide === st ? '' : st)}>{label} {n}</button>
          )
        })}
        <span className="small muted">Drop a card on one of these too.</span>
      </div>
      {showSide && <div className="grid">{jobs.filter((j) => j.status === showSide).map(card)}</div>}

      {sel.size > 0 && (
        <div className="bulk" role="region" aria-label="Selected jobs">
          <b style={{ fontFamily: 'var(--display)' }}>{sel.size} selected</b>
          <button className="btn btn-sm btn-primary" onClick={() => confirmRuns(`Fill or retry ${sel.size} jobs with the agent?`, sel.size, s.usage.avg_cost, s.usage.avg_turns) && act('retry', { ids: [...sel], verb: 'Fill selected' }).then(() => setSel(new Set()))}>Retry with the agent</button>
          <button className="btn btn-sm btn-secondary" onClick={() => bulk('skipped')}>Skip</button>
          <button className="btn btn-sm btn-secondary" onClick={() => bulk('closed')}>Mark closed</button>
          <button className="btn btn-sm btn-secondary" onClick={() => bulk('new')}>Back to new</button>
          <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}><span className="visually-hidden" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden' }}>Tag</span>
            <input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="tag" style={{ width: 120, height: 36 }} /></label>
          <button className="btn btn-sm btn-secondary" disabled={!tag.trim()} onClick={() => bulk('tag')}>Add tag</button>
          <button className="btn btn-sm btn-quiet" onClick={() => setSel(new Set())}>Clear</button>
        </div>
      )}
    </main>
  )
}
