"""The clinic: doctors, opening hours, the appointment book and the FAQ the receptionist answers from.

Sehat Family Clinic is fictional. Times are in India Standard Time. Each conversation gets its own in-memory book,
seeded with existing appointments so availability looks like a real week.
"""

from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CLINIC_NAME = "Sehat Family Clinic"
CLINIC_NAME_HI = "सेहत फ़ैमिली क्लिनिक"
BOOKING_WINDOW_DAYS = 14


@dataclass(frozen=True)
class Doctor:
    id: str
    name: str
    name_hi: str
    specialty: str
    specialty_hi: str
    days: tuple[int, ...]  # 0 = Monday
    start: time
    end: time
    slot_minutes: int
    fee_inr: int


DOCTORS: tuple[Doctor, ...] = (
    Doctor("mehta", "Dr. Anjali Mehta", "डॉ. अंजलि मेहता", "general physician", "जनरल फ़िज़िशियन",
           (0, 1, 2, 3, 4, 5), time(9, 0), time(13, 0), 15, 600),
    Doctor("khan", "Dr. Imran Khan", "डॉ. इमरान ख़ान", "paediatrician", "बच्चों के डॉक्टर",
           (0, 2, 4, 5), time(16, 0), time(20, 0), 20, 800),
    Doctor("iyer", "Dr. Priya Iyer", "डॉ. प्रिया अय्यर", "dentist", "दाँतों की डॉक्टर",
           (1, 3, 5), time(10, 0), time(18, 0), 30, 700),
    Doctor("singh", "Dr. Harpreet Singh", "डॉ. हरप्रीत सिंह", "dermatologist", "त्वचा रोग विशेषज्ञ",
           (0, 3), time(14, 0), time(18, 0), 20, 900),
)  # fmt: skip
DOCTOR_BY_ID = {d.id: d for d in DOCTORS}

SPECIALTY_ALIASES = {
    "general physician": "mehta", "general": "mehta", "physician": "mehta", "gp": "mehta", "fever": "mehta",
    "paediatrician": "khan", "pediatrician": "khan", "child": "khan", "children": "khan", "baby": "khan",
    "dentist": "iyer", "dental": "iyer", "teeth": "iyer", "tooth": "iyer",
    "dermatologist": "singh", "skin": "singh", "derma": "singh",
}  # fmt: skip

