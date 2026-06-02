import { useEffect, useRef, useState, useCallback } from 'react'

const WS_BASE = 'ws://localhost:8000/ws'

export function useWebSocket(sessionId) {
  const [status, setStatus] = useState('disconnected') // disconnected | connecting | connected | error
  const [lastMessage, setLastMessage] = useState(null)
  const wsRef = useRef(null)
  const pingRef = useRef(null)

  const disconnect = useCallback(() => {
    clearInterval(pingRef.current)
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
      wsRef.current = null
    }
    setStatus('disconnected')
  }, [])

  const connect = useCallback(() => {
    if (!sessionId) return
    if (wsRef.current) disconnect()

    setStatus('connecting')
    const ws = new WebSocket(`${WS_BASE}/${sessionId}`)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('connected')
      // keepalive ping every 20 s
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: 'ping' }))
      }, 20000)
    }

    ws.onmessage = (e) => {
      try {
        setLastMessage(JSON.parse(e.data))
      } catch {
        setLastMessage({ type: 'raw', data: e.data })
      }
    }

    ws.onerror = () => setStatus('error')

    ws.onclose = () => {
      clearInterval(pingRef.current)
      setStatus('disconnected')
      wsRef.current = null
    }
  }, [sessionId, disconnect])

  const send = useCallback((payload) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof payload === 'string' ? payload : JSON.stringify(payload))
      return true
    }
    return false
  }, [])

  const sendBinary = useCallback((buffer) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(buffer)
      return true
    }
    return false
  }, [])

  useEffect(() => () => disconnect(), [disconnect])

  return { status, lastMessage, connect, disconnect, send, sendBinary }
}
