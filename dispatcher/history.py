from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .storage import data_dir


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

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

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

    def recent(self, limit: int = 200) -> list[HistoryRecord]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [HistoryRecord(**dict(row)) for row in rows]

    def last_for_task(self, task_id: str) -> HistoryRecord | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return HistoryRecord(**dict(row)) if row else None
