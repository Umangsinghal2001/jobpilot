import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../store'
import { LogDialog, Ring, confirmRuns } from '../components'
import { getJSON, phase, plain, type Job, type Step, type Task } from '../api'

/** The running agent task, the job it is on (if known) and that job's steps — polled every 2 s. */
export function useAgentRun() {
  const { state } = useApp()
  const task = state?.tasks.find((t) => t.status === 'running' && t.agent) ?? null
  const ids = state?.active_jobs.length ? state.active_jobs : task?.job_id ? [task.job_id] : []
  const jobs = ids.map((id) => state!.jobs.find((j) => j.id === id)).filter(Boolean) as Job[]
  const job = jobs[0] ?? null
  const steps = useSteps(task ? job?.id : undefined)
  const last = steps[steps.length - 1]
  return { task, jobs, job, steps, phase: phase(steps), now: last ? plain(last) : 'Starting…' }
}

export function useSteps(jobId?: string) {
  const [steps, setSteps] = useState<Step[]>([])
  useEffect(() => {
    if (!jobId) return setSteps([])
    let stop = false
    const tick = async () => {
      const r = await getJSON<{ steps: Step[] }>(`/api/log/${jobId}`).catch(() => null)
      if (stop) return
      if (r) setSteps(r.steps)
      window.setTimeout(tick, 2000)
    }
    tick()
    return () => { stop = true }
  }, [jobId])
  return steps
}

