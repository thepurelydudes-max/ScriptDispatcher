APP_STYLESHEET = r"""
QWidget {
    color: #e8edf2;
    background: #0f171f;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog {
    background: #0f171f;
}
QToolBar {
    background: #111c25;
    border: none;
    border-bottom: 1px solid #263747;
    spacing: 8px;
    padding: 8px 12px;
}
QToolButton, QPushButton {
    background: #1b2a36;
    border: 1px solid #304354;
    border-radius: 5px;
    min-height: 30px;
    padding: 0 14px;
}
QToolButton:hover, QPushButton:hover {
    background: #25394a;
    border-color: #42617a;
}
QToolButton:pressed, QPushButton:pressed {
    background: #14212c;
}
QPushButton#primaryButton {
    background: #1769d2;
    border-color: #2782ef;
    color: white;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: #2078e6;
}
QPushButton#dangerButton {
    background: #51232a;
    border-color: #7d333d;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QPlainTextEdit {
    background: #111c25;
    border: 1px solid #304354;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #1f6dc1;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {
    border-color: #2d83da;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QTableView, QTableWidget {
    background: #101a23;
    alternate-background-color: #13202a;
    border: 1px solid #2a3b4a;
    gridline-color: #2a3b4a;
    selection-background-color: #173f68;
    selection-color: white;
}
QHeaderView::section {
    background: #17232e;
    color: #e9eef3;
    border: none;
    border-right: 1px solid #30404f;
    border-bottom: 1px solid #30404f;
    padding: 9px 8px;
    font-weight: 600;
}
QTableView::item, QTableWidget::item {
    padding: 7px;
}
QGroupBox {
    border: 1px solid #2b3d4c;
    border-radius: 5px;
    margin-top: 16px;
    padding-top: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QTabWidget::pane {
    border: 1px solid #2b3d4c;
    border-radius: 4px;
}
QTabBar::tab {
    background: #15212b;
    border: 1px solid #2b3d4c;
    padding: 9px 16px;
}
QTabBar::tab:selected {
    background: #1d3142;
    color: white;
    border-bottom-color: #2d83da;
}
QScrollArea {
    border: none;
}
QStatusBar {
    background: #111c25;
    border-top: 1px solid #263747;
}
QProgressBar {
    background: #111c25;
    border: 1px solid #304354;
    border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk {
    background: #2381dc;
    border-radius: 3px;
}
QMenu {
    background: #14202a;
    border: 1px solid #304354;
}
QMenu::item:selected {
    background: #1c4f7d;
}
QToolTip {
    color: #ffffff;
    background: #172630;
    border: 1px solid #3c5367;
}
QLabel#pageTitle { font-size: 20pt; font-weight: 600; color: #f0f5fa; }
QLabel#detailTitle { font-size: 13pt; font-weight: 600; padding-bottom: 4px; }
QLabel#mutedText { color: #9bacbc; padding: 4px; }
QWidget#rowControls, QWidget#taskStatus, QWidget#rowControls QCheckBox,
QWidget#taskStatus QLabel { background: transparent; }
QWidget#rowControls QToolButton {
    min-width: 25px; max-width: 25px; min-height: 25px; max-height: 25px;
    padding: 0; border-radius: 4px;
}
QPushButton:disabled, QToolButton:disabled { color: #607080; border-color: #273644; background: #14202a; }
QSplitter::handle { background: #253a4b; height: 6px; }
QSplitter::handle:hover { background: #3b6b91; }
QProgressBar#rowProgress { border: none; background: #263747; border-radius: 2px; }
QProgressBar#rowProgress::chunk { background: #66b2ff; border-radius: 2px; }
"""
