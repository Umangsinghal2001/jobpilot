import { useEffect, useState } from 'react'
import { useApp } from '../store'
import { getJSON, trackName } from '../api'

const NAMES: Record<string, string> = { profile: 'Profile', answers: 'Standard answers', resume: 'Resume text', targets: 'Target companies' }

export default function Knowledge() {
  const { state, act } = useApp()
  const s = state!
  const [file, setFile] = useState('profile')
  const [text, setText] = useState<string | null>(null)
  const [orig, setOrig] = useState('')
  const [name, setName] = useState('')
  useEffect(() => {
    setText(null)
    getJSON<{ text: string; name: string }>(`/api/file/${file}`).then((r) => { setText(r.text); setOrig(r.text); setName(r.name) })
  }, [file])
  const dirty = text !== null && text !== orig
  const pick = (f: string) => (!dirty || window.confirm('Discard your unsaved changes?')) && setFile(f)
  const upload = (track: string) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) act(`resume/${track}`, {}, f)
    e.target.value = ''
  }

  return (
    <main className="main">
      <section className="head">
        <span className="label">Knowledge</span>
        <h1 className="display">What the agent reads before every run.</h1>
        <p className="lead">Change it here; the next run uses it. Every save keeps the previous version.</p>
      </section>

      <section className="section">
        <div className="pills" role="tablist" aria-label="File">
          {Object.keys(s.files).map((f) => (
            <button key={f} role="tab" aria-selected={f === file} className="pill" onClick={() => pick(f)}
              style={{ border: 0, cursor: 'pointer', fontSize: 15, padding: '8px 18px', background: f === file ? 'var(--primary)' : 'var(--surface-mid)', color: f === file ? 'var(--white)' : undefined }}>{NAMES[f] ?? f}</button>
          ))}
        </div>
        <p className="muted" style={{ margin: 0 }}>{s.files[file]} <span className="small">({name})</span></p>
        {text === null ? <div className="empty">Opening…</div> : (
          <textarea className="box code" spellCheck={false} aria-label={NAMES[file]} value={text} onChange={(e) => setText(e.target.value)} />
        )}
        <div className="row-actions">
          <button className="btn btn-primary" disabled={!dirty} onClick={async () => { const r = await act(`file/${file}`, { text }); if (!r.error) setOrig(text ?? '') }}>Save</button>
          <button className="btn btn-quiet" disabled={!dirty} onClick={() => setText(orig)}>Discard changes</button>
        </div>
      </section>

      <section className="section" aria-labelledby="res-h">
        <div className="section-head"><h2 id="res-h" className="title-lg">Resumes</h2>
          <span className="muted">The agent picks a track for each job and uploads that PDF. Tracks without one use {s.resumes.master || 'your master resume'}.</span></div>
        <div className="grid">
          {s.resumes.tracks.map((t) => (
            <div key={t.track} className={`tile${t.own ? ' white' : ''}`} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <h3 style={{ fontSize: 20 }}>{trackName(t.track)}</h3>
              <span className="small muted">{t.jobs} job{t.jobs === 1 ? '' : 's'} so far · {t.own ? t.file : 'uses your master resume'}</span>
              <span className="row-actions">
                <label className="btn btn-sm btn-secondary" style={{ cursor: 'pointer' }}>
                  {t.own ? 'Replace PDF' : 'Upload a PDF'}
                  <input type="file" accept="application/pdf" hidden onChange={upload(t.track)} />
                </label>
                {t.own && <button className="btn btn-sm btn-quiet" onClick={() => window.confirm(`Remove the ${trackName(t.track)} PDF?`) && act(`resume-remove/${t.track}`)}>Remove</button>}
              </span>
            </div>
          ))}
        </div>
      </section>
    </main>
  )
}
