from calendar import Calendar
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from booking.calendar import as_clinic_time, clinic_zone
from booking.models import Appointment
from clinic.models import Doctor
from voice.formatting import format_clock


WEEKDAY_HEADERS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def doctor_list(request):
    doctors = (
        Doctor.objects.filter(is_active=True)
        .select_related("specialty")
        .prefetch_related("working_hours")
        .order_by("name")
    )
    rows = []
    for doctor in doctors:
        hours = doctor.working_hours.all()
        rows.append(
            {
                "doctor": doctor,
                "hours_summary": _hours_summary(hours),
            }
        )
    return render(
        request,
        "doctors/list.html",
        {
            "rows": rows,
            "clinic_timezone": settings.CLINIC_TIMEZONE,
        },
    )


def doctor_detail(request, doctor_id):
    doctor = get_object_or_404(
        Doctor.objects.select_related("specialty").prefetch_related(
            "working_hours",
            "unavailabilities",
        ),
        pk=doctor_id,
        is_active=True,
    )
    today = timezone.now().astimezone(clinic_zone()).date()
    year = _int_param(request.GET.get("year"), today.year)
    month = _int_param(request.GET.get("month"), today.month)
    if month < 1 or month > 12:
        month = today.month
        year = today.year

    month_start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    month_end = next_month - timedelta(days=1)
    prev_month_date = month_start - timedelta(days=1)

    window_start = datetime.combine(month_start, time.min, tzinfo=clinic_zone())
    window_end = datetime.combine(month_end, time.max, tzinfo=clinic_zone())

    appointments = (
        Appointment.objects.filter(
            doctor=doctor,
            status=Appointment.Status.CONFIRMED,
            starts_at__lt=window_end,
            ends_at__gt=window_start,
        )
        .select_related("patient")
        .order_by("starts_at")
    )
    unavailabilities = doctor.unavailabilities.filter(
        starts_at__lt=window_end,
        ends_at__gt=window_start,
    ).order_by("starts_at")

    events_by_day = {}
    for appointment in appointments:
        start = as_clinic_time(appointment.starts_at)
        end = as_clinic_time(appointment.ends_at)
        day_key = start.date().isoformat()
        events_by_day.setdefault(day_key, []).append(
            {
                "kind": "appointment",
                "label": "{}–{} · {} ({})".format(
                    format_clock(start),
                    format_clock(end),
                    appointment.patient.name,
                    appointment.booking_reference,
                ),
            }
        )
    for blocked in unavailabilities:
        start = as_clinic_time(blocked.starts_at)
        end = as_clinic_time(blocked.ends_at)
        cursor = start.date()
        last = end.date()
        while cursor <= last:
            day_key = cursor.isoformat()
            reason = blocked.reason or "Unavailable"
            if cursor == start.date() and cursor == end.date():
                label = "{}–{} · {}".format(format_clock(start), format_clock(end), reason)
            elif cursor == start.date():
                label = "From {} · {}".format(format_clock(start), reason)
            elif cursor == end.date():
                label = "Until {} · {}".format(format_clock(end), reason)
            else:
                label = "All day · {}".format(reason)
            events_by_day.setdefault(day_key, []).append(
                {
                    "kind": "unavailable",
                    "label": label,
                }
            )
            cursor += timedelta(days=1)

    weeks = []
    for week in Calendar(firstweekday=0).monthdatescalendar(year, month):
        days = []
        for day in week:
            key = day.isoformat()
            days.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "events": events_by_day.get(key, []),
                }
            )
        weeks.append(days)

    return render(
        request,
        "doctors/detail.html",
        {
            "doctor": doctor,
            "clinic_timezone": settings.CLINIC_TIMEZONE,
            "duration_minutes": settings.APPOINTMENT_DURATION_MINUTES,
            "working_hours": doctor.working_hours.all(),
            "calendar_weeks": weeks,
            "weekday_headers": WEEKDAY_HEADERS,
            "month_label": month_start.strftime("%B %Y"),
            "prev_year": prev_month_date.year,
            "prev_month": prev_month_date.month,
            "next_year": next_month.year,
            "next_month": next_month.month,
        },
    )


def _hours_summary(hours):
    if not hours:
        return "No hours set"
    parts = []
    for block in hours:
        parts.append(
            "{} {}–{}".format(
                block.get_weekday_display()[:3],
                format_clock(block.start_time),
                format_clock(block.end_time),
            )
        )
    return ", ".join(parts)


def _int_param(raw, default):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
