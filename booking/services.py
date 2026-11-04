"""
Booking services. Availability and conflicts are enforced here, not by the AI.
"""

import secrets
import time
from datetime import date, datetime, timedelta

from django.db import IntegrityError, OperationalError, transaction
from django.utils.dateparse import parse_datetime

from booking.calendar import (
    as_clinic_time,
    available_slots,
    available_slots_in_range,
    clinic_zone,
    is_slot_bookable,
    slot_duration,
    slot_from_start,
)
from booking.models import Appointment
from clinic.models import Doctor, Patient, Specialty


class BookingError(Exception):
    """Base booking failure with a stable machine code."""

    code = "booking_error"

    def __init__(self, message):
        self.message = message
        super().__init__(message)


class SlotUnavailable(BookingError):
    code = "slot_unavailable"


class DoctorNotFound(BookingError):
    code = "doctor_not_found"


class SpecialtyNotFound(BookingError):
    code = "specialty_not_found"


class InvalidBookingRequest(BookingError):
    code = "invalid_request"


def parse_clinic_datetime(value):
    if isinstance(value, datetime):
        return as_clinic_time(value)
    if not value:
        raise InvalidBookingRequest("starts_at is required.")
    parsed = parse_datetime(value)
    if parsed is None:
        raise InvalidBookingRequest("starts_at must be an ISO-8601 datetime.")
    return as_clinic_time(parsed)


def parse_clinic_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        raise InvalidBookingRequest("date is required.")
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidBookingRequest("date must be YYYY-MM-DD.") from exc


def list_specialties():
    return [
        {
            "id": specialty.id,
            "name": specialty.name,
            "description": specialty.description,
        }
        for specialty in Specialty.objects.order_by("name")
    ]


def find_doctors(specialty_id=None):
    queryset = Doctor.objects.filter(is_active=True).select_related("specialty").order_by("name")
    if specialty_id is not None:
        if not Specialty.objects.filter(pk=specialty_id).exists():
            raise SpecialtyNotFound("Specialty {} was not found.".format(specialty_id))
        queryset = queryset.filter(specialty_id=specialty_id)
    return [
        {
            "id": doctor.id,
            "name": doctor.name,
            "specialty_id": doctor.specialty_id,
            "specialty_name": doctor.specialty.name,
        }
        for doctor in queryset
    ]


def get_doctor_or_error(doctor_id):
    try:
        return Doctor.objects.select_related("specialty").get(pk=doctor_id, is_active=True)
    except Doctor.DoesNotExist as exc:
        raise DoctorNotFound("Doctor {} was not found or is inactive.".format(doctor_id)) from exc


