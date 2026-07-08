import { useState, useEffect, useRef } from 'react'
import Orb from './components/Orb'

export default function App() {
  const [jarvisState, setJarvisState] = useState({
    state: 'idle', text: '', recentCommands: [], memory: {},
  })
  
  // 🟢 NEW: Keep a persistent reference to the WebSocket
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>
    function connect() {
      try {
        const ws = new WebSocket('ws://localhost:8765')
        wsRef.current = ws // Store reference
        
        ws.onopen = () => clearTimeout(reconnectTimer)
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            if (data.type === 'state') {
              setJarvisState(p => ({ ...p, state: data.value || 'idle', text: data.text || '' }))
            } else if (data.type === 'recent_commands') {
              setJarvisState(p => ({ ...p, recentCommands: data.value || [] }))
            } else if (data.type === 'memory') {
              setJarvisState(p => ({ ...p, memory: data.value || {} }))
            }
          } catch {}
        }
        ws.onclose = () => { reconnectTimer = setTimeout(connect, 2000) }
      } catch {
        reconnectTimer = setTimeout(connect, 2000)
      }
    }
    connect()
    return () => { wsRef.current?.close(); clearTimeout(reconnectTimer) }
  }, [])

  // 🟢 NEW: Function to pass down to Orb.tsx
  const sendToBackend = (command: string, payload: any = {}) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "ui_command", command, payload }))
    }
  }

  return (
    <div style={{ width: '100vw', height: '100vh', position: 'fixed', top: 0, left: 0, background: 'transparent', pointerEvents: 'none' }}>
      <Orb 
        state={jarvisState.state as any} 
        transcript={jarvisState.text} 
        recentCommands={jarvisState.recentCommands} 
        memory={jarvisState.memory} 
        sendCommand={sendToBackend} // 👈 Passed to Orb
      />
    </div>
  )
}