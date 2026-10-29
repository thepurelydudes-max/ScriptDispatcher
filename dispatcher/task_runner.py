from __future__ import annotations

import multiprocessing
import os
import queue
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .history import HistoryStore
from .models import TaskDefinition
from .modules import run_module
from .modules.common import TaskCancelled, TaskContext
from .storage import logs_dir, temp_dir


@dataclass(slots=True)
class RunResult:
    task_id: str
    status: str
    message: str
    data: dict[str, Any]
    log_path: str


def _hide_windows_subprocess_windows() -> None:
    """Запускает FFmpeg и другие дочерние консольные процессы без видимого окна."""
    if os.name != "nt" or getattr(subprocess.Popen, "_dispatcher_hidden", False):
        return
    original_popen = subprocess.Popen

    def hidden_popen(*args, **kwargs):
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags", 0))
            | int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        )
        startupinfo = kwargs.get("startupinfo")
        if startupinfo is None:
            startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 1))
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
        kwargs["startupinfo"] = startupinfo
        return original_popen(*args, **kwargs)

    hidden_popen._dispatcher_hidden = True
    subprocess.Popen = hidden_popen


def _module_process_main(
    task: TaskDefinition,
    settings: dict[str, Any],
    secrets: dict[str, str],
    run_dir: str,
    cancel_event,
    messages,
) -> None:
    """Запускается в отдельном процессе, чтобы «Стоп» мог прервать любой модуль."""
    _hide_windows_subprocess_windows()

    def log(message: str) -> None:
        messages.put(("log", str(message)))

    def progress(value: int, message: str) -> None:
        messages.put(("progress", int(value), str(message)))

    try:
        context = TaskContext(
            task=task,
            settings=settings,
            secrets=secrets,
            run_dir=Path(run_dir),
            cancel_event=cancel_event,
            log=log,
            progress=progress,
        )
        data = run_module(context) or {}
        messages.put(("result", "success", str(data.get("message", "Выполнено")), data, ""))
    except TaskCancelled as exc:
        messages.put(("result", "cancelled", str(exc), {}, ""))
    except BaseException as exc:
        message = f"{type(exc).__name__}: {exc}"
        messages.put(("result", "error", message, {}, traceback.format_exc()))


