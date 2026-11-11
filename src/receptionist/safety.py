"""Safety rules that do not depend on the LLM.

1. Emergencies: red-flag symptoms in English, Hindi or Hinglish get a fixed answer (call 112 / 108) and the call is
   flagged for a human, before the LLM sees the message. Over-triggering is accepted: a receptionist who hears
   "chest pain" should always say so.
2. No medical advice: replies that look like dosing or treatment instructions are replaced with a safe deferral.
3. Logs and the dashboard never show full phone numbers.
"""

from __future__ import annotations

import re

from .lang import Lang, reply_lang

EMERGENCY: dict[str, list[str]] = {
    "cardiac": [
        r"chest pain",
        r"pain in (my|the) chest",
        r"heart attack",
        r"seen[ae] (me|mein|mai|main) (\w+ ){0,2}dard",
        r"chh?aa?ti (me|mein|mai|main) (\w+ ){0,2}dard",
        r"dil ka daura",
        r"सीने में (\S+ ){0,2}दर्द",
        r"छाती में (\S+ ){0,2}दर्द",
        r"दिल का दौरा",
        r"हार्ट अटैक",
    ],
    "breathing": [
        r"can'?t breathe",
        r"cannot breathe",
        r"unable to breathe",
        r"difficulty breathing",
        r"short(ness)? of breath",
        r"saa?ns? (nahi|nahin|nhi) (aa|le) ",
        r"saa?ns? (lene )?(me|mein) (dikkat|taklee?f|pareshani)",
        r"saa?ns? (phool|ruk)",
        r"साँस नहीं",
        r"सांस नहीं",
        r"सांस लेने में",
        r"साँस लेने में",
        r"सांस फूल",
        r"साँस फूल",
    ],
    "stroke": [
        r"\bstroke\b",
        r"face (is )?drooping",
        r"slurred speech",
        r"lakwa",
        r"laqwa",
        r"muh tedha",
        r"लकवा",
        r"मुँह टेढ़ा",
        r"मुंह टेढ़ा",
    ],
    "unconscious": [
        r"unconscious",
        r"not responding",
        r"passed out",
        r"fainted",
        r"collapsed",
        r"behosh",
        r"hosh (nahi|nahin)",
        r"बेहोश",
        r"होश नहीं",
    ],
    "bleeding": [
        r"(heavy|severe|lot of|lots of) bleeding",
        r"bleeding (heavily|badly|a lot)",
        r"won'?t stop bleeding",
        r"bleeding (won'?t|will not|doesn'?t) stop",
        r"khoon (nahi|nahin) ruk",
        r"bahut khoon",
        r"खून नहीं रुक",
        r"बहुत खून",
    ],
    "self_harm": [
        r"suicid",
        r"kill myself",
        r"end my life",
        r"want to die",
        r"khudkushi",
        r"aatmahatya",
        r"atmahatya",
        r"jeena nahi chahta",
        r"jeena nahi chahti",
        r"marna chahta",
        r"marna chahti",
        r"आत्महत्या",
        r"ख़ुदकुशी",
        r"खुदकुशी",
        r"जीना नहीं चाहत",
        r"मरना चाहत",
    ],
    "poisoning": [r"poison", r"overdose", r"swallowed (bleach|acid|pills)", r"zeh?a?r (kha|pi)", r"ज़हर", r"जहर"],
    "seizure": [
        r"seizure",
        r"convulsion",
        r"(having|getting|had) (a )?fits?\b",
        r"\bfits? (aa|aaye|pad)",
        r"mirgi",
        r"daura pad",
        r"दौरा पड़",
        r"मिर्गी",
    ],
    "allergy": [r"throat (is )?(swelling|closing)", r"anaphyla", r"gala (soojh|sujh)", r"गला सूज"],
}
_EMERGENCY_RE = {k: [re.compile(p, re.I) for p in v] for k, v in EMERGENCY.items()}

EMERGENCY_REPLY = {
    "en": "This sounds like an emergency. Please call 112 or 108 for an ambulance right now, or go to the nearest "
    "hospital emergency. I'm also alerting our staff to call you back.",
    "hi": "यह इमरजेंसी लग रही है। कृपया अभी 112 या एम्बुलेंस के लिए 108 पर कॉल करें, या सबसे नज़दीकी अस्पताल की इमरजेंसी में जाएँ। "
    "मैं हमारे स्टाफ़ को भी बता रही हूँ ताकि वे आपको कॉल करें।",
}

SELF_HARM_REPLY = {
    "en": "I'm really sorry you're going through this. Please call 112 now if you are in danger, or the Tele-MANAS "
    "helpline on 14416, any time, free. I'm alerting our staff to call you back.",
    "hi": "मुझे बहुत अफ़सोस है कि आप ऐसा महसूस कर रहे हैं। अगर आप ख़तरे में हैं तो अभी 112 पर कॉल करें, या टेली-मानस हेल्पलाइन 14416 पर, "
    "किसी भी समय, मुफ़्त। मैं हमारे स्टाफ़ को बता रही हूँ ताकि वे आपको कॉल करें।",
}


def emergency(text: str) -> str | None:
    t = f" {(text or '').lower()} "
    for kind, pats in _EMERGENCY_RE.items():
        if any(p.search(t) for p in pats):
            return kind
    return None


def emergency_reply(kind: str, caller: Lang) -> str:
    table = SELF_HARM_REPLY if kind == "self_harm" else EMERGENCY_REPLY
    return table[reply_lang(caller)]


# Dosing or treatment instructions: numbers with units, "take X tablets", named OTC drugs with a verb.
_ADVICE = [
    re.compile(p, re.I)
    for p in (
        r"\b\d+(\.\d+)?\s?(mg|ml|mcg|tablets?|capsules?|drops|goli|गोली)\b",
        r"\b(take|have|give)\b[^.]{0,40}\b(paracetamol|ibuprofen|crocin|dolo|antibiotic|aspirin|combiflam|cetirizine|azithromycin)\b",
        r"\b(paracetamol|ibuprofen|crocin|dolo|antibiotic|aspirin|combiflam|cetirizine|azithromycin)\b[^.]{0,30}\b(le lijiye|lijiye|lein|le lo|khaiye|do baar|teen baar)\b",
        r"(पैरासिटामोल|आइबुप्रोफ़ेन|क्रोसिन|डोलो|एंटीबायोटिक)[^।]{0,30}(लीजिए|लें|खाइए|दीजिए)",
        r"\b(twice|three times) a day\b",
    )
]

NO_ADVICE_REPLY = {
    "en": "I'm not able to give medical advice, but I can book you with a doctor today if a slot is free. Shall I check?",
    "hi": "मैं मेडिकल सलाह नहीं दे सकती, लेकिन अगर स्लॉट खाली हो तो आज ही डॉक्टर से अपॉइंटमेंट बुक कर सकती हूँ। क्या मैं देखूँ?",
}


def looks_like_medical_advice(reply: str) -> bool:
    return any(p.search(reply or "") for p in _ADVICE)


def redact_phones(text: str) -> str:
    return re.sub(r"(?<!\d)([6-9]\d)\d{6}(\d{2})(?!\d)", r"\1******\2", text or "")
