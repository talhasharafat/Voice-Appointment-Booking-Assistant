from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from voice.formatting import format_clock, format_slot_label, slots_list_message


@override_settings(CLINIC_TIMEZONE="Asia/Karachi", TIME_ZONE="Asia/Karachi")
class FormattingTests(SimpleTestCase):
    def test_slot_label_uses_zero_padded_hour(self):
        moment = datetime(2026, 11, 10, 9, 0, tzinfo=ZoneInfo("Asia/Karachi"))
        label = format_slot_label(moment)
        self.assertEqual(label, "Tuesday, 10 November 2026 at 09:00 AM")
        self.assertNotIn(" at 9:00 ", label)
        self.assertNotIn("T09:00:00", label)

    def test_format_clock_zero_pads(self):
        self.assertEqual(format_clock(time(1, 5)), "01:05 AM")
        self.assertEqual(format_clock(time(13, 0)), "01:00 PM")

    def test_slots_list_message_uses_numbered_lines(self):
        message = slots_list_message(
            [{"starts_at": "2026-11-10T09:00:00+05:00"}],
            intro="Here are other open times:",
        )
        self.assertIn("1. Tuesday, 10 November 2026 at 09:00 AM", message)
