from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QDate, QEvent
from PySide6.QtWidgets import QApplication

from dispatcher.history import HistoryStore
from dispatcher.ui.dialogs import HistoryDialog


@contextmanager
def local_timezone(name):
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


class HistoryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = HistoryStore(Path(self.temp.name) / "history.sqlite3")

    def add_run(self, task_id="a", stamp="2026-10-29T12:00:00+03:00", status="success", **kwargs):
        with self.store._connect() as db:
            cursor = db.execute(
                "INSERT INTO runs(task_id, task_name, started_at, finished_at, status, message, log_path) "
                "VALUES(?,?,?,?,?,?,?)",
                (task_id, kwargs.get("task_name", task_id), stamp, stamp, status,
                 kwargs.get("message", ""), kwargs.get("log_path", "")),
            )
            return cursor.lastrowid


class HistoryStoreTests(HistoryFixture, unittest.TestCase):
    def test_connection_context_commits_and_closes_even_while_referenced(self):
        run_id = self.add_run(message="before")
        with self.store._connect() as db:
            db.execute("UPDATE runs SET message=? WHERE id=?", ("committed", run_id))
        self.assertEqual(self.store.last_for_task("a").message, "committed")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_connection_context_rolls_back_and_closes_after_an_exception(self):
        run_id = self.add_run(message="original")
        with self.assertRaisesRegex(RuntimeError, "abort transaction"):
            with self.store._connect() as db:
                db.execute("UPDATE runs SET message=? WHERE id=?", ("discarded", run_id))
                raise RuntimeError("abort transaction")
        self.assertEqual(self.store.last_for_task("a").message, "original")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_task_and_status_filters_apply_before_limit_and_bind_literal_input(self):
        task_id = "selected' OR 1=1 --"
        first = self.add_run(task_id, status="error")
        second = self.add_run(task_id, status="error")
        for _ in range(220):
            self.add_run("other", status="error")
            self.add_run(task_id, status="success")
        rows = self.store.recent(2, task_id=task_id, status="error")
        self.assertEqual([row.id for row in rows], [second, first])
        self.assertEqual(self.store.recent(task_id="missing"), [])
        self.assertEqual(len(self.store.recent()), 200)

    def test_scheduler_dates_are_inclusive_for_aware_midnight_before_limit(self):
        self.add_run(stamp="2026-10-28T20:59:59+00:00")
        start = self.add_run(stamp="2026-10-28T21:00:00+00:00")
        end = self.add_run(stamp="2026-10-29T23:59:59.999999+03:00")
        for _ in range(220):
            self.add_run(stamp="2026-10-30T00:00:00+03:00")
        rows = self.store.recent(2, date_from="2026-10-29", date_to="2026-10-29",
                                 timezone="Europe/Moscow")
        self.assertEqual([row.id for row in rows], [end, start])

    @unittest.skipUnless(hasattr(time, "tzset"), "Changing the OS-local timezone requires tzset")
    def test_old_naive_history_uses_os_local_time_at_scheduler_day_boundaries(self):
        with local_timezone("America/New_York"):
            self.add_run(stamp="2026-10-28T16:59:59")
            start = self.add_run(stamp="2026-10-28T17:00:00")
            end = self.add_run(stamp="2026-10-29T16:59:59.999999")
            self.add_run(stamp="2026-10-29T17:00:00")
            rows = self.store.recent(date_from="2026-10-29", date_to="2026-10-29",
                                     timezone="Europe/Moscow")
            self.assertEqual([row.id for row in rows], [end, start])

    def test_date_bounds_can_be_used_independently_and_ignore_malformed_timestamps(self):
        old = self.add_run(stamp="2026-10-28T12:00:00+03:00")
        new = self.add_run(stamp="2026-10-30T12:00:00+03:00")
        malformed = self.add_run(stamp="legacy unknown time")
        self.assertEqual([row.id for row in self.store.recent(date_from="2026-10-29")], [new])
        self.assertEqual([row.id for row in self.store.recent(date_to="2026-10-29")], [old])
        self.assertIn(malformed, [row.id for row in self.store.recent()])

    def test_invalid_date_input_does_not_change_history(self):
        self.add_run()
        with self.assertRaises(ValueError):
            self.store.recent(date_from="2026-10-29' OR 1=1 --")
        self.assertEqual(len(self.store.recent()), 1)

    def test_latest_by_task_uses_latest_record_id_and_preserves_all_tasks(self):
        self.add_run("a", stamp="2026-10-30T12:00:00+03:00")
        b = self.add_run("b", status="error")
        a = self.add_run("a", stamp="2026-10-28T12:00:00+03:00", status="cancelled")
        latest = self.store.latest_by_task()
        self.assertEqual({key: row.id for key, row in latest.items()}, {"a": a, "b": b})
        self.assertEqual(latest["a"].status, "cancelled")


