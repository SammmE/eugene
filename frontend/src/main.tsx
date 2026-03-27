import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

function connectFrontendReloadSocket() {
  if (import.meta.env.DEV) {
    return
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/frontend-reload`)

  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data) as { type?: string }
      if (payload.type === 'frontend.reload') {
        window.location.reload()
      }
    } catch {
      // Ignore non-JSON messages.
    }
  }

  socket.onclose = () => {
    window.setTimeout(connectFrontendReloadSocket, 1000)
  }
}

connectFrontendReloadSocket()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
