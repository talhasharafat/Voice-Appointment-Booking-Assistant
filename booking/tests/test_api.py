from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings

from booking.models import Appointment
from clinic.models import Doctor, Specialty, Weekday, WorkingHours


TZ = ZoneInfo("Asia/Karachi")
MONDAY = datetime(2026, 11, 9, tzinfo=TZ).date()


@override_settings(
    CLINIC_TIMEZONE="Asia/Karachi",
    TIME_ZONE="Asia/Karachi",
    APPOINTMENT_DURATION_MINUTES=30,
)
class BookingApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.specialty = Specialty.objects.create(
            name="General Practice",
            description="Cough and fever.",
        )
        self.other = Specialty.objects.create(
            name="Dermatology",
            description="Skin concerns.",
        )
        self.doctor = Doctor.objects.create(
            name="Dr. API",
            specialty=self.specialty,
            is_active=True,
        )
        WorkingHours.objects.create(
            doctor=self.doctor,
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.slot = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)

    def test_list_specialties_and_find_doctors(self):
        specialties = self.client.get("/booking/api/specialties/")
        self.assertEqual(specialties.status_code, 200)
        names = [item["name"] for item in specialties.json()["specialties"]]
        self.assertEqual(names, ["Dermatology", "General Practice"])

        doctors = self.client.get(
            "/booking/api/doctors/",
            {"specialty_id": self.specialty.id},
        )
        self.assertEqual(doctors.status_code, 200)
        self.assertEqual(len(doctors.json()["doctors"]), 1)
        self.assertEqual(doctors.json()["doctors"][0]["name"], "Dr. API")

    def test_availability_marks_preferred_and_lists_slots(self):
        response = self.client.get(
            "/booking/api/availability/",
            {
                "doctor_id": self.doctor.id,
                "date": MONDAY.isoformat(),
                "preferred_starts_at": self.slot.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["availability"]
        self.assertTrue(payload["preferred_available"])
        self.assertEqual(payload["clinic_timezone"], "Asia/Karachi")
        self.assertEqual(payload["duration_minutes"], 30)
        starts = [item["starts_at"] for item in payload["slots"]]
        self.assertIn(self.slot.isoformat(), starts)

    def test_create_appointment_confirms_only_after_backend_check(self):
        response = self.client.post(
            "/booking/api/appointments/",
            data={
                "doctor_id": self.doctor.id,
                "starts_at": self.slot.isoformat(),
                "patient_name": "Nadia Ali",
                "contact_number": "03001112233",
                "reason": "Sore throat",
                "idempotency_key": "api-create-1",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["appointment"]["created"])
        self.assertTrue(body["appointment"]["booking_reference"].startswith("BK"))
        self.assertEqual(Appointment.objects.count(), 1)

    def test_idempotent_retry_returns_same_appointment(self):
        payload = {
            "doctor_id": self.doctor.id,
            "starts_at": self.slot.isoformat(),
            "patient_name": "Nadia Ali",
            "contact_number": "03001112233",
            "reason": "Sore throat",
            "idempotency_key": "api-retry-1",
        }
        first = self.client.post(
            "/booking/api/appointments/",
            data=payload,
            content_type="application/json",
        )
        second = self.client.post(
            "/booking/api/appointments/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            first.json()["appointment"]["booking_reference"],
            second.json()["appointment"]["booking_reference"],
        )
        self.assertTrue(second.json()["appointment"]["replayed"])
        self.assertEqual(Appointment.objects.count(), 1)

    def test_occupied_slot_returns_conflict_and_alternatives(self):
        create = self.client.post(
            "/booking/api/appointments/",
            data={
                "doctor_id": self.doctor.id,
                "starts_at": self.slot.isoformat(),
                "patient_name": "First Patient",
                "contact_number": "03000000001",
                "reason": "Fever",
                "idempotency_key": "api-first",
            },
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 201)

        conflict = self.client.post(
            "/booking/api/appointments/",
            data={
                "doctor_id": self.doctor.id,
                "starts_at": self.slot.isoformat(),
                "patient_name": "Second Patient",
                "contact_number": "03000000002",
                "reason": "Cough",
                "idempotency_key": "api-second",
            },
            content_type="application/json",
        )
        self.assertEqual(conflict.status_code, 409)
        body = conflict.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "slot_unavailable")
        self.assertTrue(body["alternatives"])
        alt_starts = [item["starts_at"] for item in body["alternatives"]]
        self.assertNotIn(self.slot.isoformat(), alt_starts)
        self.assertEqual(Appointment.objects.count(), 1)
