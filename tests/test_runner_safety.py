from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatcher.history import HistoryStore
from dispatcher.models import TaskDefinition
from dispatcher.modules.common import TaskContext
from dispatcher.modules.web_tools import run_make_links
from dispatcher.task_runner import TaskRunner


class RunnerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Redirect storage only; real directories, logs and SQLite are used.
        self.storage_root = patch("dispatcher.storage.application_root", return_value=self.root)
        self.storage_root.start()
        self.addCleanup(self.storage_root.stop)
        self.history = HistoryStore(self.root / "history.sqlite3")
        self.runner = TaskRunner(self.history)
        (self.root / "names.txt").write_text("Artist - Track\n", encoding="utf-8")
        self.task = TaskDefinition(
            id="links", name="Links", module="yandex.make_links", workdir=str(self.root),
            params={"input_file": "names.txt", "output_file": "links.txt"},
        )

    def execute_without_raising(self, task=None):
        try:
            return self.runner.execute(task or self.task, {}, {})
        except Exception as exc:
            self.fail(f"execute must return a RunResult after an IO failure: {exc!r}")

    def assert_released(self):
        self.assertFalse(self.runner.is_active(self.task.id))
        self.assertTrue(self.runner.queue(self.task.id))
        self.runner.unqueue(self.task.id)

    def test_history_start_failure_returns_error_and_releases_reservation(self):
        self.assertTrue(self.runner.queue(self.task.id))
        with patch.object(self.history, "start", side_effect=OSError("history start unavailable")):
            result = self.execute_without_raising()
        self.assertEqual(result.status, "error")
        self.assertIn("history start unavailable", result.message)
        self.assert_released()
        self.assertEqual(self.execute_without_raising().status, "success")

    def test_mkdir_failure_returns_error_and_releases_reservation(self):
        data = self.root / "data"
        data.mkdir()
        (data / "temp").write_bytes(b"a file blocks the run directory")
        self.assertTrue(self.runner.queue(self.task.id))
        result = self.execute_without_raising()
        self.assertEqual(result.status, "error")
        self.assert_released()
        (data / "temp").unlink()
        self.assertEqual(self.execute_without_raising().status, "success")

    def test_history_finish_failure_returns_error_and_releases_reservation(self):
        self.assertTrue(self.runner.queue(self.task.id))
        with patch.object(self.history, "finish", side_effect=OSError("history finish unavailable")):
            result = self.execute_without_raising()
        self.assertTrue((self.root / "links.txt").is_file())
        self.assertEqual(result.status, "error")
        self.assertIn("history finish unavailable", result.message)
        self.assert_released()
        self.assertEqual(self.execute_without_raising().status, "success")

    def test_log_failure_does_not_escape_or_keep_task_reserved(self):
        logs = self.root / "data" / "logs"
        logs.mkdir(parents=True)
        with patch.object(Path, "open", side_effect=OSError("log disk unavailable")):
            result = self.execute_without_raising()
        self.assertEqual(result.status, "error")
        self.assertIn("log disk unavailable", result.message)
        self.assert_released()

    def test_slow_resource_resolution_does_not_block_cancellation(self):
        entered = threading.Event()
        release = threading.Event()
        observer_done = threading.Event()
        results = []
        cancelled = []

        def resources(_task):
            entered.set()
            release.wait(timeout=3)
            return set()

        def cancel():
            cancelled.append(self.runner.cancel(self.task.id))
            observer_done.set()

        self.runner.queue(self.task.id)
        worker = threading.Thread(target=lambda: results.append(self.runner.execute(self.task, {}, {})))
        observer = threading.Thread(target=cancel)
        with patch.object(self.runner, "_task_resources", side_effect=resources):
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                observer.start()
                responsive = observer_done.wait(timeout=0.3)
            finally:
                release.set()
                worker.join(timeout=5)
                observer.join(timeout=5)
        self.assertTrue(responsive, "A slow network path must not hold the runner's state lock")
        self.assertEqual(cancelled, [True])
        self.assertEqual(results[0].status, "cancelled")
        self.assert_released()

    def test_shared_interpreter_and_script_do_not_conflict_for_separate_workdirs(self):
        import sys
        shared_script = self.root / "tools" / "run.py"
        shared_script.parent.mkdir()
        shared_script.write_text("pass", encoding="utf-8")
        tasks = [TaskDefinition(
            id=name, name=name, module="external", workdir=str(self.root / name),
            params={"program": str(shared_script), "python_path": sys.executable},
        ) for name in ("a", "b")]
        resources = [self.runner._task_resources(task) for task in tasks]
        self.assertIsNone(self.runner._shared_resource(*resources))

    def test_external_without_workdir_reserves_effective_program_directory(self):
        script = self.root / "tools" / "run.py"
        script.parent.mkdir()
        script.write_text("pass", encoding="utf-8")
        task = TaskDefinition(id="external", name="External", module="external", params={"program": str(script)})
        self.assertEqual(self.runner._task_resources(task), {os.path.normcase(str(script.parent.resolve()))})

    def test_parent_and_child_source_folders_cannot_run_together(self):
        source = self.root / "library"
        child = source / "child"
        child.mkdir(parents=True)
        (source / "track.mp3").write_bytes(b"fixture")
        first = TaskDefinition(
            id="first", name="First", module="music.list_mp3",
            params={"source_dir": str(source), "output_file": str(self.root / "exports" / "first" / "list.txt")},
        )
        second = TaskDefinition(
            id="second", name="Second", module="music.list_mp3",
            params={"source_dir": str(child), "output_file": str(self.root / "exports" / "second" / "list.txt")},
        )
        started = threading.Event()
        release = threading.Event()
        results = []

        def pause_after_reservation(_task_id, _message):
            started.set()
            if not release.wait(10):
                raise TimeoutError("test did not release the first runner")

        worker = threading.Thread(
            target=lambda: results.append(self.runner.execute(first, {}, {}, on_log=pause_after_reservation)),
            daemon=True,
        )
        worker.start()
        try:
            self.assertTrue(started.wait(5))
            blocked = self.execute_without_raising(second)
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(blocked.status, "skipped")
        self.assertFalse((self.root / "exports" / "second" / "list.txt").exists())
        self.assertEqual(results[0].status, "success")


