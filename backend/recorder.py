"""
VAD-based microphone recorder.
Starts capturing when speech is detected, stops after sustained silence.
Optionally streams PCM frames to a StreamingSTT instance in real time.
"""

import tempfile
import collections
from typing import Optional, TYPE_CHECKING
import numpy as np
import sounddevice as sd
import soundfile as sf
import webrtcvad

if TYPE_CHECKING:
    from stt import StreamingSTT

SAMPLE_RATE = 16000
FRAME_MS = 30                  # webrtcvad supports 10 / 20 / 30 ms
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)   # 480 samples
FRAME_BYTES = FRAME_SAMPLES * 2                       # int16 = 2 bytes

VAD_AGGRESSIVENESS = 2         # 0–3: higher = more aggressive silence filtering

# How many consecutive silent frames end the recording (~1.5 s)
SILENCE_FRAMES_CUTOFF = int(1500 / FRAME_MS)
# Max recording duration as a safety cap (seconds)
MAX_DURATION_S = 30


def record_until_silence(stt: Optional["StreamingSTT"] = None) -> str:
    """
    Blocks until speech is detected, records until silence, returns path to WAV.
    If stt is provided, each voiced frame is streamed to it in real time.
    """
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

    print("🎙  Listening... (speak anytime, auto-stops on silence)")

    voiced_frames: list[bytes] = []
    ring: collections.deque[bool] = collections.deque(maxlen=SILENCE_FRAMES_CUTOFF)
    speech_started = False
    max_frames = int(MAX_DURATION_S * 1000 / FRAME_MS)
    total_frames = 0

    # Use a raw InputStream to pull exact frame sizes
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
    ) as stream:
        while total_frames < max_frames:
            raw, _ = stream.read(FRAME_SAMPLES)
            frame: bytes = bytes(raw)
            total_frames += 1

            is_speech = vad.is_speech(frame, SAMPLE_RATE)

            if not speech_started:
                if is_speech:
                    speech_started = True
                    print("🗣  Speech detected — recording...")
                    voiced_frames.append(frame)
                    if stt is not None:
                        stt.send_frame(frame)
                # drop pre-speech silence
                continue

            voiced_frames.append(frame)
            if stt is not None:
                stt.send_frame(frame)
            ring.append(is_speech)

            # Stop once the ring buffer is full and all recent frames are silent
            if len(ring) == SILENCE_FRAMES_CUTOFF and not any(ring):
                print("🔇 Silence detected — processing...")
                break

    if not voiced_frames:
        return ""

    # Convert raw int16 bytes → numpy → WAV
    pcm = np.frombuffer(b"".join(voiced_frames), dtype=np.int16)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, pcm, SAMPLE_RATE)
    return tmp.name


def record_after_barge_in(
    stream: sd.RawInputStream,
    initial_frames: list[bytes],
    stt: Optional["StreamingSTT"] = None,
) -> str:
    """
    Continue recording on an already-open stream after barge-in was detected.
    initial_frames: frames already captured that triggered the barge-in.
    If stt is provided, all frames (including initial_frames) are streamed to it.
    Returns path to WAV file (empty string if nothing captured).
    """
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    voiced_frames = list(initial_frames)
    ring: collections.deque[bool] = collections.deque(maxlen=SILENCE_FRAMES_CUTOFF)
    max_frames = int(MAX_DURATION_S * 1000 / FRAME_MS)

    # Stream any already-captured initial frames
    if stt is not None:
        for f in initial_frames:
            stt.send_frame(f)

    for _ in range(max_frames):
        raw, _ = stream.read(FRAME_SAMPLES)
        frame = bytes(raw)
        voiced_frames.append(frame)
        if stt is not None:
            stt.send_frame(frame)
        ring.append(vad.is_speech(frame, SAMPLE_RATE))
        if len(ring) == SILENCE_FRAMES_CUTOFF and not any(ring):
            print("🔇 Silence detected — processing barge-in...")
            break

    if not voiced_frames:
        return ""

    pcm = np.frombuffer(b"".join(voiced_frames), dtype=np.int16)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, pcm, SAMPLE_RATE)
    return tmp.name
