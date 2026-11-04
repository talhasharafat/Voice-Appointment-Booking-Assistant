from datetime import time, timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from booking.models import Appointment
from clinic.models import Doctor, Patient, Specialty, Unavailability, Weekday, WorkingHours


@override_settings(CLINIC_TIMEZONE="Asia/Karachi", TIME_ZONE="Asia/Karachi", APPOINTMENT_DURATION_MINUTES=30)
class DoctorsDirectoryTests(TestCase):
    def setUp(self):
        self.client = Client()
        specialty = Specialty.objects.create(
            name="General Practice",
            description="Cough and fever.",
        )
        self.doctor = Doctor.objects.create(
            name="Dr. Amina Khan",
            specialty=specialty,
            is_active=True,
        )
        WorkingHours.objects.create(
            doctor=self.doctor,
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        self.patient = Patient.objects.create(name="Test Patient", contact_number="03001110000")
        start = timezone.now() + timedelta(days=1)
        start = start.replace(hour=10, minute=0, second=0, microsecond=0)
        Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            reason="Fever",
            status=Appointment.Status.CONFIRMED,
            booking_reference="CAL1",
            idempotency_key="cal-1",
        )
        Unavailability.objects.create(
            doctor=self.doctor,
            starts_at=start + timedelta(days=1),
            ends_at=start + timedelta(days=1, hours=2),
            reason="Clinic meeting",
        )

    def test_doctors_list_table(self):
        response = self.client.get("/doctors/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dr. Amina Khan")
        self.assertContains(response, "General Practice")
        self.assertContains(response, 'href="/doctors/{}/"'.format(self.doctor.id))

    def test_doctor_detail_shows_calendar_events(self):
        response = self.client.get("/doctors/{}/".format(self.doctor.id))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule calendar")
        self.assertContains(response, "Confirmed appointment")
        self.assertContains(response, "Unavailable period")
        self.assertContains(response, "CAL1")
        self.assertContains(response, "Clinic meeting")
        self.assertNotContains(response, "Available bookings")
