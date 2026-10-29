from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date
from unittest.mock import patch

from dispatcher.daily_status import scheduled_status, started_on_day


@dataclass
class Run:
    started_at: str
    status: str


class DailyStatusTests(unittest.TestCase):
    def test_completed_today(self):
        self.assertEqual(scheduled_status(Run("2026-10-29T11:00:00+03:00", "success"), date(2026, 10, 29), "Europe/Moscow"), "success")

    def test_completed_yesterday_becomes_waiting(self):
        run = Run("2026-10-29T11:00:00+03:00", "success")
        self.assertEqual(scheduled_status(run, date(2026, 10, 30), "Europe/Moscow"), "waiting")

    def test_no_history_is_waiting(self):
        self.assertEqual(scheduled_status(None, date(2026, 10, 30), "Europe/Moscow"), "waiting")

    def test_todays_error_remains_visible(self):
        run = Run("2026-10-29T12:00:00+03:00", "error")
        self.assertEqual(scheduled_status(run, date(2026, 10, 29), "Europe/Moscow"), "error")
        self.assertEqual(scheduled_status(run, date(2026, 10, 30), "Europe/Moscow"), "waiting")

    def test_start_date_not_finish_date_matters(self):
        # Jobs started yesterday and finished after midnight were NOT started today.
        self.assertEqual(scheduled_status(Run("2026-10-29T23:59:00+03:00", "success"), date(2026, 10, 30), "Europe/Moscow"), "waiting")

    def test_scheduler_timezone_used(self):
        run = Run("2026-10-29T23:50:00+00:00", "success")
        self.assertEqual(scheduled_status(run, date(2026, 10, 30), "Europe/Moscow"), "success")
        self.assertEqual(scheduled_status(run, date(2026, 10, 29), "Europe/Moscow"), "waiting")

    def test_naive_old_timestamps(self):
        # Old DB records without UTC offsets are interpreted using the machine timezone.
        expected_day = __import__("datetime").datetime.fromisoformat("2026-10-29T12:00:00").astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).date()
        self.assertTrue(started_on_day("2026-10-29T12:00:00", expected_day, "Europe/Moscow"))

    def test_invalid_timezone_uses_machine_local(self):
        day = __import__("datetime").datetime.fromisoformat("2026-10-29T12:00:00").astimezone().date()
        self.assertTrue(started_on_day("2026-10-29T12:00:00", day, "Not/AZone"))

    def test_running_history_after_restart_is_waiting(self):
        self.assertEqual(scheduled_status(Run("2026-10-29T12:00:00+03:00", "running"), date(2026, 10, 29), "Europe/Moscow"), "waiting")


if __name__ == "__main__":
    unittest.main()