FAQ: dict[str, dict[str, str]] = {
    "hours": {
        "en": "The clinic is open Monday to Saturday, 9 am to 8 pm, and closed on Sundays and national holidays. "
        "Each doctor has their own days and hours.",
        "hi": "क्लिनिक सोमवार से शनिवार, सुबह 9 बजे से रात 8 बजे तक खुला रहता है। रविवार और राष्ट्रीय छुट्टियों पर बंद रहता है। "
        "हर डॉक्टर के अपने दिन और समय हैं।",
    },
    "address": {
        "en": "We are on the 2nd floor of Sunrise Plaza, Sector 21, Dwarka, New Delhi, a 5-minute walk from Dwarka "
        "Sector 21 metro station.",
        "hi": "हम सनराइज़ प्लाज़ा की दूसरी मंज़िल पर हैं, सेक्टर 21, द्वारका, नई दिल्ली। द्वारका सेक्टर 21 मेट्रो स्टेशन से 5 मिनट पैदल।",
    },
    "fees": {
        "en": "Consultation fees: Dr. Mehta 600 rupees, Dr. Khan 800, Dr. Iyer 700, Dr. Singh 900. Follow-ups within 7 "
        "days are free.",
        "hi": "कंसल्टेशन फ़ीस: डॉ. मेहता 600 रुपये, डॉ. ख़ान 800, डॉ. अय्यर 700, डॉ. सिंह 900। 7 दिन के अंदर फ़ॉलो-अप मुफ़्त है।",
    },
    "payment": {
        "en": "We accept UPI, debit and credit cards, and cash.",
        "hi": "हम UPI, डेबिट और क्रेडिट कार्ड, और कैश लेते हैं।",
    },
    "insurance": {
        "en": "We offer cashless consultations with Star Health, HDFC ERGO and ICICI Lombard. For other insurers, "
        "pay at the clinic and claim reimbursement with our bill.",
        "hi": "स्टार हेल्थ, HDFC ERGO और ICICI लोम्बार्ड के साथ कैशलेस सुविधा है। बाकी बीमा कंपनियों के लिए भुगतान करके बिल से क्लेम कर सकते हैं।",
    },
    "parking": {
        "en": "Free parking is available in the Sunrise Plaza basement.",
        "hi": "सनराइज़ प्लाज़ा के बेसमेंट में मुफ़्त पार्किंग है।",
    },
    "lab": {
        "en": "Blood samples are collected from 8 am to 11 am, Monday to Saturday. Most reports are ready the same "
        "evening and are sent on WhatsApp.",
        "hi": "ब्लड सैंपल सोमवार से शनिवार सुबह 8 से 11 बजे तक लिए जाते हैं। ज़्यादातर रिपोर्ट उसी शाम व्हाट्सऐप पर भेज दी जाती हैं।",
    },
    "vaccination": {
        "en": "Child vaccinations are given by Dr. Khan during his clinic hours. Please bring the vaccination card.",
        "hi": "बच्चों का टीकाकरण डॉ. ख़ान के समय में होता है। टीकाकरण कार्ड साथ लाएँ।",
    },
    "teleconsultation": {
        "en": "Video consultations are available with Dr. Mehta and Dr. Singh at the same fee. Book a normal slot and "
        "ask for video.",
        "hi": "डॉ. मेहता और डॉ. सिंह के साथ वीडियो कंसल्टेशन उसी फ़ीस पर मिलता है। सामान्य स्लॉट बुक करें और वीडियो के लिए बताएँ।",
    },
    "documents": {
        "en": "Please bring a photo ID, previous prescriptions and any recent test reports.",
        "hi": "कृपया फ़ोटो आईडी, पुराने पर्चे और हाल की जाँच रिपोर्ट साथ लाएँ।",
    },
    "cancellation": {
        "en": "You can cancel or reschedule free of charge up to 2 hours before the appointment.",
        "hi": "अपॉइंटमेंट से 2 घंटे पहले तक आप मुफ़्त में कैंसल या रीशेड्यूल कर सकते हैं।",
    },
}


def now_ist() -> datetime:
    return datetime.now(IST)


