"""
Realtime streaming TTS with barge-in support.
Streams PCM from Sarvam /text-to-speech/stream and feeds directly into
a sounddevice OutputStream — no temp file, playback starts on first chunk.
"""

import os
import queue
import threading
import collections

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
import webrtcvad
from dotenv import load_dotenv

from recorder import record_after_barge_in

load_dotenv()

_HEADERS = {
    "api-subscription-key": os.getenv("SARVAM_API_KEY"),
    "Content-Type": "application/json",
}

SAMPLE_RATE = 22050          # Sarvam bulbul:v3 output rate
_BLOCKSIZE  = 2205           # 100 ms per callback block at 22050 Hz

# ── Barge-in constants (same logic as before, now lives here) ─────────────────
_MIC_RATE               = 16000
_FRAME_MS               = 30
_FRAME_SAMPLES          = int(_MIC_RATE * _FRAME_MS / 1000)   # 480
_VAD_AGGRESSIVENESS     = 2
_BARGE_IN_GRACE_S       = 0.6
_BARGE_IN_CONFIRM_FRAMES = 10
_BARGE_IN_ENERGY_MUL    = 3.0
_BARGE_IN_MIN_DURATION_S = 0.8


def _rms(frame_bytes: bytes) -> float:
    samples = np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def play_tts_with_barge_in(text: str, language_code: str) -> str:
    """
    Stream TTS from Sarvam and play in realtime via sounddevice OutputStream.
    Simultaneously monitors the mic for barge-in (same echo-rejection as before).

    Returns path to barge-in WAV file, or "" if playback finished cleanly.
    """
    print(f"🔈 Streaming TTS (Bulbul v3) [{language_code}]")

    audio_q: "queue.Queue[bytes | None]" = queue.Queue(maxsize=128)
    stop_event  = threading.Event()   # signal fetcher to abort early
    stream_done = threading.Event()   # fetcher finished (sentinel received)
    leftover    = bytearray()         # PCM bytes not yet consumed by callback

    # ── Background HTTP fetch thread ──────────────────────────────────────────
    def _fetch() -> None:
        try:
            with requests.post(
                "https://api.sarvam.ai/text-to-speech/stream",
                headers=_HEADERS,
                json={
                    "text": text,
                    "target_language_code": language_code,
                    "model": "bulbul:v3",
                    "output_audio_codec": "linear16",
                    "speech_sample_rate": SAMPLE_RATE,
                },
                stream=True,
                timeout=30,
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=4096):
                    if stop_event.is_set():
                        break
                    if chunk:
                        audio_q.put(chunk)
        except Exception as e:
            print(f"⚠️  TTS fetch error: {e}")
        finally:
            audio_q.put(None)   # sentinel — always sent even on error

    # ── sounddevice callback (runs on audio thread) ───────────────────────────
    def _callback(outdata: np.ndarray, frames: int, time_info, status) -> None:
        needed = frames * 2   # int16 → 2 bytes/sample

        # Drain queue into leftover buffer
        while len(leftover) < needed:
            try:
                chunk = audio_q.get_nowait()
                if chunk is None:
                    stream_done.set()
                    break
                leftover.extend(chunk)
            except queue.Empty:
                break   # underrun — pad below

        take = min(len(leftover), needed)
        pcm  = bytes(leftover[:take])
        del leftover[:take]

        if len(pcm) < needed:
            pcm += b'\x00' * (needed - len(pcm))   # silence pad for underrun

        outdata[:] = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 1)

        # Stop stream once all fetched audio is drained
        if stream_done.is_set() and len(leftover) == 0:
            raise sd.CallbackStop()

    # ── Start fetcher and output stream ──────────────────────────────────────
    fetcher = threading.Thread(target=_fetch, daemon=True)
    fetcher.start()

    out_stream = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        callback=_callback,
        blocksize=_BLOCKSIZE,
    )

    barge_in_path    = ""
    barge_in_triggered = False
    vad = webrtcvad.Vad(_VAD_AGGRESSIVENESS)

    print("▶️  Playing response... (speak to interrupt)")

    with out_stream:
        with sd.RawInputStream(
            samplerate=_MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=_FRAME_SAMPLES,
        ) as mic:

            # ── Phase 1: grace period — measure speaker bleed ─────────────────
            bleed_frames: list[float] = []
            grace_count = int(_BARGE_IN_GRACE_S * 1000 / _FRAME_MS)
            for _ in range(grace_count):
                if not out_stream.active:
                    break
                raw, _ = mic.read(_FRAME_SAMPLES)
                bleed_frames.append(_rms(bytes(raw)))

            bleed_energy    = float(max(np.mean(bleed_frames) if bleed_frames else 0.0, 1.0))
            energy_threshold = bleed_energy * _BARGE_IN_ENERGY_MUL

            # ── Phase 2: barge-in detection ───────────────────────────────────
            initial_frames: list[bytes] = []
            ring: collections.deque[bool] = collections.deque(maxlen=_BARGE_IN_CONFIRM_FRAMES)

            while out_stream.active:
                raw, _ = mic.read(_FRAME_SAMPLES)
                frame = bytes(raw)
                frame_energy = _rms(frame)
                is_speech = (
                    bool(vad.is_speech(frame, _MIC_RATE))
                    and frame_energy > energy_threshold
                )
                ring.append(is_speech)
                if is_speech:
                    initial_frames.append(frame)

                if len(ring) == _BARGE_IN_CONFIRM_FRAMES and all(ring):
                    stop_event.set()
                    out_stream.stop()
                    print(
                        f"🗣  Barge-in confirmed "
                        f"(energy {frame_energy:.0f} vs bleed {bleed_energy:.0f}) — listening..."
                    )
                    barge_in_triggered = True

                    candidate = record_after_barge_in(mic, initial_frames)
                    if candidate:
                        captured_s = sf.info(candidate).duration
                        if captured_s < _BARGE_IN_MIN_DURATION_S:
                            print(f"⚠️  Barge-in too short ({captured_s:.2f}s) — likely echo, ignoring.")
                            os.unlink(candidate)
                            candidate = ""
                    barge_in_path = candidate
                    break

    fetcher.join(timeout=2.0)
    return barge_in_path
