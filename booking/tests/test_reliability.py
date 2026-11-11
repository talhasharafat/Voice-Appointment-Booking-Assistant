"""
Reliability checks required by the assessment:

- two simultaneous requests for the same slot cannot both succeed
- a retried booking request does not create a duplicate
- an abandoned / unconfirmed conversation never writes an appointment
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TransactionTestCase, override_settings

from booking.models import Appointment
from booking.services import (
    BookingError,
    SlotUnavailable,
    check_availability,
    create_appointment,
    find_doctors,
    get_or_create_patient,
    list_specialties,
)
from clinic.models import Doctor, Patient, Specialty, Weekday, WorkingHours


TZ = ZoneInfo("Asia/Karachi")
SLOT = datetime(2027, 3, 1, 10, 0, tzinfo=TZ)


@override_settings(
    CLINIC_TIMEZONE="Asia/Karachi",
    TIME_ZONE="Asia/Karachi",
    APPOINTMENT_DURATION_MINUTES=30,
)
class BookingReliabilityTests(TransactionTestCase):
    def setUp(self):
        self.specialty = Specialty.objects.create(
            name="General Practice",
            description="Cough and fever.",
        )
        self.doctor = Doctor.objects.create(
            name="Dr. Reliability",
            specialty=self.specialty,
            is_active=True,
        )
        WorkingHours.objects.create(
            doctor=self.doctor,
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )

    def _book(self, *, patient_name, contact_number, idempotency_key, starts_at=SLOT):
        return create_appointment(
            doctor_id=self.doctor.id,
            starts_at=starts_at.isoformat(),
            patient_name=patient_name,
            contact_number=contact_number,
            reason="Fever",
            idempotency_key=idempotency_key,
            now=datetime(2027, 3, 1, 8, 0, tzinfo=TZ),
        )

    def test_model_status_choices(self):
        values = {choice.value for choice in Appointment.Status}
        self.assertEqual(values, {"pending", "confirmed", "cancelled"})
        self.assertNotIn("draft", values)
        self.assertNotIn("unconfirmed", values)

    def test_pre_confirmation_flow_does_not_create_appointment(self):
        """Browsing and collecting details must not persist a booking."""
        self.assertEqual(Appointment.objects.count(), 0)

        list_specialties()
        find_doctors(specialty_id=self.specialty.id)
        check_availability(
            doctor_id=self.doctor.id,
            day="2027-03-01",
            preferred_starts_at=SLOT.isoformat(),
            now=datetime(2027, 3, 1, 8, 0, tzinfo=TZ),
        )
        get_or_create_patient("Abandoned Caller", "03990000001")

        self.assertEqual(Patient.objects.filter(name="Abandoned Caller").count(), 1)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_retry_with_same_idempotency_key_does_not_duplicate(self):
        first = self._book(
            patient_name="Retry Patient",
            contact_number="03990000002",
            idempotency_key="retry-key-1",
        )
        second = self._book(
            patient_name="Retry Patient",
            contact_number="03990000002",
            idempotency_key="retry-key-1",
        )

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["booking_reference"], second["booking_reference"])
        self.assertEqual(Appointment.objects.count(), 1)

    def test_http_retry_also_stays_idempotent(self):
        payload = {
            "doctor_id": self.doctor.id,
            "starts_at": SLOT.isoformat(),
            "patient_name": "HTTP Retry",
            "contact_number": "03990000003",
            "reason": "Cough",
            "idempotency_key": "http-retry-key",
        }
        first = self.client.post(
            "/api/appointments/",
            data=payload,
            content_type="application/json",
        )
        second = self.client.post(
            "/api/appointments/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_second_distinct_request_for_taken_slot_fails(self):
        self._book(
            patient_name="First Winner",
            contact_number="03990000004",
            idempotency_key="taken-slot-a",
        )
        with self.assertRaises(SlotUnavailable):
            self._book(
                patient_name="Second Loser",
                contact_number="03990000005",
                idempotency_key="taken-slot-b",
            )
        self.assertEqual(Appointment.objects.filter(status="confirmed").count(), 1)

    def test_concurrent_same_slot_only_one_succeeds(self):
        Patient.objects.create(name="Concurrent 1", contact_number="03990000101")
        Patient.objects.create(name="Concurrent 2", contact_number="03990000102")

        barrier = threading.Barrier(2)
        results = []
        errors = []
        lock = threading.Lock()

        def worker(index):
            connection.close()
            barrier.wait(timeout=5)
            try:
                payload = self._book(
                    patient_name="Concurrent {}".format(index),
                    contact_number="0399000010{}".format(index),
                    idempotency_key="concurrent-slot-{}".format(index),
                )
                with lock:
                    results.append(payload)
            except (SlotUnavailable, BookingError) as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(1,)),
            threading.Thread(target=worker, args=(2,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())

        confirmed = Appointment.objects.filter(
            doctor=self.doctor,
            starts_at=SLOT,
            status=Appointment.Status.CONFIRMED,
        )
        self.assertEqual(confirmed.count(), 1)
        self.assertEqual(len(results) + len(errors), 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(results[0]["starts_at"], SLOT.isoformat())

    def test_concurrent_identical_idempotency_key_creates_one_row(self):
        Patient.objects.create(name="Same Key Patient", contact_number="03990000006")
        barrier = threading.Barrier(4)
        outcomes = []
        lock = threading.Lock()

        def worker(index):
            connection.close()
            barrier.wait(timeout=5)
            try:
                payload = self._book(
                    patient_name="Same Key Patient",
                    contact_number="03990000006",
                    idempotency_key="shared-idempotency-key",
                )
                with lock:
                    outcomes.append(("ok", payload["booking_reference"], payload.get("created")))
            except Exception as exc:  # noqa: BLE001 - collect race outcomes for assertion
                with lock:
                    outcomes.append(("error", type(exc).__name__, str(exc)))
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(4)]
            for future in as_completed(futures):
                future.result(timeout=15)

        self.assertEqual(Appointment.objects.count(), 1)
        ok_refs = [item[1] for item in outcomes if item[0] == "ok"]
        self.assertGreaterEqual(len(ok_refs), 1)
        self.assertEqual(set(ok_refs), {ok_refs[0]})
        # Every successful caller must see the same booking; at most one created it.
        created_flags = [item[2] for item in outcomes if item[0] == "ok"]
        self.assertEqual(sum(1 for flag in created_flags if flag is True), 1)
