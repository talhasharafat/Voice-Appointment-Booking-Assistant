from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings

from clinic.models import Doctor, Specialty, Weekday, WorkingHours


TZ = ZoneInfo("Asia/Karachi")


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

    def test_doctors_list_table(self):
        response = self.client.get("/doctors/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dr. Amina Khan")
        self.assertContains(response, "General Practice")
        self.assertContains(response, 'href="/doctors/{}/"'.format(self.doctor.id))

    def test_doctor_detail_shows_available_bookings(self):
        response = self.client.get("/doctors/{}/".format(self.doctor.id))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available bookings")
        self.assertContains(response, "Dr. Amina Khan")
