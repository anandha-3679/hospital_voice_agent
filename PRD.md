# Hospital Voice Agent — Product Requirements Document

**Version**: 1.1  
**Date**: 2026-05-28  
**Status**: Draft

---

## 1. Overview

A real-time multilingual voice agent for hospital environments. Patients and staff speak naturally; the agent responds by voice. It handles general hospital queries directly and fetches patient/doctor/appointment data from the hospital management system via MCP tools.

---

## 2. Goals

- Sub-3-second end-to-end latency from end-of-user-speech to first audio out.
- Support Hindi, Telugu, English, and mixed-language (code-switching) input/output.
- Seamless integration with hospital MS SQL database via MCP tool layer.
- No audio transcoding overhead — raw PCM from browser, no Opus/WebM.
- Graceful degradation: API failures produce a spoken fallback, never a silent hang.

---

## 3. Non-Goals (v1)

- Mobile native app (web browser only for now).
- Wake word detection ("Hey Hospital").
- Multi-party/conference calls.
- Voice biometric authentication.
- Real-time MS SQL integration (Phase 1 uses dummy MCP tools).

---

## 4. Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| **Audio capture** | Web Audio API + AudioWorklet | Raw PCM 16kHz/16-bit/mono, no transcoding needed |
| **Transport** | WebSocket (binary frames) | Simple, bidirectional, works everywhere |
| **VAD** | Silero VAD (Python, server-side) | Accurate, runs on CPU, no cloud dependency |
| **STT** | Sarvam Saaras v3 | Best-in-class for Hindi/Telugu/English + code-switch |
| **LLM** | Groq — Llama 3.3 70B | Low-latency inference, supports tool calling |
| **MCP** | FastMCP (Python) | Tool routing to hospital DB, clean schema definition |
| **TTS** | Sarvam Bulbul v3 | Native Indian language voices, low latency |
| **Backend** | FastAPI + WebSocket | Async, lightweight, pairs well with FastMCP |
| **Database** (future) | MS SQL via `aioodbc` / `pyodbc` | Hospital management system |

---

## 5. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        BROWSER                          │
│                                                         │
│  Microphone                                             │
│      │                                                  │
│      ▼                                                  │
│  AudioContext                                           │
│      │                                                  │
│      ▼                                                  │
│  AudioWorkletNode (pcm-processor.js)                    │
│   - Converts Float32 → Int16 PCM                        │
│   - 512-sample chunks at 16kHz                          │
│   - Sends binary frames over WebSocket                  │
│      │                                                  │
│      │  WebSocket binary (PCM Int16, 16kHz, mono)       │
│      ▼                                                  │
│  WebSocket client                                       │
│   - Receives binary audio from server                   │
│   - Pushes PCM chunks into AudioPlaybackQueue           │
│     (see Section 6.8 — gap-free sequential playback)    │
└────────────────────┬────────────────────────────────────┘
                     │  ws://host/ws/{session_id}
                     │
┌────────────────────▼────────────────────────────────────┐
│                    FASTAPI BACKEND                       │
│                                                         │
│  WebSocket handler                                      │
│      │                                                  │
│      ▼                                                  │
│  Session Manager                                        │
│   - Creates/retrieves SessionState per connection       │
│   - Stores: language, history, vad_buffer,              │
│             tts_playing, tts_cancel_event               │
│      │                                                  │
│      ▼                                                  │
│  Silero VAD                                             │
│   - Processes each 512-sample chunk                     │
│   - Buffers frames while speech_prob > 0.5              │
│   - Triggers end-of-speech after 400ms silence          │
│   - If tts_playing=True and speech detected → barge-in  │
│      │                                                  │
│      │ end-of-speech: flush buffer                      │
│      ▼                                                  │
│  STT — Sarvam Saaras v3                                 │
│   - Input: PCM buffer (16kHz, 16-bit, mono)             │
│   - Output: transcript + language_code                  │
│   - Updates session.language if confident               │
│      │                                                  │
│      ▼                                                  │
│  LLM — Groq Llama 3.3 70B                               │
│   - System prompt: hospital assistant persona           │
│   - Receives: transcript + last 6 turns history         │
│   - Streams response tokens                             │
│   - Two paths:                                          │
│     (A) Plain text → sentence chunker → TTS             │
│     (B) Tool call → FastMCP → response → TTS            │
│      │                                                  │
│   ┌──┴──────────────────┐                               │
│   │                     │                               │
│   ▼                     ▼                               │
│ Path A               Path B                             │
│ Plain response       Tool call detected                 │
│   │                     │                               │
│   │               FastMCP server                        │
│   │                - patient_lookup()                   │
│   │                - doctor_schedule()                  │
│   │                - appointment_status()               │
│   │                - bed_availability()                 │
│   │                     │                               │
│   └──────────┬──────────┘                               │
│              │                                          │
│              ▼                                          │
│   Sentence Chunker                                      │
│    - Splits LLM stream on . ? ! । \n                    │
│    - Min chunk: 15 chars (avoid single-word TTS calls)  │
│              │                                          │
│              ▼  (per sentence, streamed)                │
│   TTS — Sarvam Bulbul v3                                │
│    - language_code from session state                   │
│    - Returns PCM audio bytes                            │
│    - Checks tts_cancel_event before each chunk          │
│              │                                          │
│              │  WebSocket binary (PCM audio)            │
│              ▼                                          │
│   WebSocket → Browser                                   │
└─────────────────────────────────────────────────────────┘
```

---

## 6. Audio Pipeline Detail

### 6.1 Browser — Audio Capture (AudioWorklet)

**File**: `frontend/pcm-processor.js` (AudioWorkletProcessor)

```
Microphone (any sample rate, browser default)
    │
    ▼
