import { Link } from 'react-router-dom'
import { useApp } from '../store'
import { AskList, JobRow, Ring, confirmRuns, readyJobs } from '../components'
import { DONE, HEARD } from '../api'
import { LiveScreen, useAgentRun } from './Live'

export default function Today() {
  const { state, act } = useApp()
  const s = state!
  const ready = readyJobs(s.jobs)
  const held = s.jobs.filter((j) => ['needs-you', 'form-ready'].includes(j.status))
  const count = (c: string) => held.filter((j) => j.cat === c).length
  const pending = Object.values(s.memory.pending).flat().length
  const submitted = s.jobs.filter((j) => DONE.includes(j.status))
  const heard = s.jobs.filter((j) => HEARD.includes(j.status)).length
  const nNew = s.jobs.filter((j) => j.status === 'new').length
  const run = useAgentRun()
  const hour = new Date().getHours()
  const first = (s.me.name || '').split(' ')[0]
  const newest = ready[0]
  const walls = held.filter((j) => ['login', 'account_required'].includes(j.cat) && j.filled_at.startsWith(s.today))
  const wallSites = new Set(walls.map((j) => j.where.key)).size
  const lastFilled = [...s.jobs].filter((j) => j.shot).sort((a, b) => (a.filled_at < b.filled_at ? 1 : -1))[0]
  const hero = run.job ?? lastFilled

  return (
    <main className="main">
      <section className="head">
        <span className="label">{new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}</span>
        <h1 className="display">Good {hour < 12 ? 'morning' : hour < 17 ? 'afternoon' : 'evening'}{first ? `, ${first}` : ''}.</h1>
        <p className="lead">
          {ready.length
            ? `${ready.length} application${ready.length > 1 ? 's are' : ' is'} filled and waiting for your review. Newest first: applying early has the best odds.`
            : nNew ? `Nothing is waiting for review. ${nNew} new job${nNew > 1 ? 's are' : ' is'} ready to be filled.` : 'Nothing is waiting for you. Find new jobs when you are ready.'}
        </p>
        <div className="row-actions" style={{ marginTop: 10 }}>
          {newest && <Link to={`/review/${newest.id}`} className="btn btn-primary">Review the newest</Link>}
          {nNew > 0 && <button className="btn btn-secondary" onClick={() => confirmRuns(`Fill ${nNew} new jobs?`, nNew, s.usage.avg_cost, s.usage.avg_turns) && act('fill', {})}>Fill {nNew} new jobs</button>}
          <button className="btn btn-secondary" onClick={() => window.confirm('Find new jobs now? One discovery agent run, about five minutes.') && act('discover')}>Find new jobs</button>
          <button className="btn btn-secondary" onClick={() => act('inbox')}>Check my inbox</button>
        </div>
        {walls.length > 0 && (
          <Link to="/held" className="pill peach" style={{ alignSelf: 'flex-start', textDecoration: 'none', marginTop: 6 }}>
            Today’s run hit {wallSites} login wall{wallSites > 1 ? 's' : ''} · {walls.length} job{walls.length > 1 ? 's' : ''} waiting on a login →
          </Link>
        )}
      </section>

      <AskList jobs={s.jobs} />

      <section aria-label="The agent" className="grid-wide">
        {hero ? (
          <Link to={run.job ? '/live' : `/review/${hero.id}`} className="hero">
            {run.job ? <LiveScreen jobId={run.job.id} style={{ position: 'absolute', inset: 0 }} /> : hero.shot && <img src={`/${hero.shot}`} alt="" />}
            <span className="fade" />
            <span className="caption">
              <span className="label">{run.job ? 'Filling now' : 'Last filled'}</span>
              <span className="big">{hero.company}, {hero.title}</span>
              <span className="sub">{run.job ? run.now : `${hero.answers} answers, ${hero.guesses ? `${hero.guesses} to check` : 'none to check'}.`}</span>
            </span>
          </Link>
        ) : <div className="hero" />}
        <div className="tile pulse-card">
          <span className="pulse" aria-hidden="true" />
          {run.task ? <Ring value={run.phase} total={6} /> : <Ring value={ready.length} total={Math.max(ready.length, 1)} label={`${ready.length}`} />}
          <span style={{ color: 'var(--ink-soft)', maxWidth: 320 }}>
            {run.task ? `${run.task.label}. Each job runs in its own background window, so your tabs stay where you left them.`
              : 'No agent is working right now. Everything filled is waiting for you.'}
          </span>
          <Link to="/live" className="btn btn-tertiary">{run.task ? 'Watch it live' : 'Open Live'}</Link>
        </div>
      </section>

      <section className="section" aria-labelledby="wait-h">
        <div className="section-head"><h2 id="wait-h" className="title-lg">Waiting for your review</h2><span className="muted">Newest first. Postings close within days.</span></div>
        {ready.length ? (
          <div className="rows">{ready.slice(0, 6).map((j, k) => <JobRow key={j.id} job={j} lift={k === 0} />)}</div>
        ) : <div className="empty">Nothing waiting. Filled forms land here.</div>}
        {ready.length > 6 && <Link to="/pipeline" className="btn btn-tertiary" style={{ alignSelf: 'flex-start' }}>See all {ready.length}</Link>}
      </section>

      <section aria-label="Everything else" className="grid">
        <Link to="/held" className="tile peach tile-link">
          <span className="label" style={{ color: 'inherit' }}>Held for you</span>
          <span className="tile-number">{held.length}</span>
          <span>{count('login')} need you to log in, {count('account_required')} an account you create, {count('email_apply')} an email you send. Each is one step, then Retry.</span>
        </Link>
        <Link to="/memory" className="tile mid tile-link">
          <span className="label">Memory</span>
          <span className="tile-number" style={{ color: 'var(--primary)' }}>{pending}</span>
          <span style={{ color: 'var(--ink-soft)' }}>new notes the agent wrote about the sites it filled. Approve the ones that hold everywhere.</span>
        </Link>
        <Link to="/pipeline" className="tile dark tile-link">
          <span className="label" style={{ color: 'var(--on-primary-container)' }}>Submitted</span>
          <span className="tile-number" style={{ color: 'var(--white)' }}>{submitted.length}</span>
          <span>{heard ? `${heard} heard back so far.` : 'No replies yet.'} Submitting in the agent's window is picked up automatically.</span>
        </Link>
      </section>
    </main>
  )
}
