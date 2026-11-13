import { useState } from 'react'
import { useApp } from '../store'
import { LogDialog, confirmRuns } from '../components'
import { ago, type Task } from '../api'

export default function Control() {
  const { state, act } = useApp()
  const s = state!
  const h = s.health, d = s.daily
  const [log, setLog] = useState<Task | null>(null)
  const [cfg, setCfg] = useState(s.settings)
  const [add, setAdd] = useState({ url: '', company: '', title: '', fill: false })
  const nNew = s.jobs.filter((j) => j.status === 'new').length
  const [limit, setLimit] = useState('')
  const browserWords: Record<string, string> = { visible: 'Running in its own window', invisible: 'Running invisibly for the daily run', stopped: 'Not running' }

  return (
    <main className="main">
      <section className="head">
        <span className="label">Control</span>
        <h1 className="display">Everything you used to ask for in the terminal.</h1>
      </section>

      <div className="grid-wide">
        <section className="tile" aria-labelledby="run-h" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <h2 id="run-h" className="title-md">Run</h2>
          <div className="row-actions" style={{ justifyContent: 'space-between' }}>
            <span><b>Find new jobs</b><div className="small muted">Discovery agent, about five minutes</div></span>
            <button className="btn btn-sm btn-secondary" onClick={() => window.confirm('Find new jobs now?') && act('discover')}>Find</button>
          </div>
          <div className="row-actions" style={{ justifyContent: 'space-between' }}>
            <span><b>Fill new jobs</b><div className="small muted">{nNew} new</div></span>
            <span className="row-actions">
              <label className="field" style={{ width: 90 }}>How many<input inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ''))} placeholder="all" /></label>
              <button className="btn btn-sm btn-secondary" disabled={!nNew} onClick={() => { const n = +limit || nNew; if (confirmRuns(`Fill ${n} new jobs?`, n, s.usage.avg_cost, s.usage.avg_turns)) act('fill', { limit: +limit || 0 }) }}>Fill</button>
            </span>
          </div>
          <div className="row-actions" style={{ justifyContent: 'space-between' }}>
            <span><b>Check postings</b><div className="small muted">Free. Marks closed jobs on Greenhouse, Ashby and Lever.</div></span>
            <button className="btn btn-sm btn-secondary" onClick={() => act('check')}>Check</button>
          </div>
          <div className="row-actions" style={{ justifyContent: 'space-between' }}>
            <span><b>Detect submissions</b><div className="small muted">Live: a confirmation in a job's tab counts within seconds</div></span>
            <button className="btn btn-sm btn-secondary" onClick={() => act('detect')}>Detect</button>
          </div>
          <div className="row-actions" style={{ justifyContent: 'space-between' }}>
            <span><b>Check my inbox</b><div className="small muted">Reads your job emails: submitted, replies, rejections, interviews. Also after each daily run.</div></span>
            <button className="btn btn-sm btn-secondary" onClick={() => act('inbox')}>Check</button>
          </div>
          <div className="row-actions" style={{ justifyContent: 'space-between' }}>
            <span><b>Reconnect Gmail</b><div className="small muted">Google expires the approval every 7 days (Testing mode). Opens the approval page.</div></span>
            <button className="btn btn-sm btn-secondary" onClick={() => act('gmail-auth')}>Reconnect</button>
          </div>
        </section>

        <section className="tile" aria-labelledby="chrome-h" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <h2 id="chrome-h" className="title-md">The agent's Chrome</h2>
          <span className="pills"><span className={`pill${h.browser === 'visible' ? ' mint' : h.browser === 'stopped' ? ' peach' : ''}`}>{browserWords[h.browser] ?? h.browser}</span></span>
          <p className="small muted" style={{ margin: 0 }}>Its own profile with your logins. Extensions are off, and each agent job gets a background window.</p>
          <div className="row-actions">
            <button className="btn btn-sm btn-secondary" disabled={h.browser !== 'stopped'} onClick={() => act('browser/start')}>Start</button>
            <button className="btn btn-sm btn-secondary" disabled={h.browser === 'stopped'} onClick={() => window.confirm('Stop the agent’s Chrome? Its tabs close; filled forms come back with Refill.') && act('browser/stop')}>Stop</button>
            <button className="btn btn-sm btn-secondary" disabled={h.browser !== 'visible'} onClick={() => window.confirm('Restart the agent’s Chrome?') && act('browser/restart')}>Restart</button>
            <button className="btn btn-sm btn-quiet" onClick={() => act('browser/normal')}>Open my normal Chrome</button>
          </div>
          <h2 className="title-md" style={{ marginTop: 8 }}>Claude login</h2>
          <span className="pills"><span className={`pill${h.login === false || h.login_error ? ' peach' : ' mint'}`}>{h.login === false || h.login_error ? 'Expired' : h.login ? 'Signed in' : 'Unknown'}</span></span>
          <div className="row-actions">
            <button className="btn btn-sm btn-secondary" onClick={() => act('login/go')}>Sign in again</button>
            <button className="btn btn-sm btn-quiet" onClick={() => act('login/recheck')}>Check again</button>
          </div>
        </section>

        <section className="tile" aria-labelledby="daily-h" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="section-head"><h2 id="daily-h" className="title-md">Daily run</h2>
            <label className="check" style={{ marginLeft: 'auto' }}><input type="checkbox" checked={d.on} onChange={() => act('daily', { do: d.on ? 'off' : 'on' })} /> On</label></div>
          <p className="small muted" style={{ margin: 0 }}>Checks every 30 minutes while you use the Mac and it has power, then runs once a day, invisibly.
            Last run {d.last_date || 'never'}{d.status ? `. Last check: ${d.status} (${ago(d.status_at)})` : ''}.</p>
          <div className="grid" style={{ gap: 16 }}>
            <label className="field">On battery, only above (%)<input inputMode="numeric" value={cfg.min_battery} onChange={(e) => setCfg({ ...cfg, min_battery: +e.target.value || 0 })} /></label>
            <label className="field">Counts as in use within (minutes)<input inputMode="numeric" value={cfg.max_idle_min} onChange={(e) => setCfg({ ...cfg, max_idle_min: +e.target.value || 1 })} /></label>
            <label className="field">Daily usage cap ($, 0 is none)<input inputMode="decimal" value={cfg.daily_cap_usd} onChange={(e) => setCfg({ ...cfg, daily_cap_usd: +e.target.value || 0 })} /></label>
          </div>
          <label className="check"><input type="checkbox" checked={cfg.auto_detect} onChange={(e) => setCfg({ ...cfg, auto_detect: e.target.checked })} /> Notice submissions automatically</label>
          <div className="row-actions">
            <button className="btn btn-sm btn-primary" onClick={() => act('daily', { do: 'settings', settings: cfg })}>Save settings</button>
            <button className="btn btn-sm btn-secondary" onClick={() => confirmRuns('Run the whole daily pipeline now?', Math.max(nNew, 1), s.usage.avg_cost, s.usage.avg_turns) && act('daily', { do: 'now' })}>Run it now</button>
          </div>
        </section>

        <section className="tile" aria-labelledby="add-h" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <h2 id="add-h" className="title-md">Add a job you found</h2>
          <label className="field">Job link<input type="url" value={add.url} onChange={(e) => setAdd({ ...add, url: e.target.value })} placeholder="https://jobs.ashbyhq.com/…" /></label>
          <label className="field">Company, if the link doesn't say<input value={add.company} onChange={(e) => setAdd({ ...add, company: e.target.value })} /></label>
          <label className="field">Role<input value={add.title} onChange={(e) => setAdd({ ...add, title: e.target.value })} /></label>
          <label className="check"><input type="checkbox" checked={add.fill} onChange={(e) => setAdd({ ...add, fill: e.target.checked })} /> Fill it now with the agent</label>
          <button className="btn btn-sm btn-primary" style={{ alignSelf: 'flex-start' }} disabled={!add.url}
            onClick={async () => { const r = await act('add', add); if (!r.error) setAdd({ url: '', company: '', title: '', fill: false }) }}>Add job</button>
          {s.managed && <>
            <h2 className="title-md" style={{ marginTop: 8 }}>Dashboard server</h2>
            <p className="small muted" style={{ margin: 0 }}>Runs in the background all the time. Restart it after JobPilot's code changes; running tasks keep going.</p>
            <button className="btn btn-sm btn-secondary" style={{ alignSelf: 'flex-start' }} onClick={() => window.confirm('Restart the dashboard server?') && act('restart')}>Restart server</button>
          </>}
        </section>
      </div>

      <section className="section" aria-labelledby="tasks-h">
        <div className="section-head"><h2 id="tasks-h" className="title-lg">Tasks</h2><span className="muted">Everything started from here, newest first.</span></div>
        <div className="rows">
          {[...s.tasks].reverse().slice(0, 20).map((t) => (
            <div key={t.id} className="job-row" style={{ padding: '14px 22px' }}>
              <div className="who"><b>{t.label}</b><div className="small muted" style={{ overflowWrap: 'anywhere' }}>{t.result.split('\n').slice(-1)[0] || ' '}</div></div>
              <span className={`pill${t.status === 'running' ? ' mint' : t.status === 'failed' ? ' peach' : ' white'}`}>{t.status}</span>
              <span className="small muted">{ago(t.started)}</span>
              <span className="row-actions">
                <button className="btn btn-sm btn-secondary" onClick={() => setLog(t)}>Log</button>
                {t.status === 'running' && t.pid > 0 && <button className="btn btn-sm btn-danger" onClick={() => window.confirm('Stop this task?') && act(`cancel/${t.id}`)}>Stop</button>}
              </span>
            </div>
          ))}
          {!s.tasks.length && <div className="empty">No tasks yet.</div>}
        </div>
      </section>
      <LogDialog task={log} onClose={() => setLog(null)} />
    </main>
  )
}
