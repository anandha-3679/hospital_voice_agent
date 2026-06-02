import { useRef, useCallback, useState } from 'react'

const TTS_SAMPLE_RATE = 22050

export function useAudioPlayer() {
  const ctxRef          = useRef(null)
  const nextTimeRef     = useRef(0)
  const activeCountRef  = useRef(0)        // how many sources are still scheduled/playing
  const [speaking, setSpeaking] = useState(false)

  function getCtx() {
    if (!ctxRef.current || ctxRef.current.state === 'closed') {
      ctxRef.current      = new AudioContext({ sampleRate: TTS_SAMPLE_RATE })
      nextTimeRef.current = 0
      activeCountRef.current = 0
    }
    return ctxRef.current
  }

  function trackSource(source) {
    activeCountRef.current += 1
    setSpeaking(true)
    source.onended = () => {
      activeCountRef.current = Math.max(0, activeCountRef.current - 1)
      if (activeCountRef.current === 0) setSpeaking(false)
    }
  }

  const enqueue = useCallback(async (base64) => {
    if (!base64) return
    const ctx = getCtx()

    if (ctx.state === 'suspended') await ctx.resume()

    // base64 → Uint8Array → Int16Array → Float32Array
    const binary  = atob(base64)
    const bytes   = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)

    const int16   = new Int16Array(bytes.buffer)
    const float32 = new Float32Array(int16.length)
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768

    const buffer  = ctx.createBuffer(1, float32.length, TTS_SAMPLE_RATE)
    buffer.copyToChannel(float32, 0)

    const source  = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(ctx.destination)

    // Schedule gaplessly
    const now     = ctx.currentTime
    const startAt = Math.max(nextTimeRef.current, now + 0.04)
    source.start(startAt)
    nextTimeRef.current = startAt + buffer.duration

    trackSource(source)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const stop = useCallback(() => {
    if (ctxRef.current && ctxRef.current.state !== 'closed') {
      ctxRef.current.close()
      ctxRef.current = null
    }
    nextTimeRef.current    = 0
    activeCountRef.current = 0
    setSpeaking(false)
  }, [])

  return { enqueue, stop, speaking }
}
