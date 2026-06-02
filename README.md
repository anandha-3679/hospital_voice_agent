# City Care Hospital — Voice Assistant

---

## 1. Project Goal

A real-time, browser-based voice assistant for City Care Hospital that lets patients speak naturally to book appointments, check doctor availability, and get general medical guidance — without typing anything. The assistant speaks back using text-to-speech and the conversation continues hands-free turn by turn.

---

## 2. High-Level Architecture

```
Browser (React + Vite)
│
│  WebSocket (ws://localhost:8000/ws/{session_id})
│  Binary frames: int16 PCM @ 16 kHz
│  JSON frames:   control events + LLM sentences
│
FastAPI Backend (Python)
│
├── Sarvam AI  →  STT (saaras:v3, streaming WebSocket)
├── Groq       →  LLM (llama-3.1-8b-instant, tool-calling)
└── Sarvam AI  →  TTS (bulbul:v3, streaming HTTP)
```

---

## 3. Conversation Flow (One Turn)

```
1. User taps mic button
2. Browser opens mic (echo-cancelled)
3. AudioWorklet downsamples to 16 kHz int16, streams PCM chunks via WS
4. VAD detects silence → sends end_audio
5. Backend finalises STT → gets transcript + detected language
6. Groq LLM generates reply (with optional tool calls for patient/doctor data)
7. Each LLM sentence → streamed to TTS → base64 PCM chunks sent back via WS
8. Browser plays audio gaplessly via Web Audio API
9. VAD resets → mic auto-listens for next turn
10. User can barge-in mid-TTS: speech_start event → barge_in sent → TTS cut
11. Red terminate button ends the session
```

---

## 4. File Structure

```
voice_agent/
│
├── PRD.md                          ← this file
│
├── backend/
│   ├── api.py                      ← FastAPI app, WebSocket endpoint, pipeline orchestration
│   ├── stt.py                      ← Streaming STT via Sarvam WebSocket (saaras:v3)
│   ├── llm.py                      ← Groq LLM with agentic tool-calling loop
│   ├── tts.py                      ← Local TTS playback (legacy, not used by API)
│   ├── session.py                  ← Session create/load/save, conversation history
│   ├── tools.py                    ← Tool schemas + execution (appointments, doctors, wards)
│   ├── fake_db.py                  ← In-memory hospital database (patients, doctors, slots)
│   ├── recorder.py                 ← Audio recording utilities
│   └── .env                        ← SARVAM_API_KEY, GROQ_API_KEY
│
└── frontend/
    ├── vite.config.js              ← Vite + React + Tailwind v4 config
    ├── public/
    │   └── worklets/
    │       └── mic-processor.js    ← AudioWorklet: downsample, int16, VAD
    └── src/
        ├── main.jsx                ← React entry point
        ├── index.css               ← Tailwind import
        ├── App.jsx                 ← Root component, dark background
        ├── components/
        │   ├── VoiceChat.jsx       ← Main UI: chat, mic button, terminate, error banner
        │   └── OrbAnimation.jsx    ← Animated orb (idle / listening / speaking states)
        └── hooks/
            ├── useWebSocket.js     ← WS connect/disconnect, send, sendBinary, keepalive ping
            ├── useMicrophone.js    ← getUserMedia, AudioWorklet setup, VAD callbacks
            └── useAudioPlayer.js   ← Gapless PCM playback via Web Audio API, speaking state
```

---

## 5. File-by-File Breakdown

### Backend

| File | Responsibility |
|------|---------------|
| `api.py` | FastAPI app. Hosts `/ws/{session_id}` WebSocket. Orchestrates the STT → LLM → TTS pipeline. Manages connection lifecycle, barge-in cancellation, and audio saving. Also exposes REST routes: `POST /sessions`, `GET /sessions`, `GET /health`. |
| `stt.py` | `StreamingSTT` class. Opens a Sarvam WebSocket, streams int16 PCM frames, receives partial transcripts in a background thread. `finalize()` flushes the buffer and returns the best transcript + detected language. Includes a minimum-frame guard to prevent hallucination on short/silent clips. |
| `llm.py` | Groq LLM client. `generate_stream()` runs an agentic loop: detects if the user message needs tool calls (keyword match), calls Groq with or without tools, executes tools via `execute_tool()`, and yields sentence-level chunks for immediate TTS. System prompt is `Aarogya`, a hospital voice assistant. |
| `session.py` | Creates and persists sessions as JSON files under `backend/sessions/`. Each session stores patient name, session ID, and conversation history (role/content pairs passed to the LLM as context). |
| `tools.py` | Defines LLM tool schemas (JSON Schema format) and `execute_tool()` dispatcher. Tools: look up patients, doctors, appointments, wards from `fake_db`. |
| `fake_db.py` | Static in-memory hospital data: patient records, doctor schedules, appointment slots, ward information. Used only by tools — never called directly by the LLM. |
| `recorder.py` | Helpers for writing PCM audio to WAV files after barge-in. Used by the legacy local `tts.py`. |
| `tts.py` | Original local TTS playback via sounddevice (legacy). Not used by the WebSocket API — the API streams TTS directly to the browser. |

