"""
Reject Whisper noise hallucinations so they never become patient turns.

False transcripts on silence / room noise often look like: "What?", "Huh?",
spell-outs like "T-O-R-I", YouTube outros. Real answers like "yes" / "no" /
names / phone numbers must still pass (needed for booking).
"""

from __future__ import annotations

import re

_SPACED_LETTERS = re.compile(
    r"^(?:come\s+on[,!]?\s+)?(?:[A-Za-z][\s\-\.]+){2,}[A-Za-z][.!?]*$",
    re.IGNORECASE,
)
_ONLY_PUNCT = re.compile(r"^[\W_]+$")

# Alone, these are almost never intentional patient answers in this clinic flow.
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
}


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

    key = norm.rstrip(" .!?,;:")
    if key in _BLOCKLIST:
        return True

    # "Come on, T-O-R-I!" / letter-by-letter garbage
    if _SPACED_LETTERS.match(raw.replace(",", " ")):
        return True
    if re.search(r"\b[A-Za-z](?:[\s\-][A-Za-z]){2,}\b", raw) and len(raw) < 40:
        letters = re.findall(r"\b[A-Za-z]\b", raw)
        if len(letters) >= 3 and len(letters) >= max(1, len(raw.split()) - 2):
            return True

    words = [w for w in re.findall(r"[a-z0-9]+", key) if w]
    if not words:
        return True
    # Lone 1–2 letter grunt — but allow yes/no/ok (needed for booking).
    if len(words) == 1 and len(words[0]) <= 2 and words[0] not in {"no", "ok", "ye"}:
        return True

    return False