/** The agent's window for one job, streamed as JPEG frames over /api/live/<job> (reconnects by itself). */
export function LiveScreen({ jobId, style }: { jobId: string; style?: React.CSSProperties }) {
  const [frame, setFrame] = useState('')
  const [note, setNote] = useState('Connecting to its window…')
  useEffect(() => {
    let ws: WebSocket | null = null, retry = 0, stop = false
    const open = () => {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/live/${jobId}`)
      ws.onmessage = (e) => {
        if (typeof e.data !== 'string') return
        if (e.data.startsWith('{')) { const m = JSON.parse(e.data); setNote(m.error ?? 'Waiting for the page to change…') } else setFrame(e.data)
      }
      ws.onclose = () => { if (!stop) retry = window.setTimeout(open, 3000) }
    }
    open()
    return () => { stop = true; window.clearTimeout(retry); ws?.close() }
  }, [jobId])
  return frame
    ? <img src={`data:image/jpeg;base64,${frame}`} alt="The agent's window, live" style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top', ...style }} />
    : <span className="small" style={{ color: 'var(--mint)', ...style }}>{note}</span>
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

export default function Live() {
  const { state, act } = useApp()
  const s = state!
  const run = useAgentRun()
  const [log, setLog] = useState<Task | null>(null)
  const nNew = s.jobs.filter((j) => j.status === 'new').length

  if (!run.task) {
    const last = [...s.tasks].reverse().find((t) => t.agent)
    return (
      <main className="main">
        <section className="head">
          <span className="label">Live</span>
          <h1 className="display">No agent is working right now.</h1>
          <p className="lead">When you fill or retry jobs, you can watch each one here: what it's doing, step by step, in its own window.</p>
          <div className="row-actions" style={{ marginTop: 10 }}>
            {nNew > 0 && <button className="btn btn-primary" onClick={() => confirmRuns(`Fill ${nNew} new jobs?`, nNew, s.usage.avg_cost, s.usage.avg_turns) && act('fill', {})}>Fill {nNew} new jobs</button>}
            <Link to="/held" className="btn btn-secondary">Retry held jobs</Link>
          </div>
        </section>
        {last && (
          <section className="tile" style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
            <div style={{ flexGrow: 1 }}><span className="label">Last run</span><div className="company">{last.label}</div>
              <div className="muted small">{last.status}, {last.result.split('\n').slice(-1)[0]}</div></div>
            <button className="btn btn-sm btn-secondary" onClick={() => setLog(last)}>Read its log</button>
          </section>
        )}
        <LogDialog task={log} onClose={() => setLog(null)} />
      </main>
    )
  }

  const job = run.job
  return (
    <main className="main" style={{ gap: 40 }}>
      <section style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '16px 32px' }}>
        <div className="head" style={{ flex: '1 1 520px' }}>
          <span className="label">{run.task.label}</span>
          <h1 className="display" style={{ fontSize: 48 }}>{job ? `The agent is filling ${job.company}.` : 'The agent is working.'}</h1>
          <p className="lead">Keep working; its windows open behind yours and nothing will jump in front of you.</p>
        </div>
        <div className="row-actions">
          <button className="btn btn-secondary" onClick={() => setLog(run.task)}>Full log</button>
          <button className="btn btn-danger" onClick={() => window.confirm('Stop this task? Forms already filled stay open.') && act(`cancel/${run.task!.id}`)}>Stop this task</button>
        </div>
      </section>

      {job ? (
        <div className="split">
          <figure className="body" style={{ margin: 0, position: 'relative', borderRadius: 28, overflow: 'hidden', background: 'var(--gradient)', aspectRatio: '16 / 10', minHeight: 360, display: 'grid', placeItems: 'center' }}>
            <LiveScreen jobId={job.id} style={{ position: 'absolute', inset: 0 }} />
            <figcaption style={{ position: 'absolute', left: 16, right: 16, bottom: 16, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px 18px', padding: '14px 18px', borderRadius: 20, background: 'rgba(250,250,245,0.85)', backdropFilter: 'blur(20px)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 10, flex: '1 1 260px' }}><span className="dot now" />
                <span><b style={{ fontFamily: 'var(--display)' }}>{job.company}</b><span className="muted">, {run.now}</span></span></span>
              <span className="small muted">{run.steps.length ? `${clock(run.steps[run.steps.length - 1].s)} in` : ''}</span>
              <button className="btn btn-sm btn-secondary" onClick={() => act(`open/${job.id}`)}>Bring its window forward</button>
            </figcaption>
          </figure>
          <aside className="side" style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
            <div className="tile pulse-card" style={{ flexDirection: 'row', textAlign: 'left', padding: 24 }}>
              <span className="pulse" aria-hidden="true" style={{ width: 180, height: 170, left: -20, top: -10 }} />
              <Ring value={run.phase} total={6} size={104} />
              <span style={{ color: 'var(--ink-soft)' }}>{run.now}</span>
            </div>
            <ol className="dots">
              {run.steps.slice(-9).map((st, k, arr) => (
                <li key={k}><span className={`dot ${st.deny ? 'bad' : k === arr.length - 1 ? 'now' : 'on'}`} />
                  <span style={{ flexGrow: 1, fontWeight: k === arr.length - 1 ? 600 : 400, color: st.deny ? 'var(--error)' : undefined }}>
                    {st.deny ? `Blocked by the guard: ${st.what}` : plain(st)}</span>
                  <span className="small" style={{ color: 'var(--outline)' }}>{clock(st.s)}</span></li>
              ))}
            </ol>
            <p className="tile mid" style={{ margin: 0, padding: 20, fontSize: 15, color: 'var(--ink-soft)' }}>The agent can't press Submit. It stops on the review screen and leaves the form for you.</p>
          </aside>
        </div>
      ) : (
        <div className="tile pulse-card" style={{ minHeight: 320 }}>
          <span className="pulse" aria-hidden="true" />
          <Ring value={0} total={6} label="…" />
          <span style={{ color: 'var(--ink-soft)' }}>The agent is starting. Its job appears here as soon as it opens a page.</span>
        </div>
      )}

      {run.jobs.length > 1 && (
        <section className="section">
          <h2 className="title-md">Also in this task</h2>
          <div className="grid">
            {run.jobs.slice(1).map((j) => (
              <div key={j.id} className="tile white" style={{ padding: 20 }}><b style={{ fontFamily: 'var(--display)' }}>{j.company}</b><div className="small muted">{j.title}</div></div>
            ))}
          </div>
        </section>
      )}
      <LogDialog task={log} onClose={() => setLog(null)} />
    </main>
  )
}
