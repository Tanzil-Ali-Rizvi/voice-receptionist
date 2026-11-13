"""Compare names written in Latin and Devanagari script ("Sunita Verma" vs "सुनिता वर्मा").

Hindi speech recognition returns names in Devanagari, while a booking may have been made in English. Both are reduced
to a consonant skeleton (vowels and doubled letters dropped, v/w and similar sounds merged), which is enough to match
a first name against a booking that is also tied to the caller's phone number.
"""

from __future__ import annotations

import re
import unicodedata

_DEV = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n", "च": "c", "छ": "c", "ज": "j", "झ": "j", "ञ": "n",
    "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n", "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "b", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "s",
    "ष": "s", "स": "s", "ह": "h", "क़": "k", "ख़": "k", "ग़": "g", "ज़": "j", "ड़": "r", "ढ़": "r", "फ़": "f",
    "य़": "y", "ळ": "l",
}  # fmt: skip
_DEV["ं"] = "n"  # anusvara: सिंह = Singh
_LATIN = [("ph", "f"), ("sh", "s"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"), ("bh", "b"),
          ("ng", "n"), ("w", "v"), ("z", "j"), ("q", "k"), ("x", "ks")]  # fmt: skip


def skeleton(word: str) -> str:
    """Consonants only, with h and y dropped (they come and go in transliteration) and doubles collapsed. A leading
    vowel is kept as "_", so Anaya ("_n") and Neha ("n") stay apart."""
    w = unicodedata.normalize("NFC", (word or "").lower())
    lead = "_" if re.match(r"[aeiou\u0904-\u0914]", w) else ""
    if re.search(r"[\u0900-\u097f]", w):
        out = "".join(_DEV.get(ch, "") for ch in unicodedata.normalize("NFD", w))  # NFD drops the nukta of क़
        out = out.replace("ng", "n")
    else:
        out = re.sub(r"[^a-z]", "", w)
        for x, y in _LATIN:
            out = out.replace(x, y)
        out = re.sub(r"[aeiou]", "", out)
    out = re.sub(r"[hy]", "", out)
    return lead + re.sub(r"(.)\1+", r"\1", out)


def same_full_name(a: str, b: str) -> bool:
    wa, wb = (a or "").split(), (b or "").split()
    return bool(wa) and len(wa) == len(wb) and all(skeleton(x) == skeleton(y) for x, y in zip(wa, wb, strict=True))


def same_name(a: str, b: str) -> bool:
    """First names match in either script (the phone number has matched already). Very short first names such as
    "Neha" (skeleton "n") must match in full, so they can't collide with "Anaya"."""
    wa, wb = (a or "").split(), (b or "").split()
    if not (wa and wb):
        return False
    ka, kb = skeleton(wa[0]), skeleton(wb[0])
    if len(ka.strip("_")) < 2 or len(kb.strip("_")) < 2:
        return same_full_name(a, b) or (len(wa) == 1 and ka == kb != "")
    return ka == kb
