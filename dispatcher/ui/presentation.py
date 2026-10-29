"""Shared display values for the task table and history."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


STATUS_LABELS = {
    "ready": "Готово к запуску", "queued": "В очереди",
    "running": "Выполняется", "stopping": "Останавливается",
    "waiting": "Ожидает расписания", "success": "Выполнено",
    "error": "Ошибка", "cancelled": "Остановлено",
    "disabled": "Отключено", "skipped": "Пропущено",
}

STATUS_COLORS = {
    "ready": "#72bca7", "queued": "#bd9ef0", "running": "#66b2ff",
    "stopping": "#f1b36b", "waiting": "#e7c66b", "success": "#65d0a0",
    "error": "#ff8585", "cancelled": "#f1b36b", "disabled": "#93a3b3",
    "skipped": "#93a3b3",
}


def format_history_time(value: str | None, timezone: str = "Europe/Moscow") -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
        try:
            zone = ZoneInfo(timezone)
        except (KeyError, ValueError):
            zone = None
        return moment.astimezone(zone).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError, OverflowError):
        return str(value).replace("T", " ")[:19]
