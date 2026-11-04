"""Patient-friendly formatting for clinic desk replies."""

from datetime import datetime

from django.conf import settings
from django.utils.dateparse import parse_datetime

from booking.calendar import as_clinic_time, clinic_zone


def format_slot_label(value):
    """
    Turn an ISO datetime into a patient-readable label.

    Example: Tuesday, 10 November 2026 at 09:00 AM
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        moment = as_clinic_time(value)
    else:
        parsed = parse_datetime(str(value))
        if parsed is None:
            return str(value)
        moment = as_clinic_time(parsed)

    hour_12 = moment.hour % 12 or 12
    meridiem = "AM" if moment.hour < 12 else "PM"
    return "{weekday}, {day} {month} {year} at {hour:02d}:{minute:02d} {meridiem}".format(
        weekday=moment.strftime("%A"),
        day=moment.day,
        month=moment.strftime("%B"),
        year=moment.year,
        hour=hour_12,
        minute=moment.minute,
        meridiem=meridiem,
    )


def format_clock(value):
    """Format a time or datetime as 01:00 AM style."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        moment = as_clinic_time(value)
        hour = moment.hour
        minute = moment.minute
    else:
        hour = value.hour
        minute = value.minute
    hour_12 = hour % 12 or 12
    meridiem = "AM" if hour < 12 else "PM"
    return "{:02d}:{:02d} {}".format(hour_12, minute, meridiem)


def numbered_list(items, *, intro, outro=None):
    """Build a multi-line reply with a numbered list for speech + screen."""
    lines = [intro.rstrip(), ""]
    for index, item in enumerate(items, start=1):
        lines.append("{}. {}".format(index, item))
    if outro:
        lines.extend(["", outro.rstrip()])
    return "\n".join(lines)


def doctors_list_message(specialty_name, doctors):
    labels = [doctor["name"] for doctor in doctors]
    return numbered_list(
        labels,
        intro="For {}, these doctors are available:".format(specialty_name),
        outro="Please say the option number or the doctor’s name.",
    )


def slots_list_message(slots, *, intro, outro=None):
    labels = []
    for slot in slots:
        starts = slot["starts_at"] if isinstance(slot, dict) else slot
        labels.append(format_slot_label(starts))
    if outro is None:
        outro = "Please say the option number you want. Times are in {}.".format(
            getattr(settings, "CLINIC_TIMEZONE", str(clinic_zone()))
        )
    return numbered_list(labels, intro=intro, outro=outro)