def normalize_phone(raw: str) -> str | None:
    """Indian mobile numbers: 10 digits starting 6-9, optionally with +91 / 0 prefix and spaces or dashes."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def mask_phone(phone: str) -> str:
    return phone[:2] + "******" + phone[-2:] if len(phone) == 10 else "**********"


def slot_id(doctor_id: str, start: datetime) -> str:
    return f"{doctor_id}@{start.strftime('%Y-%m-%dT%H:%M')}"


def parse_slot_id(sid: str) -> tuple[Doctor, datetime] | None:
    m = re.fullmatch(r"([a-z]+)@(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", sid or "")
    if not m or m.group(1) not in DOCTOR_BY_ID:
        return None
    return DOCTOR_BY_ID[m.group(1)], datetime.fromisoformat(m.group(2)).replace(tzinfo=IST)


class Clinic:
    """One conversation's appointment book (SQLite, in memory)."""

    def __init__(self, today: datetime | None = None, seed: int = 7, fill: float = 0.45):
        self.now = (today or now_ist()).astimezone(IST)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE appointments (ref TEXT PRIMARY KEY, patient_name TEXT, phone TEXT, doctor_id TEXT, "
            "start TEXT, status TEXT, created_at TEXT)"
        )
        self._rng = random.Random(seed)
        self._seed(fill)

    # ------------------------------------------------------------ schedule
    def all_slots(self, doc: Doctor, day: date) -> list[datetime]:
        if day.weekday() not in doc.days:
            return []
        t = datetime.combine(day, doc.start, IST)
        end = datetime.combine(day, doc.end, IST)
        out = []
        while t + timedelta(minutes=doc.slot_minutes) <= end:
            out.append(t)
            t += timedelta(minutes=doc.slot_minutes)
        return out

    def is_free(self, doc: Doctor, start: datetime) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM appointments WHERE doctor_id=? AND start=? AND status='booked'",
            (doc.id, start.isoformat()),
        ).fetchone()
        return row is None

    def in_window(self, start: datetime) -> bool:
        return self.now + timedelta(minutes=30) <= start <= self.now + timedelta(days=BOOKING_WINDOW_DAYS)

    def free_slots(self, doc: Doctor, day: date, part_of_day: str = "any") -> list[datetime]:
        ranges = {"morning": (0, 12), "afternoon": (12, 16), "evening": (16, 24), "any": (0, 24)}
        lo, hi = ranges.get(part_of_day, (0, 24))
        return [s for s in self.all_slots(doc, day) if lo <= s.hour < hi and self.in_window(s) and self.is_free(doc, s)]

    # ------------------------------------------------------------ bookings
    def _ref(self) -> str:
        while True:
            ref = f"SFC-{self._rng.randint(1000, 9999)}"
            if not self.db.execute("SELECT 1 FROM appointments WHERE ref=?", (ref,)).fetchone():
                return ref

    def book(self, doc: Doctor, start: datetime, patient_name: str, phone: str) -> str:
        ref = self._ref()
        self.db.execute(
            "INSERT INTO appointments VALUES (?,?,?,?,?,?,?)",
            (ref, patient_name.strip(), phone, doc.id, start.isoformat(), "booked", self.now.isoformat()),
        )
        return ref

    def get(self, ref: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM appointments WHERE ref=?", ((ref or "").upper().strip(),)).fetchone()

    def find_by_phone(self, phone: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM appointments WHERE phone=? AND status='booked' AND start>=? ORDER BY start",
            (phone, self.now.isoformat()),
        ).fetchall()

    def cancel(self, ref: str) -> None:
        self.db.execute("UPDATE appointments SET status='cancelled' WHERE ref=?", (ref,))

    def move(self, ref: str, doc: Doctor, start: datetime) -> None:
        self.db.execute("UPDATE appointments SET doctor_id=?, start=? WHERE ref=?", (doc.id, start.isoformat(), ref))

    def bookings(self, include_seeded: bool = False) -> list[dict]:
        q = "SELECT * FROM appointments" + ("" if include_seeded else " WHERE patient_name NOT LIKE '~%'")
        return [dict(r) for r in self.db.execute(q + " ORDER BY start").fetchall()]

    def _seed(self, fill: float) -> None:
        """Fill part of the calendar with other patients (names start with ~ so the demo can hide them)."""
        for offset in range(BOOKING_WINDOW_DAYS + 1):
            day = (self.now + timedelta(days=offset)).date()
            for doc in DOCTORS:
                for s in self.all_slots(doc, day):
                    if self._rng.random() < fill:
                        self.db.execute(
                            "INSERT INTO appointments VALUES (?,?,?,?,?,?,?)",
                            (self._ref(), "~other patient", "9000000000", doc.id, s.isoformat(), "booked", ""),
                        )
        # Two known patients so callers can test "find / reschedule / cancel my appointment".
        for name, phone, doc_id, days_ahead in (
            ("Rahul Sharma", "9811122233", "mehta", 2),
            ("Sunita Verma", "9876543210", "iyer", 3),
        ):
            doc = DOCTOR_BY_ID[doc_id]
            for k in range(days_ahead, days_ahead + 7):
                day = (self.now + timedelta(days=k)).date()
                free = [s for s in self.all_slots(doc, day) if self.is_free(doc, s)]
                if free:
                    self.book(doc, free[len(free) // 2], name, phone)
                    break


def describe_slot(doc: Doctor, start: datetime, lang: str) -> str:
    if lang == "hi":
        days = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
        months = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
        h = start.hour % 12 or 12
        part = "सुबह" if start.hour < 12 else "दोपहर" if start.hour < 16 else "शाम"
        mins = f":{start.minute:02d}" if start.minute else ""
        return (
            f"{days[start.weekday()]}, {start.day} {months[start.month - 1]}, {part} {h}{mins} बजे, {doc.name_hi} के साथ"
        )
    return f"{start.strftime('%A, %d %B')} at {start.strftime('%I:%M %p').lstrip('0')} with {doc.name}"
