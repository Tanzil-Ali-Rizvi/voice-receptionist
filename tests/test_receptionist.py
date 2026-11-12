from datetime import datetime

import pytest

from receptionist.agent import Receptionist
from receptionist.clinic import IST, Clinic, normalize_phone, parse_slot_id
from receptionist.lang import confirmation, detect, reply_matches
from receptionist.llm import ScriptedLLM
from receptionist.safety import emergency, looks_like_medical_advice, redact_phones
from receptionist.tools import Desk

MONDAY = datetime(2026, 11, 16, 9, 0, tzinfo=IST)  # Dr. Iyer (dentist) works on Tuesday the 17th


def first_free(clinic: Clinic, doctor: str, day: str) -> str:
    res = Desk(clinic).run("find_slots", {"doctor": doctor, "date": day})
    assert res.ok and res.data["slots"], res.data
    return res.data["slots"][0]["slot_id"]


# ------------------------------------------------------------------ language
@pytest.mark.parametrize(
    "text,lang",
    [
        ("I need an appointment with the dentist tomorrow", "en"),
        ("कल सुबह डॉक्टर से मिलना है", "hi"),
        ("mujhe kal subah dentist ka appointment chahiye", "hinglish"),
        ("bachche ko bukhar hai, doctor kab milenge?", "hinglish"),
        ("Is parking available?", "en"),
        ("main kal aa sakta hoon kya", "hinglish"),
        ("haan", "hinglish"),
    ],
)
def test_language_detection(text, lang):
    assert detect(text) == lang


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes", "yes"), ("haan ji", "yes"), ("हाँ, कर दीजिए", "yes"), ("theek hai book kar do", "yes"), ("ok please", "yes"),
        ("no", "no"), ("nahi, rukiye", "no"), ("नहीं", "no"), ("wait, change the time", "no"),
        ("haan but 5 baje karo", "unclear"), ("yes, lekin kal", "unclear"), ("what is the fee?", "unclear"),
        ("ok so first tell me the address and also whether parking is there", "unclear"),
    ],
)  # fmt: skip
def test_confirmation_is_strict(text, expected):
    assert confirmation(text) == expected


def test_reply_language_check():
    assert reply_matches("hi", "आपका अपॉइंटमेंट SFC-1234 बुक हो गया है।")
    assert not reply_matches("hinglish", "Your appointment is booked.")
    assert reply_matches("en", "Booked for 10:30 am.")


# ------------------------------------------------------------------ safety
@pytest.mark.parametrize(
    "text,kind",
    [
        ("my father has severe chest pain", "cardiac"), ("papa ke seene mein dard ho raha hai", "cardiac"),
        ("मेरी माँ को सांस लेने में दिक्कत है", "breathing"), ("saans nahi aa rahi", "breathing"),
        ("he fainted and is not responding", "unconscious"), ("dadi behosh ho gayi", "unconscious"),
        ("I want to kill myself", "self_harm"), ("jeena nahi chahta", "self_harm"),
        ("my son swallowed bleach", "poisoning"), ("bachche ko mirgi ka daura pad raha hai", "seizure"),
        ("uska chehra tedha ho gaya, lakwa jaisa lag raha", "stroke"),
    ],
)  # fmt: skip
def test_emergencies_detected_in_three_languages(text, kind):
    assert emergency(text) == kind


@pytest.mark.parametrize(
    "text",
    ["I need a dentist appointment", "bachche ko 2 din se bukhar hai", "can I get my chest x-ray report",
     "does the 5 pm slot fit my schedule", "skin allergy ke liye doctor", "मुझे दाँत में दर्द है"],
)  # fmt: skip
def test_routine_requests_are_not_emergencies(text):
    assert emergency(text) is None


def test_medical_advice_guard_and_phone_redaction():
    assert looks_like_medical_advice("Take 500 mg paracetamol twice a day.")
    assert looks_like_medical_advice("Dolo 650 le lijiye")
    assert not looks_like_medical_advice("Dr. Mehta can see you at 10 am tomorrow.")
    assert redact_phones("call me on 9876543210") == "call me on 98******10"


