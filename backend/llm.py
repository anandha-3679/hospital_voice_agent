import os
import re
from datetime import date
from typing import Any, Iterator
from groq import Groq
from dotenv import load_dotenv
from tools import TOOL_SCHEMAS, execute_tool

load_dotenv()

_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

_FAST_MODEL = "llama-3.1-8b-instant"   # no-tool replies
_TOOL_MODEL  = "llama-3.3-70b-versatile"  # tool-calling iterations

# ── System prompt ─────────────────────────────────────────────────────────────

_BASE_PROMPT = f"""You are Aarogya, a friendly and professional hospital voice assistant for City Care Hospital.
Today's date is {date.today().isoformat()}.

Your responsibilities:
- Help patients book, reschedule, or cancel appointments
- Provide doctor availability and department information
- Answer general medical knowledge questions (symptoms, conditions, procedures)
- Guide patients to the right department or specialist
- Provide hospital timings, contact details, and location information
- Use the provided tools to look up real patient, doctor, ward, and appointment data

Strict boundaries:
- NEVER prescribe medication, dosage, or treatment plans — always direct the patient to consult a doctor
- NEVER diagnose a condition — you can explain symptoms but always recommend an in-person consultation
- For emergencies, immediately advise calling 108 (ambulance) or visiting the nearest emergency room

Tone: Warm, calm, and reassuring. Keep responses concise and conversational — this is a voice interface, so avoid bullet points, markdown, or long paragraphs. Speak naturally.

TOOL USE RULES — critical:
- ONLY call a tool when the user explicitly mentions a patient, doctor, appointment, ward, or related hospital data need.
- For greetings, general questions, or medical knowledge — answer directly WITHOUT calling any tool.
- ALWAYS use the native tool-calling API. NEVER output tool calls as raw text, JSON, or any <function=...> format.
- If you call a tool in raw text format, the system will crash. Use only the structured tool interface.

Hospital details:
- Name: City Care Hospital
- Departments: General Medicine, Cardiology, Orthopedics, Pediatrics, Gynecology, Neurology, Dermatology, ENT, Ophthalmology, Dentistry
- OPD hours: Monday-Saturday, 8 AM to 8 PM
- Emergency: Open 24/7
- Appointment helpline: 1800-XXX-XXXX

Reply in the SAME language the user speaks.

- NEVER hallucinate patient or doctor data — always call a tool to fetch it."""

# Keywords that indicate the user needs database/tool data
_TOOL_KEYWORDS = {
    # English
    "appointment", "appointments", "book", "booking", "cancel", "reschedule",
    "doctor", "doctors", "patient", "patients", "ward", "wards",
    "revisit", "follow-up", "followup", "schedule", "available", "availability",
    "admit", "admitted", "discharge", "discharged", "record", "records",
    "report", "reports", "history", "bill", "billing",
    # Hindi transliterations
    "appointment", "doctor", "patient", "apoinment",
    # Telugu / Hindi common words (romanised)
    "daktar", "aspatri", "vyakti",
}


def _needs_tools(text: str) -> bool:
    """Return True if the user message contains any tool-triggering keyword."""
    lower = text.lower()
    return any(kw in lower for kw in _TOOL_KEYWORDS)


_LANG_ADDENDUM = {
    "hi-IN": "उपयोगकर्ता हिंदी में बात कर रहा है। हिंदी में ही उत्तर दें।",
    "ta-IN": "பயனர் தமிழில் பேசுகிறார். தமிழிலேயே பதிலளிக்கவும்.",
    "te-IN": "వినియోగదారు తెలుగులో మాట్లాడుతున్నారు. తెలుగులోనే సమాధానం ఇవ్వండి.",
    "en-IN": "The user is speaking in English. Reply in English.",

}


# ── Agentic loop ──────────────────────────────────────────────────────────────

MAX_TOOL_ITERATIONS = 5