class HistoryDialogTests(HistoryFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.TemporaryDirectory()
        cls.cache_environment = patch.dict(os.environ, {"XDG_CACHE_HOME": cls.cache.name})
        cls.cache_environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        cls.cache_environment.stop()
        cls.cache.cleanup()

    def setUp(self):
        super().setUp()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def dialog(self, source=None, **kwargs):
        dialog = HistoryDialog(source if source is not None else self.store, **kwargs)
        self.dialogs.append(dialog)
        return dialog

    def test_selected_task_is_default_and_task_filter_can_show_all(self):
        self.add_run("a", task_name="Задача А")
        self.add_run("b", task_name="Задача Б")
        dialog = self.dialog(task_id="a", task_names={"a": "Новое имя А", "empty": "Без запусков"})
        self.assertEqual(dialog.task_filter.currentData(), "a")
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "Задача А")
        self.assertGreaterEqual(dialog.task_filter.findData("empty"), 0)
        dialog.task_filter.setCurrentIndex(dialog.task_filter.findData(None))
        self.assertEqual(dialog.table.rowCount(), 2)

    def test_selected_task_uses_its_history_name_without_a_name_mapping(self):
        self.add_run("a", task_name="Утренняя программа")
        dialog = self.dialog(task_id="a")
        self.assertEqual(dialog.task_filter.currentText(), "Утренняя программа")

    def test_status_and_optional_date_controls_filter_store_and_refresh(self):
        self.add_run("a", status="error", stamp="2026-10-28T21:00:00+00:00")
        self.add_run("a", status="success")
        self.add_run("a", status="error", stamp="2026-10-30T00:00:00+03:00")
        dialog = self.dialog(task_id="a", timezone="Europe/Moscow")
        dialog.status_filter.setCurrentIndex(dialog.status_filter.findData("error"))
        dialog.date_from.setDate(QDate(2026, 10, 29))
        dialog.date_to.setDate(QDate(2026, 10, 29))
        dialog.date_from_enabled.setChecked(True)
        dialog.date_to_enabled.setChecked(True)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 1).text(), "29.10.2026 00:00")
        self.assertEqual(dialog.table.item(0, 3).text(), "Ошибка")
        self.add_run("a", status="error")
        dialog.refresh_button.click()
        self.assertEqual(dialog.table.rowCount(), 2)

    def test_legacy_record_lists_filter_before_limit(self):
        self.add_run("a", status="error", message="Полное сообщение\n" + "Детали " * 200)
        for _ in range(210):
            self.add_run("b")
        dialog = self.dialog(self.store.recent(500), task_id="a")
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.message_detail.toPlainText(), "Полное сообщение\n" + "Детали " * 200)
        self.assertFalse(dialog.open_log_button.isEnabled())

    def test_log_opening_requires_explicit_action_and_uses_only_selected_local_file(self):
        path = Path(self.temp.name) / "run.log"
        path.write_text("local log", encoding="utf-8")
        self.add_run("a", log_path=str(path))
        self.add_run("b", message="No log")
        with patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True) as opener:
            dialog = self.dialog()
            self.assertFalse(dialog.open_log_button.isEnabled())
            dialog.table.selectRow(1)
            self.assertTrue(dialog.open_log_button.isEnabled())
            opener.assert_not_called()
            dialog.open_log_button.click()
            opener.assert_called_once()
            opened_path = Path(opener.call_args.args[0].toLocalFile())
            self.assertEqual(opened_path.resolve(), path.resolve())

    def test_network_or_url_log_paths_are_not_opened(self):
        for path in ("https://example.invalid/log", r"\\server\private\run.log", "//server/private/run.log"):
            with self.subTest(path=path):
                self.add_run(log_path=path)
                dialog = self.dialog()
                self.assertFalse(dialog.open_log_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
