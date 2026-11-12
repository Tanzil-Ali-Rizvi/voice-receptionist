"""The receptionist's tools. The LLM chooses which tool to call; this code decides what is allowed.

* Every argument is validated here (doctor exists, date inside the booking window, slot actually free, phone is a
  valid Indian mobile). Invalid calls return an error the model must recover from; nothing is guessed.
* Booking, rescheduling and cancelling are two-phase. The `propose_*` tools only create a *pending action* and return
  the read-back the receptionist must say. The change is committed by `commit_pending()`, which the agent calls only
  after a deterministic check that the caller said yes. No tool lets the model commit on its own.
* Lookups only reveal a booking when the caller gives the phone number *and* the name on it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .clinic import (
    DOCTOR_BY_ID,
    DOCTORS,
    FAQ,
    SPECIALTY_ALIASES,
    Clinic,
    Doctor,
    describe_slot,
    mask_phone,
    normalize_phone,
    parse_slot_id,
    slot_id,
)

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "find_slots",
            "description": "Free slots for a doctor or specialty on a date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor": {"type": "string", "description": "mehta, khan, iyer, singh, or a specialty"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "part_of_day": {"type": "string", "enum": ["morning", "afternoon", "evening", "any"]},
                },
                "required": ["doctor", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "next_available",
            "description": "Earliest free slots in the next 14 days, starting from the day the caller asked for.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor": {"type": "string"},
                    "part_of_day": {"type": "string", "enum": ["morning", "afternoon", "evening", "any"]},
                },
                "required": ["doctor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_booking",
            "description": "Prepare a booking (not booked until the caller confirms).",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_id": {"type": "string", "description": "from find_slots/next_available"},
                    "patient_name": {"type": "string"},
                    "phone": {"type": "string", "description": "10-digit mobile"},
                    "second_visit": {"type": "boolean", "description": "true only if the caller wants another visit"},
                },
                "required": ["slot_id", "patient_name", "phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_my_appointments",
            "description": "A caller's upcoming appointments; needs phone and patient name.",
            "parameters": {
                "type": "object",
                "properties": {"phone": {"type": "string"}, "patient_name": {"type": "string"}},
                "required": ["phone", "patient_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_reschedule",
            "description": "Prepare moving an appointment (not done until the caller confirms).",
            "parameters": {
                "type": "object",
                "properties": {"booking_ref": {"type": "string"}, "new_slot_id": {"type": "string"}},
                "required": ["booking_ref", "new_slot_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_cancel",
            "description": "Prepare cancelling an appointment (not done until the caller confirms).",
            "parameters": {
                "type": "object",
                "properties": {"booking_ref": {"type": "string"}},
                "required": ["booking_ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clinic_info",
            "description": "Clinic facts; answer clinic questions only from this.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "enum": sorted(FAQ)}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": "Hand the call to staff: medical questions, complaints, billing, or when asked for a person.",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        },
    },
]


@dataclass
class Pending:
    kind: str  # book | reschedule | cancel
    args: dict[str, Any]
    readback_en: str
    readback_hi: str


@dataclass
class ToolResult:
    name: str
    args: dict[str, Any]
    ok: bool
    data: dict[str, Any]

    def for_model(self) -> str:
        return json.dumps({"ok": self.ok, **self.data}, ensure_ascii=False)


@dataclass
class Desk:
    """Tool executor for one conversation."""

    clinic: Clinic
    pending: Pending | None = None
    verified_phone: str | None = None  # set after a successful find_my_appointments
    handoff: str | None = None
    lang: str = "en"  # the reply language; tool results describe slots only in it, to keep prompts small
    asked_day: date | None = None  # the day the caller named this turn ("kal"), resolved in code
    log: list[ToolResult] = field(default_factory=list)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _doctor(spec: str) -> Doctor | None:
        s = (spec or "").lower().strip()
        if s in DOCTOR_BY_ID:
            return DOCTOR_BY_ID[s]
        for d in DOCTORS:
            if d.id in s or d.name.lower() in s:
                return d
        for k, v in SPECIALTY_ALIASES.items():
            if k in s:
                return DOCTOR_BY_ID[v]
        return None

    def _slots(self, doc: Doctor, starts: list[datetime]) -> list[dict[str, str]]:
        return [{"slot_id": slot_id(doc.id, s), "when": describe_slot(doc, s, self.lang)} for s in starts]

    def _working_days_from(self, doc: Doctor, day: date, n: int = 2) -> list[str]:
        out, k = [], 0
        last = (self.clinic.now + timedelta(days=14)).date()
        while len(out) < n and day + timedelta(days=k) <= last:
            d = day + timedelta(days=k)
            if d.weekday() in doc.days:
                out.append(d.strftime("%A %Y-%m-%d"))
            k += 1
        return out

    def _rb(self) -> str:
        assert self.pending is not None
        return self.pending.readback_hi if self.lang == "hi" else self.pending.readback_en

    def _err(self, name: str, args: dict, msg: str, **extra: Any) -> ToolResult:
        return ToolResult(name, args, False, {"error": msg, **extra})

    # ------------------------------------------------------------ dispatch
    def run(self, name: str, args: dict[str, Any]) -> ToolResult:
        fn = getattr(self, f"t_{name}", None)
        try:
            res = fn(args) if fn else self._err(name, args, f"unknown tool {name}")
        except (TypeError, ValueError, KeyError) as e:
            res = self._err(name, args, f"bad arguments: {e}")
        self.log.append(res)
        return res

    def t_find_slots(self, a: dict) -> ToolResult:
        doc = self._doctor(a.get("doctor", ""))
        if doc is None:
            return self._err("find_slots", a, "unknown doctor or specialty", doctors=self.doctor_list())
        try:
            day = date.fromisoformat(a["date"])
        except (KeyError, ValueError):
            return self._err("find_slots", a, "date must be YYYY-MM-DD")
        if not self.clinic.now.date() <= day <= (self.clinic.now + timedelta(days=14)).date():
            return self._err(
                "find_slots", a, "date is outside the next 14 days", today=self.clinic.now.date().isoformat()
            )
        if self.asked_day and day < self.asked_day:
            return self._err(
                "find_slots",
                a,
                f"the caller asked for {self.asked_day.strftime('%A %Y-%m-%d')}, not an earlier day; search that day",
            )
        if day.weekday() not in doc.days:
            days = ", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in doc.days)
            return self._err(
                "find_slots",
                a,
                f"{doc.name} does not see patients on {day.strftime('%A')}s. Tell the caller this plainly, "
                "then offer the nearest day.",
                works_on=days,
                nearest_days=self._working_days_from(doc, day),
            )
        part = a.get("part_of_day") or "any"
        free = self.clinic.free_slots(doc, day, part)
        data: dict[str, Any] = {"doctor": doc.name}
        if not free and part != "any":
            # "Afternoon" for a doctor who works 4-8 pm: show that day's times instead of an empty list.
            free = self.clinic.free_slots(doc, day, "any")
            if free:
                data["note"] = f"nothing in the {part}; these are the other free times that day"
        data |= {"slots": self._slots(doc, free[:5]), "more": max(0, len(free) - 5)}
        return ToolResult("find_slots", a, True, data)

    def t_next_available(self, a: dict) -> ToolResult:
        doc = self._doctor(a.get("doctor", ""))
        if doc is None:
            return self._err("next_available", a, "unknown doctor or specialty", doctors=self.doctor_list())
        today = self.clinic.now.date()
        start = self.asked_day if self.asked_day and self.asked_day >= today else today
        found: list[datetime] = []
        for k in range(15):
            day = start + timedelta(days=k)
            if day > today + timedelta(days=14):
                break
            found += self.clinic.free_slots(doc, day, a.get("part_of_day") or "any") or (
                self.clinic.free_slots(doc, day, "any") if (a.get("part_of_day") or "any") != "any" else []
            )
            if len(found) >= 3:
                break
        data: dict[str, Any] = {"doctor": doc.name, "slots": self._slots(doc, found[:3])}
        if self.asked_day and found and found[0].date() != self.asked_day:
            why = "does not work" if self.asked_day.weekday() not in doc.days else "is fully booked"
            data["note"] = (f"The caller asked for {self.asked_day.strftime('%A %Y-%m-%d')}; {doc.name} {why} that "
                            "day. Say so plainly before offering these.")  # fmt: skip
        return ToolResult("next_available", a, True, data)

    def t_propose_booking(self, a: dict) -> ToolResult:
        parsed = parse_slot_id(a.get("slot_id", ""))
        if parsed is None:
            return self._err("propose_booking", a, "invalid slot_id; use one returned by find_slots")
        doc, start = parsed
        if not (
            self.clinic.in_window(start)
            and start in self.clinic.all_slots(doc, start.date())
            and self.clinic.is_free(doc, start)
        ):
            return self._err("propose_booking", a, "that slot is not available; search again")
        phone = normalize_phone(a.get("phone", ""))
        if phone is None:
            return self._err(
                "propose_booking", a, "phone must be a valid 10-digit Indian mobile number; ask the caller again"
            )
        name = " ".join((a.get("patient_name") or "").split())[:60]
        if len(name) < 2:
            return self._err("propose_booking", a, "patient name is missing")
        # A patient who already has a visit with this doctor usually wants to move it, not add a second one.
        same = [
            b for b in self.clinic.find_by_phone(phone)
            if b["doctor_id"] == doc.id and b["patient_name"].lower() == name.lower()
        ]  # fmt: skip
        if same and not a.get("second_visit"):
            b = same[0]
            when = describe_slot(DOCTOR_BY_ID[b["doctor_id"]], datetime.fromisoformat(b["start"]), "en")
            return self._err(
                "propose_booking",
                a,
                f"{name} already has {b['ref']} ({when}). To move it use propose_reschedule with booking_ref "
                f"{b['ref']}; only if the caller wants an extra visit, call again with second_visit=true.",
            )
        self.pending = Pending(
            "book",
            {"doctor_id": doc.id, "start": start.isoformat(), "patient_name": name, "phone": phone},
            f"{name}, {describe_slot(doc, start, 'en')}, phone ending {phone[-4:]}. Shall I confirm?",
            f"{name}, {describe_slot(doc, start, 'hi')}, फ़ोन नंबर जिसके आख़िरी अंक {phone[-4:]} हैं। क्या मैं कन्फ़र्म कर दूँ?",
        )
        return ToolResult(
            "propose_booking", a, True, {"status": "awaiting caller confirmation", "read_back": self._rb()}
        )

    def t_find_my_appointments(self, a: dict) -> ToolResult:
        phone = normalize_phone(a.get("phone", ""))
        if phone is None:
            return self._err("find_my_appointments", a, "phone must be a valid 10-digit Indian mobile number")
        name = (a.get("patient_name") or "").strip().lower()
        rows = [r for r in self.clinic.find_by_phone(phone) if name and name.split()[0] in r["patient_name"].lower()]
        if not rows:
            # Same answer whether the number is unknown or the name doesn't match: no information leaks.
            return ToolResult(
                "find_my_appointments",
                a,
                True,
                {"appointments": [], "note": "no upcoming appointment matches that name and number"},
            )
        self.verified_phone = phone
        out = []
        for r in rows:
            doc = DOCTOR_BY_ID[r["doctor_id"]]
            start = datetime.fromisoformat(r["start"])
            out.append({"booking_ref": r["ref"], "when": describe_slot(doc, start, self.lang)})
        return ToolResult("find_my_appointments", a, True, {"appointments": out})

    def _own_booking(self, ref: str):
        row = self.clinic.get(ref)
        if row is None or row["status"] != "booked" or row["patient_name"].startswith("~"):
            return None, "no active appointment with that reference"
        if self.verified_phone != row["phone"]:
            return None, "verify the caller first with find_my_appointments (phone and name)"
        return row, ""

    def t_propose_reschedule(self, a: dict) -> ToolResult:
        row, err = self._own_booking(a.get("booking_ref", ""))
        if row is None:
            return self._err("propose_reschedule", a, err)
        parsed = parse_slot_id(a.get("new_slot_id", ""))
        if parsed is None:
            return self._err("propose_reschedule", a, "invalid new_slot_id; use one returned by find_slots")
        doc, start = parsed
        if not (
            self.clinic.in_window(start)
            and start in self.clinic.all_slots(doc, start.date())
            and self.clinic.is_free(doc, start)
        ):
            return self._err("propose_reschedule", a, "that slot is not available; search again")
        self.pending = Pending(
            "reschedule",
            {"ref": row["ref"], "doctor_id": doc.id, "start": start.isoformat()},
            f"Move {row['patient_name']}'s appointment {row['ref']} to {describe_slot(doc, start, 'en')}. Shall I confirm?",
            f"{row['patient_name']} का अपॉइंटमेंट {row['ref']} बदलकर {describe_slot(doc, start, 'hi')} कर दूँ? क्या मैं कन्फ़र्म करूँ?",
        )
        return ToolResult(
            "propose_reschedule", a, True, {"status": "awaiting caller confirmation", "read_back": self._rb()}
        )

    def t_propose_cancel(self, a: dict) -> ToolResult:
        row, err = self._own_booking(a.get("booking_ref", ""))
        if row is None:
            return self._err("propose_cancel", a, err)
        doc = DOCTOR_BY_ID[row["doctor_id"]]
        start = datetime.fromisoformat(row["start"])
        self.pending = Pending(
            "cancel",
            {"ref": row["ref"]},
            f"Cancel {row['patient_name']}'s appointment on {describe_slot(doc, start, 'en')}. Shall I confirm?",
            f"{row['patient_name']} का {describe_slot(doc, start, 'hi')} वाला अपॉइंटमेंट कैंसल कर दूँ? क्या मैं कन्फ़र्म करूँ?",
        )
        return ToolResult(
            "propose_cancel", a, True, {"status": "awaiting caller confirmation", "read_back": self._rb()}
        )

    def t_clinic_info(self, a: dict) -> ToolResult:
        topic = a.get("topic", "")
        if topic not in FAQ:
            return self._err("clinic_info", a, "unknown topic", topics=sorted(FAQ))
        return ToolResult(
            "clinic_info", a, True, {"topic": topic, "answer": FAQ[topic]["hi" if self.lang == "hi" else "en"]}
        )

    def t_transfer_to_human(self, a: dict) -> ToolResult:
        self.handoff = str(a.get("reason", "caller request"))[:200]
        return ToolResult("transfer_to_human", a, True, {"status": "a staff member will call back within 15 minutes"})

    # ------------------------------------------------------------ commit (called by the agent, never by the model)
    def commit_pending(self) -> ToolResult:
        p = self.pending
        assert p is not None
        self.pending = None
        a = p.args
        if p.kind == "book":
            doc = DOCTOR_BY_ID[a["doctor_id"]]
            start = datetime.fromisoformat(a["start"])
            if not self.clinic.is_free(doc, start):  # someone else took it meanwhile
                return self._logged(ToolResult("commit", a, False, {"error": "slot was just taken; offer another"}))
            ref = self.clinic.book(doc, start, a["patient_name"], a["phone"])
            self.verified_phone = a["phone"]
            return self._logged(
                ToolResult(
                    "commit",
                    {"kind": "book"},
                    True,
                    {"booked": True, "booking_ref": ref, "when": describe_slot(doc, start, self.lang)},
                )
            )
        if p.kind == "reschedule":
            doc = DOCTOR_BY_ID[a["doctor_id"]]
            start = datetime.fromisoformat(a["start"])
            if not self.clinic.is_free(doc, start):
                return self._logged(ToolResult("commit", a, False, {"error": "slot was just taken; offer another"}))
            self.clinic.move(a["ref"], doc, start)
            return self._logged(
                ToolResult(
                    "commit",
                    {"kind": "reschedule"},
                    True,
                    {"rescheduled": True, "booking_ref": a["ref"], "when": describe_slot(doc, start, self.lang)},
                )
            )
        self.clinic.cancel(a["ref"])
        return self._logged(
            ToolResult("commit", {"kind": "cancel"}, True, {"cancelled": True, "booking_ref": a["ref"]})
        )

    def _logged(self, r: ToolResult) -> ToolResult:
        self.log.append(r)
        return r

    @staticmethod
    def doctor_list() -> list[dict[str, Any]]:
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return [
            {"id": d.id, "name": d.name, "specialty": d.specialty, "days": ", ".join(names[i] for i in d.days),
             "hours": f"{d.start.strftime('%H:%M')}-{d.end.strftime('%H:%M')}", "fee_inr": d.fee_inr}
            for d in DOCTORS
        ]  # fmt: skip


def public_log(results: list[ToolResult]) -> list[dict[str, Any]]:
    """Tool calls for the dashboard, with phone numbers masked."""
    out = []
    for r in results:
        args = {k: (mask_phone(normalize_phone(v) or "") if k == "phone" else v) for k, v in r.args.items()}
        out.append(
            {"tool": r.name, "args": args, "ok": r.ok, "result": {k: v for k, v in r.data.items() if k not in ("hi",)}}
        )
    return out