AudioContext (resampled to 16000 Hz)
    │
    ▼
AudioWorkletNode
    - process() called every 128 samples by browser
    - Accumulate into 512-sample chunks
    - Convert Float32Array → Int16Array
      formula: sample = Math.max(-1, Math.min(1, f32)) * 32767
    - postMessage to main thread → WebSocket.send(int16.buffer)
```

**Why AudioWorklet over ScriptProcessorNode**: ScriptProcessorNode runs on the main thread and causes dropouts under load. AudioWorklet runs on a dedicated audio thread. ScriptProcessorNode is also deprecated.

**Why 512 samples at 16kHz**: ~32ms per chunk. Fine-grained enough for VAD to detect speech boundaries accurately without excessive WebSocket overhead.

#### AudioContext Resampling Risk — Cross-Browser

This is the biggest hidden risk in the capture pipeline. Different browsers report different native sample rates and resample differently when you request 16kHz.

| Browser | Typical native rate | Behaviour with `sampleRate: 16000` |
|---|---|---|
| Chrome | 48000 Hz | Resamples internally, delivers 16kHz reliably |
| Edge (Chromium) | 48000 Hz | Same as Chrome |
| Firefox | Follows OS audio device rate (44100 or 48000) | **May ignore `sampleRate` hint** and deliver at native rate |
| Safari | 44100 Hz | Respects `sampleRate` but resampler quality varies |

**Problem**: If Firefox delivers 48kHz PCM but your server expects 16kHz, Silero VAD receives 3× the samples per chunk, speech detection timestamps are wrong, and Sarvam STT will transcribe at wrong pitch.

**Mitigation (required before Phase 1 exit)**:

```javascript
// audio_client.js — safe AudioContext init
const audioCtx = new AudioContext({ sampleRate: 16000 });