class TaskPathTests(unittest.TestCase):
    def path_helpers(self):
        try:
            from dispatcher.task_paths import task_input_paths, task_output_path, task_resource_paths
        except ImportError as exc:
            self.fail(f"shared task path helpers must be available: {exc}")
        return task_input_paths, task_output_path, task_resource_paths

    def test_input_candidates_preserve_scalar_paths_and_skip_disabled_sources(self):
        task_input_paths, _output, _resources = self.path_helpers()
        task = TaskDefinition(
            id="cut", name="Cut", module="broadcast.universal_cut",
            params={"source_name": "Caption", "sources": [
                {"folder": " recordings "}, {"enabled": False, "folder": "offline"},
                {"folder": ["invalid"]},
            ]},
        )
        self.assertEqual(task_input_paths(task), [("sources[1].folder", "recordings")])

    def test_effective_output_matches_module_relative_bases_and_defaults(self):
        _inputs, task_output_path, _resources = self.path_helpers()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                ("broadcast.universal_cut", {}, Path(__file__).resolve().parents[1] / "cash"),
                ("broadcast.local_news", {"source_dir": "library"}, root / "library"),
                ("music.list_mp3", {"source_dir": "library"}, root / "library" / "mp3_files.txt"),
                ("music.foreign_agents", {"source_dir": "library"}, root / "library" / "найденные_совпадения.txt"),
                ("music.download_performers", {"input_file": "lists/names.txt"}, root / "lists"),
                ("yandex.make_links", {"input_file": "lists/names.txt", "output_file": "links.txt"}, root / "lists" / "links.txt"),
                ("excel.report", {"input_file": "sheets/source.xlsx"}, root / "sheets" / "source_готово.xlsx"),
            ]
            for module, params, expected in cases:
                with self.subTest(module=module):
                    task = TaskDefinition(id="output", name="Output", module=module, workdir=str(root), params=params)
                    self.assertEqual(task_output_path(task), str(expected))

    def test_output_display_does_not_resolve_or_probe_filesystem_paths(self):
        _inputs, task_output_path, _resources = self.path_helpers()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = TaskDefinition(
                id="output", name="Output", module="music.list_mp3", workdir=str(root),
                params={"source_dir": "missing/library", "output_file": "../list.txt"},
            )
            with patch.object(Path, "resolve", side_effect=OSError("filesystem is unavailable")):
                self.assertEqual(task_output_path(task), str(root / "missing" / "list.txt"))

    def test_symlink_aliases_share_real_directory_resources(self):
        _inputs, _outputs, task_resource_paths = self.path_helpers()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "library"
            source.mkdir()
            alias = root / "alias"
            try:
                alias.symlink_to(source, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            task = TaskDefinition(id="alias", name="Alias", module="music.list_mp3", params={"source_dir": str(alias)})
            self.assertEqual(task_resource_paths(task), {os.path.normcase(str(source.resolve()))})

    def test_symlink_parent_segments_reserve_the_module_actual_directory(self):
        _inputs, _outputs, task_resource_paths = self.path_helpers()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "library"
            nested = source / "nested"
            nested.mkdir(parents=True)
            alias = root / "alias"
            try:
                alias.symlink_to(nested, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            task = TaskDefinition(
                id="alias", name="Alias", module="yandex.make_links",
                params={"input_file": str(alias / ".." / "names.txt")},
            )
            # Path.resolve handles a symlink followed by ".." differently on
            # Windows and POSIX. Exercise the module's real IO so its reserved
            # directory must match the one it actually reads and writes.
            (root / "names.txt").write_text("Root Fixture\n", encoding="utf-8")
            (source / "names.txt").write_text("Library Fixture\n", encoding="utf-8")
            context = TaskContext(
                task=task, settings={}, secrets={}, run_dir=root / "run",
                cancel_event=threading.Event(), log=lambda _message: None,
                progress=lambda _value, _message: None,
            )
            result = run_make_links(context)
            self.assertEqual(len(result["files"]), 1)
            output = Path(result["files"][0])
            expected_contents = {
                root.resolve(): b"https://music.yandex.ru/search?text=Root%20Fixture",
                source.resolve(): b"https://music.yandex.ru/search?text=Library%20Fixture",
            }
            self.assertIn(output.parent, expected_contents)
            self.assertEqual(output.read_bytes(), expected_contents[output.parent])
            self.assertEqual(
                sum((folder / "ссылки на песни.txt").is_file() for folder in (root, source)), 1,
            )
            self.assertEqual(task_resource_paths(task), {os.path.normcase(str(output.parent.resolve()))})

    def test_nested_sources_are_paths_but_names_and_lists_are_not(self):
        task = TaskDefinition(
            id="cut", name="Cut", module="broadcast.universal_cut",
            params={
                "source_name": "a caption, not a folder",
                "sources": [
                    {"enabled": True, "folder": "audio", "name": "Studio"},
                    {"enabled": False, "folder": "disabled"},
                    {"folder": ["not", "a", "path"]},
                    "invalid profile",
                ],
            },
        )
        resources = TaskRunner._task_resources(task)
        self.assertIn(os.path.normcase(str((Path.cwd() / "audio").resolve())), resources)
        self.assertFalse(any("disabled" in path or "caption" in path or "invalid profile" in path for path in resources))

    def test_default_cash_is_shared_by_all_broadcast_cutters(self):
        for module in ("broadcast.news", "broadcast.morning_shift", "broadcast.morning_guest", "broadcast.universal_cut"):
            with self.subTest(module=module):
                task = TaskDefinition(id="cut", name="Cut", module=module)
                self.assertIn(
                    os.path.normcase(str((Path(__file__).resolve().parents[1] / "cash").resolve())),
                    TaskRunner._task_resources(task),
                )

    def test_aliases_and_input_files_reserve_the_actual_parent_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "library"
            folder.mkdir()
            (folder / "names.txt").write_text("Artist", encoding="utf-8")
            task = TaskDefinition(
                id="links", name="Links", module="yandex.make_links", workdir=str(root / "."),
                params={"input_file": "library/../library/names.txt", "output_file": "links.txt"},
            )
            resources = TaskRunner._task_resources(task)
            self.assertEqual(resources, {os.path.normcase(str(root.resolve())), os.path.normcase(str(folder.resolve()))})

    def test_source_relative_output_defaults_use_the_source_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = TaskDefinition(
                id="list", name="List", module="music.list_mp3", workdir=str(root / "work"),
                params={"source_dir": str(root / "library")},
            )
            self.assertEqual(
                TaskRunner._task_resources(task),
                {os.path.normcase(str((root / "work").resolve())), os.path.normcase(str((root / "library").resolve()))},
            )


if __name__ == "__main__":
    unittest.main()
