import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './styles.css'
import { AppProvider, useApp } from './store'
import { Palette, TopBar } from './components'
import Today from './pages/Today'
import Live from './pages/Live'
import Review from './pages/Review'
import Pipeline from './pages/Pipeline'
import Held from './pages/Held'
import Memory from './pages/Memory'
import Insights from './pages/Insights'
import Knowledge from './pages/Knowledge'
import Control from './pages/Control'

function Shell() {
  const { state, down } = useApp()
  const [palette, setPalette] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((p) => !p) } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return (
    <>
      <TopBar onSearch={() => setPalette(true)} />
      {down && <div className="toast error" style={{ margin: '12px 48px' }}><b>The dashboard server isn't answering.</b> It restarts by itself; this page reconnects when it's back.</div>}
      {!state ? <main className="main"><div className="empty">Loading your applications…</div></main> : (
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/live" element={<Live />} />
          <Route path="/review" element={<Review />} />
          <Route path="/review/:id" element={<Review />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/held" element={<Held />} />
          <Route path="/memory" element={<Memory />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/control" element={<Control />} />
        </Routes>
      )}
      <Palette open={palette} onClose={() => setPalette(false)} />
    </>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AppProvider><Shell /></AppProvider>
    </BrowserRouter>
  </StrictMode>,
)
