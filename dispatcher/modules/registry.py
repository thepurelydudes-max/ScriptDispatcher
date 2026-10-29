from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import AppConfig, ScheduleConfig, TaskDefinition
from ..private_defaults import (
    DEFAULT_INTERNAL_BOT_BASE_URL,
    DEFAULT_INTERNAL_BOT_CHANNEL,
    DEFAULT_INTERNAL_BOT_TITLE,
)
from . import broadcast as broadcast_module
from . import music as music_module
from . import reports as reports_module
from . import web_tools as web_tools_module
from .common import TaskContext


@dataclass(slots=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "text"  # text | int | bool | choice | folder | file | save_file | multiline | date
    default: Any = ""
    choices: list[tuple[str, str]] = field(default_factory=list)
    help: str = ""


@dataclass(slots=True)
class ModuleSpec:
    key: str
    title: str
    category: str
    description: str
    runner: Callable[[TaskContext], dict[str, Any]]
    fields: list[FieldSpec]


_RUNNER_MODULES = {
    "broadcast": broadcast_module,
    "music": music_module,
    "reports": reports_module,
    "web_tools": web_tools_module,
}


def _lazy(module: str, function: str):
    def runner(ctx: TaskContext):
        imported = _RUNNER_MODULES[module]
        return getattr(imported, function)(ctx)

    return runner


DATE_CHOICES = [
    ("today", "Сегодня"),
    ("last_workday", "Последний рабочий день"),
    ("manual", "Указать вручную"),
]


MODULES: dict[str, ModuleSpec] = {
    "broadcast.news": ModuleSpec(
        "broadcast.news",
        "Новости, Тормозок и интервью",
        "Эфир",
        "Вырезает блоки из MP3+CUE трёх станций и отправляет их во внутренний сетевой бот.",
        _lazy("broadcast", "run_news"),
        [
            FieldSpec("use_kometa", "Обрабатывать Комету", "bool", True),
            FieldSpec("source_kometa", "Папка Кометы", "folder", r"\\192.168.2.170\d\ЗАПИСЬ ЭФИРА"),
            FieldSpec("use_stolica", "Обрабатывать Столицу", "bool", True),
            FieldSpec("source_stolica", "Папка Столицы", "folder", r"\\192.168.2.98\обмен\ЭФИР"),
            FieldSpec("use_briz", "Обрабатывать Бриз", "bool", True),
            FieldSpec("source_briz", "Папка Бриза", "folder", r"\\192.168.2.230\обмен\ЗАПИСЬ ЭФИРА"),
            FieldSpec("date_mode", "Дата", "choice", "today", DATE_CHOICES),
            FieldSpec("manual_date", "Ручная дата", "date", ""),
            FieldSpec("hours_kometa", "Часы Кометы", "text", "9-19"),
            FieldSpec("hours_stolica", "Часы Столицы", "text", "9-19"),
            FieldSpec("hours_briz", "Часы Бриза", "text", "10-16"),
            FieldSpec("manual_hours_kometa", "Часы Кометы", "text", "9-19"),
            FieldSpec("manual_hours_stolica", "Часы Столицы", "text", "9-19"),
            FieldSpec("manual_hours_briz", "Часы Бриза", "text", "10-16"),
            FieldSpec("timezone", "Часовой пояс", "text", ""),
            FieldSpec("output_dir", "Папка результатов (пусто = cash рядом с программой)", "folder", ""),
            FieldSpec("news_marker", "Маркер новостей в CUE", "text", "новости"),
            FieldSpec("tormozok_name", "Название программы — Тормозок", "program_name", "Тормозок"),
            FieldSpec("include_tormozok", "Вырезать «Тормозок»", "bool", True),
            FieldSpec("tormozok_hour", "Час «Тормозка»", "int", 12),
            FieldSpec("tormozok_start", "Начальный маркер «Тормозка»", "text", "влет тормозок"),
            FieldSpec("tormozok_end", "Конечный маркер «Тормозка»", "text", "вылет тормозок"),
            FieldSpec("interview_name", "Название программы — Интервью", "program_name", "Интервью"),
            FieldSpec("include_interview", "Вырезать «Интервью»", "bool", True),
            FieldSpec("interview_hour", "Час интервью", "int", 10),
            FieldSpec("interview_start", "Начальный маркер интервью", "text", "rolik гость в студии"),
            FieldSpec("interview_end", "Конечный маркер интервью", "text", "rolik вылет гость в студии"),
            FieldSpec("internal_bot_enabled", "Отправлять во внутренний бот", "bool", True),
            FieldSpec(
                "internal_bot_message_kometa",
                "Сообщение — Комета",
                "multiline",
                "НОВОСТИ КОМЕТА {date}",
                help="Текст перед файлами Кометы. {date} автоматически заменяется датой эфира.",
            ),
            FieldSpec(
                "internal_bot_filename_kometa",
                "Название файла — Комета",
                "text",
                "kometa",
                help=(
                    "Укажите обычное название без даты и времени. "
                    "Например kometa даст kometa_17-08-2026_11-00.mp3. "
                    "Дата, время и .mp3 сохраняются автоматически."
                ),
            ),
            FieldSpec(
                "internal_bot_message_stolica",
                "Сообщение — Столица",
                "multiline",
                "НОВОСТИ СТОЛИЦА {date}",
                help="Текст перед файлами Столицы. {date} автоматически заменяется датой эфира.",
            ),
            FieldSpec(
                "internal_bot_filename_stolica",
                "Название файла — Столица",
                "text",
                "stolica",
                help=(
                    "Укажите обычное название без даты и времени. "
                    "Например stolica даст stolica_17-08-2026_11-00.mp3. "
                    "Дата, время и .mp3 сохраняются автоматически."
                ),
            ),
            FieldSpec(
                "internal_bot_message_briz",
                "Сообщение — Бриз",
                "multiline",
                "НОВОСТИ БРИЗ {date}",
                help="Текст перед файлами Бриза. {date} автоматически заменяется датой эфира.",
            ),
            FieldSpec(
                "internal_bot_filename_briz",
                "Название файла — Бриз",
                "text",
                "briz",
                help=(
                    "Укажите обычное название без даты и времени. "
                    "Например briz даст briz_17-08-2026_11-00.mp3. "
                    "Дата, время и .mp3 сохраняются автоматически."
                ),
            ),
            FieldSpec(
                "internal_bot_message_tormozok",
                "Сообщение — Тормозок",
                "multiline",
                "ТОРМОЗОК {date}",
                help="Текст перед файлом Тормозка. {date} автоматически заменяется датой эфира.",
            ),
            FieldSpec(
                "internal_bot_filename_tormozok",
                "Название файла — Тормозок",
                "text",
                "tormozok",
                help="Дата, время и .mp3 добавляются к этому названию автоматически.",
            ),
            FieldSpec(
                "internal_bot_message_interview",
                "Сообщение — Интервью",
                "multiline",
                "ИНТЕРВЬЮ {date} {hour}:00",
                help=(
                    "Текст перед интервью. {date} заменяется датой, "
                    "{hour} — настроенным часом интервью."
                ),
            ),
            FieldSpec(
                "internal_bot_filename_interview",
                "Название файла — Интервью",
                "text",
                "interview",
                help="Дата, время и .mp3 добавляются к этому названию автоматически.",
            ),
            FieldSpec("cleanup_after_send", "Удалять созданные файлы после отправки", "bool", True),
        ],
    ),
    "broadcast.morning_shift": ModuleSpec(
        "broadcast.morning_shift",
        "Утренняя смена",
        "Эфир",
        "Вырезает утреннюю смену из записей выбранных часов.",
        _lazy("broadcast", "run_morning_shift"),
        [
            FieldSpec("source_dir", "Папка записей", "folder", r"\\192.168.2.98\обмен\ЭФИР"),
            FieldSpec("hours", "Часы записей", "text", "8,9,10"),
            FieldSpec("date_mode", "Дата", "choice", "today", DATE_CHOICES),
            FieldSpec("manual_date", "Ручная дата", "date", ""),
            FieldSpec("timezone", "Часовой пояс", "text", ""),
            FieldSpec("start_marker", "Начальный маркер", "text", "rolik утренняя смена влет"),
            FieldSpec("end_marker", "Конечный маркер", "text", "rolik утренняя смена вылет"),
            FieldSpec("output_dir", "Папка результатов (пусто = cash рядом с программой)", "folder", ""),
            FieldSpec("internal_bot_enabled", "Отправлять во внутренний бот", "bool", True),
            FieldSpec(
                "internal_bot_message",
                "Сообщение перед файлами",
                "multiline",
                "УТРЕННЯЯ СМЕНА {date}",
                help=(
                    "Это сообщение, которое раньше формировалось автоматически "
                    "перед отправкой файлов. Можно использовать {date}, {task} "
                    "и {group}."
                ),
            ),
            FieldSpec(
                "internal_bot_filename",
                "Имя файла в сетевом боте",
                "text",
                "{type}_{source}{ext}",
                help=(
                    "Текущее правило имени: {type}_{source}{ext}. "
                    "{type} — тип вырезки (kometa, stolica, briz, tormozok, "
                    "interview, smena, guest), {source} — имя исходной эфирной "
                    "записи без префикса вырезки. Также доступны {date}, {task}, "
                    "{group}, {original}, {stem}, {ext} и {index}."
                ),
            ),
            FieldSpec("cleanup_after_send", "Удалять созданные файлы после отправки", "bool", True),
        ],
    ),
    "broadcast.morning_guest": ModuleSpec(
        "broadcast.morning_guest",
        "Утренний гость",
        "Эфир",
        "Вырезает передачу между двумя одинаковыми маркерами.",
        _lazy("broadcast", "run_morning_guest"),
        [
            FieldSpec("source_dir", "Папка записей", "folder", r"\\192.168.2.98\обмен\ЭФИР"),
            FieldSpec("hours", "Часы записей", "text", "8,9,10"),
            FieldSpec("date_mode", "Дата", "choice", "today", DATE_CHOICES),
            FieldSpec("manual_date", "Ручная дата", "date", ""),
            FieldSpec("timezone", "Часовой пояс", "text", ""),
            FieldSpec("marker", "Маркер влёта/вылета", "text", "rolik влет - вылет утренний гость"),
            FieldSpec("output_dir", "Папка результатов (пусто = cash рядом с программой)", "folder", ""),
            FieldSpec("internal_bot_enabled", "Отправлять во внутренний бот", "bool", True),
            FieldSpec(
                "internal_bot_message",
                "Сообщение перед файлами",
                "multiline",
                "УТРЕННИЙ ГОСТЬ {date}",
                help=(
                    "Это сообщение, которое раньше формировалось автоматически "
                    "перед отправкой файлов. Можно использовать {date}, {task} "
                    "и {group}."
                ),
            ),
            FieldSpec(
                "internal_bot_filename",
                "Имя файла в сетевом боте",
                "text",
                "{type}_{source}{ext}",
                help=(
                    "Текущее правило имени: {type}_{source}{ext}. "
                    "{type} — тип вырезки (kometa, stolica, briz, tormozok, "
                    "interview, smena, guest), {source} — имя исходной эфирной "
                    "записи без префикса вырезки. Также доступны {date}, {task}, "
                    "{group}, {original}, {stem}, {ext} и {index}."
                ),
            ),
            FieldSpec("cleanup_after_send", "Удалять созданные файлы после отправки", "bool", True),
        ],
    ),
    "broadcast.universal_cut": ModuleSpec(
        "broadcast.universal_cut",
        "Универсальная вырезка",
        "Эфир",
        "Гибкая вырезка MP3+CUE из нескольких папок: отдельные часы, сообщение и имя файла для каждого источника.",
        _lazy("broadcast", "run_universal_cut"),
        [
            FieldSpec("date_mode", "Дата", "choice", "today", DATE_CHOICES),
            FieldSpec("manual_date", "Ручная дата", "date", ""),
            FieldSpec(
                "cut_mode",
                "Способ вырезки",
                "choice",
                "between",
                [
                    ("between", "Влёт → вылет: всё между двумя разными маркерами"),
                    ("track", "Один маркер: подряд идущие треки с этим названием = один выпуск"),
                ],
            ),
            FieldSpec("start_marker", "Начальный маркер / влёт", "text", ""),
            FieldSpec("end_marker", "Конечный маркер / вылет", "text", ""),
            FieldSpec("same_marker", "Маркер подряд идущих CUE-треков", "text", ""),
            FieldSpec(
                "sources",
                "Источники",
                "source_profiles",
                [],
                help=(
                    "Добавьте одну или несколько папок. Для каждой папки отдельно "
                    "задаются часы, сообщение и шаблон имени MP3 в cash. "
                    "Например: Новости столица {dd-mm-yy} {hour+1-minute+30} → "
                    "Новости столица 12-08-26 19-30.mp3 (для записи 18-00). "
                    "Также: {dd-mm}, {date}, {hour-minute}. "
                    "Простое имя без переменных работает по-старому."
                ),
            ),
            FieldSpec("output_dir", "Папка результатов (пусто = cash рядом с программой)", "folder", ""),
            # Сохраняем старый ключ первой галочки для совместимости с заданиями 1.2.15.
            FieldSpec("internal_bot_enabled", "Отправлять в архив радио", "bool", True),
            FieldSpec("internal_bot_republic_enabled", "Отправлять в архив республика", "bool", False),
            FieldSpec(
                "skip_completed_cuts",
                "Не вырезать повторно уже обработанные часы (сохранять историю)",
                "bool",
                False,
                help=(
                    "Запоминает успешно обработанные MP3+CUE по источнику, дате и часу "
                    "в папке data/universal_cut_history. После отправки в выбранные архивы "
                    "повторная вырезка пропускается, даже если cash очищена. "
                    "Учёт начинается после включения галочки."
                ),
            ),
            FieldSpec("cleanup_after_send", "Удалять созданные файлы после отправки", "bool", True),
        ],
    ),
    "broadcast.local_news": ModuleSpec(
        "broadcast.local_news",
        "Локальная вырезка новостей",
        "Эфир",
        "Обрабатывает MP3+CUE непосредственно в выбранной папке без отправки в бот.",
        _lazy("broadcast", "run_local_news"),
        [
            FieldSpec("source_dir", "Папка MP3+CUE", "folder", ""),
            FieldSpec(
                "marker_mode",
                "Способ поиска",
                "choice",
                "news",
                [("news", "По слову «новости»"), ("rolik", "Между 1. ROLIK и 4. ROLIK")],
            ),
            FieldSpec("output_dir", "Папка результатов (пусто = исходная папка)", "folder", ""),
        ],
    ),
    "music.bpm_to_ape": ModuleSpec(
        "music.bpm_to_ape",
        "BPM → APEv2",
        "Музыка",
        "Переносит BPM из ID3 TBPM в тег APEv2 BPM.",
        _lazy("music", "run_bpm_to_ape"),
        [
            FieldSpec("source_dir", "Папка MP3", "folder", ""),
            FieldSpec("recursive", "Обрабатывать подпапки", "bool", False),
            FieldSpec("backup", "Создать резервные копии", "bool", True),
        ],
    ),
    "music.foreign_agents": ModuleSpec(
        "music.foreign_agents",
        "Проверка иноагентов",
        "Проверки",
        "Ищет совпадения имён MP3 с блок-листом.",
        _lazy("music", "run_foreign_agents"),
        [
            FieldSpec("source_dir", "Папка музыкальной базы", "folder", ""),
            FieldSpec("block_list", "Файл блок-листа", "file", "block_list.txt"),
            FieldSpec("report_file", "Файл отчёта", "save_file", "найденные_совпадения.txt"),
        ],
    ),
    "music.language_sort": ModuleSpec(
        "music.language_sort",
        "Распределение РУС/АНГЛ",
        "Музыка",
        "Определяет язык имени файла и распределяет MP3 по папкам.",
        _lazy("music", "run_language_sort"),
        [
            FieldSpec("source_dir", "Исходная папка", "folder", ""),
            FieldSpec("russian_folder", "Папка русских", "text", "russian"),
            FieldSpec("english_folder", "Папка английских", "text", "english"),
            FieldSpec("unknown_folder", "Папка неопределённых", "text", "unknown"),
            FieldSpec("operation", "Операция", "choice", "move", [("move", "Переместить"), ("copy", "Копировать")]),
        ],
    ),
    "music.list_mp3": ModuleSpec(
        "music.list_mp3",
        "Составить список песен",
        "Музыка",
        "Записывает имена всех MP3 из папки и подпапок в TXT.",
        _lazy("music", "run_list_mp3"),
        [
            FieldSpec("source_dir", "Папка MP3", "folder", ""),
            FieldSpec("output_file", "Файл результата", "save_file", "mp3_files.txt"),
            FieldSpec("include_relative_path", "Записывать относительный путь", "bool", False),
        ],
    ),
    "music.musician_card": ModuleSpec(
        "music.musician_card",
        "Карточка музыканта",
        "Музыка",
        "Получает сведения Яндекс Музыки и заполняет APEv2-теги.",
        _lazy("music", "run_musician_card"),
        [
            FieldSpec("source_dir", "Папка MP3", "folder", ""),
            FieldSpec("output_file", "Файл отчёта", "save_file", "track_info.txt"),
            FieldSpec("update_tags", "Изменять APEv2-теги", "bool", True),
            FieldSpec("backup", "Создать резервные копии", "bool", True),
            FieldSpec("request_timeout", "Тайм-аут запроса, сек.", "int", 20),
            FieldSpec("max_retries", "Количество повторов", "int", 4),
            FieldSpec("delay_min", "Минимальная пауза, сек.", "int", 2),
            FieldSpec("delay_max", "Максимальная пауза, сек.", "int", 5),
        ],
    ),
    "music.download_performers": ModuleSpec(
        "music.download_performers",
        "Скачивание по списку",
        "Музыка",
        "Ищет и загружает композиции из заданного источника по списку «Исполнитель - Название».",
        _lazy("music", "run_download_performers"),
        [
            FieldSpec("input_file", "Список исполнителей", "file", "исполнители.txt"),
            FieldSpec("output_dir", "Папка загрузки (пусто = рядом со списком)", "folder", ""),
            FieldSpec("errors_file", "Файл ошибок", "file", "errors.txt"),
            FieldSpec("search_url", "Адрес поиска", "text", "https://eu.hitmotop.com/search?q={query}"),
            FieldSpec("timeout", "Тайм-аут, сек.", "int", 15),
        ],
    ),
    "yandex.make_links": ModuleSpec(
        "yandex.make_links",
        "Ссылки на песни в Яндекс",
        "Яндекс",
        "Создаёт поисковые ссылки Яндекс Музыки из текстового списка.",
        _lazy("web_tools", "run_make_links"),
        [
            FieldSpec("input_file", "Исходный TXT", "file", "исполнители.txt"),
            FieldSpec("output_file", "Файл ссылок", "save_file", "ссылки на песни.txt"),
        ],
    ),
    "yandex.open_links": ModuleSpec(
        "yandex.open_links",
        "Открыть ссылки в браузере",
        "Яндекс",
        "Последовательно открывает ссылки в выбранном браузере и профиле.",
        _lazy("web_tools", "run_open_links"),
        [
            FieldSpec("input_file", "Файл ссылок", "file", "ссылки на песни.txt"),
            FieldSpec("browser_path", "Путь к браузеру", "file", ""),
            FieldSpec("profile", "Профиль браузера", "text", "Profile 1"),
            FieldSpec("delay", "Пауза между ссылками, сек.", "int", 5),
        ],
    ),
    "excel.report": ModuleSpec(
        "excel.report",
        "Обработка Excel-отчёта",
        "Отчёты",
        "Очищает отчёт либо только форматирует длительность и считает итог.",
        _lazy("reports", "run_excel_report"),
        [
            FieldSpec("input_file", "Входной Excel", "file", ""),
            FieldSpec("mode", "Режим", "choice", "clean", [("clean", "Полная очистка"), ("format_only", "Только время и итог")]),
            FieldSpec(
                "output_file",
                "Имя результата (необязательно; пусто = рядом с исходным)",
                "save_file",
                "",
            ),
            FieldSpec("name_header", "Столбец названия", "text", "Название"),
            FieldSpec("genre_header", "Столбец жанра", "text", "Жанр"),
            FieldSpec("duration_header", "Столбец длительности", "text", "Общая длительность"),
            FieldSpec("bad_value", "Удаляемое значение", "text", "не указано"),
        ],
    ),
    "external": ModuleSpec(
        "external",
        "Внешний скрипт или программа",
        "Другое",
        "Запускает любой .py, .bat, .cmd или .exe в выбранной рабочей папке.",
        _lazy("web_tools", "run_external"),
        [
            FieldSpec("program", "Файл программы/скрипта", "file", ""),
            FieldSpec("arguments", "Аргументы", "text", ""),
            FieldSpec("python_path", "Python для .py", "file", ""),
            FieldSpec("timeout", "Тайм-аут, сек. (0 — без ограничения)", "int", 0),
        ],
    ),
}


def defaults_for(module_key: str) -> dict[str, Any]:
    spec = MODULES[module_key]
    return {field.key: copy.deepcopy(field.default) for field in spec.fields}


def _task(task_id: str, name: str, module: str, params: dict[str, Any] | None = None) -> TaskDefinition:
    values = defaults_for(module)
    values.update(params or {})
    return TaskDefinition(
        id=task_id,
        name=name,
        module=module,
        enabled=False,
        schedule=ScheduleConfig(),
        params=values,
    )


def create_default_config() -> AppConfig:
    settings = {
        "timezone": "Europe/Moscow",
        "ffmpeg_path": "",
        "browser_path": r"C:\Users\ADMIN\AppData\Local\Yandex\YandexBrowser\Application\browser.exe",
        "browser_profile": "Profile 1",
        "internal_bot_base_url": DEFAULT_INTERNAL_BOT_BASE_URL,
        "internal_bot_channel": DEFAULT_INTERNAL_BOT_CHANNEL,
        "internal_bot_title": DEFAULT_INTERNAL_BOT_TITLE,
        "internal_bot_max_retries": 0,
        "internal_bot_retry_delay": 2,
        "internal_bot_separator_delay": 2,
        "internal_bot_file_delay": 0,
        "max_parallel": 2,
        "start_minimized": False,
        "close_to_tray": True,
    }
    tasks = [
        _task("news_today", "Новости", "broadcast.news"),
        _task("morning_shift", "Утренняя смена", "broadcast.morning_shift"),
        _task("morning_guest", "Утренний гость", "broadcast.morning_guest"),
        _task("local_news", "Локальная вырезка новостей", "broadcast.local_news"),
        _task("excel_report", "Excel-отчёт", "excel.report"),
        _task("musician_card", "Карточка музыканта", "music.musician_card"),
        _task("foreign_agents", "Проверка иноагентов", "music.foreign_agents"),
        _task("bpm_ape", "BPM → APEv2", "music.bpm_to_ape"),
        _task("language_sort", "Распределение РУС/АНГЛ", "music.language_sort"),
        _task("mp3_list", "Составить список песен", "music.list_mp3"),
        _task("yandex_links", "Ссылки на песни", "yandex.make_links"),
        _task("open_yandex_links", "Открыть ссылки", "yandex.open_links"),
        _task("download_performers", "Скачивание по списку", "music.download_performers"),
        _task("external_script", "Внешний скрипт", "external"),
    ]
    return AppConfig(version=1, settings=settings, tasks=tasks)


def run_module(ctx: TaskContext) -> dict[str, Any]:
    spec = MODULES.get(ctx.task.module)
    if not spec:
        raise KeyError(f"Неизвестный модуль: {ctx.task.module}")
    return spec.runner(ctx)