// After context is created, verify the actual rate:
if (audioCtx.sampleRate !== 16000) {
    console.warn(`AudioContext gave ${audioCtx.sampleRate}Hz, expected 16000. Will resample in worklet.`);
    // Pass actualSampleRate to pcm-processor.js via AudioWorkletNode options
}
```

```javascript
// pcm-processor.js — resample Float32 in worklet if needed
// If actualSampleRate !== 16000, downsample by integer or fractional ratio
// using a simple linear interpolation resampler before Int16 conversion.
// This avoids depending on the browser's resampler being correct.
```

**Test matrix (mandatory before Phase 1 exit)**:
- [ ] Chrome (Windows) — confirm `audioCtx.sampleRate === 16000`
- [ ] Edge (Windows) — confirm `audioCtx.sampleRate === 16000`
- [ ] Firefox (Windows) — log actual rate, verify worklet resampler kicks in
- [ ] (Optional) Safari (Mac) — confirm quality at 44100→16000 downsample

**Server-side guard**: Log the first chunk's byte count on connect. If `len(chunk) != 1024` (not 512 Int16 samples), reject with an error frame and surface to the client — do not silently process mismatched audio.

### 6.2 Server — VAD (Silero VAD)

- Model: `silero_vad` (ONNX, CPU inference, ~1ms per chunk)
- Input: 512 samples (must match — Silero requires 256 or 512 at 16kHz)
- `speech_prob > 0.5` → speech frame, append to `vad_buffer`
- `speech_prob < 0.5` for 400ms continuously → end-of-speech
- On end-of-speech: concatenate `vad_buffer` → send to STT, clear buffer
- **Barge-in**: VAD runs even when `tts_playing=True`. If speech detected, set `tts_cancel_event`, clear TTS queue, restart pipeline.

### 6.3 STT — Sarvam Saaras v3

- Endpoint: `POST /v1/speech:recognize` (Sarvam API)
- Input: WAV bytes (PCM wrapped in WAV header) or raw PCM
- Config: `sample_rate=16000`, `language_code="unknown"` (auto-detect)
- Output: `{ transcript: str, language_code: "hi"|"te"|"en" }`
- Empty transcript (noise, silence) → discard, do not call LLM
- Timeout: 5 seconds hard limit; on timeout → TTS "Sorry, I didn't catch that."

### 6.4 Language State Management

```python
# Priority rules for language_code updates:
# 1. If session.language is None → set from STT response
# 2. If STT confidence is high → update session.language
# 3. If STT returns "en" but session.language is "hi" → keep "hi" 
#    (user likely code-switching, not changing language)
# 4. Pass session.language to Bulbul TTS for consistent voice output
```

Supported language codes: `hi` (Hindi), `te` (Telugu), `en` (English).  
Bulbul voice mapping:
- `hi` → `meera` or `maya` (female) / `arjun` (male)
- `te` → `pavithra` (female) / `manu` (male)  
- `en` → `meera` (Indian English accent)

### 6.5 LLM — Groq Llama 3.3 70B

**System prompt structure**:
```
You are a hospital voice assistant. You speak in {language}.
You help patients and staff with: appointments, doctor availability, 
patient records, ward information, and general hospital queries.
For data lookups (patient info, doctor schedules, beds, appointments),
use the provided tools. For general questions, answer directly.
Keep responses concise — they will be spoken aloud.
Do not use markdown, bullet points, or lists in your responses.
Speak in complete sentences suitable for voice output.
```

**Tool calling**: Llama 3.3 70B supports native function/tool calling.  
Tools are defined in FastMCP and passed as `tools` parameter to Groq API.

**History**: Last 6 turns (3 user + 3 assistant). Trimmed FIFO.

**Streaming**: Use `stream=True`. Accumulate tokens → sentence chunker → TTS.  
Do not wait for full LLM response before starting TTS.

**Sentence chunker flush rules** (all are OR conditions — first hit wins):

| Condition | Flush trigger |
|---|---|
| Punctuation boundary | `.` `?` `!` `।` followed by space or end of token |
| Max character count | Accumulated chars > 80 |
| Max wait timeout | No punctuation received in last 1.0 second |
| Stream end | LLM response complete, flush remainder regardless of length |

The timeout guard prevents the chunker from waiting forever on unpunctuated speech like "yes yes yes yes yes..." — without it, TTS never fires until the LLM finishes the entire response, destroying the streaming benefit.

```python
# sentence_chunker.py — flush logic pseudocode
async def stream(token_gen):
    buf = ""
    last_flush = time.monotonic()
    async for token in token_gen:
        buf += token
        age = time.monotonic() - last_flush
        if hits_punctuation(buf) or len(buf) > 80 or age > 1.0:
            if len(buf) >= TTS_MIN_CHUNK_CHARS:
                yield buf.strip()
                buf = ""
                last_flush = time.monotonic()
    if buf.strip():
        yield buf.strip()  # flush remainder
```

### 6.8 Frontend — Audio Playback Queue

Receiving TTS audio as multiple binary WebSocket frames requires careful sequencing. Naive approaches produce gaps (silence between sentences), overlaps (two `AudioBufferSourceNode`s playing simultaneously), or crackling (buffer underruns).

**Strategy: scheduled sequential playback**

```
WebSocket binary frame arrives
    │
    ▼
Decode Int16 PCM → Float32Array
    │
    ▼
AudioPlaybackQueue
    - Maintains: nextStartTime (AudioContext timestamp)
    - On first chunk: nextStartTime = audioCtx.currentTime + 0.05 (small warmup)
    - On each chunk:
        buffer = audioCtx.createBuffer(1, samples, 16000)
        buffer.copyToChannel(float32data, 0)
        source = audioCtx.createBufferSource()
        source.buffer = buffer
        source.connect(audioCtx.destination)
        source.start(nextStartTime)
        nextStartTime += buffer.duration
    - On barge_in_ack JSON frame:
        stop all scheduled sources immediately
        reset nextStartTime = 0
