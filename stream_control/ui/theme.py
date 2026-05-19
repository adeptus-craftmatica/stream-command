APP_STYLESHEET = """
QWidget {
    color: #e7edf3;
    font-family: "Segoe UI", "SF Pro Text", "Noto Sans";
    font-size: 13px;
}

QLabel {
    background: transparent;
    border: none;
}

QMainWindow, QWidget#appShell {
    background: #0b1218;
}

QFrame#sidebar {
    background: #0f1821;
    border-right: 1px solid #1d2a35;
}

QWidget#contentArea, QStackedWidget#contentStack {
    background: #0b1218;
}

QScrollArea#pageScrollArea, QWidget#pageViewport {
    background: transparent;
    border: none;
}

QFrame#brandCard {
    background: #131e28;
    border: 1px solid #233341;
    border-radius: 16px;
}

QLabel#brandPill {
    background: #14313a;
    color: #8ed8e5;
    border: 1px solid #25515d;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.16em;
    padding: 4px 10px;
    max-width: 180px;
}

QLabel#brandTitle {
    color: #f5f8fb;
    font-size: 22px;
    font-weight: 700;
}

QLabel#pageTitle {
    color: #f5f8fb;
    font-size: 26px;
    font-weight: 700;
}

QLabel#mutedText {
    color: #8ea0af;
}

QLabel#sidebarMeta {
    color: #6f7f8d;
    font-size: 11px;
}

QLabel#sectionTitle {
    color: #f2f6fa;
    font-size: 16px;
    font-weight: 700;
}

QLabel#metricLabel {
    color: #88a1b4;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.12em;
}

QLabel#metricValue {
    color: #ffffff;
    font-size: 27px;
    font-weight: 700;
}

QLabel#statusGood {
    color: #73d8ae;
    font-weight: 700;
}

QLabel#statusWarn {
    color: #d9b36d;
    font-weight: 700;
}

QLabel#statusInfo {
    color: #8ed8e5;
    font-weight: 700;
}

QFrame#headerCard {
    background: #13212c;
    border: 1px solid #264051;
    border-radius: 20px;
}

QFrame#panelCard, QGroupBox {
    background: #121d27;
    border: 1px solid #243746;
    border-radius: 16px;
}

QPushButton {
    background: #162430;
    color: #e8eff5;
    border: 1px solid #2a4051;
    border-radius: 10px;
    min-height: 36px;
    padding: 0 14px;
}

QPushButton:hover {
    background: #1b2d3c;
    border-color: #345167;
}

QPushButton:pressed {
    background: #13212c;
}

QPushButton#primaryButton {
    background: #3aa7c1;
    color: #08131a;
    border: 1px solid #5fc1d8;
    font-weight: 700;
}

QPushButton#primaryButton:hover {
    background: #53b9d1;
    border-color: #78d0e3;
}

QPushButton#dangerButton {
    background: #4a232c;
    color: #ffe9ee;
    border: 1px solid #70404a;
    font-weight: 700;
}

QPushButton#dangerButton:hover {
    background: #5d2b36;
    border-color: #8a505d;
}

QLineEdit, QTextEdit, QPlainTextEdit, QTableWidget, QSpinBox, QComboBox {
    background: #0d151c;
    border: 1px solid #243543;
    border-radius: 10px;
    padding: 8px 10px;
}

QListWidget {
    background: #0d151c;
    border: 1px solid #243543;
    border-radius: 12px;
    padding: 6px;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QTableWidget:focus, QListWidget:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #467ea0;
}

QAbstractItemView {
    outline: none;
}

QTabWidget::pane {
    border: none;
    background: transparent;
    margin-top: 12px;
}

QTabBar::tab {
    background: #121c25;
    border: 1px solid #223240;
    color: #a8bac8;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
    padding: 11px 18px;
    margin-right: 8px;
    min-width: 110px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background: #1d4054;
    border-color: #39708c;
    color: #ffffff;
}

QTabBar::tab:hover:!selected {
    background: #16232e;
    color: #f1f5f8;
}

QListWidget#sidebarNav {
    background: transparent;
    border: none;
    padding: 0;
}

QListWidget#sidebarNav::item {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    color: #a8bac8;
    margin: 2px 0;
    padding: 9px 14px;
}

QListWidget#sidebarNav::item:hover {
    background: #16232e;
    color: #f1f5f8;
}

QListWidget#sidebarNav::item:selected {
    background: #1d4054;
    border: 1px solid #39708c;
    color: #ffffff;
    font-weight: 700;
}

QListWidget::item:selected, QTableWidget::item:selected {
    background: #1d4054;
    color: #ffffff;
}

QHeaderView::section {
    background: #15212b;
    color: #adc0ce;
    border: none;
    border-bottom: 1px solid #243543;
    padding: 10px 8px;
}

QTableWidget {
    gridline-color: #1b2a36;
    selection-background-color: #1d4054;
    alternate-background-color: #101921;
}

QTableCornerButton::section {
    background: #15212b;
    border: none;
    border-bottom: 1px solid #243543;
}

QTableWidget::item {
    padding: 6px 8px;
}

QGroupBox {
    margin-top: 12px;
    padding: 12px;
}

QGroupBox::title {
    color: #d8e3eb;
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 4px;
}

QSlider::groove:horizontal {
    background: #101921;
    border: 1px solid #243543;
    height: 8px;
    border-radius: 999px;
}

QSlider::handle:horizontal {
    background: #68bfd4;
    border: 1px solid #8fd3e4;
    width: 18px;
    margin: -6px 0;
    border-radius: 9px;
}

QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    border: none;
    background: transparent;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #243543;
    min-height: 28px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #345066;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar:horizontal, QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
    border: none;
    width: 0;
    height: 0;
}
"""
