from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..history import HistoryRecord
from ..models import ScheduleConfig, TaskDefinition
from ..modules import MODULES
from ..modules.registry import FieldSpec, defaults_for
from ..schedule_engine import normalize_times


class PathEditor(QWidget):
    def __init__(
        self,
        value: str = "",
        file_mode: bool = False,
        save_mode: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.file_mode = file_mode
        self.save_mode = save_mode
        self.edit = QLineEdit(value)
        self.button = QPushButton("Сохранить как…" if save_mode else "Выбрать…")
        self.button.setFixedWidth(130 if save_mode else 100)
        self.button.clicked.connect(self.browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def browse(self):
        current = self.edit.text().strip()
        if self.save_mode:
            value, _ = QFileDialog.getSaveFileName(
                self,
                "Сохранить файл как",
                current,
            )
        elif self.file_mode:
            value, _ = QFileDialog.getOpenFileName(self, "Выберите файл", current)
        else:
            value = QFileDialog.getExistingDirectory(self, "Выберите папку", current)
        if value:
            self.edit.setText(value)

    def text(self) -> str:
        return self.edit.text().strip()


class ProgramNameEditor(QWidget):
    def __init__(self, value: str = "", parent=None):
        super().__init__(parent)
        self.edit = QLineEdit(str(value or ""))
        self.edit.setReadOnly(True)
        self.button = QPushButton("Редактировать название")
        self.button.setFixedWidth(180)
        self.button.clicked.connect(self.rename)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def rename(self):
        current = self.edit.text().strip()
        value, ok = QInputDialog.getText(
            self,
            "Название программы",
            "Новое название:",
            text=current,
        )
        value = value.strip()
        if ok and value:
            old = current
            self.edit.setText(value)
            parent = self.window()
            if hasattr(parent, "_program_name_changed"):
                parent._program_name_changed(self, old, value)

    def text(self) -> str:
        return self.edit.text().strip()


class UniversalSourceDialog(QDialog):
    def __init__(self, value: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Источник универсальной вырезки")
        self.resize(650, 470)
        value = copy.deepcopy(value or {})

        self.enabled = QCheckBox("Использовать этот источник")
        self.enabled.setChecked(bool(value.get("enabled", True)))
        self.name = QLineEdit(str(value.get("name", "")))
        self.name.setPlaceholderText("Например: Столица / Комета / Деловой час")
        self.folder = PathEditor(str(value.get("folder", "")))
        self.hours = QLineEdit(str(value.get("hours", "")))
        self.hours.setPlaceholderText("Например 8,9,10 или 9-12,15; пусто = все часы")
        self.message = QPlainTextEdit(str(value.get("message", "")))
        self.message.setMaximumHeight(95)
        self.message.setPlaceholderText("Например: НОВОСТИ СТОЛИЦА {date}")
        self.filename = QLineEdit(str(value.get("filename", "")))
        self.filename.setPlaceholderText("Например: Новости столица {date} {hour-minute}")

        note = QLabel(
            "Для сообщения доступны {date}, {task}, {source}, {hours} и {hour}. "
            "Для MP3 в cash: {dd-mm-yy} = 12-08-26, {dd-mm} = 12-08; "
            "{date}, {date-full}, {hour-minute}, {hour}, {minute}, {second}, {source}. "
            "Прибавка времени: {hour+1-minute} = 19-00, "
            "{hour+1-minute+30} = 19-30 (для исходной записи 18-00). "
            "При переходе через полночь дата тоже сдвигается. "
            "Пример: Новости столица {dd-mm-yy} {hour+1-minute+30}. "
            "Простое имя без скобок (stolica) работает по-старому: "
            "stolica_12-08-2026_18-00.mp3."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#9db0bf")

        form = QFormLayout()
        form.addRow("", self.enabled)
        form.addRow("Название источника:", self.name)
        form.addRow("Папка MP3+CUE:", self.folder)
        form.addRow("Часы:", self.hours)
        form.addRow("Сообщение перед файлами:", self.message)
        form.addRow("Название / шаблон MP3 в cash:", self.filename)
        form.addRow("", note)

        standard = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(standard.Save | standard.Cancel)
        buttons.button(standard.Save).setText("Сохранить")
        buttons.button(standard.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Проверьте источник", "Введите название источника")
            return
        if not self.folder.text():
            QMessageBox.warning(self, "Проверьте источник", "Выберите папку MP3+CUE")
            return
        self.accept()

    def value(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled.isChecked(),
            "name": self.name.text().strip(),
            "folder": self.folder.text(),
            "hours": self.hours.text().strip(),
            "message": self.message.toPlainText().strip(),
            "filename": self.filename.text().strip(),
        }


class SourceProfilesEditor(QWidget):
    def __init__(self, value: Any = None, parent=None):
        super().__init__(parent)
        self.sources: list[dict[str, Any]] = copy.deepcopy(value) if isinstance(value, list) else []

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Вкл.", "Источник", "Папка", "Часы", "Сообщение", "Имя файла"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(250)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self.edit_source)

        self.add_button = QPushButton("Добавить")
        self.edit_button = QPushButton("Изменить")
        self.remove_button = QPushButton("Удалить")
        self.add_button.clicked.connect(self.add_source)
        self.edit_button.clicked.connect(self.edit_source)
        self.remove_button.clicked.connect(self.remove_source)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self._refresh()

    @staticmethod
    def _short(value: Any, limit: int = 60) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _refresh(self):
        self.table.setRowCount(len(self.sources))
        for row, source in enumerate(self.sources):
            values = [
                "Да" if bool(source.get("enabled", True)) else "Нет",
                source.get("name", ""),
                source.get("folder", ""),
                source.get("hours", "") or "все",
                self._short(source.get("message", "")),
                source.get("filename", ""),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))

    def _current_row(self) -> int:
        row = self.table.currentRow()
        return row if 0 <= row < len(self.sources) else -1

    def add_source(self):
        number = len(self.sources) + 1
        dialog = UniversalSourceDialog(
            {
                "enabled": True,
                "name": f"Источник {number}",
                "hours": "",
                "message": "{source} {date}",
                "filename": f"source_{number}",
            },
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sources.append(dialog.value())
            self._refresh()
            self.table.selectRow(len(self.sources) - 1)

    def edit_source(self, *_args):
        row = self._current_row()
        if row < 0:
            return
        dialog = UniversalSourceDialog(self.sources[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sources[row] = dialog.value()
            self._refresh()
            self.table.selectRow(row)

    def remove_source(self):
        row = self._current_row()
        if row < 0:
            return
        name = str(self.sources[row].get("name", "источник"))
        answer = QMessageBox.question(
            self,
            "Удалить источник",
            f"Удалить «{name}» из этого задания?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            del self.sources[row]
            self._refresh()
            if self.sources:
                self.table.selectRow(min(row, len(self.sources) - 1))

    def value(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.sources)


class DateEditor(QDateEdit):
    def __init__(self, value: str = "", parent=None):
        super().__init__(parent)
        parsed = QDate()
        for date_format in ("dd-MM-yyyy", "yyyy-MM-dd"):
            parsed = QDate.fromString(str(value or ""), date_format)
            if parsed.isValid():
                break
        self.setDate(parsed if parsed.isValid() else QDate.currentDate())
        self.setDisplayFormat("dd-MM-yyyy")
        self.setCalendarPopup(True)
        self.setMinimumDate(QDate(2000, 1, 1))
        self.setMaximumDate(QDate(2100, 12, 31))

    def text_value(self) -> str:
        return self.date().toString("dd-MM-yyyy")


class ScheduleEditor(QWidget):
    def __init__(self, schedule: ScheduleConfig, parent=None):
        super().__init__(parent)
        self.mode = QComboBox()
        self.mode.addItem("Только вручную", "manual")
        self.mode.addItem("В указанное время", "times")
        self.mode.addItem("Через интервал", "interval")
        index = self.mode.findData(schedule.mode)
        self.mode.setCurrentIndex(max(0, index))

        self.days: list[QCheckBox] = []
        days_widget = QWidget()
        days_layout = QHBoxLayout(days_widget)
        days_layout.setContentsMargins(0, 0, 0, 0)
        for number, name in enumerate(("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")):
            checkbox = QCheckBox(name)
            checkbox.setChecked(number in schedule.weekdays)
            self.days.append(checkbox)
            days_layout.addWidget(checkbox)
        days_layout.addStretch()

        self.times = QLineEdit("; ".join(schedule.times))
        self.times.setPlaceholderText("08:05; 09:10; 12:30")
        self.interval = QSpinBox()
        self.interval.setRange(1, 10080)
        self.interval.setValue(schedule.interval_minutes)
        self.start = QLineEdit(schedule.window_start)
        self.end = QLineEdit(schedule.window_end)
        self.catch_up = QCheckBox("Выполнить пропущенный запуск после открытия программы")
        self.catch_up.setChecked(schedule.catch_up)

        form = QFormLayout(self)
        form.addRow("Режим:", self.mode)
        form.addRow("Дни недели:", days_widget)
        form.addRow("Время запусков:", self.times)
        form.addRow("Интервал, минут:", self.interval)
        window = QWidget()
        window_layout = QHBoxLayout(window)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.addWidget(QLabel("с"))
        window_layout.addWidget(self.start)
        window_layout.addWidget(QLabel("до"))
        window_layout.addWidget(self.end)
        form.addRow("Период:", window)
        form.addRow("", self.catch_up)
        self.mode.currentIndexChanged.connect(self.update_enabled)
        self.update_enabled()

    def update_enabled(self):
        mode = self.mode.currentData()
        self.times.setEnabled(mode == "times")
        self.interval.setEnabled(mode == "interval")
        self.start.setEnabled(mode == "interval")
        self.end.setEnabled(mode == "interval")
        for day in self.days:
            day.setEnabled(mode != "manual")

    def value(self) -> ScheduleConfig:
        mode = str(self.mode.currentData())
        weekdays = [index for index, checkbox in enumerate(self.days) if checkbox.isChecked()]
        if mode != "manual" and not weekdays:
            raise ValueError("Выберите хотя бы один день недели")
        raw_times = [part.strip() for part in self.times.text().replace(",", ";").split(";") if part.strip()]
        times = normalize_times(raw_times) if mode == "times" else []
        if mode == "times" and not times:
            raise ValueError("Укажите хотя бы одно время запуска")
        if mode == "interval":
            normalize_times([self.start.text().strip(), self.end.text().strip()])
        return ScheduleConfig(
            mode=mode,
            weekdays=weekdays,
            times=times,
            interval_minutes=self.interval.value(),
            window_start=self.start.text().strip(),
            window_end=self.end.text().strip(),
            catch_up=self.catch_up.isChecked(),
        )


class TaskDialog(QDialog):
    def __init__(self, task: TaskDefinition | None = None, parent=None, schedule_only: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Расписание задачи" if schedule_only else "Настройка задачи")
        self.resize(760, 680)
        self.original = copy.deepcopy(task)
        self.task = copy.deepcopy(task) if task else TaskDefinition(
            id=uuid.uuid4().hex[:12],
            name="Новая задача",
            module="external",
            params=defaults_for("external"),
        )
        self.schedule_only = schedule_only
        self.parameter_widgets: dict[str, tuple[FieldSpec, QWidget]] = {}

        self.tabs = QTabWidget()
        self.general_tab = QWidget()
        self.params_tab = QWidget()
        self.schedule_tab = QWidget()
        self.tabs.addTab(self.general_tab, "Основное")
        self.tabs.addTab(self.params_tab, "Параметры")
        self.tabs.addTab(self.schedule_tab, "Расписание")

        self.name_edit = QLineEdit(self.task.name)
        self.module_combo = QComboBox()
        for key, spec in MODULES.items():
            self.module_combo.addItem(f"{spec.category} — {spec.title}", key)
        self.module_combo.setCurrentIndex(max(0, self.module_combo.findData(self.task.module)))
        self.enabled = QCheckBox("Разрешить автоматический запуск")
        self.enabled.setChecked(self.task.enabled)
        self.workdir = PathEditor(self.task.workdir)
        self.workdir_label = QLabel("Папка запуска:")
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color:#9db0bf")

        general = QFormLayout(self.general_tab)
        general.addRow("Название:", self.name_edit)
        general.addRow("Модуль:", self.module_combo)
        general.addRow(self.workdir_label, self.workdir)
        general.addRow("", self.enabled)
        general.addRow("Описание:", self.description)

        self.params_layout = QFormLayout()
        params_container = QWidget()
        params_container.setLayout(self.params_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(params_container)
        params_outer = QVBoxLayout(self.params_tab)
        params_outer.setContentsMargins(0, 0, 0, 0)
        params_outer.addWidget(scroll)

        self.schedule_editor = ScheduleEditor(self.task.schedule)
        schedule_layout = QVBoxLayout(self.schedule_tab)
        schedule_layout.addWidget(self.schedule_editor)
        schedule_layout.addStretch()

        standard = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(standard.Save | standard.Cancel)
        buttons.button(standard.Save).setText("Сохранить")
        buttons.button(standard.Save).setObjectName("primaryButton")
        buttons.button(standard.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept_data)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

        if schedule_only:
            self.tabs.setCurrentWidget(self.schedule_tab)
            self.tabs.setTabVisible(0, False)
            self.tabs.setTabVisible(1, False)
        self.module_combo.currentIndexChanged.connect(self.rebuild_parameters)
        self.rebuild_parameters(initial=True)

    def _widget_for(self, field: FieldSpec, value: Any) -> QWidget:
        if field.kind == "program_name":
            return ProgramNameEditor(str(value or ""))
        if field.kind == "source_profiles":
            return SourceProfilesEditor(value)
        if field.kind in ("folder", "file", "save_file"):
            return PathEditor(
                str(value or ""),
                file_mode=field.kind == "file",
                save_mode=field.kind == "save_file",
            )
        if field.kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            return widget
        if field.kind == "int":
            widget = QSpinBox()
            widget.setRange(0, 1_000_000)
            widget.setValue(int(value or 0))
            return widget
        if field.kind == "choice":
            widget = QComboBox()
            for key, label in field.choices:
                widget.addItem(label, key)
            widget.setCurrentIndex(max(0, widget.findData(value)))
            return widget
        if field.kind == "multiline":
            widget = QPlainTextEdit(str(value or ""))
            widget.setMaximumHeight(120)
            return widget
        if field.kind == "date":
            return DateEditor(str(value or ""))
        return QLineEdit(str(value or ""))

    def rebuild_parameters(self, _index: int = 0, initial: bool = False):
        if not initial and self.parameter_widgets:
            current = self._collect_parameters()
            self.task.params.update(current)
        while self.params_layout.rowCount():
            self.params_layout.removeRow(0)
        self.parameter_widgets.clear()
        module_key = str(self.module_combo.currentData())
        spec = MODULES[module_key]
        self.description.setText(spec.description)
        external = module_key == "external"
        self.workdir_label.setVisible(external)
        self.workdir.setVisible(external)
        values = self.task.params if module_key == self.task.module else defaults_for(module_key)
        inline_source_toggles = {
            "source_kometa": "use_kometa",
            "source_stolica": "use_stolica",
            "source_briz": "use_briz",
        }
        inline_toggle_keys = set(inline_source_toggles.values())
        for field in spec.fields:
            value = values.get(field.key, field.default)
            if (
                field.key in {"internal_bot_message", "internal_bot_filename"}
                and not str(value or "").strip()
                and str(field.default or "").strip()
            ):
                # 1.2.6 stored legacy behaviour as an empty override.
                # Show the actual legacy template instead of an empty field.
                value = field.default
            if (
                field.key == "internal_bot_filename"
                and str(value or "").strip() == "{original}"
                and str(field.default or "").strip()
            ):
                # 1.2.7 showed the technical {original} placeholder.
                # Present the equivalent, readable naming rule instead.
                value = field.default
            widget = self._widget_for(field, value)
            if field.help:
                widget.setToolTip(field.help)
            self.parameter_widgets[field.key] = (field, widget)
            if field.key in inline_toggle_keys:
                continue
            toggle_key = inline_source_toggles.get(field.key)
            if toggle_key:
                toggle_entry = self.parameter_widgets.get(toggle_key)
                if toggle_entry and isinstance(toggle_entry[1], QCheckBox):
                    toggle = toggle_entry[1]
                    toggle.setToolTip(
                        field.label.replace("Папка ", "Включить обработку: ")
                    )
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(8)
                    row_layout.addWidget(toggle)
                    row_layout.addWidget(widget, 1)
                    self.params_layout.addRow(field.label + ":", row_widget)
                    continue
            self.params_layout.addRow(field.label + ":", widget)
        self._refresh_program_labels()
        self._update_parameter_dependencies()

    @staticmethod
    def _program_filename_base(value: str) -> str:
        translit = str.maketrans({
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
            "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
            "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
            "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
            "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
            "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
            "э": "e", "ю": "yu", "я": "ya",
        })
        result = str(value or "").strip().lower().translate(translit)
        result = "_".join(result.replace("-", " ").split())
        result = "".join(ch for ch in result if ch.isalnum() or ch == "_")
        return result.strip("_")

    def _set_parameter_label(self, key: str, text: str):
        entry = self.parameter_widgets.get(key)
        if not entry:
            return
        label = self.params_layout.labelForField(entry[1])
        if isinstance(label, QLabel):
            label.setText(text + ":")

    def _refresh_program_labels(self):
        if str(self.module_combo.currentData()) != "broadcast.news":
            return
        definitions = {
            "tormozok": "Тормозок",
            "interview": "Интервью",
        }
        for prefix, fallback in definitions.items():
            entry = self.parameter_widgets.get(f"{prefix}_name")
            if entry and isinstance(entry[1], ProgramNameEditor):
                name = entry[1].text() or fallback
            else:
                name = fallback
            self._set_parameter_label(f"{prefix}_name", f"Название программы — {name}")
            self._set_parameter_label(f"include_{prefix}", f"Вырезать «{name}»")
            self._set_parameter_label(f"{prefix}_hour", f"Час «{name}»")
            self._set_parameter_label(f"{prefix}_start", f"Начальный маркер «{name}»")
            self._set_parameter_label(f"{prefix}_end", f"Конечный маркер «{name}»")
            self._set_parameter_label(f"internal_bot_message_{prefix}", f"Сообщение — {name}")
            self._set_parameter_label(f"internal_bot_filename_{prefix}", f"Название файла — {name}")

    def _program_name_changed(self, editor: ProgramNameEditor, old: str, new: str):
        prefix = ""
        for candidate in ("tormozok", "interview"):
            entry = self.parameter_widgets.get(f"{candidate}_name")
            if entry and entry[1] is editor:
                prefix = candidate
                break
        if not prefix:
            return

        message_entry = self.parameter_widgets.get(f"internal_bot_message_{prefix}")
        if message_entry and isinstance(message_entry[1], QPlainTextEdit):
            current = message_entry[1].toPlainText().strip()
            if prefix == "interview":
                old_default = f"{old.upper()} {{date}} {{hour}}:00"
                new_default = f"{new.upper()} {{date}} {{hour}}:00"
                legacy_default = "ИНТЕРВЬЮ {date} {hour}:00"
            else:
                old_default = f"{old.upper()} {{date}}"
                new_default = f"{new.upper()} {{date}}"
                legacy_default = "ТОРМОЗОК {date}"
            if current in {old_default, legacy_default}:
                message_entry[1].setPlainText(new_default)

        filename_entry = self.parameter_widgets.get(f"internal_bot_filename_{prefix}")
        if filename_entry and isinstance(filename_entry[1], QLineEdit):
            current = filename_entry[1].text().strip()
            old_base = self._program_filename_base(old)
            legacy_base = prefix
            if current in {old_base, legacy_base}:
                new_base = self._program_filename_base(new)
                if new_base:
                    filename_entry[1].setText(new_base)

        self._refresh_program_labels()

    def _update_parameter_dependencies(self):
        date_mode_entry = self.parameter_widgets.get("date_mode")
        manual_date_entry = self.parameter_widgets.get("manual_date")

        source_toggles = {
            "use_kometa": (
                "source_kometa", "hours_kometa", "manual_hours_kometa",
                "internal_bot_message_kometa", "internal_bot_filename_kometa",
            ),
            "use_stolica": (
                "source_stolica", "hours_stolica", "manual_hours_stolica",
                "internal_bot_message_stolica", "internal_bot_filename_stolica",
            ),
            "use_briz": (
                "source_briz", "hours_briz", "manual_hours_briz",
                "internal_bot_message_briz", "internal_bot_filename_briz",
            ),
        }

        def set_parameter_visible(key: str, visible: bool):
            entry = self.parameter_widgets.get(key)
            if not entry:
                return
            widget = entry[1]
            widget.setVisible(visible)
            label = self.params_layout.labelForField(widget)
            if label:
                label.setVisible(visible)

        def update_sources():
            for toggle_key, controlled_keys in source_toggles.items():
                toggle_entry = self.parameter_widgets.get(toggle_key)
                if not toggle_entry or not isinstance(toggle_entry[1], QCheckBox):
                    continue
                enabled = toggle_entry[1].isChecked()
                for controlled_key in controlled_keys:
                    controlled_entry = self.parameter_widgets.get(controlled_key)
                    if controlled_entry:
                        controlled_entry[1].setEnabled(enabled)

            stolica_entry = self.parameter_widgets.get("use_stolica")
            stolica_enabled = bool(
                stolica_entry
                and isinstance(stolica_entry[1], QCheckBox)
                and stolica_entry[1].isChecked()
            )
            for toggle_key, detail_keys in (
                (
                    "include_tormozok",
                    (
                        "tormozok_hour", "tormozok_start", "tormozok_end",
                        "internal_bot_message_tormozok", "internal_bot_filename_tormozok",
                    ),
                ),
                (
                    "include_interview",
                    (
                        "interview_hour", "interview_start", "interview_end",
                        "internal_bot_message_interview", "internal_bot_filename_interview",
                    ),
                ),
            ):
                toggle_entry = self.parameter_widgets.get(toggle_key)
                if not toggle_entry or not isinstance(toggle_entry[1], QCheckBox):
                    continue
                toggle_entry[1].setEnabled(stolica_enabled)
                details_enabled = stolica_enabled and toggle_entry[1].isChecked()
                for detail_key in detail_keys:
                    detail_entry = self.parameter_widgets.get(detail_key)
                    if detail_entry:
                        detail_entry[1].setEnabled(details_enabled)

        def update_date_profile():
            if not date_mode_entry or not isinstance(date_mode_entry[1], QComboBox):
                return
            is_manual = date_mode_entry[1].currentData() == "manual"
            if manual_date_entry:
                manual_date_entry[1].setEnabled(is_manual)
            for key in ("hours_kometa", "hours_stolica", "hours_briz"):
                set_parameter_visible(key, not is_manual)
            for key in (
                "manual_hours_kometa",
                "manual_hours_stolica",
                "manual_hours_briz",
            ):
                set_parameter_visible(key, is_manual)
            update_sources()

        cut_mode_entry = self.parameter_widgets.get("cut_mode")

        def update_cut_mode():
            if not cut_mode_entry or not isinstance(cut_mode_entry[1], QComboBox):
                return
            mode = cut_mode_entry[1].currentData()
            marker_block = mode in {"track", "same"}
            set_parameter_visible("same_marker", marker_block)
            set_parameter_visible("start_marker", not marker_block)
            set_parameter_visible("end_marker", not marker_block)

        connected: set[int] = set()
        for toggle_key in (*source_toggles, "include_tormozok", "include_interview"):
            toggle_entry = self.parameter_widgets.get(toggle_key)
            if toggle_entry and isinstance(toggle_entry[1], QCheckBox):
                widget_id = id(toggle_entry[1])
                if widget_id not in connected:
                    toggle_entry[1].stateChanged.connect(update_sources)
                    connected.add(widget_id)
        if date_mode_entry and isinstance(date_mode_entry[1], QComboBox):
            date_mode_entry[1].currentIndexChanged.connect(update_date_profile)
        if cut_mode_entry and isinstance(cut_mode_entry[1], QComboBox):
            cut_mode_entry[1].currentIndexChanged.connect(update_cut_mode)
        update_date_profile()
        update_sources()
        update_cut_mode()

    def _collect_parameters(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, (field, widget) in self.parameter_widgets.items():
            if isinstance(widget, PathEditor):
                result[key] = widget.text()
            elif isinstance(widget, ProgramNameEditor):
                result[key] = widget.text()
            elif isinstance(widget, SourceProfilesEditor):
                result[key] = widget.value()
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                result[key] = widget.value()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(widget, QPlainTextEdit):
                result[key] = widget.toPlainText()
            elif isinstance(widget, DateEditor):
                result[key] = widget.text_value()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text().strip()
        return result

    def accept_data(self):
        try:
            if not self.name_edit.text().strip():
                raise ValueError("Введите название задачи")
            self.task.name = self.name_edit.text().strip()
            self.task.enabled = self.enabled.isChecked()
            new_module = str(self.module_combo.currentData())
            self.task.workdir = self.workdir.text() if new_module == "external" else ""
            self.task.module = new_module
            self.task.params = self._collect_parameters()
            self.task.schedule = self.schedule_editor.value()
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Проверьте настройки", str(exc))

    def result_task(self) -> TaskDefinition:
        return self.task


class SettingsDialog(QDialog):
    def __init__(self, settings: dict[str, Any], internal_bot_key: str, yandex_cookie: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Общие настройки")
        self.resize(660, 560)
        self.settings = copy.deepcopy(settings)

        tabs = QTabWidget()
        general_tab = QWidget()
        internal_bot_tab = QWidget()
        yandex_tab = QWidget()
        tabs.addTab(general_tab, "Программа")
        tabs.addTab(internal_bot_tab, "Внутренний бот")
        tabs.addTab(yandex_tab, "Яндекс")

        self.timezone = QLineEdit(str(settings.get("timezone", "Europe/Moscow")))
        self.ffmpeg = PathEditor(str(settings.get("ffmpeg_path", "")), file_mode=True)
        self.browser = PathEditor(str(settings.get("browser_path", "")), file_mode=True)
        self.browser_profile = QLineEdit(str(settings.get("browser_profile", "Profile 1")))
        self.max_parallel = QSpinBox()
        self.max_parallel.setRange(1, 8)
        self.max_parallel.setValue(int(settings.get("max_parallel", 2)))
        self.close_to_tray = QCheckBox("При закрытии сворачивать в область уведомлений")
        self.close_to_tray.setChecked(bool(settings.get("close_to_tray", True)))
        general = QFormLayout(general_tab)
        general.addRow("Часовой пояс:", self.timezone)
        general.addRow("FFmpeg:", self.ffmpeg)
        general.addRow("Браузер:", self.browser)
        general.addRow("Профиль браузера:", self.browser_profile)
        general.addRow("Одновременных задач:", self.max_parallel)
        general.addRow("", self.close_to_tray)

        self.internal_bot_base_url = QLineEdit(str(settings.get("internal_bot_base_url", "")))
        self.internal_bot_key = QLineEdit(internal_bot_key)
        self.internal_bot_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.internal_bot_channel = QLineEdit(str(settings.get("internal_bot_channel", "extracted_media")))
        self.internal_bot_title = QLineEdit(str(settings.get("internal_bot_title", "Вырезки из эфира")))
        self.internal_bot_retries = QSpinBox()
        self.internal_bot_retries.setRange(0, 100)
        self.internal_bot_retries.setSpecialValueText("До успеха")
        self.internal_bot_retries.setValue(int(settings.get("internal_bot_max_retries", 0)))
        self.internal_bot_retry_delay = QSpinBox()
        self.internal_bot_retry_delay.setRange(1, 300)
        self.internal_bot_retry_delay.setValue(int(settings.get("internal_bot_retry_delay", 2)))
        self.internal_bot_separator_delay = QSpinBox()
        self.internal_bot_separator_delay.setRange(0, 300)
        self.internal_bot_separator_delay.setValue(
            int(settings.get("internal_bot_separator_delay", 2))
        )
        self.internal_bot_file_delay = QSpinBox()
        self.internal_bot_file_delay.setRange(0, 300)
        self.internal_bot_file_delay.setValue(int(settings.get("internal_bot_file_delay", 0)))
        internal_bot = QFormLayout(internal_bot_tab)
        internal_bot.addRow("Адрес сервера:", self.internal_bot_base_url)
        internal_bot.addRow("Внутренний ключ:", self.internal_bot_key)
        internal_bot.addRow("Канал:", self.internal_bot_channel)
        internal_bot.addRow("Заголовок:", self.internal_bot_title)
        internal_bot.addRow("Попыток (0 — до успеха):", self.internal_bot_retries)
        internal_bot.addRow("Пауза повтора, сек.:", self.internal_bot_retry_delay)
        internal_bot.addRow("Пауза после разделителя, сек.:", self.internal_bot_separator_delay)
        internal_bot.addRow("Пауза между файлами, сек.:", self.internal_bot_file_delay)
        note = QLabel(
            "Отправка выполняется только по внутренним адресам "
            "/api/internal/message и /api/internal/upload."
        )
        note.setWordWrap(True)
        internal_bot.addRow(note)

        self.yandex_cookie = QPlainTextEdit(yandex_cookie)
        self.yandex_cookie.setPlaceholderText("Вставьте Cookie одной строкой")
        yandex = QVBoxLayout(yandex_tab)
        yandex.addWidget(QLabel("Cookie Яндекс Музыки:"))
        yandex.addWidget(self.yandex_cookie)
        yandex.addWidget(QLabel("Cookie используется только задачей «Карточка музыканта»."))

        standard = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(standard.Save | standard.Cancel)
        buttons.button(standard.Save).setText("Сохранить")
        buttons.button(standard.Save).setObjectName("primaryButton")
        buttons.button(standard.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def values(self):
        self.settings.update(
            {
                "timezone": self.timezone.text().strip() or "Europe/Moscow",
                "ffmpeg_path": self.ffmpeg.text(),
                "browser_path": self.browser.text(),
                "browser_profile": self.browser_profile.text().strip(),
                "max_parallel": self.max_parallel.value(),
                "close_to_tray": self.close_to_tray.isChecked(),
                "internal_bot_base_url": self.internal_bot_base_url.text().strip().rstrip("/"),
                "internal_bot_channel": self.internal_bot_channel.text().strip(),
                "internal_bot_title": self.internal_bot_title.text().strip(),
                "internal_bot_max_retries": self.internal_bot_retries.value(),
                "internal_bot_retry_delay": self.internal_bot_retry_delay.value(),
                "internal_bot_separator_delay": self.internal_bot_separator_delay.value(),
                "internal_bot_file_delay": self.internal_bot_file_delay.value(),
            }
        )
        return (
            self.settings,
            self.internal_bot_key.text().strip(),
            self.yandex_cookie.toPlainText().strip(),
        )


class HistoryDialog(QDialog):
    def __init__(self, records: list[HistoryRecord], parent=None):
        super().__init__(parent)
        self.setWindowTitle("История запусков")
        self.resize(980, 560)
        table = QTableWidget(len(records), 6)
        table.setHorizontalHeaderLabels(
            ["Задача", "Начало", "Окончание", "Состояние", "Сообщение", "Журнал"]
        )
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        for row, record in enumerate(records):
            values = [
                record.task_name,
                record.started_at.replace("T", " "),
                (record.finished_at or "—").replace("T", " "),
                record.status,
                record.message,
                record.log_path,
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        close_button = QDialogButtonBox.StandardButton.Close
        close = QDialogButtonBox(close_button)
        close.button(close_button).setText("Закрыть")
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(table)
        layout.addWidget(close)