```

**Why this works**:
- `source.start(scheduledTime)` lets the Web Audio API handle gapless scheduling internally — no JS timer jitter.
- Each sentence chunk is scheduled to begin exactly where the previous one ends.
- `barge_in_ack` clears the queue and resets the clock so the bot goes silent instantly.

**Edge cases**:
- `audioCtx.currentTime` may be 0 until first user gesture (browser autoplay policy). Start AudioContext on button click, not on page load.
- If a TTS chunk arrives late (network jitter), `nextStartTime` may already be in the past → clamp: `source.start(Math.max(nextStartTime, audioCtx.currentTime + 0.01))` and resync `nextStartTime`.
- On Firefox: AudioContext sample rate may not match 16kHz — resample in queue using `OfflineAudioContext` if `audioCtx.sampleRate !== 16000`.

---

### 6.6 MCP — FastMCP Tool Layer

**Server**: Runs as a subprocess or in-process, accessed via MCP protocol.

**Tools (Phase 1 — dummy)**:

```python
@mcp.tool()
def patient_lookup(patient_id: str) -> dict:
    """Fetch patient details by ID."""
    # Returns hardcoded mock data

@mcp.tool()
def doctor_schedule(doctor_id: str, date: str) -> dict:
    """Get doctor's availability for a given date."""

@mcp.tool()
def appointment_status(appointment_id: str) -> dict:
    """Check status of a specific appointment."""

@mcp.tool()
def bed_availability(ward: str) -> dict:
    """Check available beds in a ward."""

@mcp.tool()
def search_doctor_by_specialty(specialty: str) -> list:
    """Find doctors by specialty."""
```

**Phase 2**: Replace dummy implementations with `aioodbc` queries against MS SQL.  
Schema will be derived from the hospital DB column/datatype PDF provided separately.

### 6.7 TTS — Sarvam Bulbul v3

- Endpoint: `POST /v1/text:synthesize` (Sarvam API)
- Input: sentence string + `language_code` + `speaker`
- Output: PCM audio bytes (or MP3, decode server-side to PCM)
- Called per sentence chunk (not full response) for streaming effect
- Before each chunk send: check `tts_cancel_event.is_set()` → abort if barge-in
- Set `session.tts_playing = True` before first chunk, `False` after last

---

## 7. Session State

```python
@dataclass
class ConversationContext:
    """
    Structured memory extracted from conversation — persists across turns.
    Injected into LLM system prompt as a fact block, separate from raw history.
    Prevents the LLM from re-asking for information already provided.
    """
    patient_id: str | None = None
    department: str | None = None        # e.g. "cardiology", "OPD"
    appointment_date: str | None = None  # ISO date string
    doctor_name: str | None = None
    chief_complaint: str | None = None   # why patient is calling


@dataclass
class SessionState:
    session_id: str
    language: str | None = None          # "hi", "te", "en"
    history: list[dict] = field(default_factory=list)  # last 6 turns (raw chat)
    context: ConversationContext = field(default_factory=ConversationContext)
    vad_buffer: list[bytes] = field(default_factory=list)
    tts_playing: bool = False
    tts_cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    silence_frames: int = 0
    connected_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
