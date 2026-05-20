from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QTableWidget, QVBoxLayout


class PanelCard(QFrame):
    def __init__(self, title: str | None = None, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelCard")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(10)

        if title:
            label = QLabel(title, self)
            label.setObjectName("sectionTitle")
            self.layout.addWidget(label)


class MetricCard(PanelCard):
    def __init__(self, title: str, value: str, detail: str = "", parent: QFrame | None = None) -> None:
        super().__init__(parent=parent)
        self.layout.setSpacing(4)

        title_label = QLabel(title, self)
        title_label.setObjectName("metricLabel")
        self.layout.addWidget(title_label)

        self.value_label = QLabel(value, self)
        self.value_label.setObjectName("metricValue")
        self.layout.addWidget(self.value_label)

        self.detail_label = QLabel(detail, self)
        self.detail_label.setObjectName("mutedText")
        self.detail_label.setWordWrap(True)
        self.layout.addWidget(self.detail_label)
        self.layout.addStretch(1)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_detail(self, detail: str) -> None:
        self.detail_label.setText(detail)


def set_status_label(label: QLabel, ok: bool, message: str) -> None:
    label.setObjectName("statusGood" if ok else "statusWarn")
    label.setText(message)
    label.style().unpolish(label)
    label.style().polish(label)
    label.update()


def configure_readonly_line(widget: QLabel | QLineEdit) -> None:
    if isinstance(widget, QLabel):
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return
    widget.setCursorPosition(0)


def restore_table_column_widths(table: QTableWidget, widths: list[int]) -> None:
    if not widths:
        return
    header = table.horizontalHeader()
    blocker = QSignalBlocker(header)
    for index, width in enumerate(widths):
        if index >= table.columnCount():
            break
        safe_width = max(40, int(width))
        header.resizeSection(index, safe_width)
    del blocker


def capture_table_column_widths(table: QTableWidget) -> list[int]:
    header = table.horizontalHeader()
    return [header.sectionSize(index) for index in range(table.columnCount())]
