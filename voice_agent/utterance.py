"""Lightweight parsing of patient utterances (spoken digits, slot picks)."""

from __future__ import annotations

import re
from typing import Any, Optional

_WORD_DIGITS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}

_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def digits_from_utterance(text: str) -> str:
    """Extract a phone-like digit string from numeric or spoken-digit speech."""
    raw = (text or "").strip().lower()
    if not raw:
        return ""

    numeric = re.sub(r"\D", "", raw)
    if len(numeric) >= 10:
        return numeric

    tokens = re.findall(r"[a-z0-9]+", raw)
    spoken = []
    for tok in tokens:
        if tok.isdigit():
            spoken.append(tok)
        elif tok in _WORD_DIGITS:
            spoken.append(_WORD_DIGITS[tok])
    joined = "".join(spoken)
    return joined if len(joined) >= 10 else ""


def parse_list_index(text: str, *, size: int) -> Optional[int]:
    """
    Resolve '6' / 'number six' / 'the second one' / "let's load number 5"
    to a 0-based index.

    Returns None when the utterance is not a clear list choice.
    """
    if size <= 0:
        return None
    raw = (text or "").strip().lower()
    if not raw:
        return None

    cleaned = re.sub(r"[,.!?]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # "number 5" / "option 5" / "slot 5" anywhere in the sentence
    m = re.search(r"\b(?:number|option|choice|slot)\s*(\d{1,2})\b", cleaned)
    if m:
        n = int(m.group(1))
        if 1 <= n <= size:
            return n - 1

    # "load 5" / "pick 5" / "choose 5" / "take number 5"
    m = re.search(
        r"\b(?:load|pick|choose|select|take|want)\s+(?:number\s*)?(\d{1,2})\b",
        cleaned,
    )
    if m:
        n = int(m.group(1))
        if 1 <= n <= size:
            return n - 1

    # Bare number or "6 please" / STT "6 load"
    m = re.fullmatch(
        r"(?:number|option|choice|slot)?\s*(\d{1,2})\s*(?:please|thanks|thank you|o'?clock|load|ok|okay)?",
        cleaned,
    )
    if m:
        n = int(m.group(1))
        if 1 <= n <= size:
            return n - 1

    words = re.findall(r"[a-z0-9]+", raw)
    if not words:
        return None

    # "six" / "number six"
    for i, word in enumerate(words):
        if word in _WORD_DIGITS and _WORD_DIGITS[word] != "0":
            n = int(_WORD_DIGITS[word])
            # Avoid treating a full spoken phone as a slot index.
            if len(words) <= 6 and 1 <= n <= size:
                if i == 0 or words[i - 1] in {
                    "number",
                    "option",
                    "choice",
                    "slot",
                    "the",
                    "load",
                    "pick",
                    "choose",
                    "select",
                    "take",
                    "want",
                }:
                    return n - 1
                if len(words) == 1:
                    return n - 1

    for word in words:
        if word in _ORDINALS:
            n = _ORDINALS[word]
            if 1 <= n <= size:
                return n - 1

    return None


_YES_WORDS = {
    "yes",
    "yeah",
    "yep",
    "yup",
    "ya",
    "yea",
    "sure",
    "ok",
    "okay",
    "alright",
    "correct",
    "confirm",
    "confirmed",
}

# Common Whisper mishearings of short affirmatives.
_YES_ALIASES = {
    "yse",
    "yess",
    "yass",
    "yas",
    "ye",
    "yaa",
    "yah",
    "aye",
    "yee",
}

_YES_PHRASE = re.compile(
    r"^\s*("
    r"go\s+ahead|please\s+book|book\s+it|book\s+please|"
    r"sounds?\s+good|that\s+works|do\s+it|proceed|"
    r"yes\s+please|yes\s+book(?:\s+it)?|yeah\s+book(?:\s+it)?"
    r")\s*$",
    re.IGNORECASE,
)

_NO_ONLY = re.compile(
    r"^\s*("
    r"no|nope|nah|cancel|stop|abort|don'?t|do\s+not|"
    r"not\s+now|wrong|wait"
    r")\b",
    re.IGNORECASE,
)

_EDIT_CUES = (
    "change my",
    "change the",
    "change it",
    "instead",
    "different",
    "phone number",
    "my phone",
    "another time",
    "hold on",
    "can i change",
    "could i change",
)


def _normalize_confirm_text(text: str) -> str:
    """Strip STT punctuation (Yes? / Yes.) so short confirms still match."""
    raw = (text or "").strip().lower()
    if not raw:
        return ""
    # Keep letters/digits; treat ? ! . , as noise, not meaning.
    raw = re.sub(r"[?!.]+$", "", raw).strip()
    raw = re.sub(r"[^\w\s']", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def is_explicit_yes(text: str) -> bool:
    """True for a clear booking confirmation, including Yes? / Yes."""
    cleaned = _normalize_confirm_text(text)
    if not cleaned:
        return False
    if _NO_ONLY.match(cleaned):
        return False
    if any(phrase in cleaned for phrase in _EDIT_CUES):
        return False
    if cleaned in _YES_WORDS or cleaned in _YES_ALIASES:
        return True
    if _YES_PHRASE.match(cleaned):
        return True
    words = cleaned.split()
    if not words:
        return False
    head = words[0]
    if head in _YES_WORDS or head in _YES_ALIASES:
        blocked = {"no", "not", "dont", "don't", "cancel", "stop", "change", "wait"}
        # Accept "yes" + STT junk ("yes you make them fun") unless negated.
        return not (set(words) & blocked)
    return False


def is_explicit_no(text: str) -> bool:
    cleaned = _normalize_confirm_text(text)
    if not cleaned:
        return False
    return bool(_NO_ONLY.match(cleaned))


def slot_start_from_choice(text: str, offered_slots: list[Any]) -> Optional[str]:
    """Map a patient slot choice to ISO starts_at from the last offered list."""
    if not offered_slots:
        return None
    idx = parse_list_index(text, size=len(offered_slots))
    if idx is None:
        return None
    slot = offered_slots[idx]
    if isinstance(slot, dict):
        return str(slot.get("starts_at") or slot.get("start") or "") or None
    return str(slot) if slot else None


def match_doctor_from_text(text: str, doctors: list) -> Optional[dict]:
    """Match 'Amina' / 'doctor 2' / 'number one' to an offered doctor dict."""
    if not doctors:
        return None
    raw = (text or "").strip().lower()
    if not raw:
        return None

    idx = parse_list_index(raw, size=len(doctors))
    if idx is not None:
        doc = doctors[idx]
        return doc if isinstance(doc, dict) else None

    for doc in doctors:
        if not isinstance(doc, dict):
            continue
        name = str(doc.get("name") or "").lower()
        if not name:
            continue
        # e.g. first name token inside "dr. <name> <surname>"
        tokens = [t for t in re.findall(r"[a-z]+", name) if t not in {"dr", "doctor"}]
        if any(tok and tok in raw for tok in tokens if len(tok) >= 4):
            return doc
        if name in raw or raw in name:
            return doc
    return None


def looks_like_reason(text: str) -> bool:
    raw = (text or "").strip().lower()
    if len(raw) < 4:
        return False
    if "not feeling that" in raw or "not feel that" in raw:
        return False
    cues = (
        "feeling",
        "feel like",
        "pain",
        "hurt",
        "sick",
        "dizzy",
        "dizziness",
        "cough",
        "fever",
        "rash",
        "ache",
        "ill",
        "symptom",
        "because",
        "for a ",
        "i have",
        "i've",
        "headache",
        "nausea",
    )
    return any(c in raw for c in cues)