```

**Why two memory layers**:

Raw `history` (6 turns) captures conversational flow but forgets earlier facts as it rolls over. `ConversationContext` is a structured fact store that persists for the entire session and never gets trimmed. Once a patient says "I'm calling about appointment ID A123", that goes into `context.appointment_id` — the LLM won't ask again even after 10 turns.

**LLM prompt injection**:
```
# Injected above history, below system prompt:
Known context:
- Patient ID: {context.patient_id or "unknown"}
- Department: {context.department or "not mentioned"}
- Appointment date: {context.appointment_date or "not mentioned"}
- Doctor: {context.doctor_name or "not mentioned"}
```

**Context extraction**: After each LLM response, run a lightweight extraction pass — either a regex on the transcript or a second small LLM call — to populate `ConversationContext` fields. Do not rely on the main LLM to self-report this.

Session is keyed by `session_id` (UUID in WebSocket URL).  
Sessions are garbage collected 10 minutes after last activity (`last_activity` timestamp).

---

## 8. Project Structure

```
hospital_voice_agent/
│
├── PRD.md                            ← this file
│
├── frontend/
│   ├── index.html                    ← mic button, status indicator, audio playback
│   ├── audio_client.js               ← WebSocket client, AudioWorklet setup, playback
│   └── pcm-processor.js             ← AudioWorkletProcessor (Float32→Int16, chunking)
│
├── backend/
│   ├── main.py                       ← FastAPI app, /ws/{session_id} WebSocket route
│   ├── session.py                    ← SessionState dataclass, session registry
│   ├── config.py                     ← API keys, VAD thresholds, constants
│   │
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── vad.py                    ← Silero VAD wrapper, barge-in logic
│   │   ├── stt.py                    ← Sarvam Saaras v3 client
│   │   ├── llm.py                    ← Groq client, tool routing, history management
│   │   ├── tts.py                    ← Sarvam Bulbul v3 client, streaming per sentence
│   │   └── sentence_chunker.py      ← Splits LLM token stream into TTS-ready sentences
│   │
│   └── mcp/
│       ├── server.py                 ← FastMCP server setup
│       └── tools/
│           ├── __init__.py
│           ├── patient.py            ← patient_lookup, appointment_status
│           ├── doctor.py             ← doctor_schedule, search_doctor_by_specialty
│           └── ward.py              ← bed_availability
│
├── .env                              ← SARVAM_API_KEY, GROQ_API_KEY
└── requirements.txt
```

---

## 9. API Contracts

### 9.1 WebSocket Messages

**Client → Server**:
- Binary frame: raw PCM Int16, 16kHz, mono, 512 samples (1024 bytes) per frame
- Text frame (JSON): `{ "type": "ping" }` for keepalive

**Server → Client**:
- Binary frame: raw PCM Int16 audio (TTS output), streamed per sentence
- Text frame (JSON):
  ```json
  { "type": "transcript", "text": "...", "language": "hi" }
  { "type": "response_start" }
  { "type": "response_end" }
  { "type": "error", "message": "..." }
  { "type": "barge_in_ack" }
  ```

### 9.2 Internal Pipeline (async)

```
ws_handler → vad.process_chunk(pcm_bytes) 
           → stt.transcribe(pcm_buffer) → str
           → llm.generate(transcript, history, tools) → AsyncGenerator[str]
           → sentence_chunker.stream(token_gen) → AsyncGenerator[str]
           → tts.synthesize(sentence, language) → bytes
           → ws.send_bytes(audio)
