from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from booking.models import Appointment
from clinic.models import Doctor, Specialty, Weekday, WorkingHours
from voice.tools import list_tools, match_specialty, run_tool


TZ = ZoneInfo("Asia/Karachi")


@override_settings(
    CLINIC_TIMEZONE="Asia/Karachi",
    TIME_ZONE="Asia/Karachi",
    APPOINTMENT_DURATION_MINUTES=30,
)
class VoiceToolsTests(TestCase):
    def setUp(self):
        self.gp = Specialty.objects.create(
            name="General Practice",
            description="Cough fever sore throat blood pressure fatigue infections check-ups.",
        )
        self.derm = Specialty.objects.create(
            name="Dermatology",
            description="Skin hair nail rash acne eczema psoriasis moles itching.",
        )
        self.amina = Doctor.objects.create(name="Dr. Amina", specialty=self.gp, is_active=True)
        self.sara = Doctor.objects.create(name="Dr. Sara", specialty=self.derm, is_active=True)
        WorkingHours.objects.create(
            doctor=self.amina,
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.slot = datetime(2027, 3, 1, 10, 0, tzinfo=TZ)

    def test_tool_specs_cover_booking_actions(self):
        names = {item["name"] for item in list_tools()}
        self.assertEqual(
            names,
            {"match_specialty", "find_doctors", "check_availability", "create_appointment"},
        )

    def test_match_specialty_for_cough_selects_general_practice(self):
        result = match_specialty("I have a persistent cough and fever")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["specialty_id"], self.gp.id)

    def test_match_specialty_for_rash_selects_dermatology(self):
        result = match_specialty("itchy rash on my arm")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["specialty_id"], self.derm.id)

    def test_ambiguous_reason_asks_clarifying_question(self):
        result = match_specialty("I need help")
        self.assertIn(result["status"], {"no_match", "ambiguous"})
        self.assertTrue(result.get("clarifying_question"))

    def test_find_doctors_tool(self):
        response = run_tool("find_doctors", {"specialty_id": self.gp.id})
        self.assertTrue(response["ok"])
        self.assertEqual(len(response["result"]["doctors"]), 1)
        self.assertEqual(response["result"]["doctors"][0]["name"], "Dr. Amina")

    def test_check_availability_and_book_through_tools(self):
        availability = run_tool(
            "check_availability",
            {
                "doctor_id": self.amina.id,
                "date": "2027-03-01",
                "preferred_starts_at": self.slot.isoformat(),
            },
        )
        self.assertTrue(availability["ok"])
        self.assertTrue(availability["result"]["preferred_available"])

        booked = run_tool(
            "create_appointment",
            {
                "doctor_id": self.amina.id,
                "starts_at": self.slot.isoformat(),
                "patient_name": "Voice Patient",
                "contact_number": "03005556677",
                "reason": "Cough",
                "idempotency_key": "voice-tool-1",
            },
        )
        self.assertTrue(booked["ok"])
        self.assertEqual(booked["result"]["status"], "booked")
        self.assertTrue(booked["result"]["appointment"]["booking_reference"])
        self.assertEqual(Appointment.objects.count(), 1)

    def test_create_appointment_conflict_is_not_success(self):
        payload = {
            "doctor_id": self.amina.id,
            "starts_at": self.slot.isoformat(),
            "patient_name": "First",
            "contact_number": "03000000011",
            "reason": "Fever",
            "idempotency_key": "voice-first",
        }
        self.assertTrue(run_tool("create_appointment", payload)["ok"])
        conflict = run_tool(
            "create_appointment",
            {
                **payload,
                "patient_name": "Second",
                "contact_number": "03000000012",
                "idempotency_key": "voice-second",
            },
        )
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"]["code"], "slot_unavailable")
        self.assertIn("alternatives", conflict)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_unknown_tool_fails_safely(self):
        response = run_tool("prescribe_medicine", {})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "unknown_tool")