# ------------------------------------------------------------------ tools
def test_phone_and_slot_validation():
    assert normalize_phone("+91 98765-43210") == "9876543210"
    assert normalize_phone("12345") is None and normalize_phone("5876543210") is None
    desk = Desk(Clinic(MONDAY))
    r = desk.run("propose_booking", {"slot_id": "iyer@2026-11-16T10:00", "patient_name": "Aman", "phone": "9876501234"})
    assert not r.ok  # Dr. Iyer doesn't work on Mondays
    r = desk.run("find_slots", {"doctor": "dentist", "date": "2027-01-01"})
    assert not r.ok and "14 days" in r.data["error"]


def test_propose_does_not_book_until_commit():
    clinic = Clinic(MONDAY)
    desk = Desk(clinic)
    sid = first_free(clinic, "iyer", "2026-11-17")
    before = len(clinic.bookings())
    r = desk.run("propose_booking", {"slot_id": sid, "patient_name": "Aman Gupta", "phone": "98765 01234"})
    assert r.ok and desk.pending is not None and len(clinic.bookings()) == before
    done = desk.commit_pending()
    assert done.ok and done.data["booking_ref"].startswith("SFC-") and len(clinic.bookings()) == before + 1
    doc, start = parse_slot_id(sid)
    assert not clinic.is_free(doc, start)


def test_lookup_needs_phone_and_name_and_reschedule_needs_verification():
    clinic = Clinic(MONDAY)
    desk = Desk(clinic)
    rahul = [b for b in clinic.bookings() if b["patient_name"] == "Rahul Sharma"][0]
    # someone else's booking reference is useless without verification
    r = desk.run("propose_cancel", {"booking_ref": rahul["ref"]})
    assert not r.ok and "verify" in r.data["error"]
    wrong = desk.run("find_my_appointments", {"phone": "9811122233", "patient_name": "Sunita"})
    assert wrong.ok and wrong.data["appointments"] == []
    right = desk.run("find_my_appointments", {"phone": "9811122233", "patient_name": "rahul"})
    assert right.data["appointments"][0]["booking_ref"] == rahul["ref"]
    assert desk.run("propose_cancel", {"booking_ref": rahul["ref"]}).ok
    desk.commit_pending()
    assert clinic.get(rahul["ref"])["status"] == "cancelled"


# ------------------------------------------------------------------ the agent
async def test_full_booking_flow_with_confirmation_gate():
    sid = first_free(Clinic(MONDAY), "iyer", "2026-11-17")
    llm = ScriptedLLM([
        [("find_slots", {"doctor": "dentist", "date": "2026-11-17", "part_of_day": "morning"})],
        "डॉ. अय्यर के पास कल सुबह 10 बजे खाली है। मरीज़ का नाम और मोबाइल नंबर बताइए।",
        [("propose_booking", {"slot_id": sid, "patient_name": "Aman Gupta", "phone": "9876501234"})],
        "बुक हो गया!",  # the model claims too early; the guard replaces it with the exact read-back
        "हो गया, कल मिलते हैं।",
    ])  # fmt: skip
    r = Receptionist(llm, Clinic(MONDAY))
    t1 = await r.respond("mujhe kal dentist ka appointment chahiye")
    assert t1.user_lang == "hinglish" and t1.tools[0]["tool"] == "find_slots"
    t2 = await r.respond("Aman Gupta, 9876501234")
    assert "क्या मैं कन्फ़र्म" in t2.reply and "exact read-back" in t2.guards  # the reply is the read-back, not "Booked!"
    assert not [b for b in r.clinic.bookings() if b["patient_name"] == "Aman Gupta"]
    t3 = await r.respond("haan ji")
    booked = [b for b in r.clinic.bookings() if b["patient_name"] == "Aman Gupta"]
    assert booked and t3.committed and booked[0]["ref"] in t3.reply  # reference added by the guard
    assert "9876501234" not in str(t2.tools)  # phone masked in the trace


