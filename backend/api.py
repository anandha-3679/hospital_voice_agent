"""
FastAPI backend — City Care Hospital Voice Assistant
STT → LLM → TTS pipeline exposed over WebSocket.
REST endpoints for session + recording management.
"""

import os
import json
import wave
import base64
import asyncio
import threading
from datetime import datetime
from typing import AsyncIterator

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from stt import StreamingSTT
from llm import generate_stream
from session import new_session, load_session, save_turn, list_sessions, get_session_for_patient

load_dotenv()

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="City Care Hospital — Voice Assistant API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Constants ─────────────────────────────────────────────────────────────────

_TTS_HEADERS = {
    "api-subscription-key": os.getenv("SARVAM_API_KEY"),
    "Content-Type": "application/json",
}
TTS_SAMPLE_RATE = 22050
_MIC_SAMPLE_RATE = 16000
_RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")


# ── Connection Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    """
    Tracks active WebSocket connections and their running pipeline tasks.
    Provides safe cancellation when barge-in or disconnect occurs.
    """

    def __init__(self):
        self._sockets: dict[str, WebSocket] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._sockets[session_id] = ws

    async def disconnect(self, session_id: str) -> None:
        async with self._lock:
            await self._cancel_task_unsafe(session_id)
            self._sockets.pop(session_id, None)

    async def set_task(self, session_id: str, task: asyncio.Task) -> None:
        async with self._lock:
            await self._cancel_task_unsafe(session_id)
            self._tasks[session_id] = task

    async def cancel_task(self, session_id: str) -> None:
        async with self._lock:
            await self._cancel_task_unsafe(session_id)

    async def _cancel_task_unsafe(self, session_id: str) -> None:
        """Must be called while holding self._lock."""
        task = self._tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    def active_sessions(self) -> list[str]:
        return list(self._sockets.keys())


manager = ConnectionManager()


# ── Async pipeline wrappers ───────────────────────────────────────────────────

async def _async_generate_stream(
    text: str, language_code: str, history: list
) -> AsyncIterator[str]:
    """
    Bridges the sync generate_stream() generator into an async generator
    by running it in a thread and forwarding items via asyncio.Queue.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _run():
        try:
            for sentence in generate_stream(text, language_code, history):
                loop.call_soon_threadsafe(q.put_nowait, sentence)
        except Exception as exc:
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_run, daemon=True).start()

    while True:
        item = await q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item


