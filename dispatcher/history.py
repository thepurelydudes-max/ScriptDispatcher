from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import data_dir


def _history_date(value: str, timezone: str) -> str | None:
    """Read old OS-local timestamps and newer aware values in scheduler time."""
    try:
        moment = datetime.fromisoformat(value)
        try:
            zone = ZoneInfo(timezone)
        except (KeyError, ValueError):
            zone = None
        return moment.astimezone(zone).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass(slots=True)
class HistoryRecord:
    id: int
    task_id: str
    task_name: str
    started_at: str
    finished_at: str | None
    status: str
    message: str
    log_path: str


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "history.sqlite3")
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    log_path TEXT NOT NULL DEFAULT ''
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id, id DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, id DESC)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_task_status ON runs(task_id, status, id DESC)"
            )

    def start(self, task_id: str, task_name: str, log_path: str) -> int:
        with self._lock, self._connect() as db:
            cur = db.execute(
                "INSERT INTO runs(task_id, task_name, started_at, status, log_path) VALUES(?,?,?,?,?)",
                (task_id, task_name, datetime.now().isoformat(timespec="seconds"), "running", log_path),
            )
            return int(cur.lastrowid)

    def finish(self, run_id: int, status: str, message: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE runs SET finished_at=?, status=?, message=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), status, message, run_id),
            )

    def recent(
        self,
        limit: int = 200,
        *,
        task_id: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        timezone: str = "Europe/Moscow",
    ) -> list[HistoryRecord]:
        """Return newest matching runs; inclusive date filters precede the limit.

        A SQL date function deliberately interprets naive values as OS-local
        time, matching existing history readers. SQLite's built-in date()
        treats naive values as UTC and would shift scheduler day boundaries.
        """
        clauses = []
        parameters: list[str | int] = []
        for column, value in (("task_id", task_id), ("status", status)):
            if value is not None:
                clauses.append(f"{column}=?")
                parameters.append(value)
        for value, operator in ((date_from, ">="), (date_to, "<=")):
            if value is not None:
                clauses.append(f"history_day(started_at){operator}?")
                parameters.append(date.fromisoformat(value).isoformat())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connect() as db:
            if date_from is not None or date_to is not None:
                db.create_function("history_day", 1, lambda value: _history_date(value, timezone))
            rows = db.execute(
                f"SELECT * FROM runs{where} ORDER BY id DESC LIMIT ?", [*parameters, limit]
            ).fetchall()
        return [HistoryRecord(**dict(row)) for row in rows]

    def latest_by_task(self) -> dict[str, HistoryRecord]:
        """Read the most recently inserted run of every task in one query."""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT runs.* FROM runs JOIN "
                "(SELECT task_id, MAX(id) AS latest_id FROM runs GROUP BY task_id) AS latest "
                "ON runs.id=latest.latest_id"
            ).fetchall()
        return {row["task_id"]: HistoryRecord(**dict(row)) for row in rows}

    def last_for_task(self, task_id: str) -> HistoryRecord | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return HistoryRecord(**dict(row)) if row else None
