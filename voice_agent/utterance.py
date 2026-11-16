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
    Resolve '6' / 'number six' / 'the second one' to a 0-based index.

    Returns None when the utterance is not a clear list choice.
    """
    if size <= 0:
        return None
    raw = (text or "").strip().lower()
    if not raw:
        return None

    # Bare number or "number 6" / "option 6" / "6 please" / STT "6, load"
    cleaned = re.sub(r"[,.!?]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
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
            if len(words) <= 4 and 1 <= n <= size:
                if i == 0 or words[i - 1] in {"number", "option", "choice", "slot", "the"}:
                    return n - 1
                if len(words) == 1:
                    return n - 1

    for word in words:
        if word in _ORDINALS:
            n = _ORDINALS[word]
            if 1 <= n <= size:
                return n - 1

    return None


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
        # "amina" in "dr. amina khan"
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
