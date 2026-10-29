from __future__ import annotations

import os
import multiprocessing
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatcher.history import HistoryStore
from dispatcher.models import TaskDefinition
from dispatcher.modules.common import TaskCancelled, TaskContext
from dispatcher.modules.web_tools import run_external
from dispatcher.process_tree import OwnedProcessTree, enter_task_process
from dispatcher.task_runner import TaskRunner


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def read_pid(path):
    try:
        return int(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        # Containers may not reap orphaned zombies; they are no longer running.
        stat = Path(f"/proc/{pid}/stat")
        return not stat.exists() or stat.read_text().split(")", 1)[1].split()[0] != "Z"
    except ProcessLookupError:
        return False


def kill_test_process(pid):
    try:
        os.kill(pid, signal.SIGKILL if os.name != "nt" else signal.SIGTERM)
    except ProcessLookupError:
        pass


def launcher(root, exit_immediately=False):
    (root / "leaf.py").write_text(
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "if os.name != 'nt': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path('child.pid').write_text(str(os.getpid()))\n"
        "while True: time.sleep(0.1)\n", encoding="utf-8"
    )
    script = root / "launcher.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "Path('launcher.pid').write_text(str(os.getpid()))\n"
        "subprocess.Popen([sys.executable, '-u', 'leaf.py'])\n"
        "while not Path('child.pid').exists(): time.sleep(0.01)\n"
        "print('готово', flush=True)\n"
        + ("" if exit_immediately else "while True: time.sleep(0.1)\n"),
        encoding="utf-8",
    )
    return script


def gated_module(ready, proceed, root_value, finish):
    enter_task_process(ready, proceed)
    root = Path(root_value)
    subprocess.Popen([sys.executable, "-u", str(root / "launcher.py")], cwd=root, stdout=subprocess.DEVNULL)
    if not finish:
        while True:
            time.sleep(0.1)


def stubborn_runner_module(task, settings, secrets, run_dir, cancel_event, messages, ready=None, proceed=None):
    """A real spawned module that requires the controller's forced cleanup."""
    if ready is not None:
        enter_task_process(ready, proceed)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    root = Path(task.workdir)
    (root / "module.pid").write_text(str(os.getpid()))
    subprocess.Popen([sys.executable, "-u", str(root / "leaf.py")], cwd=root, stdout=subprocess.DEVNULL)
    while not (root / "child.pid").exists():
        time.sleep(0.01)
    if task.params.get("finish"):
        messages.put(("result", "success", "fixture finished", {}, ""))
        return
    while True:
        time.sleep(0.1)


