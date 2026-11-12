"""Talk to Riya, the Hindi/English receptionist of the (fictional) Sehat Family Clinic."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from receptionist.agent import GREETING, Receptionist  # noqa: E402
from receptionist.clinic import DOCTOR_BY_ID, DOCTORS, Clinic, describe_slot, mask_phone  # noqa: E402
from receptionist.llm import from_env  # noqa: E402
from receptionist.speech import synthesize, transcribe  # noqa: E402

MAX_TURNS = 30
REPO = "https://github.com/Tanzil-Ali-Rizvi/voice-receptionist"

st.set_page_config(page_title="Riya, clinic receptionist", page_icon="📞", layout="wide")

try:  # Streamlit Cloud secrets -> environment, so the library code reads one place
    if "GROQ_API_KEY" in st.secrets:
        os.environ["GROQ_API_KEY"] = st.secrets["GROQ_API_KEY"]
except Exception:
    pass


@st.cache_resource
def background_loop() -> asyncio.AbstractEventLoop:
    """One event loop for the whole app, so the gateway's HTTP clients are reused across reruns."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, background_loop()).result(timeout=120)


@st.cache_resource
def shared_llm():
    return from_env()


def new_call() -> None:
    st.session_state.rec = Receptionist(shared_llm(), Clinic())
    st.session_state.chat = [{"role": "riya", "text": GREETING["en"], "lang": "en", "audio": None, "meta": ""}]
    st.session_state.last_audio = None
    st.session_state.timing = {}


if "rec" not in st.session_state:
    new_call()
rec: Receptionist = st.session_state.rec


def handle(text: str, stt_ms: float | None = None, heard_as: str = "") -> None:
    if len(rec.turns) >= MAX_TURNS:
        st.warning("This demo call has reached its turn limit. Start a new call.")
        return
    t0 = time.perf_counter()
    turn = run(rec.respond(text))
    agent_ms = (time.perf_counter() - t0) * 1000
    speech = run(synthesize(turn.reply, turn.reply_lang))
    st.session_state.chat.append(
        {"role": "caller", "text": text, "lang": turn.user_lang, "audio": None, "meta": heard_as}
    )
    meta = []
    if turn.emergency:
        meta.append(f"emergency rule: {turn.emergency}")
    meta += turn.guards
    st.session_state.chat.append({"role": "riya", "text": turn.reply, "lang": turn.reply_lang,
                                  "audio": speech.audio or None, "meta": " · ".join(meta)})  # fmt: skip
    st.session_state.timing = {
        "speech to text": stt_ms,
        "receptionist (LLM + tools)": agent_ms,
        "text to speech": speech.latency_ms,
        "llm_calls": turn.llm_calls,
        "model": turn.model,
        "tts": speech.engine,
    }


# ---------------------------------------------------------------- layout
st.title("📞 Call Riya at Sehat Family Clinic")
st.caption(
    "A Hindi and English voice receptionist: speak or type in English, हिंदी or Hinglish. It books, moves and cancels "
    "appointments, answers clinic questions and hands emergencies to people. The clinic is fictional and each visitor "
    f"gets their own private copy of its calendar. [Code and evaluation]({REPO})"
)
if rec.llm is None:
    st.error("No LLM key is configured, so Riya can only answer emergencies. Set GROQ_API_KEY to run the demo.")

left, right = st.columns([3, 2], gap="large")

