from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QProgressBar, QToolButton

from dispatcher.history import HistoryStore
from dispatcher.models import AppConfig, TaskDefinition
from dispatcher.ui.main_window import MainWindow


class InterfaceOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = patch("dispatcher.storage.application_root", return_value=self.root)
        self.storage.start()
        self.addCleanup(self.storage.stop)
        self.history = HistoryStore(self.root / "history.sqlite3")
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.scheduler_timer.stop()
            window.scheduler_enabled = False
            window.executor.shutdown(wait=True, cancel_futures=True)
            window.force_exit = True
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def window(self, tasks=None):
        if tasks is None:
            tasks = [TaskDefinition(id="a", name="Alpha", module="external", enabled=True),
                     TaskDefinition(id="b", name="Beta", module="external", enabled=False)]
        with patch.object(MainWindow, "create_tray", lambda w: setattr(w, "tray", Mock())), patch(
            "dispatcher.ui.main_window.QTimer.singleShot"
        ):
            window = MainWindow(AppConfig(settings={"timezone": "Europe/Moscow"}, tasks=tasks), {}, self.history)
        window.scheduler_timer.stop()
        window.scheduler_enabled = False
        self.windows.append(window)
        return window

    def row(self, window, task_id):
        return next(r for r in range(window.table.rowCount())
                    if window.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == task_id)

    def test_inline_run_stays_bound_to_task_after_sorting(self):
        window = self.window()
        window.table.setSortingEnabled(True)
        window.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        row = self.row(window, "b")
        controls = window.table.cellWidget(row, 7)
        self.assertIsNotNone(controls, "Each task needs inline actions")
        with patch.object(window, "start_task") as start:
            controls.findChild(QToolButton, "runTaskButton").click()
        self.assertEqual(start.call_args.args[0].id, "b")

    def test_inline_toggle_persists_only_the_target_task(self):
        window = self.window()
        controls = window.table.cellWidget(self.row(window, "b"), 7)
        self.assertIsNotNone(controls)
        controls.findChild(QCheckBox, "enableTaskCheck").click()
        saved = json.loads((self.root / "data" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual({t["id"]: t["enabled"] for t in saved["tasks"]}, {"a": True, "b": True})

    def test_pending_task_has_queued_status_and_can_be_stopped(self):
        window = self.window()
        task = window.config.tasks[0]
        window.runner.queue(task.id)
        self.addCleanup(window.runner.unqueue, task.id)
        self.assertEqual(window._task_status(task), "queued")
        window.refresh_table()
        controls = window.table.cellWidget(self.row(window, task.id), 7)
        self.assertFalse(controls.findChild(QToolButton, "runTaskButton").isEnabled())
        self.assertTrue(controls.findChild(QToolButton, "stopTaskButton").isEnabled())

    def test_row_progress_and_current_step_are_visible(self):
        window = self.window()
        window.on_task_progress("a", 45, "Обработка второго файла")
        cell = window.table.cellWidget(self.row(window, "a"), 4)
        self.assertIsNotNone(cell)
        progress = cell.findChild(QProgressBar, "rowProgress")
        self.assertEqual(progress.value(), 45)
        self.assertFalse(progress.isHidden())
        self.assertIn("Обработка второго файла", cell.toolTip())

    def test_details_show_persisted_result_and_log_availability(self):
        log = self.root / "run.log"
        log.write_text("error", encoding="utf-8")
        run_id = self.history.start("a", "Alpha", str(log))
        self.history.finish(run_id, "error", "Недоступен каталог записи")
        window = self.window()
        window.table.selectRow(self.row(window, "a"))
        self.assertTrue(hasattr(window, "detail_message"))
        self.assertEqual(window.detail_message.text(), "Недоступен каталог записи")
        self.assertTrue(window.log_button.isEnabled())

    def test_filtering_and_progress_do_not_requery_history(self):
        window = self.window()
        with patch.object(self.history, "last_for_task", side_effect=AssertionError("per-row database read")):
            window.search.setText("Alpha")
            window.apply_filters()
            window.on_task_progress("a", 20, "Чтение")
        self.assertFalse(window.table.isRowHidden(self.row(window, "a")))
        self.assertTrue(window.table.isRowHidden(self.row(window, "b")))

    def test_hidden_selection_cannot_leave_actions_enabled(self):
        window = self.window()
        window.table.selectRow(self.row(window, "b"))
        window.search.setText("Alpha")
        window.apply_filters()
        self.assertIsNone(window.selected_task())
        self.assertFalse(window.run_action.isEnabled())

    def test_view_roundtrip_preserves_width_sort_and_splitter(self):
        first = self.window()
        self.assertTrue(hasattr(first, "splitter"))
        first.table.setColumnWidth(0, 280)
        first.table.setSortingEnabled(True)
        first.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        first.splitter.setSizes([500, 260])
        first.force_exit = True
        first.close()
        second = self.window()
        self.assertEqual(second.table.columnWidth(0), 280)
        self.assertTrue(second.table.isSortingEnabled())
        self.assertEqual(second.table.item(0, 0).text(), "Beta")
        state = self.root / "data" / "ui_state.json"
        self.assertTrue(state.is_file())
        self.assertFalse((self.root / "data" / "config.json").exists(), "View-only changes must not rewrite tasks")


if __name__ == "__main__":
    unittest.main()
