import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: the React app on :5173 talks to the dashboard server on :8765 (same API the FastAPI backend will keep).
const api = { target: 'http://127.0.0.1:8765', changeOrigin: true }

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': { ...api, ws: true }, '/artifacts': api } },
})
