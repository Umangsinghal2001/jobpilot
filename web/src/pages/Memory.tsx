import { useMemo, useState } from 'react'
import { useApp } from '../store'
import { ago, nice } from '../api'

export default function Memory() {
  const { state, act } = useApp()
  const s = state!
  const m = s.memory
  const plats = Object.entries(m.pending).filter(([, ls]) => ls.length).sort((a, b) => b[1].length - a[1].length)
  const [tab, setTab] = useState('')
  const cur = tab && m.pending[tab]?.length ? tab : plats[0]?.[0] ?? ''
  const [q, setQ] = useState('')
  const [edit, setEdit] = useState<{ key: string; old: string; text: string } | null>(null)

  // suggestions: two labels holding the same single answer, and platform names that differ only by punctuation
  const merges = useMemo(() => {
    const byAnswer: Record<string, string[]> = {}
    for (const a of m.answers) if (a.variants.length === 1) (byAnswer[a.variants[0].answer.toLowerCase()] ||= []).push(a.key)
    return Object.values(byAnswer).filter((ks) => ks.length > 1).slice(0, 3)
  }, [m.answers])
  const renames = useMemo(() => {
    const all = [...new Set([...Object.keys(m.platforms), ...Object.keys(m.pending)])]
    const key = (p: string) => p.replace(/[^a-z]/g, '').replace(/s$/, '')
    return all.flatMap((a) => all.filter((b) => a < b && key(a) === key(b)).map((b) => [a, b]))
  }, [m.platforms, m.pending])
  const label = (k: string) => m.answers.find((a) => a.key === k)?.label ?? k
  const answers = m.answers.filter((a) => !q || `${a.label} ${a.variants.map((v) => v.answer).join(' ')}`.toLowerCase().includes(q.toLowerCase()))

  return (
    <main className="main">
      <section className="head">
        <span className="label">Memory</span>
        <h1 className="display">What the agent has learned.</h1>
        <p className="lead">Your past answers, and notes about each site. The agent reads all of this before every run.</p>
      </section>

      <div className="split">
        <section className="body section" aria-labelledby="notes-h">
          <div className="section-head"><h2 id="notes-h" className="title-lg">New notes to review</h2><span className="muted">{plats.reduce((n, [, ls]) => n + ls.length, 0)}</span></div>
          <p className="muted" style={{ margin: 0 }}>Approve a note when it holds for every company on that site. Approved notes are followed on every run.</p>
          {plats.length ? <>
            <div className="pills" role="tablist" aria-label="Site">
              {plats.map(([p, ls]) => (
                <button key={p} role="tab" aria-selected={p === cur} className="pill" onClick={() => setTab(p)}
                  style={{ border: 0, cursor: 'pointer', background: p === cur ? 'var(--primary)' : 'var(--surface-mid)', color: p === cur ? 'var(--white)' : undefined }}>
                  {nice(p.split(' (')[0])} {ls.length}</button>
              ))}
            </div>
            <div className="rows">
              {(m.pending[cur] ?? []).map((l) => (
                <div key={l} className="tile" style={{ padding: '18px 22px', display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'center' }}>
                  <span style={{ flex: '1 1 320px' }}>{l}</span>
                  <span className="row-actions">
                    <button className="btn btn-sm btn-primary" onClick={() => act('memory', { kind: 'lesson', ats: cur, lesson: l, action: 'approve' })}>Approve</button>
                    <button className="btn btn-sm btn-quiet" onClick={() => act('memory', { kind: 'lesson', ats: cur, lesson: l, action: 'reject' })}>Reject</button>
                  </span>
                </div>
              ))}
            </div>
          </> : <div className="empty">No new notes. The agent adds them after each run.</div>}

          {renames.map(([a, b]) => (
            <div key={a + b} className="tile" style={{ background: 'var(--mint)', display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
              <span style={{ flex: '1 1 300px' }}><b>{a}</b> and <b>{b}</b> look like the same site.</span>
              <button className="btn btn-sm btn-primary" onClick={() => act('memory', { kind: 'rename', old: a, new: b })}>Merge into {b}</button>
            </div>
          ))}

          <h2 className="title-md" style={{ marginTop: 16 }}>Approved notes</h2>
          <div className="pills">{Object.entries(m.platforms).filter(([, ls]) => ls.length).map(([p, ls]) => <span key={p} className="pill">{nice(p)} {ls.length}</span>)}</div>
        </section>

        <section className="side section" aria-labelledby="bank-h">
          <div className="section-head"><h2 id="bank-h" className="title-lg">Answer bank</h2><span className="muted">{m.answers.length}</span></div>
          <label className="field">Search your answers<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="phone, notice, visa…" /></label>
          {merges.map((ks) => (
            <div key={ks.join()} className="tile peach" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <span>{ks.map(label).join(' and ')} hold the same answer.</span>
              <button className="btn btn-sm btn-peach" style={{ alignSelf: 'flex-start' }} onClick={async () => { for (const k of ks.slice(1)) await act('memory', { kind: 'merge', src: k, dst: ks[0] }) }}>Merge them</button>
            </div>
          ))}
          <ul className="dots">
            {answers.map((a) => (
              <li key={a.key}><span className="dot" style={{ marginTop: 10 }} />
                <div style={{ flexGrow: 1, minWidth: 0 }}>
                  <div className="small muted">{a.label}</div>
                  {a.variants.map((v) => edit?.key === a.key && edit.old === v.answer ? (
                    <div key={v.answer} style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '6px 0' }}>
                      <textarea className="box" style={{ minHeight: 70 }} value={edit.text} onChange={(e) => setEdit({ ...edit, text: e.target.value })} aria-label={`Answer for ${a.label}`} />
                      <span className="row-actions">
                        <button className="btn btn-sm btn-primary" onClick={() => act('memory', { kind: 'answer', key: a.key, old: v.answer, new: edit.text }).then(() => setEdit(null))}>Save</button>
                        <button className="btn btn-sm btn-quiet" onClick={() => setEdit(null)}>Cancel</button>
                      </span>
                    </div>
                  ) : (
                    <div key={v.answer} style={{ display: 'flex', flexWrap: 'wrap', gap: '0 10px', alignItems: 'baseline' }}>
                      <span style={{ overflowWrap: 'anywhere' }}>{v.answer}</span>
                      <span className="small" style={{ color: 'var(--outline)' }}>{v.companies.join(', ')}</span>
                      <button className="btn btn-quiet small" style={{ height: 28 }} onClick={() => setEdit({ key: a.key, old: v.answer, text: v.answer })}>Edit</button>
                      <button className="btn btn-quiet small" style={{ height: 28 }} onClick={() => window.confirm('Delete this saved answer?') && act('memory', { kind: 'answer', key: a.key, old: v.answer, new: null })}>Delete</button>
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ul>

          <h2 className="title-md" style={{ marginTop: 16 }}>Undo history</h2>
          <p className="small muted" style={{ margin: 0 }}>Memory as it was before each change, by you or by an agent run. Restoring can be undone too.</p>
          <div className="rows">
            {m.snapshots.slice(0, 6).map((sn) => (
              <div key={sn.name} className="tile" style={{ padding: '14px 18px', display: 'flex', gap: 12, alignItems: 'center' }}>
                <span style={{ flexGrow: 1 }}>{ago(sn.when)}<span className="small muted"> — {sn.answers} answers, {sn.notes} notes</span></span>
                <button className="btn btn-sm btn-secondary" onClick={() => window.confirm('Restore memory to this point?') && act('memory', { kind: 'restore', name: sn.name })}>Restore</button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  )
}
