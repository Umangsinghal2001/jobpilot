import { useApp } from '../store'
import { confirmRuns } from '../components'
import { usd, nice, type Row } from '../api'

const rate = (r: Row) => (r.heard ? `${Math.round((100 * r.heard) / r.submitted)}%` : 'waiting')

function Outcomes({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="table">
        <thead><tr><th>{title}</th><th className="num">Submitted</th><th className="num">Heard back</th><th className="num">Interviews</th><th className="num">Reply rate</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.key}><td>{nice(r.key.split(' (')[0])}</td><td className="num">{r.submitted}</td><td className="num">{r.heard}</td><td className="num">{r.interviews}</td><td className="num">{rate(r)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}

export default function Insights() {
  const { state, act } = useApp()
  const s = state!
  const u = s.usage, o = s.outcomes, f = s.funnel
  const max = Math.max(...u.days.map((d) => d.cost), s.settings.daily_cap_usd || 0, 0.01)
  const ev = s.evals

  return (
    <main className="main">
      <section className="head">
        <span className="label">Insights</span>
        <h1 className="display">What gets answers, and what it costs.</h1>
      </section>

      <section className="section" aria-labelledby="out-h">
        <h2 id="out-h" className="title-lg">Outcomes</h2>
        <div className="grid">
          <div className="tile white tile-link"><span className="label">Submitted</span><span className="tile-number" style={{ color: 'var(--primary)' }}>{o.submitted}</span></div>
          <div className="tile tile-link"><span className="label">Heard back</span><span className="tile-number" style={{ color: 'var(--primary)' }}>{f.replies}</span></div>
          <div className="tile tile-link"><span className="label">Interviews</span><span className="tile-number" style={{ color: 'var(--primary)' }}>{f.interviews}</span></div>
          <div className="tile dark tile-link"><span className="label" style={{ color: 'var(--on-primary-container)' }}>Found to filled</span>
            <span className="tile-number" style={{ color: 'var(--white)' }}>{f.found ? Math.round((100 * f.filled) / f.found) : 0}%</span></div>
        </div>
        {o.submitted ? <div className="grid-wide"><Outcomes title="Resume track" rows={o.by_track} /><Outcomes title="Site" rows={o.by_platform} /></div>
          : <div className="empty">Reply rates appear once you submit and record replies in Pipeline.</div>}
      </section>

      <section className="section" aria-labelledby="use-h">
        <div className="section-head"><h2 id="use-h" className="title-lg">Usage</h2>
          <span className="muted">API-equivalent dollars. Your Claude plan isn't billed per token, but this counts toward its limits.</span></div>
        <div className="grid">
          <div className="tile tile-link"><span className="label">Today</span><span className="tile-number" style={{ color: 'var(--primary)' }}>{usd(u.today)}</span>
            <span className="small muted">{s.settings.daily_cap_usd ? `Daily cap ${usd(s.settings.daily_cap_usd)}` : 'No daily cap set'}</span></div>
          <div className="tile tile-link"><span className="label">Last 7 days</span><span className="tile-number" style={{ color: 'var(--primary)' }}>{usd(u.week)}</span></div>
          <div className="tile tile-link"><span className="label">Per job</span><span className="tile-number" style={{ color: 'var(--primary)' }}>{usd(u.avg_cost)}</span><span className="small muted">about {u.avg_turns} turns</span></div>
        </div>
        <div className="split">
          <figure className="body tile white" style={{ margin: 0 }}>
            <figcaption className="small muted" style={{ marginBottom: 14 }}>Per day</figcaption>
            <div role="img" aria-label={u.days.map((d) => `${d.day} ${usd(d.cost)}`).join(', ')}
              style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(u.days.length, 1)}, minmax(0, 1fr))`, gap: 14, alignItems: 'end', height: 240 }}>
              {u.days.map((d) => (
                <div key={d.day} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                  <span className="small muted">{usd(d.cost)}</span>
                  <div style={{ width: '100%', height: Math.max(4, (190 * d.cost) / max), borderRadius: 10, background: s.settings.daily_cap_usd && d.cost > s.settings.daily_cap_usd ? 'var(--peach)' : 'var(--mint-dim)' }} />
                  <span className="small muted">{new Date(d.day).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</span>
                </div>
              ))}
            </div>
          </figure>
          <div className="side">
            <h3 style={{ fontSize: 18, marginBottom: 10 }}>Most expensive runs</h3>
            <ul className="dots">{u.top.slice(0, 6).map((t, k) => (
              <li key={k}><span className="dot" /><span style={{ flexGrow: 1 }}>{t.company}</span><span className="small muted">{t.turns} turns, {usd(t.cost)}</span></li>
            ))}</ul>
          </div>
        </div>
      </section>

      <section className="section" aria-labelledby="eval-h">
        <div className="section-head"><h2 id="eval-h" className="title-lg">Agent check-up</h2>
          <span className="muted">Runs the agent on {ev.cases} fixed jobs across sites, without changing their status.</span>
          <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }}
            onClick={() => confirmRuns(`Run the agent check-up on ${ev.cases} jobs?`, ev.cases, s.usage.avg_cost, s.usage.avg_turns) && act('eval')}>Run it now</button></div>
        {ev.history.length ? (
          <table className="table"><thead><tr><th>Date</th><th className="num">Passed</th><th className="num">Turns</th><th className="num">Cost</th><th>What failed</th></tr></thead>
            <tbody>{ev.history.map((e) => (
              <tr key={e.date}><td>{e.date}</td><td className="num">{e.pass} of {e.cases}</td><td className="num">{e.turns}</td><td className="num">{usd(e.cost)}</td><td>{e.fails.length ? e.fails.join('; ') : 'Nothing'}</td></tr>
            ))}</tbody></table>
        ) : <div className="empty">No check-ups yet.</div>}
      </section>
    </main>
  )
}
