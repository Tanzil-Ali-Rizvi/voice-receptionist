"""Speech in and out.

Speech-to-text: Groq Whisper (`whisper-large-v3-turbo` by default). Whisper sometimes writes Hindi speech in Urdu
script; when that happens the clip is transcribed again with the language pinned to Hindi.

Text-to-speech: Microsoft neural voices through edge-tts (Hindi `hi-IN-SwaraNeural`, Indian English
`en-IN-NeerjaNeural`), with Google TTS as a fallback. Groq's own TTS voices are English and Arabic only.
"""

from __future__ import annotations

import io
import os
import re
import time
from dataclasses import dataclass

import httpx

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
ARABIC = re.compile(r"[؀-ۿ]")
VOICES = {"hi": "hi-IN-SwaraNeural", "en": "en-IN-NeerjaNeural"}


@dataclass
class Transcript:
    text: str
    language: str  # as reported by Whisper, e.g. "hindi", "english"
    duration_s: float
    latency_ms: float
    model: str
    retried_as_hindi: bool = False
    error: str = ""


async def transcribe(audio: bytes, filename: str = "audio.wav", model: str = "whisper-large-v3-turbo",
                     language: str | None = None, prompt: str | None = None) -> Transcript:  # fmt: skip
    key = os.getenv("GROQ_API_KEY", "")
    t0 = time.perf_counter()
    if not key:
        return Transcript("", "", 0.0, 0.0, model, error="GROQ_API_KEY is not set")
    data = {"model": model, "response_format": "verbose_json", "temperature": "0"}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt  # vocabulary hint: clinic and doctor names
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(GROQ_STT_URL, headers={"Authorization": f"Bearer {key}"}, data=data,
                             files={"file": (filename, audio)})  # fmt: skip
        r.raise_for_status()
    except httpx.HTTPError as e:
        return Transcript("", "", 0.0, (time.perf_counter() - t0) * 1000, model, error=str(e)[:200])
    j = r.json()
    out = Transcript(
        (j.get("text") or "").strip(), j.get("language") or "", float(j.get("duration") or 0.0),
        (time.perf_counter() - t0) * 1000, model,
    )  # fmt: skip
    if not language and ARABIC.search(out.text):
        again = await transcribe(audio, filename, model, language="hi", prompt=prompt)
        if not again.error:
            again.retried_as_hindi = True
            again.latency_ms += out.latency_ms
            return again
    return out


@dataclass
class Speech:
    audio: bytes
    mime: str
    engine: str
    latency_ms: float
    error: str = ""


async def synthesize(text: str, lang: str) -> Speech:
    t0 = time.perf_counter()
    try:
        import edge_tts

        buf = io.BytesIO()
        async for chunk in edge_tts.Communicate(text, VOICES.get(lang, VOICES["en"]), rate="+5%").stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        if buf.tell():
            return Speech(buf.getvalue(), "audio/mpeg", "edge-tts", (time.perf_counter() - t0) * 1000)
    except Exception as e:  # network or service change: fall back
        err = str(e)[:120]
    else:
        err = "edge-tts returned no audio"
    try:
        from gtts import gTTS

        buf = io.BytesIO()
        gTTS(text, lang="hi" if lang == "hi" else "en", tld="co.in").write_to_fp(buf)
        return Speech(buf.getvalue(), "audio/mpeg", "gTTS", (time.perf_counter() - t0) * 1000, err)
    except Exception as e:
        return Speech(b"", "", "none", (time.perf_counter() - t0) * 1000, f"{err}; gTTS: {str(e)[:120]}")
