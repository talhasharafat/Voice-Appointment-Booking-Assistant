from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from voice.formatting import format_slot_label, slots_list_message


@override_settings(CLINIC_TIMEZONE="Asia/Karachi", TIME_ZONE="Asia/Karachi")
class FormattingTests(SimpleTestCase):
    def test_slot_label_is_patient_friendly(self):
        moment = datetime(2026, 11, 10, 9, 0, tzinfo=ZoneInfo("Asia/Karachi"))
        label = format_slot_label(moment)
        self.assertEqual(label, "Tuesday, 10 November 2026 at 9:00 AM")
        self.assertNotIn("T09:00:00", label)
        self.assertNotIn("+05:00", label)

    def test_slots_list_message_uses_numbered_lines(self):
        message = slots_list_message(
            [{"starts_at": "2026-11-10T09:00:00+05:00"}],
            intro="Here are other open times:",
        )
        self.assertIn("Here are other open times:", message)
        self.assertIn("1. Tuesday, 10 November 2026 at 9:00 AM", message)
        self.assertIn("\n", message)
