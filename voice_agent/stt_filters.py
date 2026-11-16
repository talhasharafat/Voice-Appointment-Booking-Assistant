"""
Reject Whisper noise / echo hallucinations so they never become patient turns.
"""

from __future__ import annotations

import re

_SPACED_LETTERS = re.compile(
    r"^(?:come\s+on[,!]?\s+)?(?:[A-Za-z][\s\-\.]+){2,}[A-Za-z][.!?]*$",
    re.IGNORECASE,
)
_ONLY_PUNCT = re.compile(r"^[\W_]+$")
_REPEATED_CLAUSE = re.compile(r"(?i)^(.{12,}?)(?:\s*\1){1,}$")

_BLOCKLIST = {
    "what",
    "who",
    "why",
    "huh",
    "eh",
    "ah",
    "uh",
    "um",
    "hmm",
    "hm",
    "oh",
    "oh ooh",
    "ooh",
    "hey",
    "hi",
    "hello",
    "bye",
    "come on",
    "come on!",
    "thanks for watching",
    "subscribe",
    "subtitle",
    "subtitles",
    "music",
    "applause",
    "mbc",
    "www",
    "the",
    "a",
    "i",
    "me",
    "you",
    ". .",
    "...",
    "it",
    "it...",
    "good",
    "so",
    "okay",
    "ok",
    "right",
    "yeah",
    "yep",
}

# Exact booking confirmations must still pass — remove ok/yeah from block when alone
# only if needed. "yes"/"no" are intentionally NOT blocked.
_BLOCKLIST.discard("ok")
_BLOCKLIST.discard("okay")
_BLOCKLIST.discard("yeah")

_JUNK_PHRASES = (
    "ignore background noise",
    "do not invent speech",
    "don't invent speech",
    "clinic appointment booking conversation",
    "do not invent",
    "invent speech",
    "background noise",
    "thanks for watching",
    "how are you going to see the future",
    "i'm going to go to the next step",
    "im going to go to the next step",
    "i'm going to go to the next one",
    "im going to go to the next one",
    "no, you're not going to be",
    "no you're not going to be",
    "so i'm going to go ahead",
    "get a little bit of a",
    "are you talking about the same thing",
    "do you have a good food",
    "i'm gonna say that you go to me again",
    "pats in media",
    "ferdas",
    "flavio",
    "pagaligial",
    "hatsky",
    "i have a soul truth",
    "i'll find you with our email",
    "my phone, get money",
    "keep on the phone number",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_hallucinated_transcript(text: str) -> bool:
    """True when this STT text should be ignored (not treated as patient speech)."""
    raw = (text or "").strip()
    if not raw:
        return True

    norm = _normalize(raw)
    if not norm or _ONLY_PUNCT.match(norm):
        return True

    key = re.sub(r"[^\w\s]", " ", norm)
    key = re.sub(r"\s+", " ", key).strip()
    if key in _BLOCKLIST:
        return True
    # Tiny filler / gasp fragments
    if len(key.split()) <= 3 and all(w in {"oh", "ooh", "ah", "uh", "um", "hmm", "hm", "eh"} for w in key.split()):
        return True

    for phrase in _JUNK_PHRASES:
        if phrase in norm:
            return True

    if _REPEATED_CLAUSE.match(norm):
        return True
    sentences = [s.strip() for s in re.split(r"[.!?]+", norm) if s.strip()]
    if len(sentences) >= 2 and len(set(sentences)) == 1:
        return True

    if _SPACED_LETTERS.match(raw.replace(",", " ")):
        return True
    if re.search(r"\b[A-Za-z](?:[\s\-][A-Za-z]){2,}\b", raw) and len(raw) < 40:
        letters = re.findall(r"\b[A-Za-z]\b", raw)
        if len(letters) >= 3 and len(letters) >= max(1, len(raw.split()) - 2):
            return True

    words = [w for w in re.findall(r"[a-z0-9]+", key) if w]
    if not words:
        return True
    if len(words) == 1 and len(words[0]) <= 2 and words[0] not in {"no", "ok", "ye"}:
        return True

    # Digits / phone / slot numbers should pass.
    if any(ch.isdigit() for ch in key):
        return False

    # Booking-ish short answers should pass.
    booking_words = {
        "yes",
        "no",
        "yeah",
        "yep",
        "ok",
        "okay",
        "tomorrow",
        "today",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "amina",
        "bilal",
        "sara",
        "dizzy",
        "cough",
        "fever",
        "rash",
        "pain",
        "doctor",
    }
    if any(w in booking_words for w in words):
        return False
    if re.search(r"(?i)\b(dr|doctor|my name is|phone|feeling|appointment)\b", norm):
        return False
    if re.fullmatch(r"[a-z][a-z'.-]+(?:\s+[a-z][a-z'.-]+){0,3}", key):
        # Likely a person name.
        return False

    # Long unrelated chatter with no booking cues → drop.
    if len(words) >= 8 and not any(
        w in {
            "name",
            "phone",
            "doctor",
            "appointment",
            "book",
            "booking",
            "time",
            "slot",
            "tomorrow",
            "today",
            "feeling",
            "pain",
            "cough",
            "fever",
            "dizzy",
            "rash",
            "yes",
            "no",
        }
        for w in words
    ):
        return True

    if not any(ch.isdigit() for ch in key):
        weird = 0
        for w in words:
            if len(w) >= 6 and not re.search(r"[aeiou]", w):
                weird += 1
            elif len(w) >= 8 and len(set(w)) <= 3:
                weird += 1
        if weird and weird >= max(1, len(words) // 2):
            return True

    return False
