/**
 * AudioWorklet processor — downsamples to 16 kHz, int16, VAD.
 *
 * VAD state machine per turn:
 *   waiting_for_speech → speaking → silence_detected
 *
 * Posts to main thread:
 *   ArrayBuffer              — int16 PCM chunk (always)
 *   { type: 'speech_start' } — speech threshold crossed (use for barge-in detection)
 *   { type: 'silence' }      — silence after speech (end of user turn)
 *
 * Accepts from main thread:
 *   { type: 'reset' }        — resets VAD for next turn without stopping the mic
 */
class MicProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this._targetRate = options.processorOptions?.targetSampleRate ?? 16000
    this._sourceRate = sampleRate
    this._ratio      = this._sourceRate / this._targetRate
    this._buf        = []
    this._chunkSize  = this._targetRate / 10  // 100 ms

    // VAD config
    this._speechRMS    = 0.015  // ~-36 dB — above this = speech
    this._silenceRMS   = 0.008  // ~-42 dB — below this = silence
    this._speechNeeded = 3      // chunks needed to confirm speech start (~300 ms)
    this._silenceLimit = 15     // chunks of silence to fire end of turn (~1.5 s)

    this._resetVAD()

    // Listen for reset commands from the main thread
    this.port.onmessage = (e) => {
      if (e.data?.type === 'reset') this._resetVAD()
    }
  }

  _resetVAD() {
    this._speechChunks  = 0
    this._silenceChunks = 0
    this._firedSpeech   = false
    this._firedSilence  = false
  }

  _rms(samples) {
    let sum = 0
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
    return Math.sqrt(sum / samples.length)
  }

  process(inputs) {
    const channel = inputs[0]?.[0]
    if (!channel) return true

    for (let i = 0; i < channel.length; i += this._ratio) {
      this._buf.push(channel[Math.floor(i)])
    }

    while (this._buf.length >= this._chunkSize) {
      const slice = this._buf.splice(0, this._chunkSize)
      const int16 = new Int16Array(slice.length)
      for (let j = 0; j < slice.length; j++) {
        int16[j] = Math.max(-32768, Math.min(32767, Math.round(slice[j] * 32767)))
      }

      this.port.postMessage(int16.buffer, [int16.buffer])

      if (!this._firedSilence) {
        const energy = this._rms(slice)

        if (energy >= this._speechRMS) {
          this._speechChunks++
          this._silenceChunks = 0

          if (!this._firedSpeech && this._speechChunks >= this._speechNeeded) {
            this._firedSpeech = true
            this.port.postMessage({ type: 'speech_start' })
          }
        } else if (energy < this._silenceRMS && this._firedSpeech) {
          this._silenceChunks++
          if (this._silenceChunks >= this._silenceLimit) {
            this._firedSilence = true
            this.port.postMessage({ type: 'silence' })
          }
        }
      }
    }

    return true
  }
}

registerProcessor('mic-processor', MicProcessor)
