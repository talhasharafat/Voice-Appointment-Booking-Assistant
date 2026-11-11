"""
Clinic calendar: fixed-length slots in one timezone.

Availability is derived from working hours, unavailability, and confirmed
appointments. The AI layer must call this logic rather than inventing slots.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from booking.models import Appointment
from clinic.models import Doctor, Unavailability


@dataclass(frozen=True)
class Slot:
    starts_at: datetime
    ends_at: datetime

    def as_dict(self):
        return {
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
        }


def clinic_zone():
    return ZoneInfo(settings.CLINIC_TIMEZONE)


def slot_duration():
    return timedelta(minutes=int(settings.APPOINTMENT_DURATION_MINUTES))


def as_clinic_time(value):
    if timezone.is_naive(value):
        return value.replace(tzinfo=clinic_zone())
    return value.astimezone(clinic_zone())


def slot_from_start(starts_at):
    start = as_clinic_time(starts_at)
    return Slot(starts_at=start, ends_at=start + slot_duration())


def working_slot_starts(doctor, day):
    """Every on-grid start time for a doctor on a local clinic date."""
    length = slot_duration()
    zone = clinic_zone()
    starts = []
    blocks = doctor.working_hours.filter(weekday=day.weekday()).order_by("start_time")
    for block in blocks:
        cursor = datetime.combine(day, block.start_time, tzinfo=zone)
        close = datetime.combine(day, block.end_time, tzinfo=zone)
        while cursor + length <= close:
            starts.append(cursor)
            cursor += length
    return starts


def _overlaps(start, end, other_start, other_end):
    return start < other_end and end > other_start


def blocking_intervals(doctor, window_start, window_end):
    """Confirmed bookings and unavailability that overlap the window."""
    intervals = []
    appointments = Appointment.objects.filter(
        doctor=doctor,
        status__in=[Appointment.Status.CONFIRMED, Appointment.Status.PENDING],
        starts_at__lt=window_end,
        ends_at__gt=window_start,
    )
    for appointment in appointments:
        intervals.append(
            (as_clinic_time(appointment.starts_at), as_clinic_time(appointment.ends_at))
        )
    blocked = Unavailability.objects.filter(
        doctor=doctor,
        starts_at__lt=window_end,
        ends_at__gt=window_start,
    )
    for item in blocked:
        intervals.append((as_clinic_time(item.starts_at), as_clinic_time(item.ends_at)))
    return intervals


def available_slots(doctor, day, now=None):
    """Bookable slots for one local clinic date. Past and occupied slots are omitted."""
    if not doctor.is_active:
        return []
    starts = working_slot_starts(doctor, day)
    if not starts:
        return []
    if now is None:
        now = timezone.now()
    now = as_clinic_time(now)
    length = slot_duration()
    window_start = starts[0]
    window_end = starts[-1] + length
    blocked = blocking_intervals(doctor, window_start, window_end)
    open_slots = []
    for start in starts:
        end = start + length
        if start <= now:
            continue
        if any(_overlaps(start, end, block_start, block_end) for block_start, block_end in blocked):
            continue
        open_slots.append(Slot(starts_at=start, ends_at=end))
    return open_slots


def available_slots_in_range(doctor, start_day, end_day, now=None):
    """Bookable slots from start_day through end_day inclusive."""
    slots = []
    day = start_day
    while day <= end_day:
        slots.extend(available_slots(doctor, day, now=now))
        day += timedelta(days=1)
    return slots


def is_slot_bookable(doctor, starts_at, now=None):
    """True only if starts_at is an on-grid slot that is still free."""
    if not doctor.is_active:
        return False
    start = as_clinic_time(starts_at)
    day = start.date()
    grid = working_slot_starts(doctor, day)
    if start not in grid:
        return False
    return any(slot.starts_at == start for slot in available_slots(doctor, day, now=now))


def active_doctors_for_specialty(specialty):
    return Doctor.objects.filter(specialty=specialty, is_active=True).order_by("name")
