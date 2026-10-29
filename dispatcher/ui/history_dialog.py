from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..history import HistoryRecord, HistoryStore, _history_date
from .presentation import STATUS_COLORS, STATUS_LABELS, format_history_time


class HistoryDialog(QDialog):
    """Bounded, filterable run history with an explicit local-log action."""

    def __init__(
        self,
        history_or_records: HistoryStore | list[HistoryRecord],
        parent=None,
        *,
        task_id: str | None = None,
        task_names: dict[str, str] | None = None,
        timezone: str = "Europe/Moscow",
    ):
        super().__init__(parent)
        self.setWindowTitle("История запусков")
        self.resize(1100, 680)
        self.timezone = timezone
        self.history = history_or_records if isinstance(history_or_records, HistoryStore) else None
        self._legacy_records = [] if self.history is not None else list(history_or_records)
        self._task_names = dict(task_names or {})
        self._records: list[HistoryRecord] = []

        self.task_filter = QComboBox()
        self.task_filter.setMinimumWidth(180)
        self.task_filter.addItem("Все задачи", None)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Все состояния", None)
        statuses = ["running", "success", "error", "cancelled", "skipped"]
        statuses.extend(sorted({record.status for record in self._legacy_records} - set(statuses)))
        for status in statuses:
            self.status_filter.addItem(STATUS_LABELS.get(status, status), status)

        try:
            today = datetime.now(ZoneInfo(timezone)).date()
        except (KeyError, ValueError):
            today = datetime.now().date()
        self.date_from_enabled = QCheckBox("С даты")
        self.date_to_enabled = QCheckBox("По дату")
        self.date_from = QDateEdit(QDate(today.year, today.month, today.day))
        self.date_to = QDateEdit(QDate(today.year, today.month, today.day))
        for editor in (self.date_from, self.date_to):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd.MM.yyyy")
            editor.setEnabled(False)
        self.refresh_button = QPushButton("Обновить")
        self.count_label = QLabel()
        self.count_label.setWordWrap(True)

        filters = QGridLayout()
        filters.addWidget(QLabel("Задача:"), 0, 0)
        filters.addWidget(self.task_filter, 0, 1, 1, 3)
        filters.addWidget(QLabel("Состояние:"), 0, 4)
        filters.addWidget(self.status_filter, 0, 5)
        filters.addWidget(self.refresh_button, 0, 6)
        filters.addWidget(self.date_from_enabled, 1, 0)
        filters.addWidget(self.date_from, 1, 1)
        filters.addWidget(self.date_to_enabled, 1, 2)
        filters.addWidget(self.date_to, 1, 3)
        filters.addWidget(QLabel(f"Часовой пояс: {timezone}"), 1, 4, 1, 3)
        filters.setColumnStretch(5, 1)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Задача", "Начало", "Окончание", "Состояние", "Сообщение", "Журнал"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 170), (1, 140), (2, 140), (3, 125), (5, 170)):
            self.table.setColumnWidth(column, width)

        self.message_detail = QPlainTextEdit()
        self.message_detail.setReadOnly(True)
        self.message_detail.setPlaceholderText("Выберите запуск, чтобы прочитать полное сообщение")
        self.message_detail.setMaximumHeight(140)
        self.log_path = QLineEdit()
        self.log_path.setReadOnly(True)
        self.log_path.setPlaceholderText("У выбранного запуска нет журнала")
        self.open_log_button = QPushButton("Открыть журнал")
        self.open_log_button.setEnabled(False)
        log_row = QHBoxLayout()
        log_row.addWidget(QLabel("Локальный журнал:"))
        log_row.addWidget(self.log_path, 1)
        log_row.addWidget(self.open_log_button)

        close_button = QDialogButtonBox.StandardButton.Close
        buttons = QDialogButtonBox(close_button)
        buttons.button(close_button).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self.count_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("Полное сообщение выбранного запуска:"))
        layout.addWidget(self.message_detail)
        layout.addLayout(log_row)
        layout.addWidget(buttons)

        self._populate_tasks(task_id)
        self.task_filter.currentIndexChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.date_from_enabled.toggled.connect(self._dates_changed)
        self.date_to_enabled.toggled.connect(self._dates_changed)
        self.date_from.dateChanged.connect(self.refresh)
        self.date_to.dateChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self._refresh_all)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.open_log_button.clicked.connect(self.open_log)
        self.refresh()

    def _populate_tasks(self, selected: str | None):
        latest = self.history.latest_by_task().values() if self.history is not None else self._legacy_records
        names = {record.task_id: record.task_name for record in latest}
        names.update(self._task_names)
        if selected is not None:
            names.setdefault(selected, selected)
        self.task_filter.blockSignals(True)
        self.task_filter.clear()
        self.task_filter.addItem("Все задачи", None)
        for identifier, name in sorted(names.items(), key=lambda pair: (pair[1].casefold(), pair[0])):
            self.task_filter.addItem(name, identifier)
        index = self.task_filter.findData(selected)
        self.task_filter.setCurrentIndex(max(index, 0))
        self.task_filter.blockSignals(False)

    def _refresh_all(self):
        self._populate_tasks(self.task_filter.currentData())
        self.refresh()

    def _dates_changed(self):
        self.date_from.setEnabled(self.date_from_enabled.isChecked())
        self.date_to.setEnabled(self.date_to_enabled.isChecked())
        self.refresh()

    def refresh(self):
        task_id = self.task_filter.currentData()
        status = self.status_filter.currentData()
        date_from = self.date_from.date().toString(Qt.DateFormat.ISODate) if self.date_from_enabled.isChecked() else None
        date_to = self.date_to.date().toString(Qt.DateFormat.ISODate) if self.date_to_enabled.isChecked() else None
        old_record = self._selected_record()
        if date_from and date_to and date_from > date_to:
            records = []
            self.count_label.setText("Дата «с» должна быть не позже даты «по»")
        else:
            if self.history is not None:
                records = self.history.recent(
                    200, task_id=task_id, status=status, date_from=date_from,
                    date_to=date_to, timezone=self.timezone,
                )
            else:
                records = []
                for record in sorted(self._legacy_records, key=lambda row: row.id, reverse=True):
                    if task_id is not None and record.task_id != task_id:
                        continue
                    if status is not None and record.status != status:
                        continue
                    if date_from is not None or date_to is not None:
                        day = _history_date(record.started_at, self.timezone)
                        if day is None or (date_from and day < date_from) or (date_to and day > date_to):
                            continue
                    records.append(record)
                    if len(records) == 200:
                        break
            self.count_label.setText(
                f"Показано запусков: {len(records)} (не более 200, сначала новые)"
                if records else "Запусков по выбранным условиям нет"
            )
        self._records = records
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        selected_row = 0
        for row, record in enumerate(records):
            if old_record is not None and record.id == old_record.id:
                selected_row = row
            values = [
                record.task_name, format_history_time(record.started_at, self.timezone),
                format_history_time(record.finished_at, self.timezone),
                STATUS_LABELS.get(record.status, record.status),
                record.message.replace("\n", " ")[:200], record.log_path,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 3:
                    item.setForeground(QColor(STATUS_COLORS.get(record.status, "#93a3b3")))
                self.table.setItem(row, column, item)
        self.table.clearSelection()
        self.table.blockSignals(False)
        if records:
            self.table.selectRow(selected_row)
        self._selection_changed()

    def _selected_record(self) -> HistoryRecord | None:
        selected = self.table.selectionModel().selectedRows()
        row = selected[0].row() if selected else -1
        return self._records[row] if 0 <= row < len(self._records) else None

    def _selection_changed(self):
        record = self._selected_record()
        self.message_detail.setPlainText(record.message if record else "")
        self.log_path.setText(record.log_path if record else "")
        self.open_log_button.setEnabled(self._local_log_path() is not None)

    def _local_log_path(self) -> Path | None:
        record = self._selected_record()
        if record is None or not record.log_path.strip():
            return None
        value = os.path.expandvars(os.path.expanduser(record.log_path.strip()))
        if value.replace("\\", "/").startswith("//"):
            return None
        # Log records contain filesystem paths, never URLs. Permit Windows drives.
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value) and not re.match(r"^[a-zA-Z]:[/\\]", value):
            return None
        return Path(value)

    def open_log(self):
        path = self._local_log_path()
        if path is None:
            return
        try:
            if not path.is_file():
                QMessageBox.warning(self, "Журнал не найден", str(path))
                return
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute()))):
                QMessageBox.warning(self, "Не удалось открыть журнал", str(path))
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось открыть журнал", str(exc))
