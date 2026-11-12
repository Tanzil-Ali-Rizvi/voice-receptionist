"""Which language is the caller speaking, and how should the receptionist answer?

Three caller styles are common at an Indian front desk:
* English;
* Hindi in Devanagari (what speech recognition usually returns for Hindi speech);
* Hinglish: Hindi words in Latin letters mixed with English ("kal subah appointment mil jayega?").

Hindi and Hinglish callers get answers in Hindi (Devanagari, keeping everyday English words such as "appointment"),
which the Hindi voice reads naturally. English callers get English.
"""

from __future__ import annotations

import re
from typing import Literal

Lang = Literal["en", "hi", "hinglish"]

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")

# Common Hindi words in Latin script. Kept to words that are rarely English, so "main", "do", "the" don't count.
HINGLISH_MARKERS = {
    "hai", "hain", "haan", "han", "nahi", "nahin", "kya", "kyu", "kyun", "kab", "kaise", "kahan", "mujhe", "mujhko",
    "humko", "hume", "hamein", "aap", "aapka", "aapke", "apna", "apni", "mera", "meri", "mere", "tha", "thi",
    "hoga", "hogi", "chahiye", "chahte", "chahta", "chahti", "karna", "karni", "karwana", "karwani", "kar", "kardo",
    "kijiye", "dijiye", "batao", "bataiye", "bata", "milega", "milegi", "mil", "jayega", "jaayega", "sakta",
    "sakti", "sakte", "kal", "parso", "aaj", "subah", "shaam", "sham", "dopahar", "raat", "baje", "bajey", "wala",
    "wali", "waala", "liye", "lie", "ke", "ki", "ka", "ko", "se", "mein", "pe", "par", "aur", "bhi", "abhi",
    "theek", "thik", "accha", "achha", "ji", "beta", "bacche", "bachche", "dard", "bukhar", "khansi", "dawai",
    "doctor sahab", "kitne", "kitna", "kitni", "paisa", "paise", "rupaye", "naam", "number", "hum", "main",
}  # fmt: skip
_AMBIGUOUS = {"main", "number", "ke", "ki", "ka", "ko", "se", "pe", "par", "mil", "kar", "hum", "bata", "han"}

AFFIRM = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "correct", "right", "go ahead",
    "please do", "book it", "do it", "that's fine", "fine", "perfect", "haan", "han", "haa", "ha", "hanji", "haanji",
    "ji", "ji haan", "jee", "theek hai", "thik hai", "theek", "thik", "sahi hai", "kar do", "kardo", "kar dijiye",
    "kijiye", "book kar do", "book kar dijiye", "chalega", "bilkul", "zaroor", "accha", "achha",
    "हाँ", "हां", "हा", "जी", "जी हाँ", "जी हां", "हाँ जी", "हां जी", "ठीक है", "ठीक", "सही है", "कर दो", "कर दीजिए",
    "कीजिए", "बुक कर दो", "बुक कर दीजिए", "चलेगा", "बिल्कुल", "ज़रूर", "जरूर", "अच्छा", "ओके", "कन्फ़र्म", "कन्फर्म",
}  # fmt: skip
NEGATE = {
    "no", "nope", "don't", "dont", "do not", "wait", "cancel that", "not", "wrong", "change", "nahi", "nahin", "mat",
    "ruko", "ruk", "galat", "badal", "नहीं", "नही", "मत", "रुको", "रुकिए", "ग़लत", "गलत", "बदल",
}  # fmt: skip


CHANGE = {
    "but", "instead", "actually", "another", "other", "different", "earlier", "later", "lekin", "magar", "par",
    "balki", "dusra", "doosra", "pehle", "baad", "baje", "subah", "shaam", "kal", "parso",
    "लेकिन", "मगर", "पर", "दूसरा", "पहले", "बाद", "बजे", "सुबह", "शाम", "कल", "परसों",
}  # fmt: skip


ENGLISH_WORDS = {
    "i", "i'm", "my", "me", "the", "a", "an", "is", "are", "am", "to", "for", "can", "could", "please", "want", "need",
    "would", "like", "what", "when", "where", "how", "do", "does", "have", "has", "with", "on", "at", "and", "it",
    "this", "that", "you", "your", "there", "any", "of", "in", "will", "be", "book", "cancel", "move", "thanks",
    "thank", "yes", "no", "sure", "okay", "ok", "hello", "hi",
}  # fmt: skip


def english_evidence(text: str) -> int:
    return sum(1 for w in words(text) if w in ENGLISH_WORDS)


def words(text: str) -> list[str]:
    # Devanagari letters and vowel signs, but not the danda (।, ॥), which ends a Hindi sentence like a full stop.
    return re.findall(r"[\w\u0900-\u0963\u0966-\u097f']+", (text or "").lower())


def detect(text: str) -> Lang:
    text = text or ""
    letters = len(DEVANAGARI.findall(text)) + len(LATIN.findall(text)) + len(ARABIC.findall(text))
    if letters == 0:
        return "en"
    if (len(DEVANAGARI.findall(text)) + len(ARABIC.findall(text))) / letters > 0.3:
        return "hi"
    ws = words(text)
    strong = sum(1 for w in ws if w in HINGLISH_MARKERS and w not in _AMBIGUOUS)
    weak = sum(1 for w in ws if w in _AMBIGUOUS)
    if strong >= 2 or (strong >= 1 and weak >= 1) or (ws and strong / len(ws) >= 0.25):
        return "hinglish"
    return "en"


def reply_lang(caller: Lang) -> Literal["en", "hi"]:
    return "en" if caller == "en" else "hi"


def script_of(text: str) -> Literal["devanagari", "latin", "mixed", "none"]:
    d, latin = len(DEVANAGARI.findall(text or "")), len(LATIN.findall(text or ""))
    if d + latin == 0:
        return "none"
    if d / (d + latin) >= 0.7:
        return "devanagari"
    if latin / (d + latin) >= 0.9:
        return "latin"
    return "mixed"


def reply_matches(caller: Lang, reply: str) -> bool:
    """Is the reply in the language we promised? Devanagari replies may keep some English words and numbers."""
    s = script_of(reply)
    return s in ("latin", "none") if reply_lang(caller) == "en" else s in ("devanagari", "mixed", "none")


def confirmation(text: str) -> Literal["yes", "no", "unclear"]:
    """Deterministic yes/no for a pending booking. Only short, plain answers count as consent."""
    t = " ".join(words(text))
    ws = t.split()
    has = lambda vocab: any(re.search(rf"(^|\s){re.escape(v)}($|\s)", t) for v in vocab)  # noqa: E731
    if has(NEGATE):
        return "no"
    # "haan but make it 5 pm" / "ok, lekin kal" is a change request, not consent.
    if re.search(r"\d", t) or has(CHANGE):
        return "unclear"
    if len(ws) <= 7 and has(AFFIRM):
        return "yes"
    return "unclear"
