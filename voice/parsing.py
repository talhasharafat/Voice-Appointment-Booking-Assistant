"""Lightweight free parsers for speech/text turns (no paid NLP APIs)."""

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone


WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

YES_WORDS = {"yes", "yeah", "yep", "confirm", "confirmed", "ok", "okay", "sure", "book", "please"}
NO_WORDS = {"no", "nope", "cancel", "stop", "abort", "wrong"}


def clinic_zone():
    return ZoneInfo(settings.CLINIC_TIMEZONE)


def normalize_text(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_unclear(text):
    cleaned = normalize_text(text)
    return not cleaned or cleaned in {"um", "uh", "hmm", "huh", "what", "sorry", "repeat"}


def is_yes(text):
    tokens = set(re.findall(r"[a-z]+", normalize_text(text)))
    return bool(tokens & YES_WORDS) and not bool(tokens & NO_WORDS)


def is_no(text):
    tokens = set(re.findall(r"[a-z]+", normalize_text(text)))
    return bool(tokens & NO_WORDS)


def extract_phone(text):
    digits = re.sub(r"\D", "", text or "")
    if len(digits) >= 10:
        return digits[-11:] if len(digits) > 11 else digits
    spoken = normalize_text(text)
    # "zero three zero zero one two ..." is too heavy; require digit-like input.
    if re.fullmatch(r"[\d\s\-+()]+", (text or "").strip()) and len(digits) >= 7:
        return digits
    if " " in spoken and all(part.isdigit() for part in spoken.split()):
        joined = "".join(spoken.split())
        if len(joined) >= 10:
            return joined
    return None


def extract_name(text):
    cleaned = (text or "").strip()
    cleaned = re.sub(
        r"^(my name is|i am|i'm|this is)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" .,!")
    if len(cleaned) < 2 or is_unclear(cleaned):
        return None
    if extract_phone(cleaned) and re.fullmatch(r"[\d\s\-+()]+", cleaned):
        return None
    return cleaned[:150]


def parse_time_of_day(text):
    cleaned = normalize_text(text)
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b",
        cleaned,
    )
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "")
    if meridiem.startswith("p") and hour < 12:
        hour += 12
    if meridiem.startswith("a") and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_date_in_text(text, now=None):
    cleaned = normalize_text(text)
    now = now or timezone.now().astimezone(clinic_zone())
    today = now.date()

    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", cleaned)
    if iso:
        try:
            return datetime(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), tzinfo=clinic_zone()).date()
        except ValueError:
            return None

    if "today" in cleaned:
        return today
    if "tomorrow" in cleaned:
        return today + timedelta(days=1)

    for name, weekday in WEEKDAY_NAMES.items():
        if name in cleaned:
            days_ahead = (weekday - today.weekday()) % 7
            if days_ahead == 0 and now.timetz() > time(18, 0, tzinfo=clinic_zone()):
                days_ahead = 7
            if "next" in cleaned and days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead)
    return None


def parse_preferred_datetime(text, now=None):
    now = now or timezone.now().astimezone(clinic_zone())
    day = parse_date_in_text(text, now=now)
    clock = parse_time_of_day(text)
    if day is None or clock is None:
        return None
    return datetime.combine(day, clock, tzinfo=clinic_zone())


def datetime_missing_parts(text, now=None):
    """Return which pieces are missing so the assistant can clarify, not guess."""
    now = now or timezone.now().astimezone(clinic_zone())
    day = parse_date_in_text(text, now=now)
    clock = parse_time_of_day(text)
    missing = []
    if day is None:
        missing.append("date")
    if clock is None:
        missing.append("time")
    return missing


def looks_like_diagnosis_request(text):
    cleaned = normalize_text(text)
    triggers = ("diagnose", "diagnosis", "what medicine", "prescribe", "prescription", "is it cancer")
    return any(trigger in cleaned for trigger in triggers)
