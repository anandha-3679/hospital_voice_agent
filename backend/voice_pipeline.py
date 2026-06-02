"""
Voice AI Pipeline: VAD mic → STT (Sarvam Saaras v3) → LLM (Groq llama-3.3-70b) → TTS (Sarvam Bulbul v3)
Language is auto-detected. Conversation history is persisted per session.
Press Ctrl+C to stop.
"""

import os
import sys
import shutil
from datetime import datetime
import soundfile as sf

from recorder import record_until_silence

_RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(_RECORDINGS_DIR, exist_ok=True)


def _save_user_audio(audio_path: str, session_id: str) -> None:
    """Copy the user's WAV recording to recordings/<session_id>/<timestamp>.wav."""
    session_dir = os.path.join(_RECORDINGS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = os.path.join(session_dir, f"{timestamp}.wav")
    shutil.copy2(audio_path, dest)
    print(f"💾 Audio saved: {dest}")
from stt import StreamingSTT
from llm import generate
from tts import play_tts_with_barge_in
from session import new_session, save_turn, get_session_for_patient


def run_turn(session: dict) -> bool:
    """Run one full turn. Returns False always (barge-in is handled inline)."""
    with StreamingSTT() as stt:
        audio_path = record_until_silence(stt=stt)
        if not audio_path:
            print("⚠️  No speech captured.")
            return False
        transcript, lang_code = stt.finalize()

    # Save every user audio capture regardless of transcript outcome
    _save_user_audio(audio_path, session["id"])

    try:
        if not transcript.strip():
            print("⚠️  Empty transcript — try again.")
            return False

        response = generate(transcript, lang_code, history=session["history"])
        save_turn(session, transcript, response, lang_code)

        barge_in_audio = play_tts_with_barge_in(response, lang_code)

        if barge_in_audio:
            print("↩️  Processing barge-in input...")
            try:
                _save_user_audio(barge_in_audio, session["id"])
                with StreamingSTT() as bi_stt:
                    bi_pcm, _ = sf.read(barge_in_audio, dtype="int16", always_2d=False)
                    chunk = 480  # 30 ms at 16 kHz
                    for i in range(0, len(bi_pcm), chunk):
                        bi_stt.send_frame(bi_pcm[i:i + chunk].tobytes())
                    bi_transcript, bi_lang = bi_stt.finalize()
                if bi_transcript.strip():
                    bi_response = generate(bi_transcript, bi_lang, history=session["history"])
                    save_turn(session, bi_transcript, bi_response, bi_lang)
                    play_tts_with_barge_in(bi_response, bi_lang)
            finally:
                os.unlink(barge_in_audio)
    finally:
        os.unlink(audio_path)

    return False


def get_or_create_session() -> dict:
    name = input("Please enter your name: ").strip() or "Guest"
    session = get_session_for_patient(name)
    if session:
        print(f"\n👋 Welcome back, {session['patient_name']}! "
              f"Continuing your previous session ({session['turns']} turn(s)).\n")
    else:
        session = new_session(patient_name=name)
        print(f"\n👋 Hello, {name}! Starting a new session for you.\n")
    return session


def main():
    print("=== City Care Hospital — Voice Assistant (Aarogya) ===")

    session = get_or_create_session()

    print("Speak into the mic — language auto-detected, silence ends each turn.")
    print("Press Ctrl+C to end the session.\n")

    while True:
        try:
            run_turn(session)
            print()
        except KeyboardInterrupt:
            print(f"\n👋 Session ended. {session['turns']} turn(s) saved.")
            print(f"   Session ID: {session['id']}")
            sys.exit(0)


if __name__ == "__main__":
    main()
