from __future__ import annotations

import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from ..models import TaskDefinition


class TaskCancelled(RuntimeError):
    pass


class ModuleDependencyError(RuntimeError):
    pass


@dataclass(slots=True)
class TaskContext:
    task: TaskDefinition
    settings: dict[str, Any]
    secrets: dict[str, str]
    run_dir: Path
    cancel_event: threading.Event
    log: Callable[[str], None]
    progress: Callable[[int, str], None]

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelled("Задача остановлена пользователем")


def need(module_name: str, install_name: str | None = None):
    try:
        return __import__(module_name)
    except ImportError as exc:
        package = install_name or module_name
        raise ModuleDependencyError(
            f"Не установлен пакет '{package}'. Запустите install_and_run.bat."
        ) from exc


def resolve_path(value: str, workdir: str = "") -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    path = Path(expanded)
    if not path.is_absolute() and workdir:
        path = Path(workdir) / path
    return path.resolve()


def ensure_folder(path: Path, label: str = "Папка") -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{label} не найдена: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"{label} не является папкой: {path}")
    return path


def output_folder(ctx: TaskContext, value: str | None) -> Path:
    if value and value.strip():
        result = resolve_path(value, ctx.task.workdir)
    else:
        result = ctx.run_dir / "output"
    result.mkdir(parents=True, exist_ok=True)
    return result


def parse_ints(value: Any, default: Iterable[int] = ()) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        return [int(x) for x in value]
    if value is None or str(value).strip() == "":
        return list(default)
    result: list[int] = []
    for part in re.split(r"[,;\s]+", str(value).strip()):
        if "-" in part:
            left, right = part.split("-", 1)
            result.extend(range(int(left), int(right) + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def target_date(params: dict[str, Any], fallback_timezone: str) -> date:
    timezone = str(params.get("timezone") or fallback_timezone or "Europe/Moscow")
    today = datetime.now(ZoneInfo(timezone)).date()
    mode = str(params.get("date_mode", "today"))
    if mode == "manual":
        value = str(params.get("manual_date", "")).strip()
        for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
        raise ValueError("Ручная дата должна быть в формате ДД-ММ-ГГГГ")
    if mode == "last_workday":
        wd = today.weekday()
        if wd in (1, 2, 3, 4):
            return today - timedelta(days=1)
        if wd == 0:
            return today - timedelta(days=3)
        return today - timedelta(days=wd - 4)
    return today


def safe_copy_backup(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / source.name
    counter = 2
    while destination.exists():
        destination = backup_dir / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    shutil.copy2(source, destination)
    return destination


def collision_free(destination: Path) -> Path:
    if not destination.exists():
        return destination
    counter = 2
    while True:
        candidate = destination.with_name(f"{destination.stem}_{counter}{destination.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
