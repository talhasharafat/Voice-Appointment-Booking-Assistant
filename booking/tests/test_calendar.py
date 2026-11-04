from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from booking.calendar import available_slots, is_slot_bookable, working_slot_starts
from booking.models import Appointment
from clinic.models import Doctor, Patient, Specialty, Unavailability, Weekday, WorkingHours


TZ = ZoneInfo("Asia/Karachi")
MONDAY = datetime(2026, 11, 9, tzinfo=TZ).date()  # a Monday


@override_settings(
    CLINIC_TIMEZONE="Asia/Karachi",
    TIME_ZONE="Asia/Karachi",
    APPOINTMENT_DURATION_MINUTES=30,
)
class CalendarEngineTests(TestCase):
    def setUp(self):
        self.specialty = Specialty.objects.create(
            name="General Practice",
            description="Cough, fever, and general illness.",
        )
        self.doctor = Doctor.objects.create(
            name="Dr. Test",
            specialty=self.specialty,
            is_active=True,
        )
        WorkingHours.objects.create(
            doctor=self.doctor,
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.patient = Patient.objects.create(name="Test Patient", contact_number="03000000000")
        self.morning = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)

    def _at(self, hour, minute=0):
        return datetime.combine(MONDAY, time(hour, minute), tzinfo=TZ)

    def test_working_hours_split_into_fixed_slots(self):
        starts = working_slot_starts(self.doctor, MONDAY)
        self.assertEqual(
            starts,
            [
                self._at(9, 0),
                self._at(9, 30),
                self._at(10, 0),
                self._at(10, 30),
                self._at(11, 0),
                self._at(11, 30),
            ],
        )

    def test_day_without_working_hours_has_no_slots(self):
        tuesday = MONDAY + timedelta(days=1)
        self.assertEqual(working_slot_starts(self.doctor, tuesday), [])
        self.assertEqual(available_slots(self.doctor, tuesday, now=self.morning), [])

    def test_confirmed_appointment_blocks_that_slot_only(self):
        start = self._at(9, 30)
        Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            reason="Fever",
            status=Appointment.Status.CONFIRMED,
            booking_reference="CAL-1",
            idempotency_key="cal-1",
        )
        open_starts = [slot.starts_at for slot in available_slots(self.doctor, MONDAY, now=self.morning)]
        self.assertNotIn(start, open_starts)
        self.assertIn(self._at(9, 0), open_starts)
        self.assertIn(self._at(10, 0), open_starts)

    def test_cancelled_appointment_does_not_block(self):
        start = self._at(9, 30)
        Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            reason="Fever",
            status=Appointment.Status.CANCELLED,
            booking_reference="CAL-2",
            idempotency_key="cal-2",
        )
        open_starts = [slot.starts_at for slot in available_slots(self.doctor, MONDAY, now=self.morning)]
        self.assertIn(start, open_starts)

    def test_unavailability_blocks_overlapping_slots(self):
        Unavailability.objects.create(
            doctor=self.doctor,
            starts_at=self._at(11, 0),
            ends_at=self._at(12, 0),
            reason="Meeting",
        )
        open_starts = [slot.starts_at for slot in available_slots(self.doctor, MONDAY, now=self.morning)]
        self.assertEqual(
            open_starts,
            [self._at(9, 0), self._at(9, 30), self._at(10, 0), self._at(10, 30)],
        )

    def test_past_slots_are_not_offered(self):
        now = self._at(10, 5)
        open_starts = [slot.starts_at for slot in available_slots(self.doctor, MONDAY, now=now)]
        self.assertEqual(open_starts, [self._at(10, 30), self._at(11, 0), self._at(11, 30)])

    def test_off_grid_time_is_not_bookable(self):
        self.assertFalse(is_slot_bookable(self.doctor, self._at(9, 15), now=self.morning))
        self.assertTrue(is_slot_bookable(self.doctor, self._at(9, 0), now=self.morning))

    def test_inactive_doctor_has_no_bookable_slots(self):
        self.doctor.is_active = False
        self.doctor.save(update_fields=["is_active"])
        self.assertEqual(available_slots(self.doctor, MONDAY, now=self.morning), [])
        self.assertFalse(is_slot_bookable(self.doctor, self._at(9, 0), now=self.morning))

    def test_slots_use_clinic_timezone(self):
        slot = available_slots(self.doctor, MONDAY, now=self.morning)[0]
        self.assertEqual(slot.starts_at.tzinfo, TZ)
        self.assertEqual(slot.ends_at - slot.starts_at, timedelta(minutes=30))
        self.assertGreater(slot.starts_at, timezone.now().replace(year=2020))
