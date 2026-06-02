"""
Tool definitions (JSON schemas) and handler functions for the hospital DB.
The agentic loop in llm.py calls execute_tool() when Groq returns tool_calls.
"""

import json
from fake_db import (
    PATIENTS, DOCTORS, AVAILABILITY, APPOINTMENTS, WARDS,
    book_slot, cancel_appointment,
)

# ── Tool schemas (sent to Groq) ───────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_patient_info",
            "description": "Look up a patient's details by their name or patient ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Patient name or patient ID (e.g. P001)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_doctors",
            "description": "List all doctors, optionally filtered by department.",
            "parameters": {
                "type": "object",
                "properties": {
                    "department": {"type": "string", "description": "Department name (optional). Leave blank to list all."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_doctor_availability",
            "description": "Get available appointment slots for a doctor on a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_query": {"type": "string", "description": "Doctor name or doctor ID (e.g. D001)"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Use today's date if not specified."},
                },
                "required": ["doctor_query", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book an appointment for a patient with a doctor at a specific date and time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id":  {"type": "string", "description": "Patient ID (e.g. P001)"},
                    "doctor_id":   {"type": "string", "description": "Doctor ID (e.g. D001)"},
                    "date":        {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "time":        {"type": "string", "description": "Time slot in HH:MM format (e.g. 09:30)"},
                    "reason":      {"type": "string", "description": "Reason for the appointment"},
                },
                "required": ["patient_id", "doctor_id", "date", "time", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_patient_appointments",
            "description": "Get all appointments (past and upcoming) for a patient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string", "description": "Patient ID (e.g. P001)"},
                },
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an existing appointment by appointment ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string", "description": "Appointment ID (e.g. A001)"},
                },
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ward_info",
            "description": "Get details about a hospital ward including bed availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ward_query": {"type": "string", "description": "Ward name or ward ID (e.g. W001, ICU, General Ward)"},
                },
                "required": ["ward_query"],
            },
        },
    },
]


# ── Tool handler functions ─────────────────────────────────────────────────────

def _find_patient(query: str) -> dict | None:
    query_lower = query.strip().lower()
    if query.upper() in PATIENTS:
        return PATIENTS[query.upper()]
    for p in PATIENTS.values():
        if query_lower in p["name"].lower():
            return p
    return None


def _find_doctor(query: str) -> dict | None:
    query_upper = query.strip().upper()
    if query_upper in DOCTORS:
        return DOCTORS[query_upper]
    query_lower = query.strip().lower()
    for d in DOCTORS.values():
        if query_lower in d["name"].lower():
            return d
    return None


def _find_ward(query: str) -> dict | None:
    query_upper = query.strip().upper()
    if query_upper in WARDS:
        return WARDS[query_upper]
    query_lower = query.strip().lower()
    for w in WARDS.values():
        if query_lower in w["name"].lower():
            return w
    return None


def _handle_get_patient_info(query: str) -> dict:
    patient = _find_patient(query)
    if not patient:
        return {"error": f"No patient found for query '{query}'"}
    return patient


def _handle_list_doctors(department: str = "") -> list:
    if not department:
        return list(DOCTORS.values())
    dept_lower = department.lower()
    return [d for d in DOCTORS.values() if dept_lower in d["department"].lower()]


def _handle_get_doctor_availability(doctor_query: str, date: str) -> dict:
    doctor = _find_doctor(doctor_query)
    if not doctor:
        return {"error": f"No doctor found for query '{doctor_query}'"}
    slots = AVAILABILITY.get(doctor["id"], {}).get(date, [])
    return {
        "doctor": doctor["name"],
        "department": doctor["department"],
        "date": date,
        "available_slots": slots,
        "consultation_fee": doctor["consultation_fee"],
    }


def _handle_book_appointment(patient_id: str, doctor_id: str, date: str, time: str, reason: str) -> dict:
    if patient_id.upper() not in PATIENTS:
        return {"error": f"Patient ID '{patient_id}' not found"}
    if doctor_id.upper() not in DOCTORS:
        return {"error": f"Doctor ID '{doctor_id}' not found"}
    appt_id = book_slot(patient_id.upper(), doctor_id.upper(), date, time, reason)
    if not appt_id:
        return {"error": f"Slot {time} on {date} is not available for {doctor_id}"}
    doctor = DOCTORS[doctor_id.upper()]
    patient = PATIENTS[patient_id.upper()]
    return {
        "success": True,
        "appointment_id": appt_id,
        "patient": patient["name"],
        "doctor": doctor["name"],
        "department": doctor["department"],
        "date": date,
        "time": time,
        "reason": reason,
    }


def _handle_get_patient_appointments(patient_id: str) -> list:
    pid = patient_id.upper()
    appts = [a for a in APPOINTMENTS.values() if a["patient_id"] == pid]
    result = []
    for a in appts:
        doc = DOCTORS.get(a["doctor_id"], {})
        result.append({
            "appointment_id": a["id"],
            "doctor": doc.get("name", a["doctor_id"]),
            "department": doc.get("department", ""),
            "date": a["date"],
            "time": a["time"],
            "status": a["status"],
            "reason": a["reason"],
        })
    return result


def _handle_cancel_appointment(appointment_id: str) -> dict:
    success = cancel_appointment(appointment_id.upper())
    if not success:
        return {"error": f"Appointment '{appointment_id}' not found or already cancelled"}
    return {"success": True, "appointment_id": appointment_id.upper(), "status": "cancelled"}


def _handle_get_ward_info(ward_query: str) -> dict:
    ward = _find_ward(ward_query)
    if not ward:
        return {"error": f"No ward found for query '{ward_query}'"}
    available = ward["total_beds"] - ward["occupied_beds"]
    return {**ward, "available_beds": available}


# ── Dispatcher ────────────────────────────────────────────────────────────────

_HANDLERS = {
    "get_patient_info":         lambda args: _handle_get_patient_info(**args),
    "list_doctors":             lambda args: _handle_list_doctors(**args),
    "get_doctor_availability":  lambda args: _handle_get_doctor_availability(**args),
    "book_appointment":         lambda args: _handle_book_appointment(**args),
    "get_patient_appointments": lambda args: _handle_get_patient_appointments(**args),
    "cancel_appointment":       lambda args: _handle_cancel_appointment(**args),
    "get_ward_info":            lambda args: _handle_get_ward_info(**args),
}


def execute_tool(name: str, arguments: str) -> str:
    """Called by the agentic loop. Returns a JSON string result."""
    args = json.loads(arguments)
    print(f"🔧 Tool call: {name}({args})")
    handler = _HANDLERS.get(name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {name}"})
    result = handler(args)
    return json.dumps(result, ensure_ascii=False)