def generate(text: str, language_code: str, history: list[dict]) -> str:
    lang_note = _LANG_ADDENDUM.get(
        language_code,
        f"Reply in the user's language ({language_code}).",
    )
    system_prompt = f"{_BASE_PROMPT}\n\n{lang_note}"

    messages: list[Any] = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": text}]
    )

    use_tools = _needs_tools(text)
    active_tools = TOOL_SCHEMAS if use_tools else None

    for iteration in range(MAX_TOOL_ITERATIONS):
        print(f"🤖 Groq call (iteration {iteration + 1}, tools={'on' if use_tools else 'off'})...")
        response = _client.chat.completions.create(
            model=_FAST_MODEL if not use_tools else _TOOL_MODEL,
            messages=messages,                          # type: ignore[arg-type]
            tools=active_tools,                         # type: ignore[arg-type]
            tool_choice="auto" if use_tools else "none",
            temperature=0.2,
            max_tokens=512,
        )

        msg = response.choices[0].message
        content = msg.content or ""

        # ── Step 1: collect structured tool calls ────────────────────────────
        tool_calls: list[dict] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (msg.tool_calls or [])
        ]

        # ── Step 2: no tools → final reply ──────────────────────────────────
        if not tool_calls:
            print(f"💬 Aarogya: {content}")
            return content

        # ── Step 3: append assistant turn ────────────────────────────────────
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        # ── Step 4: execute each tool and append results ─────────────────────
        for tc in tool_calls:
            result = execute_tool(tc["function"]["name"], tc["function"]["arguments"])
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    return "I'm sorry, I could not complete that request. Please try again."


# ── Sentence splitter ─────────────────────────────────────────────────────────

_SENT_RE = re.compile(r'(?<=[.!?।])\s+')


def _pop_sentence(buf: str) -> tuple[str, str]:
    """Pop the first complete sentence from buf. Returns (sentence, remainder)."""
    m = _SENT_RE.search(buf)
    if m:
        return buf[:m.start() + 1].strip(), buf[m.end():]
    return "", buf


def _stream_to_sentences(stream) -> Iterator[str]:
    """Consume a Groq streaming response and yield sentence-complete chunks."""
    buf = ""
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue
        buf += delta
        sentence, buf = _pop_sentence(buf)
        while sentence:
            yield sentence
            sentence, buf = _pop_sentence(buf)
    if buf.strip():
        yield buf.strip()


# ── Streaming agentic loop ────────────────────────────────────────────────────

def generate_stream(
    text: str, language_code: str, history: list[dict]
) -> Iterator[str]:
    """
    Same tool-calling logic as generate() but yields sentence-level chunks
    for the final reply so TTS can begin immediately.

    - No tools needed  → single streaming Groq call, sentences yielded live.
    - Tools needed     → non-streaming tool iterations until resolved, then
                         the final reply content is split and yielded directly
                         (no extra API call).
    """
    lang_note = _LANG_ADDENDUM.get(
        language_code,
        f"Reply in the user's language ({language_code}).",
    )
    system_prompt = f"{_BASE_PROMPT}\n\n{lang_note}"

    messages: list[Any] = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": text}]
    )

    use_tools = _needs_tools(text)
    active_tools = TOOL_SCHEMAS if use_tools else None

    # ── No tools: go straight to streaming ───────────────────────────────────
    if not use_tools:
        print("🤖 Groq streaming (no tools)...")
        stream = _client.chat.completions.create(
            model=_FAST_MODEL,
            messages=messages,          # type: ignore[arg-type]
            tools=None,
            tool_choice="none",
            temperature=0.2,
            max_tokens=512,
            stream=True,
        )
        for sentence in _stream_to_sentences(stream):
            print(f"💬 [chunk] {sentence}")
            yield sentence
        return

    # ── Tools: resolve non-streaming, yield final reply directly ─────────────
    for iteration in range(MAX_TOOL_ITERATIONS):
        print(f"🤖 Groq call (iteration {iteration + 1}, tools=on)...")
        response = _client.chat.completions.create(
            model=_TOOL_MODEL,
            messages=messages,          # type: ignore[arg-type]
            tools=active_tools,         # type: ignore[arg-type]
            tool_choice="auto",
            temperature=0.2,
            max_tokens=512,
        )

        msg = response.choices[0].message
        content = msg.content or ""

        tool_calls: list[dict] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in (msg.tool_calls or [])
        ]

        if not tool_calls:
            # Tools resolved — yield the final reply via sentence splitter
            print(f"💬 Aarogya (post-tools): {content}")
            buf = content
            sentence, buf = _pop_sentence(buf)
            while sentence:
                yield sentence
                sentence, buf = _pop_sentence(buf)
            if buf.strip():
                yield buf.strip()
            return

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            result = execute_tool(tc["function"]["name"], tc["function"]["arguments"])
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    yield "I'm sorry, I could not complete that request. Please try again."