async def test_unclear_answer_does_not_commit():
    sid = first_free(Clinic(MONDAY), "iyer", "2026-11-17")
    llm = ScriptedLLM(
        [
            [("propose_booking", {"slot_id": sid, "patient_name": "Aman", "phone": "9876501234"})],
            "ok",
            "Sure, which time instead?",
        ]
    )
    r = Receptionist(llm, Clinic(MONDAY))
    await r.respond("book the first dentist slot tomorrow for Aman, 9876501234")
    t = await r.respond("haan but make it 5 pm")
    assert t.committed is None and not [b for b in r.clinic.bookings() if b["patient_name"] == "Aman"]


async def test_emergency_never_reaches_the_llm():
    llm = ScriptedLLM([])
    r = Receptionist(llm, Clinic(MONDAY))
    t = await r.respond("Papa ke seene mein bahut tez dard hai")
    assert t.emergency == "cardiac" and "112" in t.reply and llm.seen == [] and t.handoff
    assert t.reply_lang == "hi"


async def test_medical_advice_from_the_model_is_blocked():
    r = Receptionist(ScriptedLLM(["You should take 500 mg paracetamol twice a day."]), Clinic(MONDAY))
    t = await r.respond("I have a headache, what should I take?")
    assert "medical advice" in t.reply and "blocked medical advice" in t.guards


async def test_model_claiming_a_booking_without_tools_books_nothing():
    r = Receptionist(ScriptedLLM(["Your appointment is booked for tomorrow at 10."]), Clinic(MONDAY))
    before = len(r.clinic.bookings())
    await r.respond("book me with Dr. Mehta tomorrow 10 am, Aman, 9876501234")
    assert len(r.clinic.bookings()) == before


async def test_llm_failure_falls_back_to_human():
    r = Receptionist(ScriptedLLM([]), Clinic(MONDAY))
    t = await r.respond("what are your hours?")
    assert "staff" in t.reply and t.handoff


# ------------------------------------------------------------------ dates and language stickiness
def test_day_words_resolve_in_three_languages():
    from receptionist.dates import date_note, resolve_days

    days = dict(resolve_days("kal subah ya parso shaam", MONDAY))
    assert str(days["kal"]) == "2026-11-17" and str(days["parso"]) == "2026-11-18"
    assert str(dict(resolve_days("कल डॉक्टर मेहता", MONDAY))["कल"]) == "2026-11-17"
    assert str(dict(resolve_days("the day after tomorrow", MONDAY))["day after tomorrow"]) == "2026-11-18"
    assert "tomorrow" not in dict(resolve_days("the day after tomorrow", MONDAY))
    assert str(dict(resolve_days("shaniwar ko", MONDAY))["shaniwar"]) == "2026-11-21"
    assert str(dict(resolve_days("Monday next week", MONDAY))["monday"]) == "2026-11-23"
    assert resolve_days("कलम लेकर आना", MONDAY) == [] and resolve_days("sun lo, mujhe doctor chahiye", MONDAY) == []
    assert "Tuesday 2026-11-17" in date_note("mujhe kal appointment chahiye", MONDAY)


async def test_name_and_number_turn_keeps_the_hindi_reply():
    r = Receptionist(ScriptedLLM(["नाम बताइए।", "ठीक है।"]), Clinic(MONDAY))
    await r.respond("bachche ko bukhar hai, doctor kab milenge?")
    t = await r.respond("Beti Anaya Singh, number 9654321098")
    assert t.user_lang == "hinglish" and t.reply_lang == "hi"


async def test_date_note_reaches_the_model():
    llm = ScriptedLLM(["ठीक है।"])
    await Receptionist(llm, Clinic(MONDAY)).respond("kal dentist milega?")
    assert "kal' = tomorrow = Tuesday 2026-11-17" in llm.seen[0][0]["content"]
