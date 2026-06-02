"""
Session management — persists conversation history to disk as JSON.
Each session is saved under sessions/<session_id>.json
"""

import os
import json
import uuid
from datetime import datetime

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")


def _ensure_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


# ---------- public API ----------

def new_session(patient_name: str = "Guest") -> dict:
    """Create and persist a brand-new session. Returns the session dict."""
    _ensure_dir()
    session = {
        "id": str(uuid.uuid4()),
        "patient_name": patient_name,
        "language_code": None,          # set on first detected utterance
        "started_at": datetime.now().isoformat(),
        "last_active": datetime.now().isoformat(),
        "turns": 0,
        "history": [],                  # list of {role, content} dicts
    }
    _save(session)
    return session


def load_session(session_id: str) -> dict | None:
    """Load an existing session by ID. Returns None if not found."""
    path = _path(session_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_turn(session: dict, user_text: str, assistant_text: str, language_code: str):
    """Append a turn to the session and persist."""
    session["history"].append({"role": "user",      "content": user_text})
    session["history"].append({"role": "assistant", "content": assistant_text})
    session["turns"] += 1
    session["last_active"] = datetime.now().isoformat()
    if session["language_code"] is None:
        session["language_code"] = language_code
    _save(session)


def get_session_for_patient(patient_name: str) -> dict | None:
    """Return the most recent session for this patient name, or None."""
    _ensure_dir()
    matches = []
    for fname in os.listdir(SESSIONS_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(SESSIONS_DIR, fname), "r", encoding="utf-8") as f:
                try:
                    s = json.load(f)
                    if s.get("patient_name", "").lower() == patient_name.lower():
                        matches.append(s)
                except json.JSONDecodeError:
                    pass
    if not matches:
        return None
    matches.sort(key=lambda s: s.get("last_active", ""), reverse=True)
    return matches[0]


def list_sessions(limit: int = 10) -> list[dict]:
    """Return the most recent sessions sorted by last_active (newest first)."""
    _ensure_dir()
    sessions = []
    for fname in os.listdir(SESSIONS_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(SESSIONS_DIR, fname), "r", encoding="utf-8") as f:
                try:
                    sessions.append(json.load(f))
                except json.JSONDecodeError:
                    pass
    sessions.sort(key=lambda s: s.get("last_active", ""), reverse=True)
    return sessions[:limit]


def _save(session: dict):
    _ensure_dir()
    with open(_path(session["id"]), "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)