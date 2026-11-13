import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { getJSON, post, DONE, type State, type Reply } from './api'

type Toast = { id: number; title: string; body?: string; error?: boolean }
type Ctx = {
  state: State | null
  down: boolean
  refresh: () => Promise<void>
  act: (path: string, body?: unknown, raw?: Blob) => Promise<Reply>
  toast: (title: string, body?: string, error?: boolean) => void
}
const AppCtx = createContext<Ctx>(null as unknown as Ctx)
export const useApp = () => useContext(AppCtx)

/** Polls /api/state (fast while a task runs), turns changes into notifications, and runs actions. */
export function AppProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State | null>(null)
  const [down, setDown] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])
  const prev = useRef<State | null>(null)
  const timer = useRef<number>(0)
  const polling = useRef(false)

  const toast = useCallback((title: string, body?: string, error?: boolean) => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, title, body, error }])
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), error ? 12000 : 6000)
  }, [])

  const refresh = useCallback(async () => {
    window.clearTimeout(timer.current)
    try {
      const next = share(prev.current, await getJSON<State>('/api/state'))
      if (prev.current) notifyDiff(prev.current, next, toast)
      prev.current = next
      setState(next)
      setDown(false)
      if (polling.current) timer.current = window.setTimeout(refresh, next.tasks.some((t) => t.status === 'running') ? 3000 : 15000)
    } catch {
      setDown(true)
      timer.current = window.setTimeout(refresh, 5000)
    }
  }, [toast])

  useEffect(() => {
    // pushed updates from the server; if it can't stream (old server), fall back to polling
    const es = new EventSource('/api/events')
    es.onmessage = (e) => {
      const next = share(prev.current, JSON.parse(e.data) as State)
      if (prev.current) notifyDiff(prev.current, next, toast)
      prev.current = next
      setState(next)
      setDown(false)
    }
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED && !polling.current) { polling.current = true; refresh() }
      else setDown(true)                       // reconnecting by itself
    }
    return () => { es.close(); window.clearTimeout(timer.current) }
  }, [refresh, toast])

  const act = useCallback(async (path: string, body?: unknown, raw?: Blob) => {
    const r = await post(path, body, raw)
    if (r.error) toast("That didn't work", r.error, true)
    else if (r.message) toast(r.message)
    else if (r.task) toast(`Started: ${r.task.label}`, 'Follow it on Live, or stop it from Control.')
    await refresh()
    return r
  }, [refresh, toast])

  return (
    <AppCtx.Provider value={{ state, down, refresh, act, toast }}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.error ? ' error' : ''}`}><b>{t.title}</b>{t.body && <div className="small">{t.body}</div>}</div>
        ))}
      </div>
    </AppCtx.Provider>
  )
}

/** Same moments the old dashboard announced, as in-page toasts and (when the tab is hidden) system notifications. */
function notifyDiff(a: State, b: State, toast: (t: string, body?: string) => void) {
  const say = (title: string, body: string) => {
    toast(title, body)
    if ('Notification' in window && Notification.permission === 'granted' && document.hidden) new Notification(title, { body })
  }
  const was = Object.fromEntries(a.jobs.map((j) => [j.id, j.status]))
  for (const t of b.tasks) {
    const p = a.tasks.find((x) => x.id === t.id)
    if (p?.status === 'running' && t.status !== 'running')
      say(t.status === 'done' ? `Finished: ${t.label}` : `${t.status}: ${t.label}`, t.result.split('\n').slice(-2).join(' '))
  }
  const fresh = b.jobs.filter((j) => !(j.id in was))
  if (fresh.length) say(`${fresh.length} new job${fresh.length > 1 ? 's' : ''} found`, fresh.slice(0, 3).map((j) => j.company).join(', '))
  for (const j of b.jobs) {
    if (['new', 'needs-you', 'form-ready'].includes(was[j.id]) && j.status === 'ready-to-submit') say('Ready for your review', `${j.company}, ${j.title}`)
    if (was[j.id] && !DONE.includes(was[j.id]) && j.status === 'applied' && j.events.auto_detected) say('Submission noticed', `${j.company} moved to Submitted`)
    if (was[j.id] && was[j.id] !== 'closed' && j.status === 'closed') say('Posting closed', `${j.company}, ${j.title}`)
  }
  if (a.health.login !== false && b.health.login === false) say('Claude login expired', 'Sign in again from Control')
}

/** Keep the previous object for every job that didn't change, so memoised rows skip re-rendering. */
function share(prev: State | null, next: State): State {
  if (!prev) return next
  const old = new Map(prev.jobs.map((j) => [j.id, j]))
  next.jobs = next.jobs.map((j) => { const o = old.get(j.id); return o && JSON.stringify(o) === JSON.stringify(j) ? o : j })
  return next
}
