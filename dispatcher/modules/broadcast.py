from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .common import (
    ModuleDependencyError,
    TaskContext,
    ensure_folder,
    need,
    parse_ints,
    resolve_path,
    target_date,
)
from ..storage import application_root, atomic_json_write, data_dir


def _broadcast_output(ctx: TaskContext, value: str | None) -> Path:
    if value and value.strip():
        result = resolve_path(value, ctx.task.workdir)
    else:
        result = application_root() / "cash"
    result.mkdir(parents=True, exist_ok=True)
    return result


def _audio_segment(ctx: TaskContext):
    need("pydub")
    from pydub import AudioSegment

    ffmpeg = str(ctx.settings.get("ffmpeg_path", "")).strip()
    if ffmpeg:
        AudioSegment.converter = ffmpeg
    return AudioSegment


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower().replace("ё", "е"))


def _format_internal_bot_template(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format_map(values)
    except KeyError as exc:
        key = str(exc.args[0])
        raise ValueError(
            f"Неизвестная переменная {{{key}}} в шаблоне внутреннего бота"
        ) from exc
    except ValueError as exc:
        raise ValueError(f"Ошибка шаблона внутреннего бота: {exc}") from exc


def _internal_bot_upload_name_from_base(base_name: str, path: Path) -> str:
    """Build the bot filename from a human-editable base name.

    The generated fragment already contains the source date/time after the first
    underscore (for example ``stolica_17-08-2026_11-00.mp3``).  In the UI the
    user edits only ``stolica``; the varying suffix is preserved automatically.
    """
    base_name = str(base_name or "").strip()
    if not base_name:
        return path.name

    suffix = path.suffix
    if suffix and base_name.lower().endswith(suffix.lower()):
        base_name = base_name[: -len(suffix)].rstrip()
    base_name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", base_name).strip().strip(".")
    if not base_name:
        return path.name

    stem = path.stem
    source_stem = stem.split("_", 1)[1] if "_" in stem else stem
    return f"{base_name}_{source_stem}{suffix}"


def _internal_bot_upload_name(
    template: str,
    path: Path,
    *,
    date_text: str,
    task_name: str,
    group_title: str,
    index: int,
) -> str:
    template = str(template or "").strip()
    if not template:
        return path.name
    stem = path.stem
    if "_" in stem:
        file_type, source_stem = stem.split("_", 1)
    else:
        file_type, source_stem = "", stem
    values = {
        "date": date_text,
        "task": task_name,
        "group": group_title,
        "original": path.name,
        "stem": stem,
        "ext": path.suffix,
        "index": index,
        "type": file_type,
        "source": source_stem,
    }
    rendered = _format_internal_bot_template(template, values).strip()
    rendered = re.sub(r'[\\/:*?"<>|\r\n]+', "_", rendered).strip().strip(".")
    if not rendered:
        return path.name
    if path.suffix and not rendered.lower().endswith(path.suffix.lower()):
        rendered += path.suffix
    return rendered


def cue_time_to_seconds(value: str) -> float:
    minute, second, frame = map(int, value.split(":"))
    return minute * 60 + second + frame / 75


def parse_cue(path: Path) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line.startswith("TRACK"):
            if current:
                tracks.append(current)
            current = {"title": "", "index": ""}
        elif current is not None and line.startswith("TITLE"):
            match = re.search(r'"(.*?)"', line)
            if match:
                current["title"] = match.group(1)
        elif current is not None and line.startswith("INDEX 01"):
            current["index"] = line.split("INDEX 01", 1)[1].strip()
    if current:
        tracks.append(current)
    valid = [item for item in tracks if item.get("index")]
    for index, track in enumerate(valid):
        track["start"] = cue_time_to_seconds(track["index"])
        track["end"] = (
            cue_time_to_seconds(valid[index + 1]["index"]) if index + 1 < len(valid) else None
        )
    return valid


def collect_pairs(folder: Path, day_text: str, hours: list[int] | None = None) -> dict[str, tuple[Path, Path]]:
    pattern = re.compile(rf"^{re.escape(day_text)}_(\d{{2}})-(\d{{2}})$")
    allowed = set(hours or [])
    mp3: dict[str, Path] = {}
    cue: dict[str, Path] = {}
    for item in folder.iterdir():
        if not item.is_file():
            continue
        match = pattern.match(item.stem)
        if not match:
            continue
        if allowed and int(match.group(1)) not in allowed:
            continue
        if item.suffix.lower() == ".mp3":
            mp3[item.stem] = item
        elif item.suffix.lower() == ".cue":
            cue[item.stem] = item
    return {key: (mp3[key], cue[key]) for key in sorted(mp3.keys() & cue.keys())}


def collect_all_pairs(folder: Path) -> dict[str, tuple[Path, Path]]:
    mp3 = {p.stem: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".mp3"}
    cue = {p.stem: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".cue"}
    return {key: (mp3[key], cue[key]) for key in sorted(mp3.keys() & cue.keys())}


def group_news(tracks: list[dict[str, Any]], marker: str) -> list[list[dict[str, Any]]]:
    blocks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    marker = normalize_title(marker)
    for track in tracks:
        if marker in normalize_title(track["title"]):
            current.append(track)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def group_rolik(tracks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    blocks: list[list[dict[str, Any]]] = []
    start: int | None = None
    for index, track in enumerate(tracks):
        title = normalize_title(track["title"])
        if start is None and title.startswith("1. rolik"):
            start = index
        elif start is not None and title.startswith("4. rolik"):
            blocks.append(tracks[start : index + 1])
            start = None
    return blocks


def _slice_tracks(audio, tracks: list[dict[str, Any]]):
    AudioSegment = type(audio)
    combined = AudioSegment.silent(duration=0)
    for track in tracks:
        start_ms = int(track["start"] * 1000)
        end_seconds = track["end"] if track["end"] is not None else len(audio) / 1000
        combined += audio[start_ms : int(end_seconds * 1000)]
    return combined


def export_news(
    ctx: TaskContext,
    mp3: Path,
    cue: Path,
    output: Path,
    prefix: str,
    marker: str,
    rolik_mode: bool = False,
) -> list[Path]:
    ctx.check_cancelled()
    tracks = parse_cue(cue)
    blocks = group_rolik(tracks) if rolik_mode else group_news(tracks, marker)
    if not blocks:
        ctx.log(f"Маркерные блоки не найдены: {cue.name}")
        return []
    AudioSegment = _audio_segment(ctx)
    audio = AudioSegment.from_mp3(mp3)
    created: list[Path] = []
    for index, block in enumerate(blocks):
        ctx.check_cancelled()
        first_minute = int(block[0]["start"] // 60)
        base = cue.stem
        if index:
            base = re.sub(r"_(\d{2})-\d{2}$", rf"_\1-{first_minute:02d}", base)
        destination = output / f"{prefix}_{base}.mp3"
        _slice_tracks(audio, block).export(destination, format="mp3")
        created.append(destination)
        ctx.log(f"Сохранено: {destination}")
    return created


def export_marker_blocks(
    ctx: TaskContext,
    mp3: Path,
    cue: Path,
    output: Path,
    output_prefix: str,
    marker: str,
) -> list[Path]:
    """Export each consecutive block of CUE tracks matching *marker*.

    This mirrors the original news logic: every adjacent CUE track whose title
    contains the marker belongs to the same release.  The first non-matching
    track closes the block.  A later matching run becomes a new release.
    """
    ctx.check_cancelled()
    tracks = parse_cue(cue)
    blocks = group_news(tracks, marker)
    if not blocks:
        ctx.log(f"Блоки с маркером не найдены: {cue.name}")
        return []
    AudioSegment = _audio_segment(ctx)
    audio = AudioSegment.from_mp3(mp3)

    created: list[Path] = []
    used_names: set[str] = set()
    for occurrence, block in enumerate(blocks, start=1):
        ctx.check_cancelled()
        start_seconds = float(block[0]["start"])
        end_seconds = block[-1]["end"]
        if end_seconds is None:
            end_seconds = len(audio) / 1000

        base = _cue_base_at_offset(cue, start_seconds)
        filename = f"{output_prefix}_{base}.mp3"
        if filename in used_names:
            filename = f"{Path(filename).stem}_{occurrence}.mp3"
        used_names.add(filename)

        destination = output / filename
        audio[int(start_seconds * 1000) : int(float(end_seconds) * 1000)].export(
            destination, format="mp3"
        )
        created.append(destination)
        ctx.log(f"Сохранено (блок по одному маркеру): {destination}")
    return created


# Совместимость с заданиями/импортами версии 1.2.12–1.2.13.
export_single_marker_tracks = export_marker_blocks


def _cue_base_at_offset(cue: Path, start_seconds: float) -> str:
    """Return the hourly CUE base name adjusted to the fragment start time."""
    total_seconds = int(round(float(start_seconds)))
    minute = total_seconds // 60
    second = total_seconds % 60
    base = cue.stem
    match = re.search(r"_(\d{2})-(\d{2})$", base)
    if match:
        hour = match.group(1)
        replacement = f"_{hour}-{minute:02d}"
        if second:
            replacement += f"-{second:02d}"
        base = re.sub(r"_(\d{2})-(\d{2})$", replacement, base)
    return base


def export_between_markers(
    ctx: TaskContext,
    mp3: Path,
    cue: Path,
    output: Path,
    output_prefix: str,
    start_marker: str,
    end_marker: str | None = None,
    same_marker_twice: bool = False,
) -> Path | None:
    ctx.check_cancelled()
    tracks = parse_cue(cue)
    start_index: int | None = None
    end_index: int | None = None
    start_norm = normalize_title(start_marker)
    end_norm = normalize_title(end_marker or start_marker)
    for index, track in enumerate(tracks):
        title = normalize_title(track["title"])
        if start_index is None and start_norm in title:
            start_index = index
            continue
        if start_index is not None:
            match = start_norm in title if same_marker_twice else end_norm in title
            if match:
                end_index = index
                break
    if start_index is None or end_index is None or end_index < start_index:
        ctx.log(f"Не найдены маркеры фрагмента: {cue.name}")
        return None
    AudioSegment = _audio_segment(ctx)
    audio = AudioSegment.from_mp3(mp3)
    start_ms = int(tracks[start_index]["start"] * 1000)
    end_seconds = tracks[end_index]["end"]
    if end_seconds is None:
        end_seconds = len(audio) / 1000
    destination = output / f"{output_prefix}_{cue.stem}.mp3"
    audio[start_ms : int(end_seconds * 1000)].export(destination, format="mp3")
    ctx.log(f"Сохранено: {destination}")
    return destination


class InternalBotClient:
    def __init__(
        self,
        ctx: TaskContext,
        *,
        channel: str | None = None,
        title: str | None = None,
    ):
        self.ctx = ctx
        self.requests = need("requests")
        self.base = str(ctx.settings.get("internal_bot_base_url", "")).strip().rstrip("/")
        self.key = ctx.secrets.get("internal_bot_key", "").strip()
        self.channel = str(
            ctx.settings.get("internal_bot_channel", "") if channel is None else channel
        ).strip()
        self.title = str(
            ctx.settings.get("internal_bot_title", "") if title is None else title
        ).strip()
        if not self.base or not self.key or not self.channel or not self.title:
            raise ValueError(
                "В общих настройках внутреннего бота должны быть заполнены "
                "адрес сервера, ключ, канал и заголовок"
            )
        if not self.base.startswith(("http://", "https://")):
            raise ValueError("Адрес внутреннего бота должен начинаться с http:// или https://")
        self.retries = max(0, int(ctx.settings.get("internal_bot_max_retries", 0)))
        self.delay = max(0.2, float(ctx.settings.get("internal_bot_retry_delay", 2)))
        self.separator_delay = max(
            0,
            float(ctx.settings.get("internal_bot_separator_delay", 2)),
        )
        self.file_delay = max(0, float(ctx.settings.get("internal_bot_file_delay", 0)))

    def _retry(self, action, description: str):
        last_error: Exception | None = None
        attempt = 0
        while True:
            attempt += 1
            self.ctx.check_cancelled()
            try:
                response = action()
                if response.status_code == 200 and response.json().get("ok", False):
                    return response
                body = (response.text or "")[:300]
                last_error = RuntimeError(f"HTTP {response.status_code}: {body}")
            except Exception as exc:
                last_error = exc
            total = str(self.retries) if self.retries else "до успеха"
            self.ctx.log(f"{description}: попытка {attempt}/{total} не удалась: {last_error}")
            if self.retries and attempt >= self.retries:
                raise RuntimeError(f"{description}: {last_error}")
            backoff = self.delay * (1.8 ** min(attempt - 1, 20))
            self.ctx.cancel_event.wait(min(60, backoff))

    def send_text(self, text: str) -> None:
        payload = {
            "key": self.key,
            "channel": self.channel,
            "title": self.title,
            "text": text,
        }
        self._retry(
            lambda: self.requests.post(
                f"{self.base}/api/internal/message",
                json=payload,
                timeout=30,
            ),
            "Отправка сообщения",
        )
        self.ctx.log(f"Внутренний бот: {text}")

    def send_audio(
        self,
        path: Path,
        text: str = "",
        upload_name: str | None = None,
    ) -> None:
        upload_name = str(upload_name or path.name)

        def action():
            with path.open("rb") as stream:
                return self.requests.post(
                    f"{self.base}/api/internal/upload",
                    data={
                        "key": self.key,
                        "channel": self.channel,
                        "title": self.title,
                        "text": text,
                    },
                    files={"file": (upload_name, stream, "audio/mpeg")},
                    timeout=600,
                )

        self._retry(action, f"Отправка {path.name}")
        if upload_name == path.name:
            self.ctx.log(f"Внутренний бот принял файл: {path.name}")
        else:
            self.ctx.log(
                f"Внутренний бот принял файл: {path.name} → {upload_name}"
            )

    def send_group(
        self,
        title: str,
        paths: list[Path],
        *,
        message_template: str = "",
        filename_template: str = "",
        filename_base: str = "",
        date_text: str = "",
        template_values: dict[str, Any] | None = None,
    ) -> None:
        message_template = str(message_template or "").strip()
        if message_template:
            values = {
                "date": date_text,
                "task": self.ctx.task.name,
                "group": title,
            }
            values.update(template_values or {})
            message = _format_internal_bot_template(message_template, values)
        else:
            message = title
        self.send_text(message)

        if paths and self.separator_delay:
            self.ctx.cancel_event.wait(self.separator_delay)
        for index, path in enumerate(paths, start=1):
            self.ctx.check_cancelled()
            if str(filename_base or "").strip():
                upload_name = _internal_bot_upload_name_from_base(filename_base, path)
            else:
                upload_name = _internal_bot_upload_name(
                    filename_template,
                    path,
                    date_text=date_text,
                    task_name=self.ctx.task.name,
                    group_title=title,
                    index=index,
                )
            self.send_audio(path, upload_name=upload_name)
            if index < len(paths) and self.file_delay:
                self.ctx.cancel_event.wait(self.file_delay)


def _clean_generated(ctx: TaskContext, paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            ctx.log(f"Не удалось удалить {path}: {exc}")


def _cleanup_broadcast_output(
    ctx: TaskContext,
    output: Path,
    created: list[Path],
    custom_output: str,
) -> None:
    if custom_output.strip():
        _clean_generated(ctx, created)
        return
    default_cash = (application_root() / "cash").resolve()
    if output.resolve() != default_cash:
        raise RuntimeError(f"Отказ от очистки неожиданной папки: {output}")
    shutil.rmtree(default_cash, ignore_errors=False)
    ctx.log(f"Папка cash удалена: {default_cash}")


def run_news(ctx: TaskContext) -> dict[str, Any]:
    params = ctx.task.params
    day = target_date(params, str(ctx.settings.get("timezone", "Europe/Moscow")))
    day_text = day.strftime("%d-%m-%Y")
    custom_output = str(params.get("output_dir", ""))
    output = _broadcast_output(ctx, custom_output)
    marker = str(params.get("news_marker", "новости"))
    tormozok_name = str(params.get("tormozok_name", "Тормозок")).strip() or "Тормозок"
    interview_name = str(params.get("interview_name", "Интервью")).strip() or "Интервью"
    manual_hours = str(params.get("date_mode", "today")) == "manual"
    hours_prefix = "manual_hours_" if manual_hours else "hours_"
    sources = [
        (
            bool(params.get("use_kometa", True)),
            "kometa",
            "КОМЕТА",
            params.get("source_kometa"),
            parse_ints(params.get(hours_prefix + "kometa"), range(9, 20)),
        ),
        (
            bool(params.get("use_stolica", True)),
            "stolica",
            "СТОЛИЦА",
            params.get("source_stolica"),
            parse_ints(params.get(hours_prefix + "stolica"), range(9, 20)),
        ),
        (
            bool(params.get("use_briz", True)),
            "briz",
            "БРИЗ",
            params.get("source_briz"),
            parse_ints(params.get(hours_prefix + "briz"), range(10, 17)),
        ),
    ]
    groups: dict[str, list[Path]] = {}
    group_kinds: dict[str, str] = {}
    ctx.log(f"Дата обработки: {day_text}")
    ctx.log(
        "Профиль часов: ручная дата"
        if manual_hours
        else "Профиль часов: сегодня / последний рабочий день"
    )
    for enabled, prefix, caption, raw_path, hours in sources:
        ctx.check_cancelled()
        if not enabled:
            ctx.log(f"{caption}: источник отключён")
            continue
        try:
            folder = ensure_folder(resolve_path(str(raw_path or ""), ctx.task.workdir), f"Источник {caption}")
        except Exception as exc:
            ctx.log(str(exc))
            continue
        pairs = collect_pairs(folder, day_text, hours)
        ctx.log(f"{caption}: найдено пар MP3+CUE — {len(pairs)}")
        made: list[Path] = []
        for index, (base, (mp3, cue)) in enumerate(pairs.items(), start=1):
            ctx.progress(int(index * 100 / max(1, len(pairs))), f"{caption}: {base}")
            made.extend(export_news(ctx, mp3, cue, output, prefix, marker))
        if made:
            title = f"НОВОСТИ {caption} {day_text}"
            groups[title] = made
            group_kinds[title] = prefix

    stolica_enabled = bool(params.get("use_stolica", True))
    stolica = None
    if stolica_enabled:
        stolica_raw = str(params.get("source_stolica", ""))
        try:
            stolica = ensure_folder(resolve_path(stolica_raw, ctx.task.workdir), "Источник Столицы")
        except Exception:
            stolica = None
    if stolica and bool(params.get("include_tormozok", True)):
        hour = int(params.get("tormozok_hour", 12))
        made = []
        for _base, (mp3, cue) in collect_pairs(stolica, day_text, [hour]).items():
            result = export_between_markers(
                ctx,
                mp3,
                cue,
                output,
                "tormozok",
                str(params.get("tormozok_start", "влет тормозок")),
                str(params.get("tormozok_end", "вылет тормозок")),
            )
            if result:
                made.append(result)
        if made:
            title = f"{tormozok_name.upper()} {day_text}"
            groups[title] = made
            group_kinds[title] = "tormozok"

    if stolica and bool(params.get("include_interview", True)):
        hour = int(params.get("interview_hour", 10))
        made = []
        for _base, (mp3, cue) in collect_pairs(stolica, day_text, [hour]).items():
            result = export_between_markers(
                ctx,
                mp3,
                cue,
                output,
                "interview",
                str(params.get("interview_start", "rolik гость в студии")),
                str(params.get("interview_end", "rolik вылет гость в студии")),
            )
            if result:
                made.append(result)
        if made:
            title = f"{interview_name.upper()} {day_text} {hour:02d}:00"
            groups[title] = made
            group_kinds[title] = "interview"

    created = [path for paths in groups.values() for path in paths]
    if not created:
        return {"message": "Подходящих фрагментов не найдено", "files": []}
    send_enabled = bool(params.get("internal_bot_enabled", True))
    if send_enabled:
        internal_bot = InternalBotClient(ctx)
        defaults = {
            "kometa": ("НОВОСТИ КОМЕТА {date}", "kometa"),
            "stolica": ("НОВОСТИ СТОЛИЦА {date}", "stolica"),
            "briz": ("НОВОСТИ БРИЗ {date}", "briz"),
            "tormozok": (f"{tormozok_name.upper()} {{date}}", "tormozok"),
            "interview": (f"{interview_name.upper()} {{date}} {{hour}}:00", "interview"),
        }
        legacy_message = str(params.get("internal_bot_message", "")).strip()
        legacy_filename = str(params.get("internal_bot_filename", "")).strip()
        legacy_filename_is_default = legacy_filename in {
            "", "{original}", "{type}_{source}{ext}"
        }

        for title, paths in groups.items():
            ctx.check_cancelled()
            kind = group_kinds.get(title, "")
            default_message, default_base = defaults.get(kind, ("{group}", ""))

            specific_message_key = f"internal_bot_message_{kind}"
            if specific_message_key in params:
                message_template = str(params.get(specific_message_key, ""))
            elif legacy_message and legacy_message != "{group}":
                message_template = legacy_message
            else:
                message_template = default_message

            specific_filename_key = f"internal_bot_filename_{kind}"
            filename_template = ""
            if specific_filename_key in params:
                filename_base = str(params.get(specific_filename_key, ""))
            elif not legacy_filename_is_default:
                # Preserve a custom filename template created in 1.2.6–1.2.8.
                filename_base = ""
                filename_template = legacy_filename
            else:
                filename_base = default_base

            template_values: dict[str, Any] = {}
            if kind == "interview":
                template_values["hour"] = f"{int(params.get('interview_hour', 10)):02d}"

            internal_bot.send_group(
                title,
                sorted(paths),
                message_template=message_template,
                filename_template=filename_template,
                filename_base=filename_base,
                date_text=day_text,
                template_values=template_values,
            )
        if bool(params.get("cleanup_after_send", True)):
            ctx.check_cancelled()
            _cleanup_broadcast_output(ctx, output, created, custom_output)
    return {"message": f"Создано файлов: {len(created)}", "files": [str(x) for x in created]}


def _run_program_fragment(ctx: TaskContext, guest: bool) -> dict[str, Any]:
    params = ctx.task.params
    source = ensure_folder(resolve_path(str(params.get("source_dir", "")), ctx.task.workdir), "Папка записей")
    day = target_date(params, str(ctx.settings.get("timezone", "Europe/Moscow")))
    day_text = day.strftime("%d-%m-%Y")
    hours = parse_ints(params.get("hours"), [8, 9, 10])
    custom_output = str(params.get("output_dir", ""))
    output = _broadcast_output(ctx, custom_output)
    made: list[Path] = []
    for _base, (mp3, cue) in collect_pairs(source, day_text, hours).items():
        ctx.check_cancelled()
        if guest:
            result = export_between_markers(
                ctx,
                mp3,
                cue,
                output,
                "guest",
                str(params.get("marker", "rolik влет - вылет утренний гость")),
                same_marker_twice=True,
            )
        else:
            result = export_between_markers(
                ctx,
                mp3,
                cue,
                output,
                "smena",
                str(params.get("start_marker", "rolik утренняя смена влет")),
                str(params.get("end_marker", "rolik утренняя смена вылет")),
            )
        if result:
            made.append(result)
    if not made:
        return {"message": "Подходящие фрагменты не найдены", "files": []}
    send_enabled = bool(params.get("internal_bot_enabled", True))
    if send_enabled:
        title = "УТРЕННИЙ ГОСТЬ" if guest else "УТРЕННЯЯ СМЕНА"
        InternalBotClient(ctx).send_group(
            f"{title} {day_text}",
            made,
            message_template=str(params.get("internal_bot_message", "")),
            filename_template=str(params.get("internal_bot_filename", "")),
            date_text=day_text,
        )
        if bool(params.get("cleanup_after_send", True)):
            ctx.check_cancelled()
            _cleanup_broadcast_output(ctx, output, made, custom_output)
    return {"message": f"Создано файлов: {len(made)}", "files": [str(x) for x in made]}


def run_morning_shift(ctx: TaskContext) -> dict[str, Any]:
    return _run_program_fragment(ctx, guest=False)


def run_morning_guest(ctx: TaskContext) -> dict[str, Any]:
    return _run_program_fragment(ctx, guest=True)


def _safe_output_base(value: str, fallback: str = "fragment") -> str:
    value = str(value or "").strip()
    if value.lower().endswith(".mp3"):
        value = value[:-4].rstrip()
    value = re.sub(r'[\\/:*?"<>|\r\n]+', "_", value).strip().strip(".")
    return value or fallback


_UNIVERSAL_CUT_TIMESTAMP = re.compile(
    r"(?P<date>\d{2}-\d{2}-\d{4})_(?P<hour>\d{2})-(?P<minute>\d{2})"
    r"(?:-(?P<second>\d{2}))?(?:_(?P<occurrence>\d+))?$"
)


_UNIVERSAL_CUT_TIME_SHIFT = re.compile(
    r"\{hour(?P<hours>[+-]\d+)?-minute(?P<minutes>[+-]\d+)?\}"
)


def _universal_cut_output_name(template: str, generated: Path, source: str) -> str:
    """Render output MP3 filename from the actual time encoded in the CUE.

    Legacy {date}, {date-full}, {hour-minute} remain supported. New date
    placeholders and hour/minute offsets work only in this universal cutter.
    A shifted timestamp also changes date placeholders on midnight rollover.
    """
    match = _UNIVERSAL_CUT_TIMESTAMP.search(generated.stem)
    if not match:
        raise ValueError(f"Не удалось определить дату и время из имени: {generated.name}")

    recorded = datetime.strptime(
        f"{match.group('date')} {match.group('hour')}:{match.group('minute')}",
        "%d-%m-%Y %H:%M",
    )
    # Each shifted time token uses the original recording timestamp, not the
    # result of a previous replacement. Offsets roll over at midnight correctly.
    shifts = list(_UNIVERSAL_CUT_TIME_SHIFT.finditer(template))
    unique_offsets = {
        (int(item.group("hours") or 0), int(item.group("minutes") or 0))
        for item in shifts
    }
    date_tokens = ("{date}", "{date-full}", "{dd-mm-yy}", "{dd-mm}")
    if len(unique_offsets) > 1 and any(token in template for token in date_tokens):
        raise ValueError(
            "В шаблоне указаны разные сдвиги времени и дата: "
            "невозможно однозначно определить дату выходного файла"
        )
    date_point = recorded
    if len(unique_offsets) == 1:
        delta_h, delta_m = next(iter(unique_offsets))
        date_point += timedelta(hours=delta_h, minutes=delta_m)

    def replace_shift(item: re.Match[str]) -> str:
        shifted = recorded + timedelta(
            hours=int(item.group("hours") or 0),
            minutes=int(item.group("minutes") or 0),
        )
        return shifted.strftime("%H-%M")

    expanded = _UNIVERSAL_CUT_TIME_SHIFT.sub(replace_shift, template)
    values = {
        "date": date_point.strftime("%d-%m-%y"),
        "date-full": date_point.strftime("%d-%m-%Y"),
        "dd-mm-yy": date_point.strftime("%d-%m-%y"),
        "dd-mm": date_point.strftime("%d-%m"),
        "hour-minute": recorded.strftime("%H-%M"),
        "hour": recorded.strftime("%H"),
        "minute": recorded.strftime("%M"),
        "second": match.group("second") or "00",
        "source": source,
    }
    try:
        rendered = expanded.format_map(values).strip()
    except KeyError as exc:
        raise ValueError(
            f"Неизвестная переменная {{{exc.args[0]}}} в шаблоне имени MP3"
        ) from exc
    except (ValueError, AttributeError, IndexError) as exc:
        raise ValueError(f"Неверный шаблон имени MP3: {exc}") from exc

    if rendered.lower().endswith(".mp3"):
        rendered = rendered[:-4]
    rendered = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", rendered).strip().strip(". ")
    if not rendered:
        raise ValueError("Шаблон имени MP3 не должен давать пустое название")
    if rendered.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        rendered += "_"
    return f"{rendered}.mp3"


def _universal_cut_completed_path(
    ctx: TaskContext,
    folder: Path,
    cue: Path,
    *,
    source: str,
    cut_mode: str,
    markers: tuple[str, str, str],
    filename: str,
) -> Path:
    """Stable, per-recording completion receipt outside disposable cash.

    A receipt is written ONLY after successful extraction and all configured
    bot uploads, so failures can be retried. Files are independent to avoid
    different tasks overwriting a shared JSON database concurrently.
    """
    identity = {
        "task": ctx.task.id,
        "source": source,
        "folder": str(folder.resolve()).casefold(),
        "cue": cue.stem.casefold(),
        "mode": cut_mode,
        "markers": markers,
        "filename": filename,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return data_dir() / "universal_cut_history" / f"{digest}.json"


def _universal_cut_mark_completed(path: Path, cue: Path) -> None:
    atomic_json_write(path, {
        "version": 1,
        "cue": cue.stem,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
    })


def _universal_cut_rename(
    ctx: TaskContext,
    files: list[Path],
    template: str,
    source: str,
    claimed_names: set[str],
    *,
    destination: Path | None = None,
) -> list[Path]:
    """Rename finished exports in cash; bot uploads then use those exact paths."""
    result: list[Path] = []
    reserved = set(claimed_names)
    for original in files:
        desired_name = _universal_cut_output_name(template, original, source) if template else original.name
        candidate = (destination or original.parent) / desired_name
        counter = 2
        while candidate.name.casefold() in reserved:
            candidate = candidate.with_name(f"{Path(desired_name).stem}_{counter}.mp3")
            counter += 1
        result.append(candidate)
        reserved.add(candidate.name.casefold())

    # Final names may form a chain or swap with the original export names.
    # Stage every fragment on the same filesystem before replacing any target.
    staged: list[tuple[Path, Path]] = []
    finished: list[tuple[Path, Path]] = []
    staging_folders: dict[Path, Path] = {}
    try:
        for index, original in enumerate(files):
            if original.parent not in staging_folders:
                staging_folders[original.parent] = Path(tempfile.mkdtemp(
                    prefix=".universal-cut-", dir=original.parent,
                ))
            temporary = staging_folders[original.parent] / f"{index}.mp3"
            original.replace(temporary)
            staged.append((original, temporary))
        for (_original, temporary), candidate in zip(staged, result):
            # Previous-run outputs remain replaceable, as with the old exporter.
            temporary.replace(candidate)
            finished.append((temporary, candidate))
    except Exception:
        # Free overlapping original paths before restoring. If the filesystem
        # also refuses recovery, leave surviving bytes in place for a retry.
        for temporary, candidate in reversed(finished):
            try:
                candidate.replace(temporary)
            except OSError as exc:
                ctx.log(f"Не удалось вернуть фрагмент {candidate}: {exc}")
        for original, temporary in staged:
            if temporary.exists() and not original.exists():
                try:
                    temporary.replace(original)
                except OSError as exc:
                    ctx.log(f"Фрагмент сохранён для восстановления в {temporary}: {exc}")
        raise
    finally:
        for folder in staging_folders.values():
            try:
                # Never delete fragments left here by an unsuccessful recovery.
                folder.rmdir()
            except OSError:
                ctx.log(f"Временная папка вырезки сохранена: {folder}")

    claimed_names.update(reserved)
    for original, candidate in zip(files, result):
        if candidate.name != original.name:
            ctx.log(f"Имя файла по шаблону: {original.name} → {candidate.name}")
    return result


def run_universal_cut(ctx: TaskContext) -> dict[str, Any]:
    params = ctx.task.params
    day = target_date(params, str(ctx.settings.get("timezone", "Europe/Moscow")))
    day_text = day.strftime("%d-%m-%Y")
    cut_mode = str(params.get("cut_mode", "between"))
    start_marker = str(params.get("start_marker", "")).strip()
    end_marker = str(params.get("end_marker", "")).strip()
    same_marker = str(params.get("same_marker", "")).strip()

    # В универсальной вырезке ровно два режима. Старое значение ``same``
    # мигрирует в режим блока по одному маркеру, а краткоживущее ``combined``
    # из 1.2.13 — в обычный режим влёт → вылет.
    if cut_mode == "same":
        cut_mode = "track"
    elif cut_mode == "combined":
        cut_mode = "between"
        ctx.log("Старый совмещённый режим 1.2.13 заменён на «влёт → вылет»")

    marker_block_mode = cut_mode == "track"
    if marker_block_mode:
        if not same_marker:
            raise ValueError("Укажите маркер блока подряд идущих CUE-треков")
    else:
        if not start_marker or not end_marker:
            raise ValueError("Укажите начальный и конечный маркеры")

    raw_sources = params.get("sources") or []
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("Добавьте хотя бы один источник для универсальной вырезки")

    custom_output = str(params.get("output_dir", ""))
    output = _broadcast_output(ctx, custom_output)
    # Две независимые галочки. Старая internal_bot_enabled осталась для архива
    # радио, чтобы ранее сохранённые задания не поменяли своё поведение.
    send_radio = bool(params.get("internal_bot_enabled", True))
    send_republic = bool(params.get("internal_bot_republic_enabled", False))
    bots: list[InternalBotClient] = []
    if send_radio:
        bots.append(InternalBotClient(ctx, channel="extracted_media"))
    if send_republic:
        bots.append(
            InternalBotClient(
                ctx,
                channel="archive_respublika",
                title="Архив республика",
            )
        )
    skip_completed = bool(params.get("skip_completed_cuts", False))
    skipped_completed = 0
    all_created: list[Path] = []
    claimed_output_names: set[str] = set()
    processed_sources = 0

    ctx.log(f"Универсальная вырезка. Дата: {day_text}")
    for source_index, raw in enumerate(raw_sources, start=1):
        ctx.check_cancelled()
        if not isinstance(raw, dict) or not bool(raw.get("enabled", True)):
            continue

        name = str(raw.get("name", "")).strip() or f"Источник {source_index}"
        folder_text = str(raw.get("folder", "")).strip()
        if not folder_text:
            ctx.log(f"{name}: папка не указана — пропуск")
            continue
        try:
            folder = ensure_folder(resolve_path(folder_text, ctx.task.workdir), f"Источник {name}")
        except Exception as exc:
            ctx.log(str(exc))
            continue

        hours_text = str(raw.get("hours", "")).strip()
        hours = parse_ints(hours_text, ())
        pairs = collect_pairs(folder, day_text, hours)
        ctx.log(
            f"{name}: найдено пар MP3+CUE — {len(pairs)}"
            + (f"; часы: {hours_text}" if hours_text else "; все часы")
        )
        if not pairs:
            continue

        filename_setting = str(raw.get("filename", "")).strip()
        # Legacy source names (such as 'stolica') are kept as-is. Only a value
        # with braces activates the filename template for the cash MP3 itself.
        has_name_template = "{" in filename_setting or "}" in filename_setting
        filename_base = _safe_output_base(
            name if has_name_template else filename_setting,
            fallback=_safe_output_base(name),
        )
        if has_name_template:
            # Validate the template before exporting a potentially large recording.
            _universal_cut_output_name(
                filename_setting, Path(f"{filename_base}_{day_text}_00-00.mp3"), name
            )
        # An exporter's raw name may equal another source's finalized name.
        # Keep intermediate exports separate until all final names are reserved.
        # Staging lives on the destination filesystem for atomic replacement;
        # failed/cancelled exports remain here for inspection, never deleted.
        source_output = Path(tempfile.mkdtemp(prefix=".universal-source-", dir=output))
        made: list[Path] = []
        pending_receipts: list[tuple[Path, Path]] = []
        for item_index, (_base, (mp3, cue)) in enumerate(pairs.items(), start=1):
            ctx.check_cancelled()
            ctx.progress(
                int(item_index * 100 / max(1, len(pairs))),
                f"{name}: {cue.stem}",
            )
            receipt = None
            if skip_completed:
                receipt = _universal_cut_completed_path(
                    ctx, folder, cue, source=name, cut_mode=cut_mode,
                    markers=(start_marker, end_marker, same_marker),
                    filename=filename_setting,
                )
                if receipt.is_file():
                    skipped_completed += 1
                    ctx.log(f"{name}: {cue.stem} уже вырезан и отправлен — пропуск")
                    continue
            count_before = len(made)
            if marker_block_mode:
                made.extend(
                    export_marker_blocks(
                        ctx,
                        mp3,
                        cue,
                        source_output,
                        filename_base,
                        same_marker,
                    )
                )
            else:
                result = export_between_markers(
                    ctx,
                    mp3,
                    cue,
                    source_output,
                    filename_base,
                    start_marker,
                    end_marker,
                )
                if result:
                    made.append(result)
            if receipt is not None and len(made) > count_before:
                pending_receipts.append((receipt, cue))

        if not made:
            try:
                source_output.rmdir()
            except OSError:
                ctx.log(f"Временная папка вырезки сохранена: {source_output}")
            continue
        made = _universal_cut_rename(
            ctx, made, filename_setting if has_name_template else "", name,
            claimed_output_names, destination=output,
        )
        try:
            source_output.rmdir()
        except OSError:
            ctx.log(f"Временная папка вырезки сохранена: {source_output}")
        processed_sources += 1
        all_created.extend(made)

        if bots:
            configured_message = str(raw.get("message", "")).strip()
            default_title = f"{name.upper()} {day_text}"
            message_template = configured_message or default_title
            parsed_hours = parse_ints(hours_text, ())
            for bot in bots:
                ctx.check_cancelled()
                bot.send_group(
                    default_title,
                    sorted(made),
                    message_template=message_template,
                    filename_template="{original}",
                    date_text=day_text,
                    template_values={
                        "source": name,
                        "hours": hours_text,
                        "hour": f"{parsed_hours[0]:02d}" if len(parsed_hours) == 1 else hours_text,
                    },
                )
        # Only commit after ALL destinations successfully received this source.
        # Otherwise failed uploads can be retried on the next scheduled run.
        for receipt, cue in pending_receipts:
            _universal_cut_mark_completed(receipt, cue)
            ctx.log(f"{name}: записано подтверждение обработки {cue.stem}")

    if not all_created:
        if skipped_completed:
            return {
                "message": f"Новых фрагментов нет; уже обработано: {skipped_completed}",
                "files": [],
            }
        return {"message": "Подходящих фрагментов не найдено", "files": []}

    if bots and bool(params.get("cleanup_after_send", True)):
        ctx.check_cancelled()
        _cleanup_broadcast_output(ctx, output, all_created, custom_output)

    return {
        "message": (
            f"Создано файлов: {len(all_created)}; источников: {processed_sources}"
            + (f"; пропущено уже обработанных: {skipped_completed}" if skipped_completed else "")
        ),
        "files": [str(x) for x in all_created],
    }


def run_local_news(ctx: TaskContext) -> dict[str, Any]:
    params = ctx.task.params
    source = ensure_folder(resolve_path(str(params.get("source_dir", "")), ctx.task.workdir), "Папка MP3+CUE")
    output_value = str(params.get("output_dir", "")).strip()
    output = (
        resolve_path(output_value, ctx.task.workdir)
        if output_value
        else source
    )
    output.mkdir(parents=True, exist_ok=True)
    pairs = collect_all_pairs(source)
    made: list[Path] = []
    rolik_mode = str(params.get("marker_mode", "news")) == "rolik"
    for index, (_base, (mp3, cue)) in enumerate(pairs.items(), start=1):
        ctx.check_cancelled()
        ctx.progress(int(index * 100 / max(1, len(pairs))), cue.name)
        made.extend(export_news(ctx, mp3, cue, output, "news", "новости", rolik_mode))
    return {"message": f"Создано файлов: {len(made)}", "files": [str(x) for x in made]}