class RunnerProcessTreeTests(unittest.TestCase):
    def test_real_external_launcher_cancellation_kills_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = launcher(root)
            logs = root / "logs"
            logs.mkdir()
            runner = TaskRunner(HistoryStore(root / "history.sqlite3"))
            task = TaskDefinition("actual-external", "actual-external", "external", str(root),
                                  params={"program": str(script), "python_path": sys.executable})
            outcome = []
            # Keep the real spawned module and launcher. On Windows this tests
            # the module Job containing the external launcher's nested Job.
            with patch("dispatcher.task_runner.logs_dir", return_value=logs), patch(
                "dispatcher.task_runner.temp_dir", return_value=root / "runs"
            ):
                worker = threading.Thread(target=lambda: outcome.append(runner.execute(task, {}, {})), daemon=True)
                worker.start()
                try:
                    self.assertTrue(wait_for(lambda: read_pid(root / "child.pid")), "external descendant did not start")
                    pids = [read_pid(root / name) for name in ("launcher.pid", "child.pid")]
                    self.assertTrue(runner.cancel(task.id))
                    worker.join(timeout=8)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(outcome[0].status, "cancelled")
                    self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in pids)))
                    self.assertFalse(runner.is_active(task.id))
                finally:
                    runner.cancel(task.id)
                    for path in (root / "launcher.pid", root / "child.pid"):
                        if (pid := read_pid(path)) is not None:
                            kill_test_process(pid)
                    worker.join(timeout=5)

    def exercise_runner(self, finish=False, browser=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher(root)
            logdir = root / "logs"
            logdir.mkdir()
            runner = TaskRunner(HistoryStore(root / "history.sqlite3"))
            task = TaskDefinition("runner-tree", "runner-tree", "yandex.open_links" if browser else "external",
                                  str(root), params={"finish": finish})
            outcome = []
            with patch("dispatcher.task_runner._module_process_main", stubborn_runner_module), patch(
                "dispatcher.task_runner.logs_dir", return_value=logdir
            ), patch("dispatcher.task_runner.temp_dir", return_value=root / "runs"):
                worker = threading.Thread(target=lambda: outcome.append(runner.execute(task, {}, {})), daemon=True)
                worker.start()
                try:
                    self.assertTrue(wait_for(lambda: read_pid(root / "child.pid")), "module did not launch descendant")
                    child_pid = int((root / "child.pid").read_text())
                    if not finish:
                        self.assertTrue(runner.cancel(task.id))
                    worker.join(timeout=8)
                    self.assertFalse(worker.is_alive(), "runner did not finish")
                    self.assertEqual(outcome[0].status, "success" if finish else "cancelled")
                    if browser:
                        self.assertTrue(alive(child_pid), "browser opening unexpectedly captured existing behavior")
                    else:
                        self.assertTrue(wait_for(lambda: not alive(child_pid), 1), "runner left its module descendant alive")
                    self.assertFalse(runner.is_active(task.id))
                finally:
                    runner.cancel(task.id)
                    for path in (root / "module.pid", root / "child.pid"):
                        if (pid := read_pid(path)) is not None:
                            kill_test_process(pid)
                    worker.join(timeout=5)

    def test_forced_runner_cancellation_kills_module_descendants(self):
        self.exercise_runner()

    def test_successful_runner_finish_kills_leftover_module_descendants(self):
        self.exercise_runner(finish=True)

    def test_browser_opening_preserves_launched_processes(self):
        self.exercise_runner(finish=True, browser=True)


class OwnedModuleProcessTests(unittest.TestCase):
    def exercise_module(self, finish):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher(root)
            context = multiprocessing.get_context("spawn")
            ready, proceed = context.Event(), context.Event()
            process = context.Process(target=gated_module, args=(ready, proceed, str(root), finish))
            tree = OwnedProcessTree()
            process.start()
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                self.assertTrue(ready.wait(5), "module failed before ownership gate")
                self.assertFalse((root / "launcher.pid").exists(), "work ran before ownership attached")
                tree.attach(process.pid)
                proceed.set()
                self.assertTrue(wait_for(lambda: read_pid(root / "child.pid")))
                child_pid = int((root / "child.pid").read_text())
                launcher_pid = int((root / "launcher.pid").read_text())
                if finish:
                    process.join(timeout=5)
                    self.assertFalse(process.is_alive())
                tree.close()
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                self.assertTrue(wait_for(lambda: not alive(child_pid) and not alive(launcher_pid)))
                self.assertIsNone(unrelated.poll(), "unrelated process was terminated")
                tree.close()
            finally:
                tree.close()
                if process.is_alive():
                    process.kill()
                process.join(timeout=5)
                process.close()
                unrelated.kill()
                unrelated.wait(timeout=5)
                for path in (root / "launcher.pid", root / "child.pid"):
                    if (pid := read_pid(path)) is not None:
                        kill_test_process(pid)

    def test_forced_module_stop_owns_grandchildren_but_not_unrelated_process(self):
        self.exercise_module(False)

    def test_module_clean_exit_still_cleans_descendants(self):
        self.exercise_module(True)

    @unittest.skipIf(os.name == "nt", "POSIX group safety")
    def test_refuses_current_process_group(self):
        tree = OwnedProcessTree()
        try:
            with self.assertRaisesRegex(RuntimeError, "private process group"):
                tree.attach(os.getpid())
        finally:
            tree.close()

    @unittest.skipUnless(os.name == "nt", "native Windows Job assignment failure")
    def test_assignment_failure_never_releases_suspended_work(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "work-started"
            created = []
            original = subprocess.Popen

            def remember_process(*args, **kwargs):
                process = original(*args, **kwargs)
                created.append(process)
                return process

            tree = OwnedProcessTree()
            try:
                with patch("dispatcher.process_tree._assign_job", return_value=False), patch(
                    "dispatcher.process_tree.subprocess.Popen", side_effect=remember_process
                ):
                    with self.assertRaises(OSError):
                        tree.popen([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
                self.assertFalse(marker.exists(), "unowned work ran after assignment failure")
                self.assertEqual(len(created), 1)
                self.assertIsNotNone(created[0].poll(), "unowned suspended child was not reaped")
            finally:
                tree.close()

    @unittest.skipUnless(os.name == "nt", "native Windows Job handle cleanup")
    def test_closed_job_handle_is_released(self):
        import ctypes
        from ctypes import wintypes
        import dispatcher.process_tree as ownership

        tree = OwnedProcessTree()
        handle = tree._job
        tree.close()
        flags = wintypes.DWORD()
        function = ownership._kernel.GetHandleInformation
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        function.restype = wintypes.BOOL
        self.assertFalse(function(handle, ctypes.byref(flags)))
        self.assertEqual(ctypes.get_last_error(), 6)  # ERROR_INVALID_HANDLE

    @unittest.skipUnless(os.name == "nt", "native Windows Job error cleanup")
    def test_job_operation_failure_still_closes_handle_and_kills_descendants(self):
        for failing_api in ("_terminate_job", "_query_job"):
            with self.subTest(api=failing_api), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                script = launcher(root)
                tree = OwnedProcessTree()
                process = tree.popen([sys.executable, "-u", str(script)], cwd=root, stdout=subprocess.DEVNULL)
                try:
                    self.assertTrue(wait_for(lambda: read_pid(root / "child.pid")))
                    child_pid = int((root / "child.pid").read_text())
                    with patch(f"dispatcher.process_tree.{failing_api}", return_value=False):
                        with self.assertRaises(OSError):
                            tree.close()
                    self.assertIsNone(tree._job, "failed job operation leaked kill-on-close handle")
                    self.assertTrue(wait_for(lambda: not alive(child_pid)))
                    self.assertIsNotNone(process.poll())
                finally:
                    tree.close()
                    for path in (root / "launcher.pid", root / "child.pid"):
                        if (pid := read_pid(path)) is not None:
                            kill_test_process(pid)


class ExternalProcessTreeTests(unittest.TestCase):
    def exercise(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = launcher(root, exit_immediately=mode == "finish")
            task = TaskDefinition("tree", "tree", "external", str(root), params={
                "program": str(script), "timeout": 1 if mode == "timeout" else 0,
            })
            lines = []
            ctx = TaskContext(task, {}, {}, root, threading.Event(), lines.append, lambda *_: None)
            outcome = []

            def run():
                try:
                    outcome.append(run_external(ctx))
                except BaseException as exc:
                    outcome.append(exc)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            pids = []
            try:
                self.assertTrue(wait_for(lambda: read_pid(root / "child.pid")), "child did not start")
                pids = [int((root / name).read_text()) for name in ("launcher.pid", "child.pid")]
                self.assertTrue(wait_for(lambda: "готово" in lines), "launcher did not report readiness")
                if mode == "cancel":
                    ctx.cancel_event.set()
                worker.join(timeout=4)
                self.assertFalse(worker.is_alive(), "launcher waits for descendant's inherited stdout")
                self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in pids)), "task descendant survived")
                if mode == "cancel":
                    self.assertIsInstance(outcome[0], TaskCancelled)
                elif mode == "timeout":
                    self.assertIsInstance(outcome[0], TimeoutError)
                else:
                    self.assertIsInstance(outcome[0], dict)
                self.assertIn("готово", lines)
            finally:
                for path in (root / "launcher.pid", root / "child.pid"):
                    if (pid := read_pid(path)) is not None:
                        kill_test_process(pid)
                worker.join(timeout=3)

    def test_cancel_kills_stubborn_descendant_and_finishes_pipe(self):
        self.exercise("cancel")

    def test_timeout_kills_stubborn_descendant_and_finishes_pipe(self):
        self.exercise("timeout")

    def test_clean_launcher_exit_cleans_descendant_holding_stdout(self):
        self.exercise("finish")

    def test_launcher_exit_is_checked_even_with_continuous_descendant_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = launcher(root, exit_immediately=True)
            task = TaskDefinition("output", "output", "external", str(root), params={"program": str(script)})
            ctx = TaskContext(task, {}, {}, root, threading.Event(), lambda _: None, lambda *_: None)
            outcome = []

            def run():
                try:
                    outcome.append(run_external(ctx))
                except BaseException as exc:
                    outcome.append(exc)

            # Keep the pipe perpetually readable to exercise the exit condition;
            # real launcher/descendants and their cleanup remain active.
            with patch("dispatcher.modules.web_tools.read_pipe_available", return_value=b"output\n"):
                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                try:
                    self.assertTrue(wait_for(lambda: read_pid(root / "child.pid")))
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive(), "continuous child output hides launcher exit")
                    self.assertIsInstance(outcome[0], dict)
                finally:
                    ctx.cancel_event.set()
                    worker.join(timeout=3)
                    for path in (root / "launcher.pid", root / "child.pid"):
                        if (pid := read_pid(path)) is not None:
                            kill_test_process(pid)

    def test_launch_initialization_failure_closes_ownership(self):
        tree = OwnedProcessTree()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                tree.popen([sys.executable, "-c", "pass"], cwd=Path(temp) / "missing")
        tree.close()

    def test_cancelled_before_launch_does_not_start_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = launcher(root)
            task = TaskDefinition("cancel", "cancel", "external", str(root), params={"program": str(script)})
            event = threading.Event()
            event.set()
            ctx = TaskContext(task, {}, {}, root, event, lambda _: None, lambda *_: None)
            with self.assertRaises(TaskCancelled):
                run_external(ctx)
            self.assertFalse((root / "launcher.pid").exists())


if __name__ == "__main__":
    unittest.main()
