from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from booking.models import Appointment
from clinic.models import Doctor, Specialty, Weekday, WorkingHours
from voice.conversation import abandon_session, process_turn, start_session
from voice.models import VoiceSession
from voice.parsing import extract_phone, parse_preferred_datetime


TZ = ZoneInfo("Asia/Karachi")


@override_settings(
    CLINIC_TIMEZONE="Asia/Karachi",
    TIME_ZONE="Asia/Karachi",
    APPOINTMENT_DURATION_MINUTES=30,
)
class ConversationTests(TestCase):
    def setUp(self):
        self.gp = Specialty.objects.create(
            name="General Practice",
            description="Cough fever sore throat blood pressure fatigue infections check-ups.",
        )
        Specialty.objects.create(
            name="Dermatology",
            description="Skin hair nail rash acne eczema psoriasis moles itching.",
        )
        self.doctor = Doctor.objects.create(
            name="Dr. Amina Khan",
            specialty=self.gp,
            is_active=True,
        )
        WorkingHours.objects.create(
            doctor=self.doctor,
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        # Extra GP doctor to exercise doctor choice when needed in other cases.
        self.slot = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)

    def _turn(self, session_id, text):
        return process_turn(session_id, text)

    def test_parsers(self):
        self.assertEqual(extract_phone("0300-1234567"), "03001234567")
        preferred = parse_preferred_datetime(
            "Monday at 10 AM",
            now=datetime(2026, 11, 8, 9, 0, tzinfo=TZ),
        )
        self.assertEqual(preferred, self.slot)

    def test_full_booking_happy_path(self):
        start = start_session()
        session_id = start["session_id"]
        self.assertIn("full name", start["message"].lower())

        self.assertEqual(self._turn(session_id, "Nadia Ali")["state"], "ASK_PHONE")
        self.assertEqual(self._turn(session_id, "03001112233")["state"], "ASK_REASON")
        after_reason = self._turn(session_id, "I have a persistent cough")
        self.assertEqual(after_reason["state"], "ASK_DATETIME")
        self.assertIn("Amina", after_reason["message"])

        offered = self._turn(session_id, "Monday at 10 AM")
        self.assertEqual(offered["state"], "CONFIRM")
        self.assertIn("confirm", offered["message"].lower())

        done = self._turn(session_id, "yes")
        self.assertEqual(done["state"], "COMPLETED")
        self.assertTrue(done["data"]["booking_reference"])
        self.assertEqual(Appointment.objects.count(), 1)
        session = VoiceSession.objects.get(pk=session_id)
        self.assertEqual(session.status, VoiceSession.Status.COMPLETED)

    def test_abandoned_session_does_not_create_appointment(self):
        start = start_session()
        session_id = start["session_id"]
        self._turn(session_id, "Omar Sheikh")
        self._turn(session_id, "03219876543")
        abandoned = abandon_session(session_id)
        self.assertEqual(abandoned["status"], VoiceSession.Status.ABANDONED)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_unclear_speech_asks_clarification(self):
        start = start_session()
        reply = self._turn(start["session_id"], "uh")
        self.assertEqual(reply["state"], "ASK_NAME")
        self.assertIn("did not catch", reply["message"].lower())

    def test_refuses_diagnosis(self):
        start = start_session()
        reply = self._turn(start["session_id"], "please diagnose my cough")
        self.assertIn("cannot diagnose", reply["message"].lower())
        self.assertEqual(reply["state"], "ASK_NAME")

    def test_unavailable_slot_offers_alternatives_then_books(self):
        # Occupy 10:00 so conversation must offer alternatives.
        from clinic.models import Patient
        from datetime import timedelta

        patient = Patient.objects.create(name="Taken", contact_number="03990000999")
        Appointment.objects.create(
            patient=patient,
            doctor=self.doctor,
            starts_at=self.slot,
            ends_at=self.slot + timedelta(minutes=30),
            reason="Busy",
            status=Appointment.Status.CONFIRMED,
            booking_reference="BUSY1",
            idempotency_key="busy-1",
        )

        start = start_session()
        session_id = start["session_id"]
        self._turn(session_id, "Fatima Noor")
        self._turn(session_id, "03007654321")
        self._turn(session_id, "fever and fatigue")
        alt = self._turn(session_id, "Monday at 10 AM")
        self.assertEqual(alt["state"], "OFFER_ALTERNATIVES")
        self.assertIn("1.", alt["message"])
        self.assertIn("at", alt["message"].lower())
        self.assertNotIn("T09:", alt["message"])

        confirm = self._turn(session_id, "1")
        self.assertEqual(confirm["state"], "CONFIRM")
        done = self._turn(session_id, "yes")
        self.assertEqual(done["state"], "COMPLETED")
        self.assertEqual(Appointment.objects.filter(status="confirmed").count(), 2)

    def test_no_on_confirm_does_not_book(self):
        start = start_session()
        session_id = start["session_id"]
        self._turn(session_id, "Test User")
        self._turn(session_id, "03001231231")
        self._turn(session_id, "sore throat")
        self._turn(session_id, "Monday at 11 AM")
        cancelled = self._turn(session_id, "no")
        self.assertEqual(cancelled["status"], VoiceSession.Status.ABANDONED)
        self.assertEqual(Appointment.objects.count(), 0)
