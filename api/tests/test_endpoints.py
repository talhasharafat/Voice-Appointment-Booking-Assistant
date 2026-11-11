"""
Single HTTP API under /api/:

- specialties / doctors / availability / appointments
- concurrent POST to the same slot → one 201 and one 409
- same idempotency key twice → same booking_reference
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from booking.models import Appointment
from clinic.models import Doctor, Specialty, Weekday, WorkingHours


TZ = ZoneInfo("Asia/Karachi")
SLOT = datetime(2027, 3, 1, 10, 0, tzinfo=TZ)


@override_settings(
    CLINIC_TIMEZONE="Asia/Karachi",
    TIME_ZONE="Asia/Karachi",
    APPOINTMENT_DURATION_MINUTES=30,
)
class ApiEndpointTests(TransactionTestCase):
    def setUp(self):
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
            timezone="Asia/Karachi",
        )
        WorkingHours.objects.create(
            doctor=self.doctor,
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.client = APIClient()

    def test_list_specialties_and_doctors(self):
        specialties = self.client.get("/api/specialties/")
        self.assertEqual(specialties.status_code, 200)
        names = [row["name"] for row in specialties.json()]
        self.assertEqual(names, ["Dermatology", "General Practice"])

        doctors = self.client.get("/api/doctors/", {"specialty_id": self.specialty.id})
        self.assertEqual(doctors.status_code, 200)
        self.assertEqual([row["name"] for row in doctors.json()], ["Dr. API"])

    def test_availability_marks_preferred_slot(self):
        response = self.client.get(
            "/api/availability/",
            {
                "doctor_id": self.doctor.id,
                "date": "2027-03-01",
                "preferred_starts_at": SLOT.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["duration_minutes"], 30)
        self.assertTrue(body["preferred_available"])
        starts = [slot["starts_at"] for slot in body["slots"]]
        self.assertIn(SLOT.isoformat(), starts)

    def test_idempotent_post_returns_same_reference(self):
        payload = {
            "doctor_id": self.doctor.id,
            "starts_at": SLOT.isoformat(),
            "patient_name": "Idempotent Patient",
            "contact_number": "03991110001",
            "reason": "Fever",
            "idempotency_key": "api-idem-1",
        }
        first = self.client.post("/api/appointments/", payload, format="json")
        second = self.client.post("/api/appointments/", payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            first.json()["booking_reference"],
            second.json()["booking_reference"],
        )
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(Appointment.objects.count(), 1)

    def test_conflict_returns_409_with_alternatives(self):
        payload = {
            "doctor_id": self.doctor.id,
            "starts_at": SLOT.isoformat(),
            "patient_name": "First",
            "phone": "03991110002",
            "reason": "Fever",
            "idempotency_key": "api-taken-a",
        }
        self.assertEqual(
            self.client.post("/api/appointments/", payload, format="json").status_code,
            201,
        )
        conflict = self.client.post(
            "/api/appointments/",
            {
                **payload,
                "patient_name": "Second",
                "phone": "03991110003",
                "idempotency_key": "api-taken-b",
            },
            format="json",
        )
        self.assertEqual(conflict.status_code, 409)
        body = conflict.json()
        self.assertEqual(body["code"], "slot_unavailable")
        self.assertTrue(body["alternatives"])
        alt_starts = [item["starts_at"] for item in body["alternatives"]]
        self.assertNotIn(SLOT.isoformat(), alt_starts)

    def test_concurrent_same_slot_one_409(self):
        barrier = threading.Barrier(2)
        statuses = []
        refs = []
        lock = threading.Lock()

        def worker(index):
            connection.close()
            client = APIClient()
            barrier.wait(timeout=5)
            response = client.post(
                "/api/appointments/",
                {
                    "doctor_id": self.doctor.id,
                    "starts_at": SLOT.isoformat(),
                    "patient_name": "Concurrent {}".format(index),
                    "phone": "0399111010{}".format(index),
                    "reason": "Fever",
                    "idempotency_key": "api-concurrent-{}".format(index),
                },
                format="json",
            )
            with lock:
                statuses.append(response.status_code)
                if response.status_code in (200, 201):
                    refs.append(response.json()["booking_reference"])
            connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, i) for i in (1, 2)]
            for future in as_completed(futures):
                future.result(timeout=20)

        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(len(refs), 1)
        self.assertEqual(
            Appointment.objects.filter(
                doctor=self.doctor,
                starts_at=SLOT,
                status=Appointment.Status.CONFIRMED,
            ).count(),
            1,
        )