async def _async_tts_stream(text: str, language_code: str) -> AsyncIterator[str]:
    """
    Streams PCM chunks from Sarvam TTS in a background thread,
    yielding base64-encoded strings as they arrive.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _fetch():
        try:
            with requests.post(
                "https://api.sarvam.ai/text-to-speech/stream",
                headers=_TTS_HEADERS,
                json={
                    "text": text,
                    "target_language_code": language_code,
                    "model": "bulbul:v3",
                    "output_audio_codec": "linear16",
                    "speech_sample_rate": TTS_SAMPLE_RATE,
                },
                stream=True,
                timeout=30,
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=4096):
                    if chunk:
                        loop.call_soon_threadsafe(
                            q.put_nowait, base64.b64encode(chunk).decode()
                        )
        except Exception as exc:
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_fetch, daemon=True).start()

    while True:
        item = await q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item


# ── Audio saving ──────────────────────────────────────────────────────────────

def _save_audio(session_id: str, pcm_bytes: bytes) -> str:
    """Save raw int16 PCM bytes as a WAV file under recordings/<session_id>/."""
    session_dir = os.path.join(_RECORDINGS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(session_dir, f"{timestamp}.wav")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)          # int16
        wf.setframerate(_MIC_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    print(f"💾 Audio saved: {path}")
    return path


# ── Voice pipeline task ───────────────────────────────────────────────────────

async def _pipeline_task(
    session_id: str,
    websocket: WebSocket,
    session: dict,
    transcript: str,
    language_code: str,
) -> None:
    """
    LLM → TTS pipeline for one turn.
    Fully cancellable — barge_in message triggers task.cancel().
    Saves turn to session history in the finally block.
    """
    response_parts: list[str] = []
    try:
        async for sentence in _async_generate_stream(
            transcript, language_code, session["history"]
        ):
            sentence = sentence.strip()
            if not sentence:
                continue

            response_parts.append(sentence)
            await websocket.send_text(json.dumps({
                "type": "llm_sentence",
                "text": sentence,
            }))

            async for chunk_b64 in _async_tts_stream(sentence, language_code):
                await websocket.send_text(json.dumps({
                    "type": "tts_chunk",
                    "data": chunk_b64,
                    "sample_rate": TTS_SAMPLE_RATE,
                }))

        await websocket.send_text(json.dumps({"type": "tts_done"}))

    except asyncio.CancelledError:
        await websocket.send_text(json.dumps({"type": "cancelled"}))
        raise

    except Exception as exc:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": str(exc),
        }))

    finally:
        if response_parts:
            full_response = " ".join(response_parts)
            await asyncio.to_thread(
                save_turn, session, transcript, full_response, language_code
            )


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    Full-duplex voice pipeline.

    Client sends:
      - Binary frames : raw int16 PCM at 16 kHz (one speech segment)
      - {"type": "start_audio"}  : client VAD detected speech start
      - {"type": "end_audio"}    : client VAD detected silence — finalize STT
      - {"type": "barge_in"}     : user interrupted TTS — cancel pipeline
      - {"type": "ping"}         : keepalive

    Server sends:
      - {"type": "transcript",   "text": "...", "lang": "en-IN"}
      - {"type": "llm_sentence", "text": "..."}
      - {"type": "tts_chunk",    "data": "<base64 PCM>", "sample_rate": 22050}
      - {"type": "tts_done"}
      - {"type": "cancelled"}
      - {"type": "error",        "message": "..."}
      - {"type": "pong"}
    """
    session = await asyncio.to_thread(load_session, session_id)
    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    await manager.connect(session_id, websocket)

    stt: StreamingSTT | None = None
    audio_frames: list[bytes] = []

    try:
        while True:
            message = await websocket.receive()

            # ── Binary frame: audio chunk from client mic ─────────────────────
            if message.get("bytes"):
                frame: bytes = message["bytes"]
                audio_frames.append(frame)
                if stt is not None:
                    await asyncio.to_thread(stt.send_frame, frame)

            # ── Text frame: control event ─────────────────────────────────────
            elif message.get("text"):
                event = json.loads(message["text"])
                event_type = event.get("type")

                if event_type == "start_audio":
                    # New speech segment starting — open STT connection
                    audio_frames = []
                    stt = StreamingSTT()
                    await asyncio.to_thread(stt.__enter__)

                elif event_type == "end_audio":
                    # Client VAD detected silence — finalize STT
                    if stt is not None:
                        transcript, lang_code = await asyncio.to_thread(stt.finalize)
                        await asyncio.to_thread(stt.__exit__, None, None, None)
                        stt = None

                        # Save user audio
                        if audio_frames:
                            pcm = b"".join(audio_frames)
                            await asyncio.to_thread(_save_audio, session_id, pcm)
                        audio_frames = []

                        await websocket.send_text(json.dumps({
                            "type": "transcript",
                            "text": transcript,
                            "lang": lang_code,
                        }))

                        if transcript.strip():
                            task = asyncio.create_task(
                                _pipeline_task(
                                    session_id, websocket, session,
                                    transcript, lang_code,
                                )
                            )
                            await manager.set_task(session_id, task)

                elif event_type == "barge_in":
                    # Cancel running LLM+TTS task immediately
                    await manager.cancel_task(session_id)
                    # Clean up any open STT connection
                    if stt is not None:
                        try:
                            await asyncio.to_thread(stt.__exit__, None, None, None)
                        except Exception:
                            pass
                        stt = None
                    audio_frames = []

                elif event_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass

    except Exception as exc:
        try:
            await websocket.send_text(json.dumps({
                "type": "error", "message": str(exc)
            }))
        except Exception:
            pass

    finally:
        # Clean up open STT connection if client disconnected mid-speech
        if stt is not None:
            try:
                await asyncio.to_thread(stt.__exit__, None, None, None)
            except Exception:
                pass
        await manager.disconnect(session_id)


# ── REST routes ───────────────────────────────────────────────────────────────

class CreateSessionBody(BaseModel):
    patient_name: str = "Guest"


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "City Care Hospital Voice Assistant",
        "active_connections": len(manager.active_sessions()),
    }


@app.post("/sessions")
async def create_session(body: CreateSessionBody):
    """Create a new session or return the most recent one for this patient."""
    existing = await asyncio.to_thread(get_session_for_patient, body.patient_name)
    if existing:
        return existing
    session = await asyncio.to_thread(new_session, body.patient_name)
    return session


@app.get("/sessions")
async def get_sessions(limit: int = 10):
    """List recent sessions sorted by last activity."""
    sessions = await asyncio.to_thread(list_sessions, limit)
    return sessions


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a session by ID."""
    session = await asyncio.to_thread(load_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/recordings/{session_id}")
async def get_recordings(session_id: str):
    """List saved user audio files for a session."""
    session_dir = os.path.join(_RECORDINGS_DIR, session_id)
    if not os.path.exists(session_dir):
        return []
    files = sorted(
        f for f in os.listdir(session_dir) if f.endswith(".wav")
    )
    return [
        {"filename": f, "url": f"/recordings/{session_id}/{f}"}
        for f in files
    ]
