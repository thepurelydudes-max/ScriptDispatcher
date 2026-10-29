from __future__ import annotations

import multiprocessing
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from dispatcher.history import HistoryStore
from dispatcher.modules import create_default_config
from dispatcher.security import SecretStore
from dispatcher.storage import load_config
from dispatcher.ui.main_window import MainWindow
from dispatcher.ui.style import APP_STYLESHEET


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setApplicationName("Диспетчер скриптов")
    app.setOrganizationName("FARLINER")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    def exception_hook(exc_type, exc_value, exc_traceback):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        QMessageBox.critical(None, "Ошибка программы", f"{exc_type.__name__}: {exc_value}\n\n{text[-3000:]}")

    sys.excepthook = exception_hook

    config = load_config(create_default_config)
    secrets = SecretStore()
    history = HistoryStore()
    window = MainWindow(config, secrets, history)
    if not bool(config.settings.get("start_minimized", False)):
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
