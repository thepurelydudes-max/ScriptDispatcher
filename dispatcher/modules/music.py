from __future__ import annotations

import random
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from .common import (
    TaskContext,
    collision_free,
    ensure_folder,
    need,
    resolve_path,
    safe_copy_backup,
)


def _from_source(value: str, source: Path, workdir: str = "") -> Path:
    raw = Path(value)
    if raw.is_absolute():
        return raw
    return (source / raw).resolve()


def _mp3_files(source: Path, recursive: bool) -> list[Path]:
    iterator = source.rglob("*") if recursive else source.iterdir()
    return sorted(p for p in iterator if p.is_file() and p.suffix.lower() == ".mp3")


def run_bpm_to_ape(ctx: TaskContext):
    from mutagen.apev2 import APEv2
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3

    params = ctx.task.params
    source = ensure_folder(resolve_path(str(params.get("source_dir", "")), ctx.task.workdir), "Папка MP3")
    files = _mp3_files(source, bool(params.get("recursive", False)))
    changed = skipped = errors = 0
    backup_dir = ctx.run_dir / "backup"
    for index, path in enumerate(files, start=1):
        ctx.check_cancelled()
        ctx.progress(int(index * 100 / max(1, len(files))), path.name)
        try:
            audio = MP3(path, ID3=ID3)
            if not audio.tags or "TBPM" not in audio.tags:
                skipped += 1
                ctx.log(f"Пропуск: BPM не найден — {path}")
                continue
            bpm = audio.tags["TBPM"].text[0]
            try:
                bpm = str(int(round(float(bpm))))
            except (TypeError, ValueError):
                bpm = str(bpm)
            if bool(params.get("backup", True)):
                safe_copy_backup(path, backup_dir)
            try:
                ape = APEv2(path)
            except Exception:
                ape = APEv2()
            ape["BPM"] = bpm
            ape.save(path)
            changed += 1
            ctx.log(f"Готово: {path.name} — BPM {bpm}")
        except Exception as exc:
            errors += 1
            ctx.log(f"Ошибка {path.name}: {exc}")
    return {"message": f"Изменено: {changed}; пропущено: {skipped}; ошибок: {errors}"}


def run_foreign_agents(ctx: TaskContext):
    params = ctx.task.params
    source = ensure_folder(resolve_path(str(params.get("source_dir", "")), ctx.task.workdir), "Музыкальная база")
    block_path = _from_source(str(params.get("block_list", "block_list.txt")), source, ctx.task.workdir)
    if not block_path.exists():
        raise FileNotFoundError(f"Блок-лист не найден: {block_path}")
    blocked = {
        line.strip().lower()
        for line in block_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    }
    files = _mp3_files(source, recursive=True)
    matches: list[str] = []
    for index, path in enumerate(files, start=1):
        ctx.check_cancelled()
        ctx.progress(int(index * 100 / max(1, len(files))), path.name)
        lowered = path.name.lower()
        hit = next((name for name in blocked if name in lowered), None)
        if hit:
            record = f"{path.relative_to(source)} | совпадение: {hit}"
            matches.append(record)
            ctx.log(f"Найдено: {record}")
    report = _from_source(str(params.get("report_file", "найденные_совпадения.txt")), source, ctx.task.workdir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(matches), encoding="utf-8")
    return {
        "message": f"Проверено MP3: {len(files)}; совпадений: {len(matches)}",
        "files": [str(report)],
    }


def _detect_language(text: str, detector) -> str:
    try:
        words = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", text)
        if not words:
            return "unknown"
        counts = Counter(detector(word) for word in words)
        if counts["ru"] > counts["en"]:
            return "ru"
        if counts["en"] > counts["ru"]:
            return "en"
    except Exception:
        pass
    return "unknown"


def run_language_sort(ctx: TaskContext):
    langdetect = need("langdetect")
    params = ctx.task.params
    source = ensure_folder(resolve_path(str(params.get("source_dir", "")), ctx.task.workdir), "Исходная папка")
    folders = {
        "ru": source / str(params.get("russian_folder", "russian")),
        "en": source / str(params.get("english_folder", "english")),
        "unknown": source / str(params.get("unknown_folder", "unknown")),
    }
    operation = str(params.get("operation", "move"))
    files = _mp3_files(source, recursive=False)
    counts = Counter()
    for index, path in enumerate(files, start=1):
        ctx.check_cancelled()
        language = _detect_language(path.stem, langdetect.detect)
        destination_dir = folders[language]
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = collision_free(destination_dir / path.name)
        if operation == "copy":
            shutil.copy2(path, destination)
        else:
            shutil.move(str(path), str(destination))
        counts[language] += 1
        ctx.progress(int(index * 100 / max(1, len(files))), path.name)
        ctx.log(f"{path.name} → {destination_dir.name}")
    return {
        "message": (
            f"Русские: {counts['ru']}; английские: {counts['en']}; "
            f"не определены: {counts['unknown']}"
        )
    }


