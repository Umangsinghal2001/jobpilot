import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../store'
import { AskList, confirmRuns } from '../components'
import { days, type Job } from '../api'

/** What you do for each kind of hold, in a few words (the agent's full reason is under the row's details). */
const ACTION: Record<string, string> = {
  login: 'Log in, then Retry',
  account_required: 'Create the account, then Retry',
  email_apply: 'Send it by email',
  unfilled: 'Answer the empty field',
  captcha: 'Apply yourself (bot check)',
  error: 'Agent error: Retry',
  legacy: 'Old reader: Retry with the agent',
  other: 'Read why, then Retry or apply yourself',
}
const WALL = ['login', 'account_required']
const STALE_DAYS = 30
const SHOWN_SITES = 6
/** The question itself, not the agent's note after it (the note stays under the row's details). */
const short = (q: string) => { const t = q.split(' — ')[0].trim(); return t.length > 90 ? `${t.slice(0, 90)}…` : t }
const age = (iso: string) => { const d = days(iso); return d === null ? '' : d === 0 ? 'today' : `${d}d` }

/** Today's login walls, one step that frees several, quick answers, everything else, stale. */
function sort(held: Job[], today: string) {
  const isToday = (j: Job) => WALL.includes(j.cat) && j.filled_at.startsWith(today)
  const stale = (j: Job) => !isToday(j) && ((days(j.found) ?? 0) > STALE_DAYS || (!!j.posting.status && j.posting.status !== 'live'))
  const quick = (j: Job) => j.cat === 'unfilled' && j.unfilled.length > 0 && j.unfilled.length <= 2
  const bySite = (jobs: Job[]) => {
    const m = new Map<string, Job[]>()
    jobs.forEach((j) => m.set(j.where.key, [...(m.get(j.where.key) ?? []), j]))
    return [...m.values()].sort((a, b) => b.length - a.length)
  }
  const fresh = held.filter((j) => !stale(j))
  return {
    today: bySite(held.filter(isToday)),
    walls: bySite(fresh.filter((j) => WALL.includes(j.cat) && !isToday(j))),
    quick: fresh.filter(quick),
    rest: fresh.filter((j) => !WALL.includes(j.cat) && !quick(j)),
    stale: held.filter(stale),
  }
}

