from datetime import timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from booking.calendar import available_slots_in_range, clinic_zone
from booking.models import Appointment
from clinic.models import Doctor
from voice.formatting import format_slot_label


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
    end_day = today + timedelta(days=6)
    open_slots = available_slots_in_range(doctor, today, end_day)
    upcoming = (
        Appointment.objects.filter(
            doctor=doctor,
            status=Appointment.Status.CONFIRMED,
            starts_at__gte=timezone.now(),
        )
        .select_related("patient")
        .order_by("starts_at")[:20]
    )
    return render(
        request,
        "doctors/detail.html",
        {
            "doctor": doctor,
            "clinic_timezone": settings.CLINIC_TIMEZONE,
            "duration_minutes": settings.APPOINTMENT_DURATION_MINUTES,
            "available_slots": [
                {
                    "starts_at": slot.starts_at,
                    "ends_at": slot.ends_at,
                    "label": format_slot_label(slot.starts_at),
                }
                for slot in open_slots
            ],
            "upcoming_appointments": upcoming,
            "working_hours": doctor.working_hours.all(),
            "unavailabilities": doctor.unavailabilities.filter(ends_at__gte=timezone.now()).order_by(
                "starts_at"
            )[:10],
            "range_label": "{} – {}".format(
                today.strftime("%d %b %Y"),
                end_day.strftime("%d %b %Y"),
            ),
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
                block.start_time.strftime("%H:%M"),
                block.end_time.strftime("%H:%M"),
            )
        )
    return ", ".join(parts)
