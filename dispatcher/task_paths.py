"""Describe the paths modules actually read and write without touching them."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .models import TaskDefinition
from .modules import MODULES
from .modules.common import resolve_path
from .storage import application_root


_PATH_KINDS = {"folder", "file", "save_file"}
_OUTPUT_KEYS = {"output_dir", "output_file", "report_file", "errors_file"}
_CASH_MODULES = {
    "broadcast.news", "broadcast.morning_shift", "broadcast.morning_guest",
    "broadcast.universal_cut",
}
_SOURCE_OUTPUTS = {
    "music.foreign_agents": ("report_file", "найденные_совпадения.txt"),
    "music.list_mp3": ("output_file", "mp3_files.txt"),
    "music.musician_card": ("output_file", "track_info.txt"),
}


def _text(value: Any) -> str:
    # A list of source profiles, a flag or a caption is never a filesystem path.
    return value.strip() if isinstance(value, str) else ""


def _fields(task: TaskDefinition):
    spec = MODULES.get(task.module)
    return (field for field in spec.fields if field.kind in _PATH_KINDS) if spec else ()


def _source_folder(task: TaskDefinition, canonical: bool = False) -> Path:
    path_for = resolve_path if canonical else _absolute_path
    return path_for(_text(task.params.get("source_dir", "")), task.workdir)


def _absolute_path(value: str, workdir: str = "") -> Path:
    """Normalize display paths lexically; network access belongs in the worker."""
    candidate = Path(os.path.expandvars(os.path.expanduser(value)))
    if not candidate.is_absolute() and workdir:
        candidate = Path(workdir) / candidate
    return Path(os.path.abspath(candidate))


def _source_relative(value: str, folder: Path, canonical: bool = False) -> Path:
    # Music/report modules join these filenames to the input directory.
    candidate = Path(value)
    path = candidate if candidate.is_absolute() else folder / candidate
    return path.resolve() if canonical else Path(os.path.abspath(path))


def _news_source_enabled(task: TaskDefinition, key: str) -> bool:
    if task.module != "broadcast.news" or not key.startswith("source_"):
        return True
    station = key.removeprefix("source_")
    if bool(task.params.get("use_" + station, True)):
        return True
    # Столица supplies the two optional programme fragments independently.
    return station == "stolica" and (
        bool(task.params.get("include_tormozok", True))
        or bool(task.params.get("include_interview", True))
    )


def task_input_paths(task: TaskDefinition) -> list[tuple[str, str]]:
    """Return scalar input paths and labels, including enabled cut profiles.

    Values remain suitable for display and resolution relative to workdir.
    Source-relative block lists are resolved here to retain their actual base.
    """
    return _input_paths(task)


def _input_paths(task: TaskDefinition, canonical: bool = False) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if task.module == "external" and _text(task.workdir):
        result.append(("workdir", task.workdir.strip()))
    for field in _fields(task):
        if field.key in _OUTPUT_KEYS or not _news_source_enabled(task, field.key):
            continue
        # Registry source defaults seed newly created tasks; modules do not use
        # those defaults when an older saved task omits the parameter.
        fallback = field.default if field.key in {"input_file", "block_list"} else ""
        value = _text(task.params.get(field.key, fallback))
        if not value:
            continue
        if task.module == "music.foreign_agents" and field.key == "block_list":
            value = str(_source_relative(value, _source_folder(task, canonical), canonical))
        result.append((field.key, value))
    if task.module == "broadcast.universal_cut":
        sources = task.params.get("sources", [])
        if isinstance(sources, list):
            for index, source in enumerate(sources, start=1):
                if isinstance(source, dict) and bool(source.get("enabled", True)):
                    folder = _text(source.get("folder"))
                    if folder:
                        result.append((f"sources[{index}].folder", folder))
    return result


def task_output_path(task: TaskDefinition) -> str:
    """Return the effective output file/folder, or empty for an unknown output."""
    return _output_path(task)


def _output_path(task: TaskDefinition, canonical: bool = False) -> str:
    path_for = resolve_path if canonical else _absolute_path
    params = task.params
    output_dir = _text(params.get("output_dir", ""))
    if task.module in _CASH_MODULES:
        return str(path_for(output_dir, task.workdir) if output_dir else application_root() / "cash")
    if task.module == "broadcast.local_news":
        return str(path_for(output_dir, task.workdir) if output_dir else _source_folder(task, canonical))
    if task.module in _SOURCE_OUTPUTS:
        key, default = _SOURCE_OUTPUTS[task.module]
        value = _text(params.get(key, default))
        return str(_source_relative(value, _source_folder(task, canonical), canonical)) if value else ""
    if task.module == "music.download_performers":
        source = path_for(_text(params.get("input_file", "исполнители.txt")), task.workdir)
        return str(path_for(output_dir, task.workdir) if output_dir else source.parent)
    if task.module in {"yandex.make_links", "excel.report"}:
        input_default = "исполнители.txt" if task.module == "yandex.make_links" else ""
        output_default = "ссылки на песни.txt" if task.module == "yandex.make_links" else ""
        raw_input = _text(params.get("input_file", input_default))
        source = path_for(raw_input, task.workdir) if raw_input else None
        value = _text(params.get("output_file", output_default))
        if value:
            folder = source.parent if source else path_for(task.workdir or ".")
            return str(_source_relative(value, folder, canonical))
        if source and task.module == "excel.report":
            return str(source.with_name(f"{source.stem}_готово.xlsx"))
        return ""
    for key in ("output_dir", "output_file", "report_file"):
        value = _text(params.get(key, ""))
        if value:
            return str(path_for(value, task.workdir))
    return ""


def task_resource_paths(task: TaskDefinition) -> set[str]:
    """Return absolute real directory resources, including input-file parents."""
    resources: set[str] = set()
    kinds = {field.key: field.kind for field in _fields(task)}

    def add(path: Path, is_file: bool = False) -> None:
        actual = path.resolve()
        if is_file:
            actual = actual.parent
        resources.add(os.path.normcase(str(actual)))

    if _text(task.workdir):
        add(resolve_path(task.workdir))
    for key, value in _input_paths(task, canonical=True):
        # Executables/interpreters/browser installations are shared read-only
        # tools, not exclusive task data. Keep them in UI access checks only.
        if key in {"workdir", "program", "python_path", "browser_path"}:
            continue
        add(resolve_path(value, task.workdir), kinds.get(key) == "file")
    if task.module == "external" and not _text(task.workdir):
        program = _text(task.params.get("program", ""))
        if program:
            add(resolve_path(program).parent)
    # Blank source_dir is resolved to workdir/CWD by these modules as well.
    if "source_dir" in kinds and not _text(task.params.get("source_dir", "")):
        add(_source_folder(task, canonical=True))
    if task.module == "excel.report" and not _text(task.params.get("input_file", "")):
        add(resolve_path(task.workdir or "."))
    output = _output_path(task, canonical=True)
    if output:
        is_file = task.module in _SOURCE_OUTPUTS or task.module in {"yandex.make_links", "excel.report"}
        if not is_file:
            is_file = not ("output_dir" in kinds) and any(
                _text(task.params.get(key, "")) for key in ("output_file", "report_file")
            )
        add(Path(output), is_file)
    if task.module == "music.download_performers" and output:
        errors = _text(task.params.get("errors_file", "errors.txt"))
        if errors:
            add(_source_relative(errors, Path(output), canonical=True), True)
    return resources