```

---

## 10. Rate Limiting Strategy

### Groq (Llama 3.3 70B)
- Free tier: 30 req/min, 14,400 req/day
- Implementation: token bucket per session, refill 1 token/2sec, max 5 tokens
- On bucket empty: TTS "I'm processing a lot right now, please wait a moment." → wait
- On 429 response: exponential backoff (1s, 2s, 4s), max 3 retries, then error TTS

### Sarvam (STT + TTS)
- Check actual rate limits from Sarvam dashboard
- STT and TTS share or have separate quotas — track independently
- Global semaphore: max 10 concurrent Sarvam API calls (adjust based on plan)
- Per-session: max 1 concurrent STT call (VAD prevents double-trigger naturally)

### Hospital Overload Protection
- Max concurrent WebSocket sessions: 50 (configurable)
- Reject new connections beyond limit with HTTP 503
- Per-session idle timeout: 10 minutes (no audio received)

---

## 11. Error Handling

| Failure | Behavior |
|---|---|
| STT returns empty transcript | Discard silently, resume listening |
| STT API timeout (>5s) | TTS: "Sorry, I didn't catch that. Please repeat." |
| STT API error (5xx) | TTS: "I'm having trouble hearing you. Please try again." |
| LLM API error / 429 | TTS: "Please give me a moment." + retry with backoff |
| LLM tool call fails | TTS: "I couldn't fetch that information right now." |
| TTS API error | Send error JSON frame to client, log for monitoring |
| WebSocket disconnect | Cancel all in-flight tasks, clean up session, log |
| VAD no speech for 60s | Send `{ "type": "idle_warning" }` to client |
| VAD buffer overrun (>30s) | Discard oldest frames, log warning |

---

## 12. Multilingual Behavior

### Language Detection
- Sarvam STT auto-detects language per utterance
- Session language is set on first confident detection
- Subsequent turns update language only if STT confidence > threshold

### Code-Switching Rules
- Mixed input (e.g., "Mera appointment kab hai for Dr. Sharma?") is handled natively by Saaras v3
- LLM system prompt instructs: "Respond in {session.language}. If user mixes languages, continue in their dominant language."
- TTS voice is fixed to session language for consistency (don't switch voice mid-conversation)

### Supported Languages (v1)
- Hindi (`hi`) — Devanagari script input, Hindi voice output
- Telugu (`te`) — Telugu script input, Telugu voice output  
- English (`en`) — Latin script, Indian-accented English voice output
- Mixed Hindi-English (`hi` session) — LLM responds in Hindi

---

## 13. Implementation Phases

### Phase 1 — Audio Pipeline (WebSocket + VAD + STT)
**Goal**: Prove mic → transcript works end-to-end without LLM.

- [ ] `pcm-processor.js`: AudioWorklet Float32→Int16 conversion + 512-sample chunking
- [ ] `pcm-processor.js`: Detect `audioCtx.sampleRate !== 16000` → inline resampler fallback
- [ ] `audio_client.js`: WebSocket setup, worklet init, binary send
- [ ] `audio_client.js`: AudioPlaybackQueue — scheduled sequential playback, barge-in reset (see Section 6.8)
- [ ] `index.html`: Connect/disconnect button, transcript display
- [ ] `main.py`: FastAPI WebSocket endpoint `/ws/{session_id}`
- [ ] `main.py`: Server-side chunk-size guard (reject if `len(chunk) != 1024`)
- [ ] `session.py`: SessionState + ConversationContext dataclasses, in-memory registry
- [ ] `vad.py`: Silero VAD, buffer management, end-of-speech trigger
- [ ] `stt.py`: Sarvam Saaras v3 client, WAV header wrapping, language extraction
- [ ] Log transcript to console; send `transcript` JSON frame to browser
- [ ] Browser test matrix: Chrome, Edge, Firefox (verify `audioCtx.sampleRate`, check transcript quality)

**Exit criteria**: Speak into browser mic → transcript printed server-side within 1 second of silence. All three browsers produce correct transcripts.

---

### Phase 2 — LLM + TTS (Full Voice Loop)
**Goal**: Full voice conversation for general queries, no tools yet.

- [ ] `llm.py`: Groq streaming client, system prompt, history management (6 turns)
- [ ] `sentence_chunker.py`: Stream token accumulator, sentence boundary detection
- [ ] `tts.py`: Sarvam Bulbul v3 client, per-sentence call, cancel event check
- [ ] Wire pipeline: transcript → LLM → chunker → TTS → WebSocket audio
- [ ] `audio_client.js`: Receive PCM binary frames → AudioContext playback queue
- [ ] Set `tts_playing` flag; send `response_start` / `response_end` frames
- [ ] Rate limiting: token bucket per session for Groq calls

**Exit criteria**: Ask "What are your visiting hours?" in English → bot responds by voice within 3 seconds.

---

### Phase 3 — Multilingual
**Goal**: Hindi, Telugu, English, mixed input all work correctly.

- [ ] Language detection from STT response → update `session.language`
- [ ] Language persistence rules (see Section 12)
- [ ] LLM system prompt updated with `{language}` variable
- [ ] TTS voice selection based on `session.language` + gender config
- [ ] Test: speak Hindi → bot replies in Hindi voice
- [ ] Test: speak Telugu → bot replies in Telugu voice
- [ ] Test: mixed Hindi-English → bot replies in Hindi

**Exit criteria**: All three languages produce correct voice output with appropriate accent/script.

---

### Phase 4 — MCP Routing (Dummy Tools)
**Goal**: Tool-call routing works before real DB is connected.

- [ ] `mcp/server.py`: FastMCP server with all 5 tool stubs
- [ ] `mcp/tools/patient.py`: `patient_lookup`, `appointment_status` (hardcoded mock data)
- [ ] `mcp/tools/doctor.py`: `doctor_schedule`, `search_doctor_by_specialty`
- [ ] `mcp/tools/ward.py`: `bed_availability`
- [ ] `llm.py`: Pass MCP tool schemas to Groq API `tools` parameter
- [ ] `llm.py`: Detect `tool_use` in LLM response → call FastMCP → feed result back to LLM
- [ ] Tool error handling: MCP failure → graceful TTS fallback message

**Exit criteria**: "What is the status of appointment A123?" → LLM calls `appointment_status` → bot reads mock result aloud.

---

### Phase 5 — Barge-in + Resilience
**Goal**: Interruption works; system handles all error states gracefully.

- [ ] Barge-in: VAD detects speech during `tts_playing=True` → set `tts_cancel_event`
- [ ] TTS streamer checks `tts_cancel_event` before each sentence chunk
- [ ] Send `barge_in_ack` JSON frame to browser (browser can stop audio playback)
- [ ] All error handling from Section 11 implemented
- [ ] Idle session cleanup (10-minute timeout)
- [ ] VAD buffer overrun protection (>30 seconds)
- [ ] Global session limit (reject beyond 50 concurrent)

**Exit criteria**: Bot speaking → user interrupts → bot stops and listens within 500ms.

---

### Phase 6 — MS SQL Integration
**Goal**: Replace dummy MCP tools with real hospital DB queries.

- [ ] Add `aioodbc` / `pyodbc` to requirements
- [ ] Connection pool setup in `config.py`
- [ ] Implement real SQL queries in each MCP tool based on DB schema PDF
- [ ] Parameterized queries only (no string interpolation — SQL injection prevention)
- [ ] Query timeout: 3 seconds max
- [ ] Test with sample hospital data

**Exit criteria**: Real patient/doctor data returned and spoken correctly.

---

## 14. Configuration Reference

```python
# config.py

