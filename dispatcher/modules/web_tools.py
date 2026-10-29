from __future__ import annotations

import codecs
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from .common import TaskContext, resolve_path
from ..process_tree import OwnedProcessTree, read_pipe_available


def _relative_file(value: str, workdir: str) -> Path:
    return resolve_path(value, workdir or ".")


def run_make_links(ctx: TaskContext):
    params = ctx.task.params
    input_path = _relative_file(str(params.get("input_file", "исполнители.txt")), ctx.task.workdir)
    if not input_path.exists():
        raise FileNotFoundError(f"Исходный файл не найден: {input_path}")
    output_value = str(params.get("output_file", "ссылки на песни.txt"))
    output_candidate = Path(output_value)
    output_path = (
        output_candidate
        if output_candidate.is_absolute()
        else input_path.parent / output_candidate
    ).resolve()
    lines = [line.strip() for line in input_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    urls = [f"https://music.yandex.ru/search?text={quote(line)}" for line in lines]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(urls), encoding="utf-8")
    ctx.log(f"Ссылки сохранены: {output_path}")
    return {"message": f"Создано ссылок: {len(urls)}", "files": [str(output_path)]}


def run_open_links(ctx: TaskContext):
    params = ctx.task.params
    input_path = _relative_file(str(params.get("input_file", "ссылки на песни.txt")), ctx.task.workdir)
    if not input_path.exists():
        raise FileNotFoundError(f"Файл ссылок не найден: {input_path}")
    browser_value = str(params.get("browser_path", "")).strip() or str(ctx.settings.get("browser_path", "")).strip()
    browser = resolve_path(browser_value, ctx.task.workdir)
    if not browser.exists():
        raise FileNotFoundError(f"Браузер не найден: {browser}")
    profile = str(params.get("profile", "")).strip() or str(ctx.settings.get("browser_profile", "Profile 1"))
    delay = max(0, int(params.get("delay", 5)))
    urls = []
    for raw in input_path.read_text(encoding="utf-8-sig").splitlines():
        url = raw.strip().strip('"').strip("'")
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        urls.append(url)
    for index, url in enumerate(urls, start=1):
        ctx.check_cancelled()
        subprocess.Popen(
            [str(browser), f"--profile-directory={profile}", url],
            cwd=ctx.task.workdir or None,
        )
        ctx.progress(int(index * 100 / max(1, len(urls))), url)
        ctx.log(f"Открыто: {url}")
        if index < len(urls):
            ctx.cancel_event.wait(delay)
    return {"message": f"Открыто ссылок: {len(urls)}"}


def run_external(ctx: TaskContext):
    params = ctx.task.params
    program = resolve_path(str(params.get("program", "")), ctx.task.workdir)
    if not program.exists():
        raise FileNotFoundError(f"Файл запуска не найден: {program}")
    workdir = resolve_path(ctx.task.workdir or str(program.parent))
    arguments = shlex.split(str(params.get("arguments", "")), posix=False)
    suffix = program.suffix.lower()
    if suffix == ".py":
        python_value = str(params.get("python_path", "")).strip()
        python = str(resolve_path(python_value, ctx.task.workdir)) if python_value else sys.executable
        command = [python, "-u", str(program), *arguments]
    elif suffix in (".bat", ".cmd"):
        command = ["cmd.exe", "/d", "/c", str(program), *arguments]
    else:
        command = [str(program), *arguments]
    ctx.log("Запуск: " + subprocess.list2cmdline(command))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    timeout = max(0, int(params.get("timeout", 0)))
    tree = OwnedProcessTree()
    process = None
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    try:
        ctx.check_cancelled()
        process = tree.popen(
            command,
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        started = time.monotonic()
        while True:
            ctx.check_cancelled()
            if timeout and time.monotonic() - started > timeout:
                raise TimeoutError(f"Превышен тайм-аут {timeout} секунд")
            exited = process.poll() is not None
            output = read_pipe_available(process.stdout)
            if output:
                pending += decoder.decode(output)
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines:
                    if line.rstrip():
                        ctx.log(line.rstrip())
            if exited:
                # A child may still hold stdout open after the launcher exits.
                # Ownership cleanup stops it; waiting for EOF here would hang.
                break
            if not output:
                ctx.cancel_event.wait(0.05)
        pending += decoder.decode(b"", final=True)
        if pending.rstrip():
            ctx.log(pending.rstrip())
    finally:
        try:
            tree.close()
        finally:
            if process is not None and process.stdout:
                process.stdout.close()
    if process.returncode != 0:
        raise RuntimeError(f"Процесс завершился с кодом {process.returncode}")
    return {"message": "Внешняя программа завершена успешно"}