def check_availability(doctor_id, day=None, date_from=None, date_to=None, preferred_starts_at=None, now=None):
    doctor = get_doctor_or_error(doctor_id)
    if day is not None:
        start_day = end_day = parse_clinic_date(day)
    else:
        start_day = parse_clinic_date(date_from) if date_from else date.today()
        end_day = parse_clinic_date(date_to) if date_to else start_day
    if end_day < start_day:
        raise InvalidBookingRequest("date_to must be on or after date_from.")
    if (end_day - start_day).days > 14:
        raise InvalidBookingRequest("Availability window cannot exceed 14 days.")

    slots = available_slots_in_range(doctor, start_day, end_day, now=now)
    preferred = None
    preferred_available = None
    alternatives = []
    if preferred_starts_at is not None:
        preferred = parse_clinic_datetime(preferred_starts_at)
        preferred_available = is_slot_bookable(doctor, preferred, now=now)
        if preferred_available:
            alternatives = []
        else:
            alternatives = [slot.as_dict() for slot in slots[:5]]

    return {
        "clinic_timezone": str(clinic_zone()),
        "duration_minutes": int(slot_duration().total_seconds() // 60),
        "doctor": {
            "id": doctor.id,
            "name": doctor.name,
            "specialty_id": doctor.specialty_id,
            "specialty_name": doctor.specialty.name,
        },
        "date_from": start_day.isoformat(),
        "date_to": end_day.isoformat(),
        "slots": [slot.as_dict() for slot in slots],
        "preferred_starts_at": preferred.isoformat() if preferred else None,
        "preferred_available": preferred_available,
        "alternatives": alternatives,
    }


def get_or_create_patient(name, contact_number):
    name = (name or "").strip()
    contact_number = (contact_number or "").strip()
    if not name or not contact_number:
        raise InvalidBookingRequest("patient_name and contact_number are required.")
    try:
        patient, _ = Patient.objects.get_or_create(
            name=name,
            contact_number=contact_number,
        )
        return patient
    except IntegrityError:
        return Patient.objects.get(name=name, contact_number=contact_number)


def _new_booking_reference():
    return "BK{}".format(secrets.token_hex(4).upper())


def appointment_payload(appointment):
    return {
        "id": appointment.id,
        "booking_reference": appointment.booking_reference,
        "status": appointment.status,
        "idempotency_key": appointment.idempotency_key,
        "reason": appointment.reason,
        "starts_at": as_clinic_time(appointment.starts_at).isoformat(),
        "ends_at": as_clinic_time(appointment.ends_at).isoformat(),
        "patient": {
            "id": appointment.patient_id,
            "name": appointment.patient.name,
            "contact_number": appointment.patient.contact_number,
        },
        "doctor": {
            "id": appointment.doctor_id,
            "name": appointment.doctor.name,
            "specialty_id": appointment.doctor.specialty_id,
            "specialty_name": appointment.doctor.specialty.name,
        },
        "created": True,
    }


def create_appointment(
    *,
    doctor_id,
    starts_at,
    patient_name,
    contact_number,
    reason,
    idempotency_key,
    now=None,
):
    """
    Confirm a booking only after the backend re-checks the slot.

    Concurrent callers for the same doctor are serialized with select_for_update.
    Retries with the same idempotency_key return the original appointment.
    """
    last_lock_error = None
    for attempt in range(12):
        try:
            return _create_appointment_once(
                doctor_id=doctor_id,
                starts_at=starts_at,
                patient_name=patient_name,
                contact_number=contact_number,
                reason=reason,
                idempotency_key=idempotency_key,
                now=now,
            )
        except OperationalError as exc:
            # SQLite may briefly reject overlapping writers; retry instead of claiming success.
            last_lock_error = exc
            time.sleep(0.05 * (attempt + 1))
    raise BookingError("Database was busy while confirming the booking.") from last_lock_error


@transaction.atomic
def _create_appointment_once(
    *,
    doctor_id,
    starts_at,
    patient_name,
    contact_number,
    reason,
    idempotency_key,
    now=None,
):
    if doctor_id is None:
        raise InvalidBookingRequest("doctor_id is required.")
    key = (idempotency_key or "").strip()
    if not key:
        raise InvalidBookingRequest("idempotency_key is required.")
    reason = (reason or "").strip()
    if not reason:
        raise InvalidBookingRequest("reason is required.")

    existing = (
        Appointment.objects.select_related("patient", "doctor", "doctor__specialty")
        .filter(idempotency_key=key)
        .first()
    )
    if existing is not None:
        payload = appointment_payload(existing)
        payload["created"] = False
        payload["replayed"] = True
        return payload

    try:
        doctor = Doctor.objects.select_for_update().select_related("specialty").get(
            pk=doctor_id,
            is_active=True,
        )
    except Doctor.DoesNotExist as exc:
        raise DoctorNotFound("Doctor {} was not found or is inactive.".format(doctor_id)) from exc

    # Hold overlapping confirmed rows so two creates cannot both pass the free check.
    start = parse_clinic_datetime(starts_at)
    slot = slot_from_start(start)
    Appointment.objects.select_for_update().filter(
        doctor=doctor,
        status=Appointment.Status.CONFIRMED,
        starts_at__lt=slot.ends_at,
        ends_at__gt=slot.starts_at,
    ).exists()

    if not is_slot_bookable(doctor, slot.starts_at, now=now):
        day_slots = available_slots(doctor, slot.starts_at.date(), now=now)
        alternatives = [item.as_dict() for item in day_slots[:5]]
        raise SlotUnavailable(
            "That slot is not available. Alternatives: {}".format(
                ", ".join(item["starts_at"] for item in alternatives) or "none today"
            )
        )

    patient = get_or_create_patient(patient_name, contact_number)

    for _ in range(5):
        reference = _new_booking_reference()
        try:
            appointment = Appointment.objects.create(
                patient=patient,
                doctor=doctor,
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
                reason=reason,
                status=Appointment.Status.CONFIRMED,
                booking_reference=reference,
                idempotency_key=key,
            )
            break
        except IntegrityError:
            # Another worker won the slot or reused the key after our pre-check.
            replay = (
                Appointment.objects.select_related("patient", "doctor", "doctor__specialty")
                .filter(idempotency_key=key)
                .first()
            )
            if replay is not None:
                payload = appointment_payload(replay)
                payload["created"] = False
                payload["replayed"] = True
                return payload
            if Appointment.objects.filter(
                doctor=doctor,
                starts_at=slot.starts_at,
                status=Appointment.Status.CONFIRMED,
            ).exists():
                raise SlotUnavailable("That slot was just taken by another booking.")
            # Rare booking_reference collision; retry with a new reference.
    else:
        raise BookingError("Could not allocate a booking reference.")

    appointment = (
        Appointment.objects.select_related("patient", "doctor", "doctor__specialty")
        .get(pk=appointment.id)
    )
    payload = appointment_payload(appointment)
    payload["created"] = True
    payload["replayed"] = False
    return payload


def suggest_alternatives(doctor_id, preferred_starts_at, days_ahead=3, now=None):
    doctor = get_doctor_or_error(doctor_id)
    preferred = parse_clinic_datetime(preferred_starts_at)
    start_day = preferred.date()
    end_day = start_day + timedelta(days=days_ahead)
    slots = available_slots_in_range(doctor, start_day, end_day, now=now)
    return [slot.as_dict() for slot in slots[:8]]