# API Keys (from .env)
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")

# Audio
SAMPLE_RATE          = 16000
CHUNK_SAMPLES        = 512      # per WebSocket frame
CHUNK_BYTES          = 1024     # CHUNK_SAMPLES * 2 (Int16)

# VAD
VAD_SPEECH_THRESHOLD = 0.5      # Silero probability threshold
VAD_SILENCE_MS       = 400      # ms of silence to trigger end-of-speech
VAD_SILENCE_FRAMES   = int((VAD_SILENCE_MS / 1000) * (SAMPLE_RATE / CHUNK_SAMPLES))
VAD_MAX_BUFFER_SEC   = 30       # discard if user speaks for 30s without pause

# STT
STT_TIMEOUT_SEC      = 5

# LLM
LLM_MODEL            = "llama-3.3-70b-versatile"
LLM_MAX_TOKENS       = 300      # keep responses short for voice
LLM_HISTORY_TURNS    = 6        # 3 user + 3 assistant

# Sentence chunker
TTS_MIN_CHUNK_CHARS      = 15   # don't send tiny fragments to TTS API
CHUNKER_MAX_CHARS        = 80   # force-flush if no punctuation by this length
CHUNKER_MAX_WAIT_SEC     = 1.0  # force-flush if no punctuation for this long

# Rate limiting
GROQ_BUCKET_MAX      = 5        # max queued requests per session
GROQ_BUCKET_REFILL   = 0.5      # tokens per second
SARVAM_CONCURRENCY   = 10       # global semaphore

# Sessions
MAX_SESSIONS         = 50
SESSION_IDLE_TIMEOUT = 600      # seconds (10 min)
```

---

## 15. Security Considerations

- All Sarvam and Groq API keys stored in `.env`, never committed.
- WebSocket connections are unauthenticated in v1 (add JWT in v2 for patient auth).
- MCP tool inputs (patient IDs, etc.) are validated before SQL query construction.
- All SQL queries use parameterized statements — no string concatenation.
- Session IDs are UUID4 — not guessable.
- Audio data is not persisted to disk (processed in memory only).
- HTTPS/WSS required in production (TLS termination at reverse proxy).

---

## 16. Dependencies (requirements.txt)

```
fastapi
uvicorn[standard]
websockets
silero-vad
torch                    # for Silero VAD
torchaudio
groq
httpx                    # for Sarvam API calls
fastmcp
python-dotenv
numpy
```

Frontend: No npm/bundler. Vanilla JS served statically by FastAPI.

---

## 17. Out of Scope / Future Considerations

- **Wake word**: "Namaste" or "Help" to activate without button press.
- **Emotion detection**: Detect distress in voice, escalate to human staff.
- **Call transfer**: Hand off to human receptionist mid-conversation.
- **Logging / audit trail**: HIPAA-compliant transcript logging for compliance.
- **Multi-tenant**: Separate configurations per hospital department.
- **Mobile app**: React Native with same WebSocket protocol.
- **Analytics dashboard**: Query volumes, language breakdown, fallback rates.
