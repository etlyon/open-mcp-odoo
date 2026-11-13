import unittest

from open_connectors.reminder import reminder_text

NOW = 1_800_000_000


class Reminder(unittest.TestCase):
    def test_no_reminder_when_far_or_never(self):
        self.assertIsNone(reminder_text("odoo", {"expires_at": None, "expiry_source": "odoo"}, NOW))
        self.assertIsNone(reminder_text("odoo", {"expires_at": NOW + 30 * 86400, "expiry_source": "odoo"}, NOW))

    def test_reminder_in_last_week(self):
        text = reminder_text("odoo", {"expires_at": NOW + 3 * 86400 + 60, "expiry_source": "odoo"}, NOW)
        self.assertIn("in 3 days", text)
        self.assertNotIn("estimated", text)

    def test_estimated_is_said(self):
        text = reminder_text("odoo", {"expires_at": NOW + 86400, "expiry_source": "estimated"}, NOW)
        self.assertIn("(estimated)", text)

    def test_after_the_end(self):
        self.assertIn("has ended", reminder_text("odoo", {"expires_at": NOW - 10, "expiry_source": "odoo"}, NOW))


if __name__ == "__main__":
    unittest.main()


class Dates(unittest.TestCase):
    def test_no_year_this_year(self):
        from open_connectors.pages.flow import human_date
        import datetime as dt
        now = dt.datetime(2026, 11, 9, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(human_date(int(dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc).timestamp()), "en", now), "14 Nov")
        self.assertEqual(human_date(int(dt.datetime(2027, 1, 8, tzinfo=dt.timezone.utc).timestamp()), "en", now), "8 Jan 2027")
