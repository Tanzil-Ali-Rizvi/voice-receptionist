"""Speech-to-text evaluation.

1. FLEURS (Google's read-speech benchmark), Hindi and English test sets: word and character error rates for
   whisper-large-v3-turbo vs whisper-large-v3.
2. Receptionist utterances: what matters at a front desk is whether the *details* survive, so clinic sentences in
   English, Hindi and Hinglish (synthesised with the Indian neural voices) are checked for the phone number, doctor
   name and day. These clips are synthetic and cleaner than real phone audio; they test code-mixing and entity
   handling, not noise robustness.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import statistics
import tarfile
import unicodedata
from pathlib import Path
from typing import Any

from .speech import synthesize, transcribe

MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]
PROMPT = "Sehat Family Clinic, Dr. Mehta, Dr. Khan, Dr. Iyer, Dr. Singh, appointment"

# (language style, text to synthesise, entities that must appear in the transcript; '|' separates accepted forms)
UTTERANCES: list[tuple[str, str, list[str]]] = [
    (
        "en",
        "Hi, I'd like to see Doctor Mehta on Thursday morning. My number is 9876501234.",
        ["9876501234", "Mehta", "Thursday"],
    ),
    ("en", "Please book my son with Doctor Khan on Saturday evening.", ["Khan", "Saturday"]),
    (
        "en",
        "Can I move my dentist appointment to Tuesday? My mobile is 9811122233.",
        ["9811122233", "Tuesday", "dentist"],
    ),
    ("en", "This is Neha Arora, I need the skin doctor, Doctor Singh, on Monday.", ["Singh", "Monday", "Neha"]),
    ("en", "What are the fees for Doctor Iyer?", ["Iyer|Ayer|Aiyar", "fee"]),
    ("hi", "नमस्ते, मुझे गुरुवार को डॉक्टर मेहता से मिलना है। मेरा नंबर 9876501234 है।", ["9876501234", "मेहता|Mehta", "गुरुवार"]),
    ("hi", "मेरे बेटे के लिए शनिवार शाम को डॉक्टर ख़ान का अपॉइंटमेंट चाहिए।", ["ख़ान|खान|Khan", "शनिवार"]),
    ("hi", "क्या मंगलवार को दाँतों की डॉक्टर मिलेंगी? मेरा मोबाइल 9811122233 है।", ["9811122233", "मंगलवार"]),
    ("hi", "मेरा नाम सुरेश यादव है, सोमवार को डॉक्टर सिंह का समय मिलेगा?", ["सुरेश", "सोमवार", "सिंह|Singh"]),
    ("hi", "डॉक्टर अय्यर की फ़ीस कितनी है?", ["अय्यर|अयर|Iyer", "फ़ीस|फीस"]),
    (
        "hinglish",
        "मुझे Thursday को Doctor Mehta का appointment चाहिए, मेरा number 9876501234 है।",
        ["9876501234", "Mehta|मेहता", "Thursday|थर्सडे|गुरुवार"],
    ),
    (
        "hinglish",
        "मेरे बेटे को fever है, Saturday evening को Doctor Khan मिलेंगे क्या?",
        ["Khan|ख़ान|खान", "Saturday|सैटरडे|शनिवार"],
    ),
    (
        "hinglish",
        "मेरा dentist वाला appointment Tuesday पर shift कर दीजिए, mobile 9811122233।",
        ["9811122233", "Tuesday|ट्यूज़डे|ट्यूसडे|मंगलवार"],
    ),
    ("hinglish", "Skin doctor Doctor Singh का Monday का slot available है क्या?", ["Singh|सिंह", "Monday|मंडे|सोमवार"]),
    ("hinglish", "Doctor Iyer की consultation fees कितनी है?", ["Iyer|अय्यर|अयर", "fees|फीस|फ़ीस"]),
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.lower())
    text = re.sub(r"[।॥.,!?;:\"'()\-–—]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def digits_only(text: str) -> str:
    # Whisper sometimes writes Devanagari digits or spaces numbers out ("98765 01234").
    text = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    return re.sub(r"\D", "", text)


def entity_hit(transcript: str, entity: str) -> bool:
    alts = entity.split("|")
    if alts[0].isdigit():
        return alts[0] in digits_only(transcript)
    t = unicodedata.normalize("NFC", transcript.lower())
    t2 = t.replace("़", "")
    return any(a.lower() in t or a.lower().replace("़", "") in t2 for a in alts)


def load_fleurs(lang: str, n: int) -> list[tuple[str, bytes, str]]:
    """First n clips of the FLEURS test split, read straight from the dataset repository files."""
    from huggingface_hub import hf_hub_download

    tsv = hf_hub_download("google/fleurs", f"data/{lang}/test.tsv", repo_type="dataset")
    tar = hf_hub_download("google/fleurs", f"data/{lang}/audio/test.tar.gz", repo_type="dataset")
    refs: dict[str, str] = {}
    with open(tsv, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) >= 3:
                refs[row[1]] = row[2]  # file name -> raw transcription
    out = []
    with tarfile.open(tar) as tf:
        for m in tf:
            name = Path(m.name).name
            if m.isfile() and name in refs:
                out.append((name, tf.extractfile(m).read(), refs[name]))  # type: ignore[union-attr]
                if len(out) >= n:
                    break
    return out


async def eval_fleurs(n: int, pause: float) -> dict[str, Any]:
    import jiwer

    results: dict[str, Any] = {}
    for lang in ("hi_in", "en_us"):
        try:
            clips = load_fleurs(lang, n)
        except Exception as e:  # dataset layout or network changed
            results[lang] = {"error": str(e)[:200]}
            continue
        for model in MODELS:
            hyps, refs, lat = [], [], []
            for _, audio, ref in clips:
                t = await transcribe(audio, "clip.wav", model=model, language=lang[:2])
                if t.error:
                    continue
                hyps.append(normalize(t.text))
                refs.append(normalize(ref))
                lat.append(t.latency_ms)
                await asyncio.sleep(pause)
            if refs:
                results.setdefault(lang, {})[model] = {
                    "clips": len(refs),
                    "wer": round(jiwer.wer(refs, hyps), 4),
                    "cer": round(jiwer.cer(refs, hyps), 4),
                    "latency_ms_p50": round(statistics.median(lat)),
                }
    return results


async def eval_utterances(pause: float) -> dict[str, Any]:
    rows = []
    tts = []
    for style, text, ents in UTTERANCES:
        speech = await synthesize(text, "en" if style == "en" else "hi")
        tts.append(speech.latency_ms)
        if not speech.audio:
            rows.append({"style": style, "text": text, "error": speech.error})
            continue
        for model in MODELS:
            t = await transcribe(speech.audio, "utt.mp3", model=model, prompt=PROMPT)
            hits = [entity_hit(t.text, e) for e in ents]
            rows.append({"style": style, "model": model, "text": text, "transcript": t.text, "whisper_language": t.language,
                         "entities": len(ents), "found": sum(hits), "retried_as_hindi": t.retried_as_hindi,
                         "latency_ms": round(t.latency_ms), "tts_engine": speech.engine})  # fmt: skip
            await asyncio.sleep(pause)
    summary: dict[str, Any] = {}
    for model in MODELS:
        for style in ("en", "hi", "hinglish"):
            rs = [r for r in rows if r.get("model") == model and r["style"] == style]
            if rs:
                summary.setdefault(model, {})[style] = {
                    "utterances": len(rs),
                    "entity_accuracy": round(sum(r["found"] for r in rs) / sum(r["entities"] for r in rs), 3),
                    "all_entities_correct": round(sum(r["found"] == r["entities"] for r in rs) / len(rs), 3),
                    "urdu_script_retries": sum(r["retried_as_hindi"] for r in rs),
                    "latency_ms_p50": round(statistics.median(r["latency_ms"] for r in rs)),
                }
    return {"summary": summary, "tts_latency_ms_p50": round(statistics.median(tts)) if tts else None, "rows": rows}


def to_md(res: dict[str, Any]) -> str:
    lines = ["# Speech-to-text evaluation", ""]
    f = res.get("fleurs", {})
    if f:
        lines += [f"## FLEURS test set (first {res['fleurs_n']} clips per language)", "",
                  "| Language | Model | WER | CER | p50 latency |", "|---|---|---|---|---|"]  # fmt: skip
        for lang, models in f.items():
            if "error" in models:
                lines.append(f"| {lang} | unavailable | | | {models['error'][:60]} |")
                continue
            for m, v in models.items():
                lines.append(f"| {lang} | {m} | {v['wer']:.1%} | {v['cer']:.1%} | {v['latency_ms_p50']} ms |")
        lines.append("")
    u = res.get("utterances", {}).get("summary", {})
    if u:
        lines += ["## Clinic details in synthetic utterances", "",
                  "Phone numbers, doctor names and days in clinic sentences, synthesised with Indian neural voices.", "",
                  "| Model | Style | Utterances | Details correct | Sentences fully correct | Urdu-script retries | p50 latency |",
                  "|---|---|---|---|---|---|---|"]  # fmt: skip
        for m, styles in u.items():
            for s, v in styles.items():
                lines.append(f"| {m} | {s} | {v['utterances']} | {v['entity_accuracy']:.0%} | {v['all_entities_correct']:.0%} | "
                             f"{v['urdu_script_retries']} | {v['latency_ms_p50']} ms |")  # fmt: skip
        lines += ["", f"Text-to-speech p50 latency: {res['utterances'].get('tts_latency_ms_p50')} ms."]
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    res: dict[str, Any] = {"fleurs_n": args.fleurs_n}
    res["utterances"] = await eval_utterances(args.pause)
    if args.fleurs_n > 0:
        res["fleurs"] = await eval_fleurs(args.fleurs_n, args.pause)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stt.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    (out / "stt.md").write_text(to_md(res))
    print(to_md(res))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results")
    ap.add_argument("--fleurs-n", type=int, default=100)
    ap.add_argument("--pause", type=float, default=2.2)  # Groq free tier: 20 audio requests per minute
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
