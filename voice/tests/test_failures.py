from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from booking.models import Appointment
from clinic.models import Doctor, Patient, Specialty, Weekday, WorkingHours
from voice.conversation import abandon_session, process_turn, start_session
from voice.models import VoiceSession
from voice.parsing import datetime_missing_parts


TZ = ZoneInfo("Asia/Karachi")
SLOT = datetime(2027, 3, 1, 10, 0, tzinfo=TZ)
FROZEN_NOW = datetime(2027, 2, 28, 9, 0, tzinfo=TZ)


@override_settings(
    CLINIC_TIMEZONE="Asia/Karachi",
    TIME_ZONE="Asia/Karachi",
    APPOINTMENT_DURATION_MINUTES=30,
    VOICE_SESSION_TIMEOUT_MINUTES=30,
)
class FailureHandlingTests(TestCase):
    def setUp(self):
        self.gp = Specialty.objects.create(
            name="General Practice",
            description="Cough fever sore throat blood pressure fatigue infections check-ups.",
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
        self._now_patcher = patch("django.utils.timezone.now", return_value=FROZEN_NOW)
        self._now_patcher.start()
        self.addCleanup(self._now_patcher.stop)

    def _to_confirm(self):
        start = start_session()
        session_id = start["session_id"]
        process_turn(session_id, "Nadia Ali")
        process_turn(session_id, "03001112233")
        process_turn(session_id, "persistent cough")
        confirm = process_turn(session_id, "Monday at 10 AM")
        self.assertEqual(confirm["state"], "CONFIRM")
        self.assertFalse(confirm["booked"])
        return session_id

    def test_unclear_date_asks_for_missing_time(self):
        self.assertEqual(datetime_missing_parts("next Monday"), ["time"])
        start = start_session()
        session_id = start["session_id"]
        process_turn(session_id, "Omar Sheikh")
        process_turn(session_id, "03219876543")
        process_turn(session_id, "fever")
        reply = process_turn(session_id, "Monday")
        self.assertEqual(reply["state"], "ASK_DATETIME")
        self.assertIn("not a time", reply["message"].lower())
        self.assertEqual(Appointment.objects.count(), 0)

    def test_unclear_speech_does_not_book(self):
        start = start_session()
        reply = process_turn(start["session_id"], "hmm")
        self.assertIn("did not catch", reply["message"].lower())
        self.assertFalse(reply["booked"])
        self.assertEqual(Appointment.objects.count(), 0)

    def test_slot_taken_during_confirmation_offers_alternatives(self):
        session_id = self._to_confirm()
        patient = Patient.objects.create(name="Other", contact_number="03991112222")
        Appointment.objects.create(
            patient=patient,
            doctor=self.doctor,
            starts_at=SLOT,
            ends_at=SLOT + timedelta(minutes=30),
            reason="Taken mid-call",
            status=Appointment.Status.CONFIRMED,
            booking_reference="MID1",
            idempotency_key="mid-1",
        )
        reply = process_turn(session_id, "yes")
        self.assertFalse(reply["booked"])
        self.assertIn(reply["state"], {"OFFER_ALTERNATIVES", "ASK_DATETIME"})
        self.assertIn("no appointment was booked", reply["message"].lower())
        self.assertEqual(Appointment.objects.filter(booking_reference="MID1").count(), 1)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_backend_tool_failure_is_honest(self):
        session_id = self._to_confirm()
        with patch("voice.conversation.run_tool", side_effect=RuntimeError("db down")):
            reply = process_turn(session_id, "yes")
        self.assertFalse(reply["booked"])
        self.assertIn("did not book", reply["message"].lower())
        self.assertEqual(Appointment.objects.count(), 0)
        session = VoiceSession.objects.get(pk=session_id)
        self.assertEqual(session.status, VoiceSession.Status.ACTIVE)

    def test_create_appointment_error_envelope_never_marks_booked(self):
        session_id = self._to_confirm()

        def fake_tool(name, arguments=None):
            if name == "check_availability":
                return {
                    "ok": True,
                    "result": {"preferred_available": True, "alternatives": [], "slots": []},
                }
            if name == "create_appointment":
                return {
                    "ok": False,
                    "error": {"code": "booking_error", "message": "write failed"},
                }
            raise AssertionError(name)

        with patch("voice.conversation.run_tool", side_effect=fake_tool):
            reply = process_turn(session_id, "yes")
        self.assertFalse(reply["booked"])
        self.assertIsNone(reply["data"]["booking_reference"])
        self.assertIn("no appointment was saved", reply["message"].lower())
        self.assertEqual(Appointment.objects.count(), 0)

    def test_abandoned_session_leaves_no_appointment(self):
        start = start_session()
        session_id = start["session_id"]
        process_turn(session_id, "Fatima Noor")
        process_turn(session_id, "03007654321")
        process_turn(session_id, "sore throat")
        process_turn(session_id, "Monday at 11 AM")
        abandoned = abandon_session(session_id)
        self.assertEqual(abandoned["status"], VoiceSession.Status.ABANDONED)
        self.assertFalse(abandoned["booked"])
        self.assertEqual(Appointment.objects.count(), 0)

    def test_idle_session_times_out_without_booking(self):
        start = start_session()
        session_id = start["session_id"]
        process_turn(session_id, "Test Patient")
        session = VoiceSession.objects.get(pk=session_id)
        VoiceSession.objects.filter(pk=session_id).update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )
        reply = process_turn(session_id, "03001234567")
        self.assertEqual(reply["status"], VoiceSession.Status.ABANDONED)
        self.assertFalse(reply["booked"])
        self.assertIn("timed out", reply["message"].lower())
        self.assertEqual(Appointment.objects.count(), 0)
