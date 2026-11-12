"""Resolve the day words a caller uses into real dates, in code, so the LLM doesn't have to guess.

"kal" means both yesterday and tomorrow in Hindi; at a booking desk it means tomorrow. Weekday names in English,
Devanagari and Latin-script Hindi resolve to the next such day (today counts if it is that day). The resolved dates
are passed to the model as a note, e.g. "kal = tomorrow = Tuesday 2026-11-17".
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

RELATIVE = {
    0: [r"\btoday\b", r"\baaj\b", r"\baj\b", r"आज"],
    1: [r"\btomorrow\b", r"\bkal\b", r"\bkl\b", r"कल"],
    2: [r"day after tomorrow", r"\bparso\b", r"\bparson\b", r"\bparsoon\b", r"परसों", r"परसो"],
}
WEEKDAYS = [
    [r"\bmonday\b", r"\bsomvar\b", r"\bsomwar\b", r"सोमवार", r"मंडे"],
    [r"\btuesday\b", r"\bmangalvar\b", r"\bmangalwar\b", r"मंगलवार", r"ट्यूज़डे", r"ट्यूसडे"],
    [r"\bwednesday\b", r"\bbudhvar\b", r"\bbudhwar\b", r"बुधवार", r"वेडनेसडे"],
    [r"\bthursday\b", r"\bguruvar\b", r"\bguruwar\b", r"\bbrihaspativar\b", r"गुरुवार", r"बृहस्पतिवार", r"थर्सडे"],
    [r"\bfriday\b", r"\bshukravar\b", r"\bshukrawar\b", r"शुक्रवार", r"फ्राइडे"],
    [r"\bsaturday\b", r"\bshanivar\b", r"\bshaniwar\b", r"शनिवार", r"सैटरडे"],
    [r"\bsunday\b", r"\bravivar\b", r"\braviwar\b", r"\bitvaar\b", r"रविवार", r"इतवार", r"संडे"],
]
NEXT_WEEK = [r"next week", r"agle hafte", r"agle week", r"अगले हफ़्ते", r"अगले हफ्ते"]
_DEV = "\u0900-\u097f"


def _pat(p: str) -> str:
    """Devanagari has no reliable \\b: require that no Devanagari letter touches the word (so कल doesn't match कलम)."""
    return rf"(?<![{_DEV}]){p}(?![{_DEV}])" if re_dev.search(p) else p


re_dev = re.compile(f"[{_DEV}]")


def _has(text: str, pats: list[str]) -> str | None:
    for p in pats:
        m = re.search(_pat(p), text, re.I)
        if m:
            return m.group(0)
    return None


def resolve_days(text: str, now: datetime) -> list[tuple[str, date]]:
    """[(the caller's word, the date it means)] for every day word found."""
    t = (text or "").lower()
    today = now.date()
    found: list[tuple[str, date]] = []
    for offset in (2, 0, 1):  # "day after tomorrow" / parso first, then remove it so "tomorrow" isn't counted twice
        for p in RELATIVE[offset]:
            m = re.search(_pat(p), t, re.I)
            if m:
                found.append((m.group(0), today + timedelta(days=offset)))
                t = t[: m.start()] + " " + t[m.end() :]
                break
    nxt = bool(_has(t, NEXT_WEEK))
    for wd, pats in enumerate(WEEKDAYS):
        w = _has(t, pats)
        if w:
            ahead = (wd - today.weekday()) % 7
            if nxt:
                ahead += 7
            found.append((w, today + timedelta(days=ahead)))
    return found


def date_note(text: str, now: datetime) -> str:
    days = resolve_days(text, now)
    if not days:
        return ""
    parts = []
    for word, d in days:
        rel = {0: "today", 1: "tomorrow", 2: "the day after tomorrow"}.get((d - now.date()).days, "")
        parts.append(f"'{word}' = {rel + ' = ' if rel else ''}{d.strftime('%A %Y-%m-%d')}")
    return ("DATES IN THE CALLER'S WORDS (resolved by code, use these): " + "; ".join(parts) + ". If the doctor does not "
            "work that day, say so plainly and offer the nearest day they do.")  # fmt: skip