export default function Held() {
  const { state, act, toast } = useApp()
  const s = state!
  const held = s.jobs.filter((j) => ['needs-you', 'form-ready'].includes(j.status))
  const g = sort(held, s.today)
  const tabs = [
    { key: 'walls', title: 'One step frees several', n: g.walls.flat().length, note: 'Logins and accounts, by site: the site that frees the most jobs first.' },
    { key: 'quick', title: 'Quick answers', n: g.quick.length, note: 'One or two empty fields. Answer them and the job is ready to submit.' },
    { key: 'rest', title: 'Everything else', n: g.rest.length, note: 'Email applications, bot checks and the rest, newest first.' },
    { key: 'stale', title: 'Probably stale', n: g.stale.length, note: `Found over ${STALE_DAYS} days ago, or the last posting check wasn’t “live”. Many of these have closed.` },
  ]
  const [tab, setTab] = useState(() => tabs.find((t) => t.n)?.key ?? 'walls')
  const [openSite, setOpenSite] = useState('')
  const [allSites, setAllSites] = useState(false)
  const [openRow, setOpenRow] = useState('')

  const retry = (jobs: Job[]) => confirmRuns(`Retry ${jobs.length} job${jobs.length > 1 ? 's' : ''} with the agent?`, jobs.length, s.usage.avg_cost, s.usage.avg_turns)
    && act('retry', { ids: jobs.map((j) => j.id) })
  const copy = async (text: string, what: string) => { await navigator.clipboard.writeText(text); toast(`${what} copied`) }
  const skip = (j: Job) => act(`status/${j.id}`, { status: 'skipped' })

  /** One job on one line: who, the one thing to do, where it lives and how old, one button. Click it for the rest. */
  const Line = ({ j }: { j: Job }) => {
    const open = openRow === j.id
    const ask = j.cat === 'unfilled' && j.unfilled.length > 0
    return <>
      <div className="line">
        <div className="who" title={`${j.company} · ${j.title}`}><b>{j.company}</b> <span className="muted">· {j.title}</span></div>
        <div className={`todo${ask ? ' ask' : ''}`} title={ask ? j.unfilled.join(' · ') : ''}>{ask ? j.unfilled.map(short).join(' · ') : ACTION[j.cat] ?? ACTION.other}</div>
        <button className="tag" title={`${j.where.url}${j.where.via ? ` (via ${j.where.via})` : ''}`} onClick={() => setOpenRow(open ? '' : j.id)}>
          {j.where.site} · {age(j.found)} {open ? '▴' : '▾'}
        </button>
        <div className="acts">
          {j.cat === 'unfilled' && <Link className="btn btn-sm btn-primary" to={`/review/${j.id}`}>Answer</Link>}
          {j.cat === 'email_apply' && j.draft && <button className="btn btn-sm btn-primary" onClick={() => copy(j.draft!, 'Draft')}>Copy draft</button>}
          {j.cat === 'captcha' && <a className="btn btn-sm btn-secondary" href={j.where.url} target="_blank" rel="noreferrer">Open page</a>}
          {!['unfilled', 'email_apply', 'captcha'].includes(j.cat) && <button className="btn btn-sm btn-primary" onClick={() => retry([j])}>Retry</button>}
        </div>
      </div>
      {open && (
        <div className="more-info">
          <div className="row-actions" style={{ margin: '4px 0 8px' }}>
            <button className="btn btn-sm btn-secondary" onClick={() => act(`site/${j.id}`)}>Open {j.where.site} ↗</button>
            {j.tab && <button className="btn btn-sm btn-secondary" onClick={() => act(`open/${j.id}`)}>Show its tab</button>}
            {j.cat === 'unfilled' && <button className="btn btn-sm btn-secondary" onClick={() => retry([j])}>Retry</button>}
            {j.cat === 'email_apply' && j.email && <>
              <button className="btn btn-sm btn-secondary" onClick={() => copy(j.email, 'Address')}>Copy address</button>
              <button className="btn btn-sm btn-secondary" onClick={() => act(`reveal/${j.id}`)}>Show resume</button>
              <button className="btn btn-sm btn-secondary" onClick={() => act(`status/${j.id}`, { status: 'applied' })}>I sent it</button>
            </>}
            <button className="btn btn-sm btn-quiet" onClick={() => skip(j)}>Skip</button>
          </div>
          {j.where.via && <div>Came via {j.where.via}; stuck on {j.where.site}.</div>}
          {j.cat === 'email_apply' && j.email && <div>Send to <b>{j.email}</b></div>}
          {(ask ? j.unfilled.join(' · ') : j.reason.replace(/^\w+: /, '')) && <div style={{ marginTop: 4 }}>{ask ? j.unfilled.join(' · ') : j.reason.replace(/^\w+: /, '')}</div>}
        </div>
      )}
    </>
  }

  /** One site: one login frees every job behind it. Its jobs open under the grid when you click the card. */
  const SiteCards = ({ groups, lift }: { groups: Job[][]; lift?: boolean }) => {
    const shown = allSites || lift ? groups : groups.slice(0, SHOWN_SITES)
    const open = groups.find((jobs) => jobs[0].where.key === openSite)
    return <>
      <div className="sites">
        {shown.map((jobs) => {
          const site = jobs[0].where.site
          const verb = jobs.every((j) => j.cat === 'account_required') ? 'Create account' : 'Log in'
          return (
            <div key={jobs[0].where.key} className={`site-card${lift ? ' lift' : ''}`}>
              <div className="title-md" style={{ fontSize: 18 }}>{verb} · {site}</div>
              <button className="tag" style={{ alignSelf: 'flex-start', background: 'transparent', padding: 0 }}
                onClick={() => setOpenSite(openSite === jobs[0].where.key ? '' : jobs[0].where.key)}>
                {jobs.length} job{jobs.length > 1 ? 's' : ''}: {jobs.map((j) => j.company).slice(0, 3).join(', ')}{jobs.length > 3 ? '…' : ''} {openSite === jobs[0].where.key ? '▴' : '▾'}
              </button>
              <div className="row-actions" style={{ marginTop: 4 }}>
                <button className="btn btn-sm btn-secondary" onClick={() => act(`site/${jobs[0].id}`)}>Open site ↗</button>
                <button className="btn btn-sm btn-primary" onClick={() => retry(jobs)}>Retry{jobs.length > 1 ? ` all ${jobs.length}` : ''}</button>
              </div>
            </div>
          )
        })}
      </div>
      {!lift && !allSites && groups.length > SHOWN_SITES && (
        <button className="btn btn-tertiary" style={{ alignSelf: 'flex-start' }} onClick={() => setAllSites(true)}>
          + {groups.length - SHOWN_SITES} more sites ({groups.slice(SHOWN_SITES).flat().length} jobs)
        </button>
      )}
      {open && <div className="list">{open.map((j) => <Line key={j.id} j={j} />)}</div>}
    </>
  }

  const cur = tabs.find((t) => t.key === tab)!
  const todayN = g.today.flat().length
  return (
    <main className="main">
      <section className="head">
        <span className="label">Held for you</span>
        <h1 className="display">{held.length} applications are waiting on you.</h1>
      </section>

      <AskList jobs={s.jobs} />

      {todayN > 0 && (
        <section className="section">
          <div className="section-head"><h2 className="title-lg">Hit a login wall today</h2><span className="pill peach">{todayN} job{todayN > 1 ? 's' : ''}</span></div>
          <p className="muted" style={{ margin: 0 }}>From today’s run. Log in now while the run carries on; Retry picks them up.</p>
          <SiteCards groups={g.today} lift />
        </section>
      )}

      <section className="section">
        <div className="tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.key} role="tab" aria-selected={tab === t.key} className={`tab${tab === t.key ? ' on' : ''}`}
              onClick={() => { setTab(t.key); setOpenRow(''); setOpenSite('') }}>{t.title}<b>{t.n}</b></button>
          ))}
        </div>
        <p className="muted" style={{ margin: 0 }}>{cur.note}</p>
        {tab === 'walls' && (g.walls.length ? <SiteCards groups={g.walls} /> : <div className="empty">No logins or accounts waiting.</div>)}
        {tab === 'quick' && (g.quick.length ? <div className="list">{g.quick.map((j) => <Line key={j.id} j={j} />)}</div> : <div className="empty">No quick answers waiting.</div>)}
        {tab === 'rest' && (g.rest.length ? <div className="list">{g.rest.map((j) => <Line key={j.id} j={j} />)}</div> : <div className="empty">Nothing else is waiting.</div>)}
        {tab === 'stale' && (g.stale.length ? <>
          <div className="row-actions">
            <button className="btn btn-sm btn-secondary" onClick={() => act('check')}>Re-check all postings</button>
            <button className="btn btn-sm btn-quiet" onClick={() => window.confirm(`Skip all ${g.stale.length} stale jobs?`) && act('bulk', { ids: g.stale.map((j) => j.id), do: 'skipped' })}>Skip all {g.stale.length}</button>
          </div>
          <div className="list">{g.stale.map((j) => <Line key={j.id} j={j} />)}</div>
        </> : <div className="empty">Nothing stale.</div>)}
      </section>
      {!held.length && <div className="empty">Nothing is held. Everything the agent could do, it did.</div>}
    </main>
  )
}
