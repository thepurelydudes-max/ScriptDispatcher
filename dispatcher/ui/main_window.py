from __future__ import annotations

import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..history import HistoryStore
from ..daily_status import scheduled_status
from ..models import AppConfig, TaskDefinition
from ..modules import MODULES
from ..schedule_engine import next_run, occurrence_key, previous_run, schedule_summary
from ..security import SecretStore
from ..storage import save_config
from ..task_runner import RunResult, TaskRunner
from ..task_paths import task_input_paths, task_output_path
from .dialogs import HistoryDialog, SettingsDialog, TaskDialog


STATUS_LABELS = {
    "ready": "Готово",
    "running": "Выполняется",
    "stopping": "Останавливается",
    "waiting": "Ожидание",
    "success": "Готово",
    "error": "Ошибка",
    "cancelled": "Остановлено",
    "disabled": "Отключено",
    "skipped": "Пропущено",
}

STATUS_COLORS = {
    "ready": "#45c65a",
    "running": "#278be7",
    "stopping": "#d48b38",
    "waiting": "#e5a91f",
    "success": "#45c65a",
    "error": "#e64747",
    "cancelled": "#d48b38",
    "disabled": "#8593a0",
    "skipped": "#8593a0",
}


class UiSignals(QObject):
    log = Signal(str, str)
    progress = Signal(str, int, str)
    done = Signal(object)
    paths_checked = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: AppConfig,
        secrets: SecretStore,
        history: HistoryStore,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.secrets = secrets
        self.history = history
        self.runner = TaskRunner(history)
        self.executor = ThreadPoolExecutor(max_workers=max(1, int(config.settings.get("max_parallel", 2))))
        self.signals = UiSignals()
        self.signals.log.connect(self.on_task_log)
        self.signals.progress.connect(self.on_task_progress)
        self.signals.done.connect(self.on_task_done)
        self.signals.paths_checked.connect(self.on_paths_checked, Qt.ConnectionType.QueuedConnection)
        self.runtime_status: dict[str, str] = {}
        self.runtime_messages: dict[str, str] = {}
        self.runtime_started_day: dict[str, date] = {}
        self.task_progress: dict[str, tuple[int, str]] = {}
        self.path_checks: set[str] = set()
        self.path_worker: threading.Thread | None = None
        self.fired_occurrences: dict[str, str] = {}
        self.catch_up_checked: set[str] = set()
        self.scheduler_enabled = True
        self.force_exit = False

        self.setWindowTitle("Диспетчер скриптов")
        self.resize(1500, 900)
        self.setMinimumSize(1100, 680)
        self.setWindowIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        )

        self.create_actions()
        self.create_toolbar()
        self.create_central()
        self.create_statusbar()
        self.create_tray()
        self.refresh_table()

        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.setInterval(10_000)
        self.scheduler_timer.timeout.connect(self.check_scheduler)
        self.scheduler_timer.start()
        QTimer.singleShot(1500, self.check_scheduler)

    def create_actions(self):
        style = self.style()
        pixmap = QStyle.StandardPixmap
        self.add_action = QAction(
            style.standardIcon(pixmap.SP_FileDialogNewFolder), "Добавить", self
        )
        self.run_action = QAction(
            style.standardIcon(pixmap.SP_MediaPlay), "Запустить", self
        )
        self.stop_action = QAction(
            style.standardIcon(pixmap.SP_MediaStop), "Остановить", self
        )
        self.schedule_action = QAction(
            style.standardIcon(pixmap.SP_FileDialogDetailedView), "Расписание", self
        )
        self.edit_action = QAction(
            style.standardIcon(pixmap.SP_FileDialogContentsView), "Настроить", self
        )
        self.history_action = QAction(
            style.standardIcon(pixmap.SP_FileDialogInfoView), "История", self
        )
        self.settings_action = QAction(
            style.standardIcon(pixmap.SP_ComputerIcon), "Настройки", self
        )
        self.delete_action = QAction(
            style.standardIcon(pixmap.SP_TrashIcon), "Удалить задачу", self
        )

        self.add_action.triggered.connect(self.add_task)
        self.run_action.triggered.connect(self.run_selected)
        self.stop_action.triggered.connect(self.stop_selected)
        self.schedule_action.triggered.connect(self.edit_schedule)
        self.edit_action.triggered.connect(self.edit_selected)
        self.history_action.triggered.connect(self.show_history)
        self.settings_action.triggered.connect(self.show_settings)
        self.delete_action.triggered.connect(self.delete_selected)

    def create_toolbar(self):
        toolbar = QToolBar("Команды")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        toolbar.addAction(self.add_action)
        toolbar.addSeparator()
        toolbar.addAction(self.run_action)
        toolbar.addAction(self.stop_action)
        toolbar.addSeparator()
        toolbar.addAction(self.schedule_action)
        toolbar.addAction(self.edit_action)
        toolbar.addSeparator()
        toolbar.addAction(self.history_action)
        toolbar.addAction(self.settings_action)
        self.addToolBar(toolbar)

    def create_central(self):
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(12, 12, 12, 8)
        outer.setSpacing(10)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск задач")
        self.category_filter = QComboBox()
        self.category_filter.addItem("Все модули", "")
        for category in sorted({spec.category for spec in MODULES.values()}):
            self.category_filter.addItem(category, category)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Все состояния", "")
        for key in ("ready", "running", "waiting", "error", "disabled"):
            self.status_filter.addItem(STATUS_LABELS[key], key)
        self.search.textChanged.connect(self.apply_filters)
        self.category_filter.currentIndexChanged.connect(self.apply_filters)
        self.status_filter.currentIndexChanged.connect(self.apply_filters)
        filters.addWidget(self.search, 2)
        filters.addWidget(self.category_filter, 1)
        filters.addWidget(self.status_filter, 1)
        filters.addStretch(2)
        outer.addLayout(filters)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Задача",
                "Модуль",
                "Источник",
                "Расписание",
                "Состояние",
                "Последний запуск",
                "Следующий запуск",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(43)
        header = self.table.horizontalHeader()
        resize_mode = QHeaderView.ResizeMode
        header.setSectionResizeMode(0, resize_mode.ResizeToContents)
        header.setSectionResizeMode(1, resize_mode.ResizeToContents)
        header.setSectionResizeMode(2, resize_mode.Stretch)
        header.setSectionResizeMode(3, resize_mode.ResizeToContents)
        header.setSectionResizeMode(4, resize_mode.ResizeToContents)
        header.setSectionResizeMode(5, resize_mode.ResizeToContents)
        header.setSectionResizeMode(6, resize_mode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.update_details)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_selected())
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(self.table_menu)
        outer.addWidget(self.table, 5)

        self.details_tabs = QTabWidget()
        details = QWidget()
        details_layout = QHBoxLayout(details)
        details_layout.setContentsMargins(14, 12, 14, 12)
        self.detail_form = QFormLayout()
        self.detail_source = QLineEdit()
        self.detail_source.setReadOnly(True)
        self.detail_output = QLineEdit()
        self.detail_output.setReadOnly(True)
        self.detail_module = QLabel("—")
        self.detail_schedule = QLabel("—")
        self.detail_form.addRow("Модуль:", self.detail_module)
        self.detail_form.addRow("Источник:", self.detail_source)
        self.detail_form.addRow("Папка результатов:", self.detail_output)
        self.detail_form.addRow("Расписание:", self.detail_schedule)
        details_layout.addLayout(self.detail_form, 1)

        detail_buttons = QVBoxLayout()
        self.edit_button = QPushButton("Настроить задачу")
        self.edit_button.setObjectName("primaryButton")
        self.test_button = QPushButton("Проверить доступ")
        self.open_button = QPushButton("Открыть папку")
        self.edit_button.clicked.connect(self.edit_selected)
        self.test_button.clicked.connect(self.test_selected_paths)
        self.open_button.clicked.connect(self.open_selected_folder)
        detail_buttons.addWidget(self.edit_button)
        detail_buttons.addWidget(self.test_button)
        detail_buttons.addWidget(self.open_button)
        detail_buttons.addStretch()
        details_layout.addLayout(detail_buttons)

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(8, 8, 8, 8)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(3000)
        log_layout.addWidget(self.log_output)

        self.details_tabs.addTab(details, "Параметры выбранной задачи")
        self.details_tabs.addTab(log_widget, "Журнал текущего сеанса")
        outer.addWidget(self.details_tabs, 2)

        self.setCentralWidget(container)

    def create_statusbar(self):
        status = QStatusBar()
        self.scheduler_label = QLabel("●  Планировщик работает")
        self.scheduler_label.setStyleSheet("color:#56c967")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.setVisible(False)
        self.active_label = QLabel("Активных задач: 0")
        status.addWidget(self.scheduler_label)
        status.addPermanentWidget(self.progress)
        status.addPermanentWidget(self.active_label)
        self.setStatusBar(status)

    def create_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Диспетчер скриптов")
        menu = QMenu()
        show_action = menu.addAction("Открыть диспетчер")
        show_action.triggered.connect(self.show_from_tray)
        menu.addAction(self.run_action)
        menu.addSeparator()
        exit_action = menu.addAction("Выход")
        exit_action.triggered.connect(self.exit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_from_tray()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def _task_status(self, task: TaskDefinition) -> str:
        runtime = self.runtime_status.get(task.id)
        if runtime in ("running", "stopping"):
            return runtime
        if self.runner.is_running(task.id):
            return "running"
        if not task.enabled:
            return "disabled"
        last = self.history.last_for_task(task.id)
        if task.schedule.mode != "manual":
            day = self._scheduler_now().date()
            # A run can fail before history is written or finalized. Keep its
            # UI result for the day it started, then use persisted daily state.
            if runtime in {"success", "error", "cancelled", "skipped"} and self.runtime_started_day.get(task.id) == day:
                return runtime
            # Scheduled tasks reset to Waiting at the first scheduler tick
            # after midnight, even when the program stays open overnight.
            return scheduled_status(
                last,
                day,
                str(self.config.settings.get("timezone", "Europe/Moscow")),
            )
        # Preserve legacy behaviour for tasks without a schedule.
        if runtime:
            return runtime
        if last and last.status == "error":
            return "error"
        return "ready"

    def _format_dt(self, value: datetime | None) -> str:
        return value.strftime("%d.%m.%Y %H:%M") if value else "—"

    def _scheduler_now(self) -> datetime:
        timezone = str(self.config.settings.get("timezone", "Europe/Moscow"))
        try:
            return datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
        except Exception:
            return datetime.now()

    def refresh_table(self, preserve_id: str | None = None):
        if preserve_id is None:
            selected = self.selected_task()
            preserve_id = selected.id if selected else None
        self.table.setRowCount(0)
        for task in self.config.tasks:
            spec = MODULES.get(task.module)
            row = self.table.rowCount()
            self.table.insertRow(row)
            last = self.history.last_for_task(task.id)
            last_text = (last.finished_at or last.started_at).replace("T", " ")[:16] if last else "—"
            upcoming = next_run(task.schedule, self._scheduler_now())
            status = self._task_status(task)
            values = [
                task.name,
                spec.category if spec else task.module,
                (
                    task.workdir
                    if task.module == "external"
                    else self._primary_path(task)
                )
                or "—",
                schedule_summary(task.schedule),
                "●  " + STATUS_LABELS.get(status, status),
                last_text,
                self._format_dt(upcoming) if task.enabled else "—",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, task.id)
                if column == 4:
                    item.setForeground(QColor(STATUS_COLORS.get(status, "#ffffff")))
                self.table.setItem(row, column, item)
            if preserve_id == task.id:
                self.table.selectRow(row)
        self.apply_filters()
        self.update_active_count()
        self.update_details()

    def apply_filters(self):
        query = self.search.text().strip().lower()
        category = str(self.category_filter.currentData() or "")
        status_filter = str(self.status_filter.currentData() or "")
        for row in range(self.table.rowCount()):
            task = self.task_by_id(
                str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            )
            spec = MODULES.get(task.module) if task else None
            visible = bool(task)
            if task and query:
                visible = query in f"{task.name} {spec.title if spec else task.module}".lower()
            if visible and category:
                visible = bool(spec and spec.category == category)
            if visible and status_filter:
                status = self._task_status(task)
                visible = status in {"ready", "success"} if status_filter == "ready" else status == status_filter
            self.table.setRowHidden(row, not visible)

    def task_by_id(self, task_id: str) -> TaskDefinition | None:
        return next((task for task in self.config.tasks if task.id == task_id), None)

    def selected_task(self) -> TaskDefinition | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return (
            self.task_by_id(str(item.data(Qt.ItemDataRole.UserRole)))
            if item
            else None
        )

    def _primary_path(self, task: TaskDefinition) -> str:
        paths = task_input_paths(task)
        return next((value for key, value in paths if key != "workdir"), "")

    def _output_path(self, task: TaskDefinition) -> str:
        output = task_output_path(task)
        if output:
            return output
        if task.module == "excel.report":
            return "Рядом с выбранным Excel-файлом"
        if task.module == "broadcast.local_news":
            return "В выбранной папке MP3+CUE"
        if task.module in ("yandex.make_links", "music.download_performers"):
            return "Рядом с выбранным входным TXT-файлом"
        return "В выбранной исходной папке"

    def update_details(self):
        task = self.selected_task()
        enabled = task is not None
        for widget in (self.edit_button, self.test_button, self.open_button):
            widget.setEnabled(enabled)
        self.test_button.setEnabled(bool(task and not self.path_checks))
        self.run_action.setEnabled(enabled)
        self.stop_action.setEnabled(
            bool(
                task
                and self.runner.is_active(task.id)
                and self.runtime_status.get(task.id) != "stopping"
            )
        )
        self.schedule_action.setEnabled(enabled)
        self.edit_action.setEnabled(enabled)
        self.delete_action.setEnabled(enabled)
        self._update_progress()
        if not task:
            self.detail_module.setText("—")
            self.detail_source.clear()
            self.detail_output.clear()
            self.detail_schedule.setText("—")
            return
        spec = MODULES.get(task.module)
        self.detail_module.setText(f"{spec.category} — {spec.title}" if spec else task.module)
        self.detail_source.setText(
            task.workdir if task.module == "external" else self._primary_path(task)
        )
        self.detail_output.setText(self._output_path(task))
        self.detail_schedule.setText(schedule_summary(task.schedule))

    def add_task(self):
        dialog = TaskDialog(parent=self)
        if dialog.exec():
            task = dialog.result_task()
            self.config.tasks.append(task)
            save_config(self.config)
            self.refresh_table(task.id)

    def edit_selected(self):
        task = self.selected_task()
        if not task:
            return
        dialog = TaskDialog(task, self)
        if dialog.exec():
            updated = dialog.result_task()
            index = self.config.tasks.index(task)
            self.config.tasks[index] = updated
            save_config(self.config)
            self.refresh_table(updated.id)

    def edit_schedule(self):
        task = self.selected_task()
        if not task:
            return
        dialog = TaskDialog(task, self, schedule_only=True)
        if dialog.exec():
            updated = dialog.result_task()
            index = self.config.tasks.index(task)
            self.config.tasks[index] = updated
            save_config(self.config)
            self.refresh_table(updated.id)

    def delete_selected(self):
        task = self.selected_task()
        if not task:
            return
        answer = QMessageBox.question(
            self,
            "Удалить задачу",
            f"Удалить задачу «{task.name}»?\n\nФайлы, обработанные этой задачей, удалены не будут.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.config.tasks.remove(task)
            save_config(self.config)
            self.refresh_table()

    def table_menu(self, position):
        task = self.selected_task()
        if not task:
            return
        menu = QMenu(self)
        menu.addAction(self.run_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(self.edit_action)
        menu.addAction(self.schedule_action)
        menu.addSeparator()
        menu.addAction(self.delete_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def run_selected(self):
        task = self.selected_task()
        if task:
            self.start_task(task, foreground=True)

    def start_task(self, task: TaskDefinition, *, foreground: bool = False):
        if not self.runner.queue(task.id):
            QMessageBox.information(self, "Задача выполняется", "Эта задача уже запущена.")
            return
        self.runtime_status[task.id] = "running"
        self.runtime_messages[task.id] = ""
        self.runtime_started_day[task.id] = self._scheduler_now().date()
        self.task_progress[task.id] = (0, "")
        self.refresh_table(task.id if foreground else None)
        if foreground:
            self.details_tabs.setCurrentIndex(1)
        self.log_output.appendPlainText(f"\n—— {task.name} ——")
        secret_values = {
            "internal_bot_key": self.secrets.get("internal_bot_key"),
            "yandex_cookie": self.secrets.get("yandex_cookie"),
        }

        def work():
            try:
                result = self.runner.execute(
                    copy.deepcopy(task),
                    copy.deepcopy(self.config.settings),
                    secret_values,
                    on_log=lambda task_id, text: self.signals.log.emit(task_id, text),
                    on_progress=lambda task_id, value, text: self.signals.progress.emit(
                        task_id, value, text
                    ),
                )
            except BaseException as exc:
                self.runner.unqueue(task.id)
                result = RunResult(task.id, "error", f"{type(exc).__name__}: {exc}", {}, "")
            self.signals.done.emit(result)

        try:
            self.executor.submit(work)
        except Exception as exc:
            self.runner.unqueue(task.id)
            self.on_task_done(RunResult(task.id, "error", f"{type(exc).__name__}: {exc}", {}, ""))
        self.update_active_count()

    def stop_selected(self):
        task = self.selected_task()
        if task and self.runner.cancel(task.id):
            self.runtime_status[task.id] = "stopping"
            self.runtime_messages[task.id] = "Получена команда остановки"
            self.refresh_table(task.id)
            self.statusBar().showMessage(f"Останавливаю «{task.name}»…", 5000)

    def on_task_log(self, task_id: str, text: str):
        task = self.task_by_id(task_id)
        prefix = task.name if task else task_id
        self.log_output.appendPlainText(f"{prefix}: {text}")

    def on_task_progress(self, task_id: str, value: int, text: str):
        self.task_progress[task_id] = (value, text)
        self._update_progress()

    def _update_progress(self):
        task = self.selected_task()
        progress = self.task_progress.get(task.id) if task else None
        self.progress.setVisible(progress is not None)
        if progress is not None:
            value, text = progress
            self.progress.setValue(value)
            self.progress.setFormat(f"{value}%  {text[:35]}")

    def on_task_done(self, result: RunResult):
        self.runtime_status[result.task_id] = result.status
        self.runtime_messages[result.task_id] = result.message
        self.runtime_started_day.setdefault(result.task_id, self._scheduler_now().date())
        self.task_progress.pop(result.task_id, None)
        self.refresh_table()
        self.statusBar().showMessage(result.message, 10000)
        if result.status == "error":
            task = self.task_by_id(result.task_id)
            self.tray.showMessage(
                "Ошибка задачи",
                f"{task.name if task else result.task_id}: {result.message}",
                QSystemTrayIcon.MessageIcon.Critical,
                8000,
            )

    def check_scheduler(self):
        if not self.scheduler_enabled:
            return
        now = self._scheduler_now()
        for task in self.config.tasks:
            if not task.enabled or self.runner.is_active(task.id):
                continue
            key = occurrence_key(task.schedule, now)
            if key and self.fired_occurrences.get(task.id) != key:
                self.fired_occurrences[task.id] = key
                self.start_task(task)
                continue
            if task.id not in self.catch_up_checked:
                self.catch_up_checked.add(task.id)
                if task.schedule.catch_up:
                    previous = previous_run(task.schedule, now)
                    last = self.history.last_for_task(task.id)
                    last_value = None
                    if last:
                        try:
                            last_value = datetime.fromisoformat(last.finished_at or last.started_at)
                            # Legacy history is OS-local; scheduler values are
                            # naive wall-clock times in its configured zone.
                            if last_value.tzinfo is None:
                                last_value = last_value.astimezone()
                            try:
                                zone = ZoneInfo(str(self.config.settings.get("timezone", "Europe/Moscow")))
                            except (KeyError, ValueError):
                                zone = None
                            last_value = last_value.astimezone(zone).replace(tzinfo=None)
                        except (ValueError, TypeError, OverflowError):
                            last_value = None
                    if previous and (last_value is None or last_value < previous):
                        self.start_task(task)
        self.refresh_next_run_cells()

    def refresh_next_run_cells(self):
        now = self._scheduler_now()
        for row in range(self.table.rowCount()):
            task = self.task_by_id(
                str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            )
            if not task:
                continue
            upcoming = next_run(task.schedule, now) if task.enabled else None
            self.table.item(row, 6).setText(self._format_dt(upcoming))
            # The scheduler checks every 10s. Refresh status here too, so a
            # task that succeeded yesterday becomes Waiting after midnight.
            current_status = self._task_status(task)
            status_item = self.table.item(row, 4)
            if status_item is not None:
                status_item.setText("●  " + STATUS_LABELS.get(current_status, current_status))
                status_item.setForeground(QColor(STATUS_COLORS.get(current_status, "#ffffff")))
        self.apply_filters()

    def update_active_count(self):
        running = len(self.runner.running_ids())
        enabled = sum(1 for task in self.config.tasks if task.enabled)
        self.active_label.setText(f"Активных задач: {enabled}   Выполняется: {running}")

    def show_history(self):
        HistoryDialog(self.history.recent(), self).exec()

    def show_settings(self):
        dialog = SettingsDialog(
            self.config.settings,
            self.secrets.get("internal_bot_key"),
            self.secrets.get("yandex_cookie"),
            self,
        )
        if dialog.exec():
            settings, internal_bot_key, cookie = dialog.values()
            self.config.settings = settings
            self.secrets.set("internal_bot_key", internal_bot_key)
            self.secrets.set("yandex_cookie", cookie)
            save_config(self.config)
            if not self.runner.running_ids():
                self.executor.shutdown(wait=False, cancel_futures=False)
                self.executor = ThreadPoolExecutor(
                    max_workers=max(1, int(self.config.settings.get("max_parallel", 2)))
                )
            self.statusBar().showMessage("Настройки сохранены", 5000)

    def _candidate_paths(self, task: TaskDefinition) -> list[tuple[str, str]]:
        return task_input_paths(task)

    def test_selected_paths(self):
        task = self.selected_task()
        if not task or self.path_checks:
            return
        snapshot = copy.deepcopy(task)
        self.path_checks.add(task.id)
        self.update_details()

        def work():
            lines = []
            try:
                for key, value in self._candidate_paths(snapshot):
                    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
                    if key != "workdir" and not expanded.is_absolute() and snapshot.workdir:
                        expanded = Path(snapshot.workdir) / expanded
                    try:
                        exists = expanded.exists()
                        lines.append(f"{'✓' if exists else '✗'}  {key}: {expanded}")
                    except OSError as exc:
                        lines.append(f"✗  {key}: {expanded} — {exc}")
                if not lines:
                    lines.append("У задачи пока не указаны входные пути.")
            except Exception as exc:
                lines.append(f"Ошибка проверки доступа: {exc}")
            try:
                self.signals.paths_checked.emit(snapshot.id, "\n".join(lines))
            except RuntimeError:
                # The application may close while an OS network probe is
                # blocked. Its daemon worker must not keep the process alive.
                pass

        try:
            self.path_worker = threading.Thread(target=work, name="dispatcher-path-check", daemon=True)
            self.path_worker.start()
        except Exception as exc:
            self.on_paths_checked(task.id, f"Ошибка проверки доступа: {exc}")

    def on_paths_checked(self, task_id: str, message: str):
        self.path_checks.discard(task_id)
        self.update_details()
        QMessageBox.information(self, "Проверка доступа", message)

    def open_selected_folder(self):
        task = self.selected_task()
        if not task:
            return
        raw_output = task_output_path(task)
        if raw_output:
            path = Path(os.path.expandvars(os.path.expanduser(raw_output)))
            if not path.is_absolute() and task.module == "external" and task.workdir:
                path = Path(task.workdir) / path
        else:
            source_raw = self._primary_path(task)
            if not source_raw:
                return
            path = Path(os.path.expandvars(os.path.expanduser(source_raw)))
            if path.suffix:
                path = path.parent
        if not str(path):
            return
        spec = MODULES.get(task.module)
        output_keys = {"output_file", "report_file"}
        file_output = any(field.key in output_keys and field.kind == "save_file" for field in spec.fields) if spec else False
        file_output = file_output or any(str(task.params.get(key, "")).strip() for key in output_keys)
        output_is_file = bool(raw_output and not str(task.params.get("output_dir", "")).strip() and file_output)
        if output_is_file or path.is_file():
            path = path.parent
        if not path.exists():
            QMessageBox.warning(self, "Папка не найдена", str(path))
            return
        if os.name == "nt":
            os.startfile(path)
        else:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def exit_application(self):
        if self.runner.running_ids():
            answer = QMessageBox.question(
                self,
                "Завершить программу",
                "Есть выполняющиеся задачи. Остановить их и выйти?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            for task_id in self.runner.running_ids():
                self.runner.cancel(task_id)
        self.force_exit = True
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent):
        if self.force_exit or not bool(self.config.settings.get("close_to_tray", True)):
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Диспетчер скриптов",
            "Программа продолжает работу в области уведомлений.",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )
