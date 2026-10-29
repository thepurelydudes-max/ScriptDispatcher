from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dispatcher.models import ScheduleConfig
from dispatcher import schedule_engine


class ScheduleLookupTests(unittest.TestCase):
    def test_exact_minute_is_excluded_in_both_directions(self):
        schedule = ScheduleConfig(mode="times", weekdays=list(range(7)), times=["09:00", "12:00", "15:00"])
        now = datetime(2026, 10, 29, 12, 0, 59)
        self.assertEqual(schedule_engine.next_run(schedule, now), datetime(2026, 10, 29, 15))
        self.assertEqual(schedule_engine.previous_run(schedule, now), datetime(2026, 10, 29, 9))

    def test_weekly_rollover(self):
        schedule = ScheduleConfig(mode="times", weekdays=[0], times=["09:00"])
        now = datetime(2026, 10, 26, 9, 1)
        self.assertEqual(schedule_engine.next_run(schedule, now), datetime(2026, 11, 2, 9))
        self.assertEqual(schedule_engine.previous_run(schedule, now), datetime(2026, 10, 26, 9))
        self.assertEqual(schedule_engine.previous_run(schedule, datetime(2026, 10, 26, 9)), datetime(2026, 10, 19, 9))

    def test_unsorted_duplicate_times_are_normalized(self):
        schedule = ScheduleConfig(mode="times", weekdays=list(range(7)), times=["12:00", "9:00", "09:00"])
        self.assertEqual(schedule_engine.next_run(schedule, datetime(2026, 10, 29, 8)), datetime(2026, 10, 29, 9))
        self.assertEqual(schedule_engine.previous_run(schedule, datetime(2026, 10, 29, 13)), datetime(2026, 10, 29, 12))

    def test_empty_and_manual_schedules_have_no_occurrence(self):
        for schedule in (
            ScheduleConfig(),
            ScheduleConfig(mode="times", weekdays=[]),
            ScheduleConfig(mode="times", times=[]),
            ScheduleConfig(mode="interval", weekdays=[]),
        ):
            with self.subTest(schedule=schedule):
                self.assertIsNone(schedule_engine.next_run(schedule, datetime(2026, 10, 29)))
                self.assertIsNone(schedule_engine.previous_run(schedule, datetime(2026, 10, 29)))

    def test_interval_edges_and_non_divisible_window(self):
        schedule = ScheduleConfig(mode="interval", weekdays=list(range(7)), interval_minutes=40, window_start="08:00", window_end="09:00")
        self.assertEqual(schedule_engine.next_run(schedule, datetime(2026, 10, 29, 8, 1)), datetime(2026, 10, 29, 8, 40))
        self.assertEqual(schedule_engine.next_run(schedule, datetime(2026, 10, 29, 8, 40)), datetime(2026, 10, 30, 8))
        self.assertEqual(schedule_engine.previous_run(schedule, datetime(2026, 10, 29, 9, 1)), datetime(2026, 10, 29, 8, 40))
        schedule.interval_minutes = 30
        self.assertEqual(schedule_engine.next_run(schedule, datetime(2026, 10, 29, 8, 31)), datetime(2026, 10, 29, 9))
        self.assertEqual(schedule_engine.previous_run(schedule, datetime(2026, 10, 29, 9, 1)), datetime(2026, 10, 29, 9))

    def test_interval_larger_than_window_has_one_daily_start(self):
        schedule = ScheduleConfig(mode="interval", weekdays=list(range(7)), interval_minutes=3000, window_start="08:00", window_end="09:00")
        self.assertEqual(schedule_engine.next_run(schedule, datetime(2026, 10, 29, 8)), datetime(2026, 10, 30, 8))
        self.assertEqual(schedule_engine.previous_run(schedule, datetime(2026, 10, 29, 8)), datetime(2026, 10, 28, 8))

    def test_overnight_window_remains_unsupported(self):
        schedule = ScheduleConfig(mode="interval", weekdays=list(range(7)), window_start="19:00", window_end="08:00")
        self.assertIsNone(schedule_engine.next_run(schedule, datetime(2026, 10, 29)))
        self.assertIsNone(schedule_engine.previous_run(schedule, datetime(2026, 10, 29)))

    def test_midnight_and_year_rollover(self):
        schedule = ScheduleConfig(mode="times", weekdays=list(range(7)), times=["00:00", "23:59"])
        self.assertEqual(schedule_engine.next_run(schedule, datetime(2026, 12, 31, 23, 59)), datetime(2027, 1, 1))
        self.assertEqual(schedule_engine.previous_run(schedule, datetime(2027, 1, 1)), datetime(2026, 12, 31, 23, 59))

    def test_aware_input_preserves_timezone(self):
        zone = ZoneInfo("Europe/Moscow")
        schedule = ScheduleConfig(mode="times", weekdays=list(range(7)), times=["09:00"])
        self.assertEqual(schedule_engine.next_run(schedule, datetime(2026, 10, 29, 8, tzinfo=zone)), datetime(2026, 10, 29, 9, tzinfo=zone))
        self.assertEqual(schedule_engine.previous_run(schedule, datetime(2026, 10, 29, 10, tzinfo=zone)), datetime(2026, 10, 29, 9, tzinfo=zone))

    def test_weekly_lookup_does_not_reparse_schedule_for_each_minute(self):
        # A week-long gap must not repeatedly parse the same stored configuration
        # thousands of times on the GUI thread. Allow up to one parse per day.
        schedule = ScheduleConfig(mode="times", weekdays=list(range(7)), times=["09:00"])
        schedule.weekdays = [0]
        for lookup in (schedule_engine.next_run, schedule_engine.previous_run):
            with self.subTest(lookup=lookup.__name__):
                with patch.object(schedule_engine, "normalize_times", wraps=schedule_engine.normalize_times) as normalize:
                    lookup(schedule, datetime(2026, 10, 26, 9))
                self.assertLessEqual(normalize.call_count, 9)


if __name__ == "__main__":
    unittest.main()
