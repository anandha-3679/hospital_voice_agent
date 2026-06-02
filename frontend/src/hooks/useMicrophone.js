import { useRef, useState, useCallback } from 'react'

export function useMicrophone({ onChunk, onSpeechStart, onSilence, onStop }) {
  const [micState, setMicState] = useState('idle') // idle | requesting | active | error
  const [error, setError]       = useState(null)

  const ctxRef    = useRef(null)
  const sourceRef = useRef(null)
  const nodeRef   = useRef(null)
  const streamRef = useRef(null)

  // Keep callback refs current so start() never needs to be recreated
  const onChunkRef       = useRef(onChunk);       onChunkRef.current       = onChunk
  const onSpeechStartRef = useRef(onSpeechStart); onSpeechStartRef.current = onSpeechStart
  const onSilenceRef     = useRef(onSilence);     onSilenceRef.current     = onSilence
  const onStopRef        = useRef(onStop);        onStopRef.current        = onStop

  /** Send a VAD reset to the worklet — mic stays on, new turn begins. */
  const resetVAD = useCallback(() => {
    nodeRef.current?.port.postMessage({ type: 'reset' })
  }, [])

  /** Fully stop the mic (terminate). Does NOT send end_audio — caller's responsibility. */
  const stop = useCallback(() => {
    nodeRef.current?.disconnect()
    sourceRef.current?.disconnect()
    streamRef.current?.getTracks().forEach(t => t.stop())
    ctxRef.current?.close()

    nodeRef.current    = null
    sourceRef.current  = null
    streamRef.current  = null
    ctxRef.current     = null

    setMicState('idle')
    onStopRef.current?.()
  }, [])

  const start = useCallback(async () => {
    setError(null)
    setMicState('requesting')

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,   // kills speaker bleed → prevents echo barge-in
          noiseSuppression: true,
          autoGainControl:  true,
        },
        video: false,
      })
      streamRef.current = stream

      const ctx = new AudioContext({ sampleRate: 16000 })
      ctxRef.current = ctx

      await ctx.audioWorklet.addModule('/worklets/mic-processor.js')

      const source = ctx.createMediaStreamSource(stream)
      sourceRef.current = source

      const node = new AudioWorkletNode(ctx, 'mic-processor', {
        processorOptions: { targetSampleRate: 16000 },
      })
      nodeRef.current = node

      node.port.onmessage = (e) => {
        if (e.data instanceof ArrayBuffer) {
          onChunkRef.current?.(e.data)
        } else if (e.data?.type === 'speech_start') {
          onSpeechStartRef.current?.()
        } else if (e.data?.type === 'silence') {
          onSilenceRef.current?.()
        }
      }

      source.connect(node)
      setMicState('active')
    } catch (err) {
      setError(err.message)
      setMicState('error')
    }
  }, []) // stable — uses refs for all callbacks

  return { micState, error, start, stop, resetVAD }
}
