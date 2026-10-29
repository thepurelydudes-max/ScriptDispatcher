from __future__ import annotations

import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QAction, QColor, QCloseEvent, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
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
    QSplitter,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QToolButton,
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
from .presentation import STATUS_COLORS, STATUS_LABELS, format_history_time
from .view_state import load_view_state, save_view_state
from ..storage import data_dir


class UiSignals(QObject):
    log = Signal(str, str)
    progress = Signal(str, int, str)
    done = Signal(object)
    paths_checked = Signal(str, str)


class StatusDelegate(QStyledItemDelegate):
    """Paint the selection beneath the status widget without duplicate text."""

    def paint(self, painter, option, index):
        view = QStyleOptionViewItem(option)
        self.initStyleOption(view, index)
        view.text = ""
        style = view.widget.style() if view.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, view, painter, view.widget)


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: AppConfig,
        secrets: SecretStore,
        history: HistoryStore,
        parent=None,
        *,
        view_state_path: Path | None = None,
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
        self.view_state_path = view_state_path
        self._history_cache = {}
        self._tasks_by_id = {}
        self._row_controls = {}
        self._row_states = {}
        self._row_signatures = {}

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
        self.restore_view()
        self.refresh_table()

        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.setInterval(10_000)
        self.scheduler_timer.timeout.connect(self.check_scheduler)
        self.scheduler_timer.start()
        QTimer.singleShot(1500, self.check_scheduler)
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(500)
        self.runtime_timer.timeout.connect(self.refresh_runtime_cells)
        self.runtime_timer.start()

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
        for action, shortcut in ((self.add_action, "Ctrl+N"), (self.run_action, "F5"),
                                 (self.stop_action, "Shift+F5"), (self.edit_action, "Ctrl+E"),
                                 (self.history_action, "Ctrl+H")):
            action.setShortcut(QKeySequence(shortcut))
            self.addAction(action)
        focus_search = QAction(self)
        focus_search.setShortcut(QKeySequence("Ctrl+F"))
        focus_search.triggered.connect(lambda: self.search.setFocus())
        self.addAction(focus_search)

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

        heading = QHBoxLayout()
        title = QLabel("Задачи")
        title.setObjectName("pageTitle")
        self.summary_label = QLabel()
        self.summary_label.setObjectName("mutedText")
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(self.summary_label)
        outer.addLayout(heading)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск задач")
        self.search.setClearButtonEnabled(True)
        self.category_filter = QComboBox()
        self.category_filter.addItem("Все модули", "")
        for category in sorted({spec.category for spec in MODULES.values()}):
            self.category_filter.addItem(category, category)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Все состояния", "")
        for key in ("queued", "running", "stopping", "waiting", "ready", "success", "error", "cancelled", "skipped", "disabled"):
            self.status_filter.addItem(STATUS_LABELS[key], key)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(150)
        self.filter_timer.timeout.connect(self.apply_filters)
        self.search.textChanged.connect(lambda: self.filter_timer.start())
        self.category_filter.currentIndexChanged.connect(self.apply_filters)
        self.status_filter.currentIndexChanged.connect(self.apply_filters)
        filters.addWidget(self.search, 2)
        filters.addWidget(self.category_filter, 1)
        filters.addWidget(self.status_filter, 1)
        reset = QPushButton("Сбросить")
        reset.setToolTip("Сбросить поиск и фильтры")
        reset.clicked.connect(self.reset_filters)
        filters.addWidget(reset)
        outer.addLayout(filters)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Задача",
                "Модуль",
                "Источник",
                "Расписание",
                "Состояние",
                "Последний запуск",
                "Следующий запуск",
                "Управление",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setItemDelegateForColumn(4, StatusDelegate(self.table))
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
        self.table.verticalHeader().setDefaultSectionSize(48)
        header = self.table.horizontalHeader()
        resize_mode = QHeaderView.ResizeMode
        header.setSectionResizeMode(resize_mode.Interactive)
        header.setSectionResizeMode(2, resize_mode.Stretch)
        header.setSectionResizeMode(7, resize_mode.Fixed)
        header.setMinimumSectionSize(64)
        for column, width in enumerate((210, 70, 180, 135, 170, 128, 128, 105)):
            self.table.setColumnWidth(column, width)
        header.sectionClicked.connect(self.sort_requested)
        self.table.itemSelectionChanged.connect(self.update_details)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_selected())
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(self.table_menu)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        list_pane = QWidget()
        list_layout = QVBoxLayout(list_pane)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(4)
        list_layout.addWidget(self.table)
        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("mutedText")
        list_layout.addWidget(self.empty_label)
        self.splitter.addWidget(list_pane)

        self.details_tabs = QTabWidget()
        details = QWidget()
        details_layout = QHBoxLayout(details)
        details_layout.setContentsMargins(14, 8, 14, 8)
        self.detail_form = QFormLayout()
        self.detail_form.setVerticalSpacing(3)
        self.detail_source = QLineEdit()
        self.detail_source.setReadOnly(True)
        self.detail_output = QLineEdit()
        self.detail_output.setReadOnly(True)
        for editor in (self.detail_source, self.detail_output):
            editor.setStyleSheet("padding: 3px 6px;")
            editor.setMaximumHeight(26)
        self.detail_module = QLabel("—")
        self.detail_schedule = QLabel("—")
        self.detail_title = QLabel("Выберите задачу в списке")
        self.detail_title.setObjectName("detailTitle")
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_state = QLabel("—")
        self.detail_last_run = QLabel("—")
        self.detail_next_run = QLabel("—")
        self.detail_message = QLabel("—")
        self.detail_message.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_message.setWordWrap(True)
        self.detail_message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_message.setMaximumHeight(56)
        self.detail_step = QLabel("—")
        self.detail_step.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_step.setWordWrap(True)
        self.detail_progress = QProgressBar()
        self.detail_progress.setMaximumHeight(12)
        self.detail_progress.setTextVisible(False)
        self.detail_progress.hide()
        self.detail_form.addRow(self.detail_title)
        state_row = QHBoxLayout()
        state_row.addWidget(self.detail_state)
        state_row.addWidget(QLabel("·"))
        state_row.addWidget(self.detail_module, 1)
        self.detail_form.addRow("Состояние:", state_row)
        self.detail_form.addRow("Источник:", self.detail_source)
        self.detail_form.addRow("Папка результатов:", self.detail_output)
        self.detail_form.addRow("Расписание:", self.detail_schedule)
        times_row = QHBoxLayout()
        times_row.addWidget(QLabel("Последний:"))
        times_row.addWidget(self.detail_last_run)
        times_row.addSpacing(18)
        times_row.addWidget(QLabel("Следующий:"))
        times_row.addWidget(self.detail_next_run)
        times_row.addStretch()
        self.detail_form.addRow("Запуски:", times_row)
        self.detail_form.addRow("Последний результат:", self.detail_message)
        self.detail_form.addRow("Текущий этап:", self.detail_step)
        self.detail_form.addRow(self.detail_progress)
        details_layout.addLayout(self.detail_form, 1)

        detail_buttons = QVBoxLayout()
        self.edit_button = QPushButton("Настроить задачу")
        self.edit_button.setObjectName("primaryButton")
        self.test_button = QPushButton("Проверить доступ")
        self.open_button = QPushButton("Открыть папку")
        self.log_button = QPushButton("Открыть журнал")
        self.task_history_button = QPushButton("История задачи")
        self.edit_button.clicked.connect(self.edit_selected)
        self.test_button.clicked.connect(self.test_selected_paths)
        self.open_button.clicked.connect(self.open_selected_folder)
        self.log_button.clicked.connect(self.open_selected_log)
        self.task_history_button.clicked.connect(self.show_history)
        detail_buttons.addWidget(self.edit_button)
        detail_buttons.addWidget(self.test_button)
        detail_buttons.addWidget(self.open_button)
        detail_buttons.addWidget(self.log_button)
        detail_buttons.addWidget(self.task_history_button)
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
        self.splitter.addWidget(self.details_tabs)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([500, 320])
        outer.addWidget(self.splitter)

        self.setCentralWidget(container)

    def create_statusbar(self):
        status = QStatusBar()
        self.scheduler_label = QLabel("●  Планировщик работает")
        self.scheduler_label.setStyleSheet("color:#56c967")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(110)
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
        if runtime == "stopping":
            return "stopping"
        if self.runner.is_running(task.id):
            return "running"
        if self.runner.is_active(task.id):
            return "queued"
        if runtime == "running":
            return runtime
        if not task.enabled:
            return "disabled"
        last = self._history_cache.get(task.id)
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
        self._tasks_by_id = {task.id: task for task in self.config.tasks}
        self._history_cache = self.history.latest_by_task()
        scroll = self.table.verticalScrollBar().value()
        horizontal = self.table.horizontalScrollBar().value()
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self._row_controls.clear()
        self._row_states.clear()
        self._row_signatures.clear()
        self.table.setRowCount(0)
        for task in self.config.tasks:
            spec = MODULES.get(task.module)
            row = self.table.rowCount()
            self.table.insertRow(row)
            last = self._history_cache.get(task.id)
            last_text = format_history_time(last.finished_at or last.started_at, str(self.config.settings.get("timezone", "Europe/Moscow"))) if last else "—"
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
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            self.table.setItem(row, 7, QTableWidgetItem(""))
            self.create_row_controls(row, task)
            self.create_row_state(row, task)
        self.table.setSortingEnabled(sorting)
        self.table.blockSignals(False)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == preserve_id:
                self.table.selectRow(row)
                break
        self.apply_filters()
        self.table.verticalScrollBar().setValue(scroll)
        self.table.horizontalScrollBar().setValue(horizontal)
        self.update_active_count()
        self.update_details()

    def create_row_controls(self, row: int, task: TaskDefinition):
        container = QWidget()
        container.setObjectName("rowControls")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        play = QToolButton()
        play.setObjectName("runTaskButton")
        play.setText("▶")
        play.setToolTip(f"Запустить «{task.name}»")
        play.setAccessibleName(f"Запустить {task.name}")
        play.clicked.connect(lambda _checked=False, task_id=task.id: self.run_task_by_id(task_id))
        stop = QToolButton()
        stop.setObjectName("stopTaskButton")
        stop.setText("■")
        stop.setToolTip(f"Остановить «{task.name}»")
        stop.setAccessibleName(f"Остановить {task.name}")
        stop.clicked.connect(lambda _checked=False, task_id=task.id: self.stop_task_by_id(task_id))
        enabled = QCheckBox()
        enabled.setObjectName("enableTaskCheck")
        enabled.setChecked(task.enabled)
        enabled.setToolTip("Включить задачу для запуска по расписанию. Ручной запуск доступен всегда.")
        enabled.setAccessibleName(f"Включить задачу {task.name}")
        enabled.toggled.connect(lambda value, task_id=task.id: self.set_task_enabled(task_id, value))
        layout.addWidget(play)
        layout.addWidget(stop)
        layout.addStretch()
        layout.addWidget(enabled)
        self.table.setCellWidget(row, 7, container)
        self._row_controls[task.id] = (play, stop, enabled)
        self.update_row_state(task)

    def create_row_state(self, row: int, task: TaskDefinition):
        container = QWidget()
        container.setObjectName("taskStatus")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(3)
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        bar = QProgressBar()
        bar.setObjectName("rowProgress")
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        layout.addWidget(label)
        layout.addWidget(bar)
        self.table.setCellWidget(row, 4, container)
        self._row_states[task.id] = (container, label, bar, self.table.item(row, 4))
        self.update_row_state(task)

    def update_row_state(self, task: TaskDefinition):
        active = self.runner.is_active(task.id)
        stopping = self.runtime_status.get(task.id) == "stopping"
        if task.id in self._row_controls:
            play, stop, _enabled = self._row_controls[task.id]
            play.setEnabled(not active)
            stop.setEnabled(active and not stopping)
        if task.id not in self._row_states:
            return
        container, label, bar, item = self._row_states[task.id]
        status = self._task_status(task)
        progress = self.task_progress.get(task.id)
        signature = (status, progress, self.runtime_messages.get(task.id, ""))
        if self._row_signatures.get(task.id) == signature:
            return
        self._row_signatures[task.id] = signature
        text = STATUS_LABELS.get(status, status)
        if progress is not None:
            value, step = progress
            text += f" · {value}%"
            bar.setValue(value)
        else:
            step = self.runtime_messages.get(task.id, "")
        label.setText(text)
        label.setStyleSheet(f"color: {STATUS_COLORS.get(status, '#e8edf2')}; background: transparent;")
        bar.setVisible(progress is not None)
        container.setToolTip(step or text)
        if item.text() != text:
            item.setText(text)
            item.setForeground(QColor(STATUS_COLORS.get(status, "#ffffff")))

    def refresh_runtime_cells(self):
        for task in self.config.tasks:
            self.update_row_state(task)
        self.update_active_count()
        self.apply_filters()

    def run_task_by_id(self, task_id: str):
        task = self.task_by_id(task_id)
        if task and not self.runner.is_active(task_id):
            self.start_task(task, foreground=True)

    def stop_task_by_id(self, task_id: str):
        task = self.task_by_id(task_id)
        if task and self.runner.cancel(task_id):
            self.runtime_status[task_id] = "stopping"
            self.runtime_messages[task_id] = "Получена команда остановки"
            self.refresh_runtime_cells()
            self.statusBar().showMessage(f"Останавливаю «{task.name}»…", 5000)

    def set_task_enabled(self, task_id: str, value: bool):
        task = self.task_by_id(task_id)
        if task is None:
            return
        old_value = task.enabled
        task.enabled = value
        try:
            save_config(self.config)
        except OSError as exc:
            task.enabled = old_value
            self.statusBar().showMessage(f"Не удалось сохранить задачу: {exc}", 8000)
        self.refresh_table()

    def sort_requested(self, column: int):
        if column < 7 and not self.table.isSortingEnabled():
            self.table.setSortingEnabled(True)
            self.table.sortItems(column, Qt.SortOrder.AscendingOrder)

    def reset_filters(self):
        self.search.clear()
        self.category_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(0)
        self.apply_filters()

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
                visible = status == status_filter
            self.table.setRowHidden(row, not visible)
        visible_count = sum(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        self.empty_label.setText("По выбранным фильтрам задач нет." if self.config.tasks else "Задач пока нет. Нажмите «Добавить», чтобы создать первую.")
        self.empty_label.setVisible(visible_count == 0)
        if any(self.table.isRowHidden(index.row()) for index in self.table.selectionModel().selectedRows()):
            self.table.clearSelection()
        self.update_details()

    def task_by_id(self, task_id: str) -> TaskDefinition | None:
        return self._tasks_by_id.get(task_id)

    def selected_task(self) -> TaskDefinition | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.table.isRowHidden(rows[0].row()):
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
        active = bool(task and self.runner.is_active(task.id))
        self.run_action.setEnabled(enabled and not active)
        self.stop_action.setEnabled(
            bool(
                task
                and self.runner.is_active(task.id)
                and self.runtime_status.get(task.id) != "stopping"
            )
        )
        self.schedule_action.setEnabled(enabled)
        self.edit_action.setEnabled(enabled)
        self.delete_action.setEnabled(enabled and not active)
        self.task_history_button.setEnabled(enabled)
        last = self._history_cache.get(task.id) if task else None
        self.log_button.setEnabled(bool(last and last.log_path))
        self._update_progress()
        if not task:
            self.detail_title.setText("Выберите задачу в списке")
            for label in (self.detail_state, self.detail_last_run, self.detail_next_run, self.detail_message):
                label.setText("—")
            self.detail_module.setText("—")
            self.detail_source.clear()
            self.detail_output.clear()
            self.detail_schedule.setText("—")
            return
        spec = MODULES.get(task.module)
        self.detail_title.setText(task.name)
        status = self._task_status(task)
        self.detail_state.setText(STATUS_LABELS.get(status, status))
        self.detail_state.setStyleSheet(f"color: {STATUS_COLORS.get(status, '#e8edf2')};")
        timezone = str(self.config.settings.get("timezone", "Europe/Moscow"))
        self.detail_last_run.setText(format_history_time(last.finished_at or last.started_at, timezone) if last else "Ещё не запускалась")
        self.detail_next_run.setText(self._format_dt(next_run(task.schedule, self._scheduler_now())) if task.enabled else "Задача отключена")
        message = self.runtime_messages.get(task.id) or (last.message if last else "")
        self.detail_message.setText(message or "—")
        self.detail_message.setToolTip(message)
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
        if task:
            self.stop_task_by_id(task.id)

    def on_task_log(self, task_id: str, text: str):
        task = self.task_by_id(task_id)
        prefix = task.name if task else task_id
        self.log_output.appendPlainText(f"{prefix}: {text}")

    def on_task_progress(self, task_id: str, value: int, text: str):
        self.task_progress[task_id] = (value, text)
        task = self.task_by_id(task_id)
        if task:
            self.update_row_state(task)
        self._update_progress()

    def _update_progress(self):
        task = self.selected_task()
        progress = self.task_progress.get(task.id) if task else None
        self.progress.setVisible(progress is not None)
        self.detail_progress.setVisible(progress is not None)
        self.detail_step.setText(progress[1] or "Подготовка…" if progress else "—")
        if progress is not None:
            value, text = progress
            self.detail_progress.setValue(value)
            self.progress.setValue(value)
            self.progress.setFormat(f"{value}%")

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
        self._history_cache = self.history.latest_by_task()
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
                    last = self._history_cache.get(task.id)
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
        # Updating a sorted column can move rows; hold item identities first.
        cells = [(str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)), self.table.item(row, 6))
                 for row in range(self.table.rowCount())]
        for task_id, next_item in cells:
            task = self.task_by_id(task_id)
            if not task:
                continue
            upcoming = next_run(task.schedule, now) if task.enabled else None
            next_item.setText(self._format_dt(upcoming))
            # The scheduler checks every 10s. Refresh status here too, so a
            # task that succeeded yesterday becomes Waiting after midnight.
            self.update_row_state(task)
        self.apply_filters()

    def update_active_count(self):
        active = self.runner.running_ids()
        running = sum(self.runner.is_running(task_id) for task_id in active)
        queued = len(active) - running
        enabled = sum(1 for task in self.config.tasks if task.enabled)
        errors = sum(self._task_status(task) == "error" for task in self.config.tasks)
        self.active_label.setText(f"Выполняется: {running}   В очереди: {queued}")
        self.summary_label.setText(f"Всего: {len(self.config.tasks)}   ·   Включено: {enabled}   ·   Ошибок: {errors}")

    def show_history(self):
        task = self.selected_task()
        HistoryDialog(self.history, self, task_id=task.id if task else None,
                      task_names={task.id: task.name for task in self.config.tasks},
                      timezone=str(self.config.settings.get("timezone", "Europe/Moscow"))).exec()

    def open_selected_log(self):
        task = self.selected_task()
        record = self._history_cache.get(task.id) if task else None
        if not record or not record.log_path:
            return
        path = Path(record.log_path)
        if not path.is_file():
            QMessageBox.warning(self, "Журнал недоступен", f"Файл журнала не найден:\n{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute()))):
            QMessageBox.warning(self, "Журнал недоступен", "Не удалось открыть журнал в приложении по умолчанию.")

    def restore_view(self):
        state = load_view_state(self.view_state_path)
        for key, restore in (("geometry", self.restoreGeometry),
                             ("header", self.table.horizontalHeader().restoreState),
                             ("splitter", self.splitter.restoreState)):
            if state.get(key):
                restore(QByteArray.fromBase64(state[key].encode("ascii")))
        column = state.get("sort_column", 0)
        order = state.get("sort_order", 0)
        if state.get("sort_enabled") and 0 <= column < 7 and order in (0, 1):
            self.table.setSortingEnabled(True)
            self.table.sortItems(column, Qt.SortOrder(order))

    def save_view(self):
        header = self.table.horizontalHeader()
        state = {
            "version": 1,
            "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
            "header": bytes(header.saveState().toBase64()).decode("ascii"),
            "splitter": bytes(self.splitter.saveState().toBase64()).decode("ascii"),
            "sort_enabled": self.table.isSortingEnabled(),
            "sort_column": header.sortIndicatorSection(),
            "sort_order": header.sortIndicatorOrder().value,
        }
        try:
            save_view_state(state, self.view_state_path)
        except OSError as exc:
            self.statusBar().showMessage(f"Не удалось сохранить вид окна: {exc}", 5000)

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
        self.save_view()
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent):
        self.save_view()
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