def run_list_mp3(ctx: TaskContext):
    params = ctx.task.params
    source = ensure_folder(resolve_path(str(params.get("source_dir", "")), ctx.task.workdir), "Папка MP3")
    output = _from_source(str(params.get("output_file", "mp3_files.txt")), source, ctx.task.workdir)
    files = _mp3_files(source, recursive=True)
    relative = bool(params.get("include_relative_path", False))
    lines = [str(path.relative_to(source)) if relative else path.name for path in files]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    ctx.log(f"Список сохранён: {output}")
    return {"message": f"Найдено MP3: {len(files)}", "files": [str(output)]}


class YandexMusicApi:
    def __init__(self, ctx: TaskContext):
        self.ctx = ctx
        self.requests = need("requests")
        self.cookie = ctx.secrets.get("yandex_cookie", "").strip()
        if not self.cookie:
            raise ValueError("В общих настройках не заполнен Cookie Яндекс Музыки")
        self.timeout = max(1, int(ctx.task.params.get("request_timeout", 20)))
        self.retries = max(1, int(ctx.task.params.get("max_retries", 4)))
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Cookie": self.cookie,
            "Referer": "https://music.yandex.ru/",
            "Origin": "https://music.yandex.ru",
        }

    def get(self, url: str):
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self.ctx.check_cancelled()
            try:
                response = self.requests.get(
                    url,
                    headers=self.headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                if response.status_code == 200:
                    return response.json()
                last_error = RuntimeError(f"HTTP {response.status_code}: {(response.text or '')[:200]}")
                if response.status_code not in (429, 500, 502, 503, 504):
                    break
            except Exception as exc:
                last_error = exc
            self.ctx.cancel_event.wait(min(30, 2**attempt))
        raise RuntimeError(f"Ошибка запроса Яндекс Музыки: {last_error}")

    def search(self, query: str):
        data = self.get(
            f"https://api.music.yandex.ru/search?text={quote(query)}&type=track&page=0&pageSize=1"
        )
        results = (((data.get("result") or {}).get("tracks") or {}).get("results") or [])
        if not results:
            return None
        track = results[0]
        artists = ", ".join(
            artist.get("name", "")
            for artist in (track.get("artists") or [])
            if isinstance(artist, dict) and artist.get("name")
        )
        album_id = None
        albums = track.get("albums") or []
        if albums and isinstance(albums[0], dict):
            album_id = albums[0].get("id")
        track_id = track.get("id")
        if album_id and track_id:
            url = f"https://music.yandex.ru/album/{album_id}/track/{track_id}"
        elif track_id:
            url = f"https://music.yandex.ru/track/{track_id}"
        else:
            url = "—"
        return {
            "id": str(track_id) if track_id is not None else "",
            "title": track.get("title") or "Неизвестно",
            "artists": artists or "Неизвестно",
            "url": url,
        }

    def credits(self, track_id: str) -> dict[str, str]:
        data = self.get(f"https://api.music.yandex.ru/tracks/{track_id}/credits")
        raw = (data.get("result") or {}).get("credits") or []
        values = {
            item.get("title"): item.get("value")
            for item in raw
            if isinstance(item, dict) and item.get("title")
        }
        return {
            "Исполнитель": values.get("Исполнитель", "Не указано"),
            "Автор музыки": values.get("Автор музыки", "Не указано"),
            "Автор текста": values.get("Автор текста", "Не указано"),
            "Источник фонограммы": values.get("Источник фонограммы", "Не указано"),
        }


def run_musician_card(ctx: TaskContext):
    from mutagen.apev2 import APEv2File

    params = ctx.task.params
    source = ensure_folder(resolve_path(str(params.get("source_dir", "")), ctx.task.workdir), "Папка MP3")
    output = _from_source(str(params.get("output_file", "track_info.txt")), source, ctx.task.workdir)
    api = YandexMusicApi(ctx)
    files = _mp3_files(source, recursive=False)
    lines: list[str] = []
    updated = found_count = 0
    backup_dir = ctx.run_dir / "backup"
    delay_min = max(0, int(params.get("delay_min", 2)))
    delay_max = max(delay_min, int(params.get("delay_max", 5)))
    for index, path in enumerate(files, start=1):
        ctx.check_cancelled()
        ctx.progress(int(index * 100 / max(1, len(files))), path.name)
        try:
            found = api.search(path.stem)
            if not found:
                lines.extend([f"Название файла: {path.name}", "Найденный трек: Не найдено", "-" * 40])
                continue
            credits = api.credits(found["id"]) if found["id"] else {}
            found_count += 1
            lines.extend(
                [
                    f"Название файла: {path.name}",
                    f"Найденный трек: {found['title']} - {found['artists']}",
                    f"Ссылка: {found['url']}",
                    f"Исполнитель: {credits.get('Исполнитель', 'Не найдено')}",
                    f"Автор музыки: {credits.get('Автор музыки', 'Не найдено')}",
                    f"Автор текста: {credits.get('Автор текста', 'Не найдено')}",
                    f"Источник фонограммы: {credits.get('Источник фонограммы', 'Не найдено')}",
                    "-" * 40,
                ]
            )
            if credits and bool(params.get("update_tags", True)):
                if bool(params.get("backup", True)):
                    safe_copy_backup(path, backup_dir)
                audio = APEv2File(path)
                if audio.tags is None:
                    audio.add_tags()
                audio.tags["RBFIELD1"] = found["title"] or ""
                artist = str(credits.get("Исполнитель", "")).strip()
                if artist and artist != "Не указано":
                    audio.tags["Artist"] = artist
                audio.tags["RBFIELD2"] = credits.get("Автор музыки", "") or ""
                audio.tags["RBFIELD3"] = credits.get("Автор текста", "") or ""
                audio.tags["RBFIELD4"] = credits.get("Источник фонограммы", "") or ""
                audio.save()
                updated += 1
            ctx.log(f"Обработано: {path.name}")
        except Exception as exc:
            lines.extend([f"Название файла: {path.name}", f"Ошибка: {exc}", "-" * 40])
            ctx.log(f"Ошибка {path.name}: {exc}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8")
        if index < len(files):
            ctx.cancel_event.wait(random.randint(delay_min, delay_max))
    return {
        "message": f"Найдено: {found_count}; обновлено тегов: {updated}",
        "files": [str(output)],
    }


def run_download_performers(ctx: TaskContext):
    requests = need("requests")
    need("bs4", "beautifulsoup4")
    from bs4 import BeautifulSoup

    params = ctx.task.params
    base = resolve_path(ctx.task.workdir or ".")
    input_value = str(params.get("input_file", "исполнители.txt"))
    input_path = resolve_path(input_value, ctx.task.workdir or str(base))
    if not input_path.exists():
        raise FileNotFoundError(f"Список не найден: {input_path}")
    output = (
        resolve_path(str(params.get("output_dir", "")), ctx.task.workdir)
        if str(params.get("output_dir", "")).strip()
        else input_path.parent
    )
    output.mkdir(parents=True, exist_ok=True)
    errors_path = _from_source(str(params.get("errors_file", "errors.txt")), output)
    timeout = max(1, int(params.get("timeout", 15)))
    template = str(params.get("search_url", "https://eu.hitmotop.com/search?q={query}"))
    entries = [line.strip() for line in input_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    errors: list[str] = []
    downloaded = 0
    for index, entry in enumerate(entries, start=1):
        ctx.check_cancelled()
        ctx.progress(int(index * 100 / max(1, len(entries))), entry)
        if " - " not in entry:
            errors.append(f"{entry} | неверный формат")
            continue
        query = quote(entry.replace(" - ", " "))
        try:
            response = requests.get(template.format(query=query), timeout=timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            link = next(
                (tag.get("href") for tag in soup.find_all("a") if str(tag.get("href", "")).lower().endswith(".mp3")),
                None,
            )
            if not link:
                raise RuntimeError("ссылка MP3 не найдена")
            filename = Path(str(link).split("?", 1)[0]).name or f"{entry}.mp3"
            destination = collision_free(output / filename)
            with requests.get(link, timeout=timeout, stream=True) as audio_response:
                audio_response.raise_for_status()
                with destination.open("wb") as stream:
                    for chunk in audio_response.iter_content(1024 * 128):
                        ctx.check_cancelled()
                        if chunk:
                            stream.write(chunk)
            downloaded += 1
            ctx.log(f"Скачано: {entry}")
        except Exception as exc:
            errors.append(f"{entry} | {exc}")
            ctx.log(f"Не удалось скачать {entry}: {exc}")
    errors_path.write_text("\n".join(errors), encoding="utf-8")
    return {
        "message": f"Скачано: {downloaded}; ошибок: {len(errors)}",
        "files": [str(errors_path)],
    }
