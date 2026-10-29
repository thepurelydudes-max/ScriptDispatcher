from __future__ import annotations

import copy
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from dispatcher.history import HistoryStore
from dispatcher.models import AppConfig, ScheduleConfig, TaskDefinition
from dispatcher.storage import application_root
from dispatcher.task_runner import RunResult
from dispatcher.ui.dialogs import TaskDialog
from dispatcher.ui.main_window import MainWindow


class UiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font_cache = tempfile.TemporaryDirectory()
        cls.cache_environment = patch.dict(os.environ, {"XDG_CACHE_HOME": cls.font_cache.name})
        cls.cache_environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        cls.cache_environment.stop()
        cls.font_cache.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = HistoryStore(Path(self.temp.name) / "history.sqlite3")
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.scheduler_enabled = False
            window.scheduler_timer.stop()
            window.executor.shutdown(wait=True, cancel_futures=True)
            if window.path_worker is not None:
                window.path_worker.join(timeout=2)
                self.assertFalse(window.path_worker.is_alive(), "Path-check worker leaked from test")
            window.tray.hide()
            window.force_exit = True
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def task(self, task_id="a", **kwargs):
        return TaskDefinition(id=task_id, name=task_id, module="external", enabled=True, **kwargs)

    def window(self, tasks):
        config = AppConfig(settings={"timezone": "Europe/Moscow", "max_parallel": 1}, tasks=tasks)
        with patch.object(MainWindow, "create_tray", lambda window: setattr(window, "tray", Mock())), patch(
            "dispatcher.ui.main_window.QTimer.singleShot"
        ):
            window = MainWindow(config, {}, self.history, view_state_path=Path(self.temp.name) / "view.json")
        window.scheduler_enabled = False
        window.scheduler_timer.stop()
        self.windows.append(window)
        return window

    def history_record(self, task, stamp, status="success", finished=True):
        run_id = self.history.start(task.id, task.name, "")
        with self.history._connect() as db:
            db.execute(
                "UPDATE runs SET started_at=?, finished_at=?, status=? WHERE id=?",
                (stamp, stamp if finished else None, status, run_id),
            )

    def wait_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertTrue(predicate(), "Queued UI result did not arrive")

    def test_schedule_only_preserves_legacy_and_unknown_module_parameters(self):
        for cut_mode in ("same", "future-cut-mode"):
            with self.subTest(cut_mode=cut_mode):
                task = TaskDefinition(
                    id="cut", name="  Legacy task  ", module="broadcast.universal_cut", enabled=True,
                    workdir="legacy-workdir",
                    params={"cut_mode": cut_mode, "unknown": {"nested": [1, 2]},
                            "sources": [{"folder": "local", "enabled": True, "extra": "keep"}],
                            "send_radio_archive": False, "send_respublika_archive": True},
                )
                original = copy.deepcopy(task.to_dict())
                dialog = TaskDialog(task, schedule_only=True)
                try:
                    dialog.schedule_editor.mode.setCurrentIndex(dialog.schedule_editor.mode.findData("times"))
                    dialog.schedule_editor.times.setText("12:00")
                    dialog.accept_data()
                    result = dialog.result_task().to_dict()
                    self.assertEqual(result.pop("schedule")["times"], ["12:00"])
                    original.pop("schedule")
                    self.assertEqual(result, original)
                    self.assertEqual(task.params["cut_mode"], cut_mode)
                finally:
                    dialog.close()
                    dialog.deleteLater()

    def test_ready_and_completed_filters_are_distinct(self):
        ready = self.task("ready")
        success = self.task("success", schedule=ScheduleConfig(mode="times", times=["12:00"]))
        self.history_record(success, "2026-10-29T12:00:00+03:00")
        window = self.window([ready, success])
        with patch.object(window, "_scheduler_now", return_value=datetime(2026, 10, 29, 12, 30)):
            window.status_filter.setCurrentIndex(window.status_filter.findData("ready"))
            window.apply_filters()
        self.assertFalse(window.table.isRowHidden(0))
        self.assertTrue(window.table.isRowHidden(1))
        with patch.object(window, "_scheduler_now", return_value=datetime(2026, 10, 29, 12, 30)):
            window.status_filter.setCurrentIndex(window.status_filter.findData("success"))
            window.apply_filters()
        self.assertTrue(window.table.isRowHidden(0))
        self.assertFalse(window.table.isRowHidden(1))

    def test_background_start_keeps_selection_and_tab(self):
        first, second = self.task("first"), self.task("second")
        window = self.window([first, second])
        window.table.selectRow(0)
        window.details_tabs.setCurrentIndex(0)
        with patch.object(window.executor, "submit"):
            window.start_task(second)
        self.assertEqual(window.selected_task().id, first.id)
        self.assertEqual(window.details_tabs.currentIndex(), 0)

    def test_manual_run_opens_its_log(self):
        task = self.task()
        window = self.window([task])
        window.table.selectRow(0)
        with patch.object(window.executor, "submit"):
            window.run_selected()
        self.assertEqual(window.details_tabs.currentIndex(), 1)

    def test_selection_restores_progress_and_other_completion_keeps_it(self):
        first, second = self.task("first"), self.task("second")
        window = self.window([first, second])
        window.table.selectRow(0)
        window.on_task_progress(first.id, 45, "first progress")
        window.on_task_progress(second.id, 12, "second progress")
        window.table.selectRow(1)
        self.assertEqual(window.progress.value(), 12)
        window.table.selectRow(0)
        self.assertEqual(window.progress.value(), 45)
        window.on_task_done(RunResult(second.id, "success", "finished", {}, ""))
        self.assertEqual(window.selected_task().id, first.id)
        self.assertEqual(window.progress.value(), 45)
        self.assertFalse(window.progress.isHidden())
        window.on_task_done(RunResult(first.id, "success", "finished", {}, ""))
        self.assertTrue(window.progress.isHidden())

    def test_universal_sources_and_implicit_cash_are_visible(self):
        task = TaskDefinition(id="cut", name="cut", module="broadcast.universal_cut", params={
            "sources": [{"folder": "source-one"}, {"folder": "disabled", "enabled": False},
                        {"folder": "source-two", "enabled": True}],
        })
        window = self.window([task])
        self.assertEqual(window._primary_path(task), "source-one")
        values = [value for _, value in window._candidate_paths(task)]
        self.assertIn("source-one", values)
        self.assertIn("source-two", values)
        self.assertNotIn("disabled", values)
        self.assertFalse(any(value.startswith("[") for value in values))
        self.assertEqual(window._output_path(task), str(application_root() / "cash"))

    def test_display_path_helpers_do_not_resolve_filesystem_on_gui_thread(self):
        root = Path(self.temp.name)
        tasks = [self.task(params={"output_dir": str(root)}),
                 TaskDefinition(id="music", name="music", module="music.foreign_agents",
                                params={"source_dir": str(root), "block_list": "block.txt"})]
        resolved_threads = []
        original_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            resolved_threads.append(threading.get_ident())
            return original_resolve(path, *args, **kwargs)
        with patch("pathlib.Path.resolve", resolve):
            window = self.window(tasks)
            window.table.selectRow(0)
            window.table.selectRow(1)
        self.assertNotIn(threading.get_ident(), resolved_threads)

    def test_access_check_uses_independent_worker_and_queued_ui_result(self):
        source = Path(self.temp.name) / "source"
        source.mkdir()
        task = TaskDefinition(id="cut", name="cut", module="broadcast.universal_cut",
                              params={"sources": [{"folder": str(source)}]})
        window = self.window([task])
        window.table.selectRow(0)
        release = threading.Event()
        occupied = threading.Event()
        def occupy_task_worker():
            occupied.set()
            release.wait(3)
        window.executor.submit(occupy_task_worker)
        self.assertTrue(occupied.wait(1))
        self.addCleanup(release.set)
        probe_threads = []
        ui_threads = []
        original_exists = Path.exists
        def exists(path):
            probe_threads.append(threading.get_ident())
            return original_exists(path)
        def information(*_args):
            ui_threads.append(threading.get_ident())
        try:
            with patch("dispatcher.ui.main_window.Path.exists", exists), patch(
                "dispatcher.ui.main_window.QMessageBox.information", side_effect=information
            ) as message:
                window.test_selected_paths()
                self.wait_until(lambda: message.called)
                self.assertTrue(probe_threads)
                self.assertTrue(all(thread_id != threading.get_ident() for thread_id in probe_threads))
                self.assertEqual(ui_threads, [threading.get_ident()])
                self.assertIn(str(source), message.call_args.args[2])
        finally:
            release.set()

    def test_access_check_reports_probe_error_and_reenables_button(self):
        task = self.task(params={"program": str(Path(self.temp.name) / "program.py")})
        window = self.window([task])
        window.table.selectRow(0)
        with patch("dispatcher.ui.main_window.Path.exists", side_effect=OSError("probe unavailable")), patch(
            "dispatcher.ui.main_window.QMessageBox.information"
        ) as message:
            window.test_selected_paths()
            self.wait_until(lambda: message.called)
        self.assertIn("probe unavailable", message.call_args.args[2])
        self.assertTrue(window.test_button.isEnabled())

    def test_access_check_resolves_relative_workdir_once(self):
        root = Path(self.temp.name)
        (root / "working").mkdir()
        (root / "working" / "launch.py").write_text("", encoding="utf-8")
        original_cwd = Path.cwd()
        try:
            os.chdir(root)
            window = self.window([self.task(workdir="working", params={"program": "launch.py"})])
            window.table.selectRow(0)
            with patch("dispatcher.ui.main_window.QMessageBox.information") as message:
                window.test_selected_paths()
                self.wait_until(lambda: message.called)
            self.assertIn("✓  workdir: working\n", message.call_args.args[2])
            self.assertIn("✓  program: working/launch.py", message.call_args.args[2].replace("\\", "/"))
        finally:
            os.chdir(original_cwd)

    def test_path_checks_still_work_after_closing_and_reopening_window(self):
        task = self.task(params={"program": str(Path(self.temp.name))})
        window = self.window([task])
        window.config.settings["close_to_tray"] = False
        window.table.selectRow(0)
        window.close()
        window.show()
        with patch("dispatcher.ui.main_window.QMessageBox.information") as message:
            window.test_selected_paths()
            self.wait_until(lambda: message.called)
        self.assertIn("✓  program:", message.call_args.args[2])

    def test_open_folder_uses_parent_of_implicit_result_file(self):
        for module in ("music.foreign_agents", "music.list_mp3", "music.musician_card",
                       "yandex.make_links", "excel.report"):
            with self.subTest(module=module):
                root = Path(self.temp.name)
                task = TaskDefinition(id=module, name=module, module=module,
                                      params={"source_dir": str(root), "input_file": str(root / "input.xlsx")})
                window = self.window([task])
                window.table.selectRow(0)
                opener = "dispatcher.ui.main_window.os.startfile" if os.name == "nt" else "PySide6.QtGui.QDesktopServices.openUrl"
                with patch(opener) as opened, patch("dispatcher.ui.main_window.QMessageBox.warning") as warning:
                    window.open_selected_folder()
                warning.assert_not_called()
                self.assertTrue(opened.called)
                opened_path = str(opened.call_args.args[0]) if os.name == "nt" else opened.call_args.args[0].toLocalFile()
                self.assertEqual(Path(opened_path), root)

    def test_unexpected_worker_error_produces_done_and_releases_pending(self):
        task = self.task()
        window = self.window([task])
        with patch.object(window.runner, "execute", side_effect=RuntimeError("unexpected failure")):
            window.start_task(task)
            self.wait_until(lambda: window.runtime_status.get(task.id) == "error")
        self.assertIn("unexpected failure", window.runtime_messages[task.id])
        self.assertFalse(window.runner.is_active(task.id))

    def test_scheduled_runtime_error_is_visible_when_no_new_history_was_saved(self):
        for prior_history in (False, True):
            with self.subTest(prior_history=prior_history):
                task = self.task(str(prior_history), schedule=ScheduleConfig(mode="times", times=["12:00"]))
                if prior_history:
                    self.history_record(task, "2026-10-29T11:00:00+03:00")
                window = self.window([task])
                with patch.object(window, "_scheduler_now", return_value=datetime(2026, 10, 29, 12, 30)) as now, patch.object(
                    window.runner, "execute", side_effect=OSError("history unavailable")
                ):
                    window.start_task(task)
                    self.wait_until(lambda: window.runtime_status.get(task.id) == "error")
                    self.assertEqual(window._task_status(task), "error")
                    now.return_value = datetime(2026, 10, 30, 0, 1)
                    self.assertEqual(window._task_status(task), "waiting")

    def test_scheduled_runtime_completion_after_midnight_keeps_start_day(self):
        task = self.task(schedule=ScheduleConfig(mode="times", times=["23:59"]))
        window = self.window([task])
        with patch.object(window, "_scheduler_now", return_value=datetime(2026, 10, 29, 23, 59)) as now, patch.object(
            window.executor, "submit"
        ):
            window.start_task(task)
            window.runner.unqueue(task.id)
            now.return_value = datetime(2026, 10, 30, 0, 1)
            window.on_task_done(RunResult(task.id, "error", "history unavailable", {}, ""))
            self.assertEqual(window._task_status(task), "waiting")

    def test_pending_task_is_not_scheduled_again(self):
        task = self.task(schedule=ScheduleConfig(mode="times", times=["12:00"], weekdays=list(range(7))))
        window = self.window([task])
        window.scheduler_enabled = True
        window.runner.queue(task.id)
        with patch.object(window, "_scheduler_now", return_value=datetime(2026, 10, 29, 12)), patch.object(
            window, "start_task"
        ) as start:
            window.check_scheduler()
        start.assert_not_called()

    def check_catch_up_cases(self, cases):
        for stamp, should_start in cases:
            with self.subTest(stamp=stamp):
                task = self.task(stamp, schedule=ScheduleConfig(mode="times", times=["12:00"],
                                 weekdays=list(range(7)), catch_up=True))
                self.history_record(task, stamp)
                window = self.window([task])
                window.scheduler_enabled = True
                with patch.object(window, "_scheduler_now", return_value=datetime(2026, 10, 29, 12, 30)), patch.object(
                    window, "start_task"
                ) as start:
                    window.check_scheduler()
                self.assertEqual(start.called, should_start)

    def test_catch_up_normalizes_aware_history_to_scheduler_zone(self):
        self.check_catch_up_cases((("2026-10-29T09:10:00+00:00", False),
                                   ("2026-10-29T12:10:00+03:00", False),
                                   ("2026-10-29T08:59:00+00:00", True)))

    @unittest.skipUnless(hasattr(time, "tzset"), "Requires POSIX local timezone selection")
    def test_catch_up_normalizes_local_history_to_scheduler_zone(self):
        old_timezone = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            self.check_catch_up_cases((("2026-10-29T09:10:00", False), ("2026-10-29T08:59:00", True)))
        finally:
            if old_timezone is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_timezone
            time.tzset()


if __name__ == "__main__":
    unittest.main()
