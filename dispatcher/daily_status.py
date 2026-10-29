"""Status of scheduled jobs based on their latest persisted run and scheduler day."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


def started_on_day(started_at: str, day: date, timezone: str) -> bool:
    """History timestamps are stored in the machine's local time, not scheduler time.

    Compare in the scheduler's time zone. Naive timestamps from older history
    are interpreted as the machine's local time for backward compatibility.
    """
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.astimezone()
        try:
            desired_zone = ZoneInfo(timezone)
        except (KeyError, ValueError):
            # Match MainWindow._scheduler_now: invalid TZ uses local clock.
            return started.astimezone().date() == day
        return started.astimezone(desired_zone).date() == day
    except (ValueError, TypeError, OverflowError):
        return False


def scheduled_status(last, day: date, timezone: str) -> str:
    """Today: show last result; new day without a run: waiting."""
    if last is None or not started_on_day(last.started_at, day, timezone):
        return "waiting"
    if last.status in {"success", "error", "cancelled", "skipped"}:
        return last.status
    # Jobs active in the DB after an interrupted process aren't really running.
    return "waiting"
