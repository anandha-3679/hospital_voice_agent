import { useState, useEffect, useCallback, useRef } from 'react'
import OrbAnimation from './OrbAnimation'
import { useWebSocket } from '../hooks/useWebSocket'
import { useMicrophone } from '../hooks/useMicrophone'
import { useAudioPlayer } from '../hooks/useAudioPlayer'

export default function VoiceChat() {
  const [sessionId, setSessionId]       = useState(null)
  const [sessionReady, setSessionReady] = useState(false)
  const [sessionError, setSessionError] = useState(null)
  const [terminated, setTerminated]     = useState(false)
  const [messages, setMessages]         = useState([])
  const [errorBanner, setErrorBanner]   = useState(null)
  const bottomRef     = useRef(null)
  const autoListenRef = useRef(false)   // true once user starts first turn
  const resetVADRef   = useRef(null)    // populated after useMicrophone returns

  // ── Session ──────────────────────────────────────────────────────────────
  useEffect(() => {
    fetch('https://hospital-voice-agent-8zug.onrender.com/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patient_name: 'Guest' }),
    })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(data => { setSessionId(data.id); setSessionReady(true) })
      .catch(err => setSessionError(err.message))
  }, [])

  // ── WebSocket ────────────────────────────────────────────────────────────
  const { status, lastMessage, connect, disconnect, send, sendBinary, audioCallbackRef } = useWebSocket(sessionId)
  const { enqueue: enqueueAudio, stop: stopAudio, speaking } = useAudioPlayer()
  audioCallbackRef.current = enqueueAudio
  const speakingRef = useRef(speaking)
  speakingRef.current = speaking

  useEffect(() => {
    if (sessionReady && sessionId) connect()
  }, [sessionReady, sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!lastMessage) return
    const { type, text } = lastMessage

    if (type === 'transcript') {
      setMessages(prev => [...prev, { id: Date.now(), role: 'user', text }])
    }
    if (type === 'llm_sentence') {
      setMessages(prev => {
        const last = prev[prev.length - 1]
        if (last?.role === 'assistant')
          return [...prev.slice(0, -1), { ...last, text: last.text + ' ' + text }]
        return [...prev, { id: Date.now(), role: 'assistant', text }]
      })
    }
    if (type === 'cancelled')  stopAudio()
    if (type === 'error')      setErrorBanner(lastMessage.message)
  }, [lastMessage, stopAudio])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // ── Microphone ───────────────────────────────────────────────────────────
  const { micState, error: micError, start: startMic, stop: stopMic, resetVAD } = useMicrophone({

    onChunk: useCallback((buf) => sendBinary(buf), [sendBinary]),

    // Speech detected while TTS is playing → barge-in
    onSpeechStart: useCallback(() => {
      if (speakingRef.current) {
        stopAudio()
        send({ type: 'barge_in' })
        // VAD already in speech state — send start_audio for this new turn
        send({ type: 'start_audio' })
      }
    }, [send, stopAudio]),

    // Silence after speech → end current turn; auto-restart VAD for next turn
    onSilence: useCallback(() => {
      send({ type: 'end_audio' })
      if (autoListenRef.current) {
        setTimeout(() => {
          if (autoListenRef.current) {
            resetVADRef.current?.()
            send({ type: 'start_audio' })
          }
        }, 300)
      }
    }, [send]),

    onStop: useCallback(() => {}, []),  // mic stopped — nothing extra needed
  })

  resetVADRef.current = resetVAD  // break circular dep — ref is always up to date

  const isListening  = micState === 'active'
  const isConnected  = status === 'connected'
  const isProcessing = !isListening && !speaking &&
    messages.length > 0 && messages[messages.length - 1]?.role === 'user'

  // ── First mic click — starts the continuous loop ──────────────────────────
  async function handleMicClick() {
    if (terminated) return

    if (!isListening) {
      autoListenRef.current = true
      await startMic()
      send({ type: 'start_audio' })
    } else {
      // Manual early stop — send end_audio now, keep auto-listen for next turn
      send({ type: 'end_audio' })
      setTimeout(() => {
        if (autoListenRef.current) {
          resetVADRef.current?.()
          send({ type: 'start_audio' })
        }
      }, 300)
    }
  }

  // ── Terminate ─────────────────────────────────────────────────────────────
  function handleTerminate() {
    autoListenRef.current = false
    stopMic()
    stopAudio()
    disconnect()
    setTerminated(true)
  }

  // ── Status label ──────────────────────────────────────────────────────────
  function statusLabel() {
    if (sessionError)  return 'Failed to reach server'
    if (terminated)    return 'Session ended'
    if (!sessionReady) return 'Connecting…'
    if (!isConnected)  return 'Reconnecting…'
    if (isListening)   return 'Listening…'
    if (speaking)      return 'Speaking…'
    if (isProcessing)  return 'Processing…'
    return 'Tap mic to start'
  }

  const micDisabled = terminated || !isConnected

  return (
    <div className="w-full max-w-md h-[680px] flex flex-col rounded-2xl border border-white/10 bg-[#10101e] shadow-2xl overflow-hidden">

      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 shrink-0">
        <div>
          <h1 className="text-base font-semibold text-white tracking-tight">Voice Chat</h1>
          <p className="text-xs text-white/40">AI-powered voice assistant</p>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full transition-colors ${
            terminated            ? 'bg-white/20' :
            status === 'connected'    ? 'bg-green-500' :
            status === 'connecting'   ? 'bg-yellow-400 animate-pulse' : 'bg-white/30'
          }`} />
          <span className="text-xs text-white/30 capitalize">{terminated ? 'ended' : status}</span>
        </div>
      </div>

      {/* Error banner */}
      {errorBanner && (
        <div className="shrink-0 flex items-start gap-3 bg-red-950/80 border-b border-red-500/30 px-4 py-3">
          <span className="text-red-400 mt-0.5 shrink-0">⚠</span>
          <p className="text-xs text-red-300 leading-relaxed flex-1">{errorBanner}</p>
          <button onClick={() => setErrorBanner(null)} className="text-red-400/60 hover:text-red-300 text-lg leading-none shrink-0">×</button>
        </div>
      )}

      {/* Chat */}
      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3">
        {messages.length === 0 && (
          <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center">
            <OrbAnimation active={isListening || speaking} speaking={speaking} />
            <p className="text-sm text-white/40">{statusLabel()}</p>
            {(micError || sessionError) && (
              <p className="text-xs text-red-400">{micError || sessionError}</p>
            )}
          </div>
        )}

        {messages.map(msg => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-indigo-600 text-white rounded-br-sm'
                : 'bg-white/8 border border-white/10 text-white/85 rounded-bl-sm'
            }`}>
              {msg.text}
            </div>
          </div>
        ))}

        {messages.length > 0 && (isListening || speaking || isProcessing) && (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 px-4 py-2 rounded-2xl rounded-bl-sm bg-white/5 border border-white/10">
              <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
              <span className="text-xs text-white/50">{statusLabel()}</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Bottom bar */}
      <div className="shrink-0 border-t border-white/10 px-5 py-4 flex items-center justify-center gap-6">

        {/* Terminate */}
        <button
          onClick={handleTerminate}
          disabled={terminated}
          title="End session"
          className="w-9 h-9 rounded-full bg-red-600/20 hover:bg-red-600/50 border border-red-500/30
            flex items-center justify-center transition-all disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <span className="w-3 h-3 rounded-sm bg-red-500" />
        </button>

        {/* Mic */}
        <button
          onClick={handleMicClick}
          disabled={micDisabled}
          className={`w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-lg
            disabled:opacity-40 disabled:cursor-not-allowed
            ${isListening
              ? 'bg-red-600 shadow-red-900/50 scale-110 ring-4 ring-red-500/30'
              : speaking
                ? 'bg-teal-600 shadow-teal-900/50 scale-105'
                : 'bg-indigo-600 hover:bg-indigo-500 shadow-indigo-900/50'
            }`}
        >
          {isListening ? <StopIcon /> : <MicIcon />}
        </button>

        <div className="w-9 h-9" />
      </div>

    </div>
  )
}

function MicIcon() {
  return (
    <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
      <rect x="6" y="6" width="12" height="12" rx="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
