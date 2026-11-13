import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useApp } from '../store'
import { readyJobs } from '../components'
import { getJSON, plain, platformName, trackName, type Job, type QA, type Step } from '../api'

const norm = (s: string) => s.replaceAll('*', '').replace(/\s+/g, ' ').trim().replace(/[:?.]+$/, '').trim().toLowerCase()

export default function Review() {
  const { state, act } = useApp()
  const s = state!
  const nav = useNavigate()
  const { id } = useParams()
  const queue = readyJobs(s.jobs)
  const job = s.jobs.find((j) => j.id === id) ?? queue[0]
  const at = job ? queue.findIndex((j) => j.id === job.id) : -1
  const go = (k: number) => queue[k] && nav(`/review/${queue[k].id}`)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest('input, textarea')) return
      if (e.key === 'j') go(at + 1)
      if (e.key === 'k') go(at - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // the answers aren't in the pushed state (too big); fetch this job's, again whenever the state changes
  const [full, setFull] = useState<Job | null>(null)
  useEffect(() => {
    if (!job) return
    let on = true
    getJSON<Job>(`/api/job/${job.id}`).then((j) => on && setFull(j)).catch(() => {})
    return () => { on = false }
  }, [job?.id, state])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!job) return <main className="main"><div className="empty">Nothing is waiting for review. <Link to="/">Back to Today</Link></div></main>

  const after = async (status: string) => {
    const next = queue[at + 1] ?? queue[at - 1]
    await act(`status/${job.id}`, { status })
    nav(next && next.id !== job.id ? `/review/${next.id}` : '/')
  }
  const loaded = full?.id === job.id
  const qa = (loaded && full.qa) || []
  const known = new Set(s.memory.answers.map((a) => a.key))
  const guesses = qa.map((x, i) => ({ x, i })).filter(({ x }) => x.guess)
  const written = qa.map((x, i) => ({ x, i })).filter(({ x }) => !x.guess && !known.has(norm(x.q)))
  const saved = qa.map((x, i) => ({ x, i })).filter(({ x }) => !x.guess && known.has(norm(x.q)))

  return (
    <main className="main" style={{ gap: 40 }}>
      <section style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '20px 32px' }}>
        <div className="head" style={{ flex: '1 1 520px' }}>
          <span className="label">{at >= 0 ? `Review ${at + 1} of ${queue.length}` : job.status.replaceAll('-', ' ')}</span>
          <h1 className="display">{job.company}</h1>
          <p className="lead">{job.title}</p>
          <div className="pills" style={{ marginTop: 4 }}>
            <span className="pill">{platformName(job)}</span>
            <span className="pill">{trackName(job.track)} resume</span>
            {job.filled_at && <span className="pill">Filled {new Date(job.filled_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</span>}
            {job.tab ? <span className="pill mint">Form open in the agent's window</span> : job.recipe ? <span className="pill">Can be refilled in about 10 s</span> : null}
            {job.posting.status === 'closed' && <span className="pill peach">Posting closed</span>}
          </div>
        </div>
        <div className="row-actions">
          <button className="btn btn-primary" onClick={() => act(`open/${job.id}`)}>{job.tab ? 'Open the form to submit' : 'Refill and open the form'}</button>
          {job.status === 'ready-to-submit' && <button className="btn btn-secondary" onClick={() => after('applied')}>I already submitted</button>}
          {job.status === 'ready-to-submit' && <button className="btn btn-quiet" onClick={() => after('skipped')}>Skip this job</button>}
        </div>
      </section>

      <div className="split">
        <figure className="side" style={{ margin: 0, maxWidth: 470, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {job.shot ? (
            <div className="sheet" style={{ height: 860 }}><img src={`/${job.shot}`} alt={`The agent's screenshot of the filled ${job.company} form`} style={{ width: '100%' }} /></div>
          ) : <div className="empty">No screenshot for this job.</div>}
          <figcaption className="small muted">What the agent saw when it finished. Scroll to read the whole form.</figcaption>
          <nav className="row-actions" aria-label="Queue">
            <button className="btn btn-sm btn-secondary" disabled={at <= 0} onClick={() => go(at - 1)}>Previous</button>
            <button className="btn btn-sm btn-secondary" disabled={at < 0 || at >= queue.length - 1} onClick={() => go(at + 1)}>Next</button>
            <span className="small muted">or press K and J</span>
          </nav>
        </figure>

        <section className="body" aria-label="Answers" style={{ display: 'flex', flexDirection: 'column', gap: 36 }}>
          {job.posting.status === 'closed' && (
            <div className="tile peach"><span className="label" style={{ color: 'inherit' }}>Posting closed{job.posting.how === 'claude' ? ', read by Claude' : ''}</span>
              {job.posting.quote && <p style={{ margin: '8px 0 0', fontFamily: 'var(--display)', fontWeight: 700, fontSize: 18 }}>“{job.posting.quote}”</p>}
              {job.posting.reason && <p className="small" style={{ margin: '6px 0 0' }}>{job.posting.reason}</p>}
              {job.status === 'closed' && <button className="btn btn-sm btn-peach" style={{ marginTop: 12 }} onClick={() => act(`status/${job.id}`, { status: 'new' })}>It's still open — bring it back</button>}
            </div>
          )}
          {job.unfilled.length > 0 && (
            <div className="tile peach"><span className="label" style={{ color: 'inherit' }}>Still empty</span><p style={{ margin: '8px 0 0' }}>{job.unfilled.join(', ')}</p></div>
          )}
          {guesses.map(({ x, i }) => <Guess key={i} job={job} qa={x} idx={i} />)}
          {written.length > 0 && <Group title={`Written for ${job.company}`} job={job} items={written} />}
          {saved.length > 0 && <Group title="From your saved answers" job={job} items={saved} />}
          {loaded && !qa.length && <div className="empty">The agent recorded no answers for this job{job.reason ? `: ${job.reason}` : '.'}</div>}
          <Timeline job={job} />
          <Notes job={job} />
          <div className="tile dark">
            <h3 style={{ fontSize: 20, marginBottom: 6 }}>When you're happy, submit in the agent's window.</h3>
            JobPilot notices the confirmation page, moves {job.company} to Submitted and saves these answers for next time.
          </div>
        </section>
      </div>
    </main>
  )
}

function Guess({ job, qa, idx }: { job: Job; qa: QA; idx: number }) {
  const { act } = useApp()
  const [edit, setEdit] = useState<string | null>(null)
  return (
    <div className="tile peach" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <span className="label" style={{ color: 'inherit' }}>Check this one</span>
      <span style={{ fontSize: 15 }}>{qa.q}</span>
      {edit === null
        ? <span style={{ fontFamily: 'var(--display)', fontWeight: 700, fontSize: 22, lineHeight: 1.3, color: '#2c160c' }}>{qa.a}</span>
        : <textarea className="box" aria-label={`Answer for ${qa.q}`} value={edit} onChange={(e) => setEdit(e.target.value)} style={{ background: 'rgba(255,255,255,0.7)' }} />}
      {qa.note && <span style={{ fontSize: 15 }}>The agent guessed this: {qa.note}</span>}
      <span className="row-actions">
        {edit === null ? <>
          <button className="btn btn-sm btn-peach" onClick={() => act(`answer/${job.id}`, { idx, answer: qa.a })}>Keep it</button>
          <button className="btn btn-sm" style={{ background: 'rgba(255,255,255,0.7)', color: 'var(--peach-ink)' }} onClick={() => setEdit(qa.a)}>Change the answer</button>
        </> : <>
          <button className="btn btn-sm btn-peach" onClick={async () => { await act(`answer/${job.id}`, { idx, answer: edit }); setEdit(null); if (job.tab) act(`push/${job.id}`, { idx }) }}>Save{job.tab ? ' and type it in' : ''}</button>
          <button className="btn btn-sm btn-quiet" onClick={() => setEdit(null)}>Cancel</button>
        </>}
      </span>
    </div>
  )
}

function Group({ title, job, items }: { title: string; job: Job; items: { x: QA; i: number }[] }) {
  const { act } = useApp()
  const [editing, setEditing] = useState<number | null>(null)
  const [text, setText] = useState('')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <h2 className="title-md">{title}</h2>
      <ul className="dots">
        {items.map(({ x, i }) => (
          <li key={i}><span className="dot" style={{ marginTop: 10 }} />
            <div style={{ flexGrow: 1, minWidth: 0 }}>
              <div className="small muted">{x.q}</div>
              {editing === i ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 6 }}>
                  <textarea className="box" aria-label={`Answer for ${x.q}`} value={text} onChange={(e) => setText(e.target.value)} />
                  <span className="row-actions">
                    <button className="btn btn-sm btn-primary" onClick={async () => { await act(`answer/${job.id}`, { idx: i, answer: text }); setEditing(null); if (job.tab) act(`push/${job.id}`, { idx: i }) }}>Save{job.tab ? ' and type it in' : ''}</button>
                    <button className="btn btn-sm btn-quiet" onClick={() => setEditing(null)}>Cancel</button>
                  </span>
                </div>
              ) : <div style={{ overflowWrap: 'anywhere' }}>{x.a || <span className="muted">Left empty</span>}</div>}
              {x.note && editing !== i && <div className="small" style={{ color: 'var(--outline)', marginTop: 2 }}>{x.note}</div>}
            </div>
            {job.editable && editing !== i && <button className="btn btn-quiet small" onClick={() => { setEditing(i); setText(x.a) }}>Edit</button>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Timeline({ job }: { job: Job }) {
  const [open, setOpen] = useState(false)
  const [steps, setSteps] = useState<Step[] | null>(null)
  useEffect(() => { if (open && !steps) getJSON<{ steps: Step[] }>(`/api/log/${job.id}`).then((r) => setSteps(r.steps)).catch(() => setSteps([])) }, [open, steps, job.id])
  useEffect(() => { setSteps(null); setOpen(false) }, [job.id])
  const denied = useMemo(() => steps?.filter((x) => x.deny).length ?? 0, [steps])
  if (!job.log) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <button className="btn btn-tertiary" style={{ alignSelf: 'flex-start' }} aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? 'Hide what the agent did' : 'See what the agent did'}</button>
      {open && steps && (
        <>
          <span className="small muted">{steps.length} steps, {job.turns} turns{denied ? `, ${denied} blocked by the guard` : ''}.</span>
          <ol className="dots">{steps.map((st, k) => (
            <li key={k}><span className={`dot ${st.deny ? 'bad' : 'on'}`} />
              <span style={{ flexGrow: 1, color: st.deny ? 'var(--error)' : undefined }}>{st.deny ? `Blocked: ${st.what}` : plain(st)}</span>
              <span className="small" style={{ color: 'var(--outline)' }}>{Math.floor(st.s / 60)}:{String(st.s % 60).padStart(2, '0')}</span></li>
          ))}</ol>
        </>
      )}
    </div>
  )
}

function Notes({ job }: { job: Job }) {
  const { act } = useApp()
  const [note, setNote] = useState(job.note)
  const [tags, setTags] = useState(job.tags.join(', '))
  useEffect(() => { setNote(job.note); setTags(job.tags.join(', ')) }, [job.id, job.note, job.tags])
  const dirty = note !== job.note || tags !== job.tags.join(', ')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 className="title-md">Your notes</h2>
      <label className="field">Only you see these; the agent never does
        <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Referral from…, follow up on…" />
      </label>
      <label className="field">Tags, separated by commas<input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="dream job, referral" /></label>
      {dirty && <button className="btn btn-sm btn-primary" style={{ alignSelf: 'flex-start' }} onClick={() => act(`note/${job.id}`, { note, tags: tags.split(',') })}>Save notes</button>}
    </div>
  )
}