with left:
    audio = st.audio_input("Tap the mic, speak, then tap again to send", key=f"mic{len(rec.turns)}")
    if audio is not None:
        data = audio.getvalue()
        digest = hashlib.sha1(data).hexdigest()
        if digest != st.session_state.last_audio:
            st.session_state.last_audio = digest
            with st.spinner("Listening…"):
                tr = run(
                    transcribe(
                        data,
                        "call.wav",
                        prompt="Sehat Family Clinic, Dr. Mehta, Dr. Khan, Dr. Iyer, Dr. Singh, appointment",
                    )
                )
            if tr.error or not tr.text:
                st.warning(
                    f"Couldn't make that out ({tr.error or 'no speech detected'}). Please try again or type below."
                )
            else:
                with st.spinner("Riya is answering…"):
                    handle(
                        tr.text,
                        tr.latency_ms,
                        f"heard as {tr.language}" + (", retried as Hindi" if tr.retried_as_hindi else ""),
                    )
                st.rerun()

    st.write("Or try one of these:")
    examples = {
        "🇬🇧 Book a dentist": "Hi, I'd like to book a dentist appointment this week.",
        "🇮🇳 हिंदी": "नमस्ते, कल डॉक्टर मेहता का अपॉइंटमेंट मिल जाएगा क्या?",
        "Hinglish": "bachche ko bukhar hai, kids doctor shaam ko milenge kya?",
        "Cancel (known patient)": "I'm Rahul Sharma, phone 9811122233, I need to cancel my appointment.",
        "Emergency": "Papa ke seene mein bahut dard ho raha hai",
        "Insurance": "kya aap Star Health cashless lete ho?",
    }
    cols = st.columns(3)
    for i, (label, text) in enumerate(examples.items()):
        if cols[i % 3].button(label, width="stretch"):
            with st.spinner("Riya is answering…"):
                handle(text)
            st.rerun()

    for m in reversed(st.session_state.chat):
        with st.chat_message(
            "assistant" if m["role"] == "riya" else "user", avatar="🩺" if m["role"] == "riya" else "🧑"
        ):
            badge = {"en": "English", "hi": "हिंदी", "hinglish": "Hinglish"}.get(m["lang"], m["lang"])
            st.markdown(f"{m['text']}  \n:gray[{badge}{' · ' + m['meta'] if m['meta'] else ''}]")
            if m["audio"]:
                st.audio(m["audio"], format="audio/mpeg", autoplay=m is st.session_state.chat[-1])

    typed = st.chat_input("Type as the caller (English, हिंदी or Hinglish)")
    if typed:
        with st.spinner("Riya is answering…"):
            handle(typed)
        st.rerun()

with right:
    st.subheader("Front desk view")
    if st.button("🔄 New call", width="stretch"):
        new_call()
        st.rerun()
    if rec.emergency_flag:
        st.error(f"Emergency flagged ({rec.emergency_flag}). Staff alerted to call back.")
    elif rec.desk.handoff:
        st.warning(f"Handed to staff: {rec.desk.handoff}")
    if rec.desk.pending:
        st.info(f"Waiting for the caller's yes: {rec.desk.pending.readback_en}")

    t = st.session_state.timing
    if t:
        st.markdown("**Last turn**")
        c1, c2, c3 = st.columns(3)
        c1.metric("Speech to text", f"{t['speech to text']:.0f} ms" if t["speech to text"] else "typed")
        c2.metric(
            "Riya (LLM + tools)",
            f"{t['receptionist (LLM + tools)']:.0f} ms",
            help=f"{t['llm_calls']} LLM calls, {t['model'] or 'rules only'}",
        )
        c3.metric("Text to speech", f"{t['text to speech']:.0f} ms", help=t["tts"])

    st.markdown("**Appointments changed in this call**")
    seen = [b for b in rec.clinic.bookings() if b["created_at"] != ""]
    rows = []
    for b in seen:
        doc = DOCTOR_BY_ID[b["doctor_id"]]
        rows.append({"Ref": b["ref"], "Patient": b["patient_name"], "Phone": mask_phone(b["phone"]).replace("*", "•"),
                     "When": describe_slot(doc, datetime.fromisoformat(b["start"]), "en").split(" with ")[0],
                     "Doctor": doc.name, "Status": b["status"]})  # fmt: skip
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption(
        "Rahul Sharma (phone 9811122233) and Sunita Verma (9876543210) are fictional patients who already have "
        "appointments, so you can try cancelling or moving one."
    )

    if rec.turns and rec.turns[-1].tools:
        with st.expander(f"Tool calls in the last turn ({len(rec.turns[-1].tools)})", expanded=False):
            st.json(rec.turns[-1].tools)

    with st.expander("Doctors and hours"):
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        st.dataframe(
            [{"Doctor": d.name, "For": d.specialty, "Days": ", ".join(names[i] for i in d.days),
              "Hours": f"{d.start.strftime('%H:%M')}–{d.end.strftime('%H:%M')}", "Fee": f"₹{d.fee_inr}"} for d in DOCTORS],
            hide_index=True, width="stretch",
        )  # fmt: skip
    st.caption("Riya never commits a booking until the caller says a plain yes, never gives medical advice, and handles "
               "red-flag symptoms with fixed rules before any AI is involved.")  # fmt: skip