---

### Frontend

| File | Responsibility |
|------|---------------|
| `mic-processor.js` | AudioWorklet running off the main thread. Downsamples mic audio from browser sample rate to 16 kHz, converts float32 → int16, posts 100 ms PCM chunks. VAD state machine: detects `speech_start` (for barge-in) and `silence` (end of user turn). Accepts `{ type: 'reset' }` to restart VAD for the next turn without stopping the mic. |
| `useWebSocket.js` | Manages a single WebSocket connection to `ws://localhost:8000/ws/{sessionId}`. Exposes `send(json)`, `sendBinary(buffer)`, `connect()`, `disconnect()`, `status`, `lastMessage`. Sends a keepalive ping every 20 s. |
| `useMicrophone.js` | Requests mic with `echoCancellation: true`. Sets up AudioContext at 16 kHz + AudioWorkletNode. Routes worklet messages to `onChunk` / `onSpeechStart` / `onSilence` callbacks. Exposes `start()`, `stop()`, `resetVAD()`. |
| `useAudioPlayer.js` | Decodes base64 int16 PCM chunks → `AudioBuffer` → `AudioBufferSourceNode`. Schedules each chunk gaplessly using a `nextTime` pointer. Exposes `enqueue(base64)`, `stop()`, and `speaking` boolean (true while audio is actively playing). |
| `VoiceChat.jsx` | Main component. On mount: calls `POST /sessions` to create a session, then auto-connects WebSocket. Handles the continuous-listen loop: `onSilence` → sends `end_audio` → resets VAD → sends `start_audio`. `onSpeechStart` during TTS → barge-in. Renders chat bubbles (user right, assistant left), orb, mic toggle button, terminate button, and error banner. |
| `OrbAnimation.jsx` | Decorative orb with three visual states: idle (still, indigo), listening (pulsing indigo rings), speaking (pulsing teal rings + teal gradient). |
| `App.jsx` | Minimal root — dark background, renders `<VoiceChat />`. |

---

## 6. WebSocket Protocol

| Direction | Message | Meaning |
|-----------|---------|---------|
| Client → Server | `Binary frame` | Raw int16 PCM at 16 kHz |
| Client → Server | `{ type: "start_audio" }` | New speech segment starting |
| Client → Server | `{ type: "end_audio" }` | VAD detected silence — finalise STT |
| Client → Server | `{ type: "barge_in" }` | User interrupted TTS — cancel pipeline |
| Client → Server | `{ type: "ping" }` | Keepalive |
| Server → Client | `{ type: "transcript", text, lang }` | STT result |
| Server → Client | `{ type: "llm_sentence", text }` | One sentence from LLM |
| Server → Client | `{ type: "tts_chunk", data, sample_rate }` | Base64 int16 PCM audio |
| Server → Client | `{ type: "tts_done" }` | TTS finished |
| Server → Client | `{ type: "cancelled" }` | Pipeline cancelled (barge-in) |
| Server → Client | `{ type: "error", message }` | Any backend error (incl. Groq rate limits) |
| Server → Client | `{ type: "pong" }` | Keepalive reply |

---

## 7. External APIs

| Service | Used For | Model |
|---------|----------|-------|
| Sarvam AI | Speech-to-Text (streaming) | `saaras:v3` |
| Sarvam AI | Text-to-Speech (streaming HTTP) | `bulbul:v3` |
| Groq | LLM inference | `llama-3.1-8b-instant` |

---

## 8. Key Design Decisions

- **Continuous-listen loop** — mic stays open after the first tap; VAD automatically ends each turn and restarts listening, so the user never has to tap the mic again until they terminate the session.
- **Barge-in** — `echoCancellation: true` on `getUserMedia` suppresses speaker bleed. `speech_start` VAD event fires during TTS playback → `barge_in` sent to backend which cancels the running pipeline task.
- **Gapless TTS playback** — each PCM chunk is scheduled at exactly `previousChunk.startTime + previousChunk.duration` using Web Audio API, producing seamless audio without gaps or clicks.
- **STT hallucination guard** — `finalize()` returns an empty string if fewer than 25 frames (~1.5 s) were received, preventing Whisper-based models from hallucinating text from silence.
- **Error visibility** — any `{ type: "error" }` message from the backend (including Groq rate limit messages) surfaces as a dismissible red banner in the UI without disrupting the session.

---

## 9. Deployment

| Layer    | Platform | URL |
|----------|----------|-----|
| Frontend | Vercel   | https://hospital-voice-agent-4ssy.vercel.app/ |
| Backend  | Render   | https://hospital-voice-agent-8zug.onrender.com |

**Key backend endpoints (production):**
- `POST /sessions` — create a new session
- `WebSocket /ws/{session_id}` — real-time voice pipeline connection 



## 10. How to Run

**Backend**
```bash
cd backend
pip install -r requirements.txt
uvicorn api:main --reload --port 8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` — the frontend auto-creates a session and connects.