class TaskRunner:
    def __init__(self, history: HistoryStore | None = None):
        self.history = history or HistoryStore()
        self._guard = threading.RLock()
        self._cancel: dict[str, Any] = {}
        self._running: set[str] = set()
        self._pending: set[str] = set()
        self._cancel_requested: set[str] = set()
        self._resources: dict[str, set[str]] = {}
        self._processes: dict[str, Any] = {}

    @staticmethod
    def _task_resources(task: TaskDefinition) -> set[str]:
        values: list[str] = []
        if task.workdir:
            values.append(task.workdir)
        for key, value in task.params.items():
            if key.startswith(("source", "output", "input", "report", "block_list")):
                text = str(value).strip()
                if text:
                    values.append(text)
        result = set()
        for value in values:
            expanded = os.path.expandvars(os.path.expanduser(value))
            if not os.path.isabs(expanded) and task.workdir:
                expanded = os.path.join(task.workdir, expanded)
            result.add(os.path.normcase(os.path.normpath(expanded)))
        return result

    def is_running(self, task_id: str) -> bool:
        with self._guard:
            return task_id in self._running

    def is_active(self, task_id: str) -> bool:
        with self._guard:
            return task_id in self._running or task_id in self._pending

    def queue(self, task_id: str) -> bool:
        """Регистрирует задачу до передачи в пул потоков, закрывая гонку запуска."""
        with self._guard:
            if task_id in self._running or task_id in self._pending:
                return False
            self._pending.add(task_id)
            self._cancel_requested.discard(task_id)
            return True

    def unqueue(self, task_id: str) -> None:
        with self._guard:
            self._pending.discard(task_id)
            self._cancel_requested.discard(task_id)

    def running_ids(self) -> set[str]:
        with self._guard:
            return set(self._running | self._pending)

    def cancel(self, task_id: str) -> bool:
        with self._guard:
            event = self._cancel.get(task_id)
            if event:
                event.set()
                return True
            if task_id in self._pending:
                self._cancel_requested.add(task_id)
                return True
            return False

    def execute(
        self,
        task: TaskDefinition,
        settings: dict[str, Any],
        secrets: dict[str, str],
        on_log: Callable[[str, str], None] | None = None,
        on_progress: Callable[[str, int, str], None] | None = None,
    ) -> RunResult:
        process_context = multiprocessing.get_context("spawn")
        cancel_event = process_context.Event()
        with self._guard:
            self._pending.discard(task.id)
            if task.id in self._cancel_requested:
                self._cancel_requested.discard(task.id)
                return RunResult(
                    task.id,
                    "cancelled",
                    "Задача остановлена пользователем до начала выполнения",
                    {},
                    "",
                )
            if task.id in self._running:
                return RunResult(task.id, "skipped", "Задача уже выполняется", {}, "")
            resources = self._task_resources(task)
            for other_task_id, other_resources in self._resources.items():
                shared = resources & other_resources
                if shared:
                    return RunResult(
                        task.id,
                        "skipped",
                        f"Ресурс уже используется задачей {other_task_id}: {next(iter(shared))}",
                        {},
                        "",
                    )
            self._running.add(task.id)
            self._resources[task.id] = resources
            self._cancel[task.id] = cancel_event

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = logs_dir() / f"{stamp}_{task.id}.log"
        run_dir = temp_dir() / task.id / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        run_id = self.history.start(task.id, task.name, str(log_path))
        log_lock = threading.Lock()

        def log(message: str) -> None:
            timestamp = datetime.now().strftime("%H:%M:%S")
            line = f"[{timestamp}] {message}"
            with log_lock:
                with log_path.open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")
            if on_log:
                on_log(task.id, line)

        def progress(value: int, message: str) -> None:
            if on_progress:
                on_progress(task.id, max(0, min(100, int(value))), message)

        status = "success"
        message = "Выполнено"
        data: dict[str, Any] = {}
        process = None
        messages = None
        try:
            log(f"Запуск задачи «{task.name}»")
            messages = process_context.Queue()
            process = process_context.Process(
                target=_module_process_main,
                args=(
                    task,
                    dict(settings),
                    dict(secrets),
                    str(run_dir),
                    cancel_event,
                    messages,
                ),
                name=f"script-dispatcher-{task.id}",
                daemon=False,
            )
            process.start()
            with self._guard:
                self._processes[task.id] = process

            child_result: tuple | None = None
            cancel_started: float | None = None

            def handle_child_message(item: tuple) -> None:
                nonlocal child_result
                kind = item[0]
                if kind == "log":
                    log(str(item[1]))
                elif kind == "progress":
                    progress(int(item[1]), str(item[2]))
                elif kind == "result":
                    child_result = item

            while process.is_alive():
                try:
                    handle_child_message(messages.get(timeout=0.1))
                except (queue.Empty, EOFError, OSError):
                    pass
                if cancel_event.is_set():
                    if cancel_started is None:
                        cancel_started = time.monotonic()
                        log("Получена команда остановки…")
                    if time.monotonic() - cancel_started >= 0.75 and process.is_alive():
                        process.terminate()
                        process.join(timeout=2)
                        if process.is_alive() and hasattr(process, "kill"):
                            process.kill()
                            process.join(timeout=2)
                        break

            process.join(timeout=2)
            while True:
                try:
                    handle_child_message(messages.get_nowait())
                except (queue.Empty, EOFError, OSError):
                    break

            if cancel_event.is_set():
                status = "cancelled"
                message = "Задача остановлена пользователем"
                data = {}
            elif child_result is not None:
                _kind, status, message, data, child_traceback = child_result
                if child_traceback:
                    with log_path.open("a", encoding="utf-8") as stream:
                        stream.write("\n" + str(child_traceback))
            else:
                status = "error"
                message = f"Рабочий процесс завершился без результата (код {process.exitcode})"

            if status == "error":
                log("ОШИБКА: " + message)
            else:
                log(message)
        except Exception as exc:
            status = "error"
            message = f"{type(exc).__name__}: {exc}"
            log("ОШИБКА: " + message)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write("\n" + traceback.format_exc())
        finally:
            if process is not None and process.is_alive():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=2)
            if messages is not None:
                try:
                    messages.close()
                except Exception:
                    pass
            self.history.finish(run_id, status, message)
            with self._guard:
                self._running.discard(task.id)
                self._cancel.pop(task.id, None)
                self._resources.pop(task.id, None)
                self._processes.pop(task.id, None)
        return RunResult(task.id, status, message, data, str(log_path))
