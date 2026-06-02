import os
import base64
import threading
from typing import Optional

from sarvamai import SarvamAI
from dotenv import load_dotenv

load_dotenv()


def _make_client() -> SarvamAI:
    return SarvamAI(api_subscription_key=os.getenv("SARVAM_API_KEY"))


class StreamingSTT:
    """
    Realtime streaming STT via Sarvam WebSocket (saaras:v3).

    Usage:
        with StreamingSTT() as stt:
            record_until_silence(stt=stt)
            transcript, lang = stt.finalize()
    """

    _MIN_FRAMES = 25   # ~1.5 s worth — below this, return "" to avoid hallucination

    def __init__(self):
        self._client = _make_client()
        self._conn = None
        self._ctx = None
        self._transcript: str = ""        # last transcript received (partial or final)
        self._language_code: str = "hi-IN"
        self._transcript_updated = threading.Event()  # fires on every new transcript
        self._final_event = threading.Event()          # fires when WebSocket closes
        self._listener: Optional[threading.Thread] = None
        self._frame_count: int = 0        # counts send_frame calls

    def __enter__(self) -> "StreamingSTT":
        print("🔗 Opening Sarvam streaming STT (saaras:v3)...")
        self._ctx = self._client.speech_to_text_streaming.connect(
            language_code="unknown",
            model="saaras:v3",
            mode="transcribe",
            input_audio_codec="pcm_s16le",
        )
        self._conn = self._ctx.__enter__()
        if self._conn is None:
            raise RuntimeError("Sarvam WebSocket connect() returned None — check API key and network.")
        self._listener = threading.Thread(target=self._receive_loop, daemon=True)
        self._listener.start()
        return self

    def __exit__(self, *args) -> None:
        if self._ctx is not None:
            try:
                self._ctx.__exit__(*args)
            except Exception:
                pass

    def _receive_loop(self) -> None:
        if self._conn is None:
            self._final_event.set()
            return
        try:
            for msg in self._conn:
                if msg.type == "data":
                    d = msg.data
                    t = getattr(d, "transcript", None)
                    if t:
                        self._transcript = t
                        lang = getattr(d, "language_code", None)
                        if lang:
                            self._language_code = lang
                        print(f"📝 [live] {t}")
                        self._transcript_updated.set()
                        # Do NOT clear here — finalize() clears before waiting
                        # so it won't miss a post-flush signal
        except Exception as exc:
            print(f"⚠️  STT receive error: {exc}")
        finally:
            self._final_event.set()

    def send_frame(self, pcm_bytes: bytes) -> None:
        """Send a raw PCM int16 frame (16 kHz mono) to the WebSocket."""
        if self._conn is None:
            return
        b64 = base64.b64encode(pcm_bytes).decode()
        self._conn.transcribe(b64, sample_rate=16000)
        self._frame_count += 1

    def finalize(self) -> tuple[str, str]:
        """
        Flush the audio buffer and return the best available transcript.

        Strategy:
          1. Send flush signal to Sarvam to force-finalize any buffered audio.
          2. Wait up to 0.6 s for a post-flush transcript update.
          3. If no update arrives within that window, use the latest partial
             transcript already captured during recording — it is almost always
             complete by silence-detection time.
        """
        # Guard: too few frames → likely silence/noise → would hallucinate
        if self._frame_count < self._MIN_FRAMES:
            print(f"⚠️  Only {self._frame_count} frames received — skipping STT to prevent hallucination")
            return "", self._language_code

        snapshot = self._transcript   # capture whatever arrived during recording

        if self._conn is not None:
            try:
                # Clear the event BEFORE flush so we can't miss the post-flush signal
                self._transcript_updated.clear()
                self._conn.flush()
            except Exception:
                pass

        # Wait for a post-flush transcript update (typically 200-400 ms)
        self._transcript_updated.wait(timeout=0.6)

        result = self._transcript if self._transcript else snapshot
        print(f"🌐 Detected language: {self._language_code}")
        print(f"📝 Transcript: {result}")
        return result, self._language_code