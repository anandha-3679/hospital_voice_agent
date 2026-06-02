"""
In-memory fake hospital database.
All data is fabricated for development/testing purposes.
"""

from datetime import date, timedelta

today = date.today()

# ── Patients ──────────────────────────────────────────────────────────────────
PATIENTS: dict[str, dict] = {
    "P001": {"id": "P001", "name": "Ravi Kumar",     "age": 45, "gender": "Male",   "blood_group": "O+", "phone": "9876543210", "address": "12 MG Road, Chennai"},
    "P002": {"id": "P002", "name": "Priya Sharma",   "age": 32, "gender": "Female", "blood_group": "A+", "phone": "9123456780", "address": "34 Anna Nagar, Chennai"},
    "P003": {"id": "P003", "name": "Arjun Reddy",    "age": 60, "gender": "Male",   "blood_group": "B-", "phone": "9988776655", "address": "5 Jubilee Hills, Hyderabad"},
    "P004": {"id": "P004", "name": "Meena Iyer",     "age": 28, "gender": "Female", "blood_group": "AB+","phone": "9845123456", "address": "7 Koramangala, Bangalore"},
    "P005": {"id": "P005", "name": "Suresh Pillai",  "age": 52, "gender": "Male",   "blood_group": "O-", "phone": "9001234567", "address": "18 Park Street, Kolkata"},
}

# ── Doctors ───────────────────────────────────────────────────────────────────
DOCTORS: dict[str, dict] = {
    "D001": {"id": "D001", "name": "Dr. Anitha Krishnan", "department": "Cardiology",      "qualification": "MD, DM Cardiology", "experience_years": 15, "consultation_fee": 800},
    "D002": {"id": "D002", "name": "Dr. Ramesh Babu",     "department": "Orthopedics",     "qualification": "MS Orthopedics",    "experience_years": 12, "consultation_fee": 700},
    "D003": {"id": "D003", "name": "Dr. Sunita Patel",    "department": "Pediatrics",      "qualification": "MD Pediatrics",     "experience_years": 10, "consultation_fee": 600},
    "D004": {"id": "D004", "name": "Dr. Vijay Menon",     "department": "General Medicine", "qualification": "MBBS, MD",          "experience_years": 8,  "consultation_fee": 500},
    "D005": {"id": "D005", "name": "Dr. Lakshmi Rao",     "department": "Gynecology",      "qualification": "MS Gynecology",     "experience_years": 14, "consultation_fee": 750},
    "D006": {"id": "D006", "name": "Dr. Karthik Nair",    "department": "Neurology",       "qualification": "MD, DM Neurology",  "experience_years": 11, "consultation_fee": 900},
    "D007": {"id": "D007", "name": "Dr. Deepa Joshi",     "department": "Dermatology",     "qualification": "MD Dermatology",    "experience_years": 7,  "consultation_fee": 650},
    "D008": {"id": "D008", "name": "Dr. Arun Thomas",     "department": "ENT",             "qualification": "MS ENT",            "experience_years": 9,  "consultation_fee": 600},
}

# Available slots per doctor: dates relative to today
_SLOT_TIMES = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "14:00", "14:30", "15:00", "15:30"]

AVAILABILITY: dict[str, dict[str, list[str]]] = {}
for _did in DOCTORS:
    AVAILABILITY[_did] = {}
    for _offset in range(7):
        _day = (today + timedelta(days=_offset)).isoformat()
        # Simulate some slots already booked
        _available = [t for i, t in enumerate(_SLOT_TIMES) if (i + _offset) % 3 != 0]
        AVAILABILITY[_did][_day] = _available

# ── Appointments ──────────────────────────────────────────────────────────────
APPOINTMENTS: dict[str, dict] = {
    "A001": {"id": "A001", "patient_id": "P001", "doctor_id": "D001", "date": today.isoformat(),                        "time": "09:00", "status": "confirmed", "reason": "Chest pain follow-up"},
    "A002": {"id": "A002", "patient_id": "P002", "doctor_id": "D003", "date": (today + timedelta(days=1)).isoformat(),  "time": "10:00", "status": "confirmed", "reason": "Child vaccination"},
    "A003": {"id": "A003", "patient_id": "P003", "doctor_id": "D002", "date": (today + timedelta(days=2)).isoformat(),  "time": "11:00", "status": "confirmed", "reason": "Knee pain"},
}
_appt_counter = 4

# ── Wards ─────────────────────────────────────────────────────────────────────
WARDS: dict[str, dict] = {
    "W001": {"id": "W001", "name": "General Ward",    "floor": 1, "total_beds": 30, "occupied_beds": 22, "nurse_station": "1A"},
    "W002": {"id": "W002", "name": "ICU",             "floor": 2, "total_beds": 10, "occupied_beds": 8,  "nurse_station": "2A"},
    "W003": {"id": "W003", "name": "Cardiac Care",    "floor": 2, "total_beds": 8,  "occupied_beds": 5,  "nurse_station": "2B"},
    "W004": {"id": "W004", "name": "Maternity Ward",  "floor": 3, "total_beds": 12, "occupied_beds": 7,  "nurse_station": "3A"},
    "W005": {"id": "W005", "name": "Pediatric Ward",  "floor": 3, "total_beds": 10, "occupied_beds": 4,  "nurse_station": "3B"},
    "W006": {"id": "W006", "name": "Orthopedic Ward", "floor": 4, "total_beds": 15, "occupied_beds": 10, "nurse_station": "4A"},
}


# ── Mutating helpers (used by tools) ─────────────────────────────────────────

def book_slot(patient_id: str, doctor_id: str, date_str: str, time_str: str, reason: str) -> str | None:
    global _appt_counter
    if doctor_id not in AVAILABILITY:
        return None
    slots = AVAILABILITY[doctor_id].get(date_str, [])
    if time_str not in slots:
        return None
    slots.remove(time_str)
    appt_id = f"A{_appt_counter:03d}"
    _appt_counter += 1
    APPOINTMENTS[appt_id] = {
        "id": appt_id, "patient_id": patient_id, "doctor_id": doctor_id,
        "date": date_str, "time": time_str, "status": "confirmed", "reason": reason,
    }
    return appt_id


def cancel_appointment(appt_id: str) -> bool:
    if appt_id not in APPOINTMENTS:
        return False
    appt = APPOINTMENTS[appt_id]
    APPOINTMENTS[appt_id]["status"] = "cancelled"
    # Return the slot
    doctor_id, date_str, time_str = appt["doctor_id"], appt["date"], appt["time"]
    AVAILABILITY.setdefault(doctor_id, {}).setdefault(date_str, []).append(time_str)
    AVAILABILITY[doctor_id][date_str].sort()
    return True
