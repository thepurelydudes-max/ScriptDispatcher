from __future__ import annotations

from datetime import datetime, timedelta

from .models import ScheduleConfig


WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Неверное время: {value}")
    hour, minute = int(parts[0]), int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Неверное время: {value}")
    return hour, minute


def normalize_times(values: list[str]) -> list[str]:
    result = set()
    for value in values:
        hour, minute = parse_hhmm(value)
        result.add(f"{hour:02d}:{minute:02d}")
    return sorted(result)


def schedule_summary(schedule: ScheduleConfig) -> str:
    if schedule.mode == "manual":
        return "Вручную"
    days = sorted(set(schedule.weekdays))
    if days == [0, 1, 2, 3, 4]:
        day_text = "Пн–Пт"
    elif days == list(range(7)):
        day_text = "Ежедневно"
    else:
        day_text = ", ".join(WEEKDAY_NAMES[x] for x in days if 0 <= x <= 6)
    if schedule.mode == "times":
        return f"{day_text} {'; '.join(normalize_times(schedule.times))}" if schedule.times else day_text
    return (
        f"{day_text}, каждые {schedule.interval_minutes} мин. "
        f"{schedule.window_start}–{schedule.window_end}"
    )


def occurrence_key(schedule: ScheduleConfig, now: datetime) -> str | None:
    if schedule.mode == "manual" or now.weekday() not in schedule.weekdays:
        return None
    minute_now = now.replace(second=0, microsecond=0)
    hhmm = minute_now.strftime("%H:%M")
    if schedule.mode == "times":
        if hhmm in normalize_times(schedule.times):
            return minute_now.isoformat(timespec="minutes")
        return None
    start_h, start_m = parse_hhmm(schedule.window_start)
    end_h, end_m = parse_hhmm(schedule.window_end)
    start = minute_now.replace(hour=start_h, minute=start_m)
    end = minute_now.replace(hour=end_h, minute=end_m)
    if not start <= minute_now <= end:
        return None
    delta_minutes = int((minute_now - start).total_seconds() // 60)
    if delta_minutes % max(1, schedule.interval_minutes) == 0:
        return minute_now.isoformat(timespec="minutes")
    return None


def next_run(schedule: ScheduleConfig, now: datetime | None = None) -> datetime | None:
    return _adjacent_run(schedule, now or datetime.now(), 1)


def previous_run(schedule: ScheduleConfig, now: datetime | None = None) -> datetime | None:
    return _adjacent_run(schedule, now or datetime.now(), -1)


def _adjacent_run(schedule: ScheduleConfig, now: datetime, direction: int) -> datetime | None:
    """Find an occurrence within eight days without walking every minute.

    Match occurrence_key's wall-clock semantics, including excluding the entire
    current minute and treating an overnight interval window as empty.
    """
    if schedule.mode == "manual" or not schedule.weekdays:
        return None
    now = now.replace(second=0, microsecond=0)
    weekdays = set(schedule.weekdays)
    if not weekdays.intersection(range(7)):
        return None
    boundary = now + timedelta(days=8 * direction)
    if schedule.mode == "times":
        times = normalize_times(schedule.times)
        if not times:
            return None
        minutes = [int(value[:2]) * 60 + int(value[3:]) for value in times]
        if direction < 0:
            minutes.reverse()
    else:
        start_h, start_m = parse_hhmm(schedule.window_start)
        end_h, end_m = parse_hhmm(schedule.window_end)
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        step = max(1, schedule.interval_minutes)
        if start > end:
            return None
    for offset in range(9):
        day = now + timedelta(days=offset * direction)
        if day.weekday() not in weekdays:
            continue
        if schedule.mode == "times":
            candidates = minutes
        else:
            current = now.hour * 60 + now.minute
            if direction > 0:
                earliest = max(start, current + 1) if offset == 0 else start
                candidate_minute = start + max(0, (earliest - start + step - 1) // step) * step
            else:
                latest = min(end, current - 1) if offset == 0 else end
                candidate_minute = start + ((latest - start) // step) * step
            candidates = [candidate_minute] if start <= candidate_minute <= end else []
        for minute in candidates:
            candidate = day.replace(hour=minute // 60, minute=minute % 60)
            if direction > 0 and now < candidate <= boundary:
                return candidate
            if direction < 0 and boundary <= candidate < now:
                return candidate
    return None
