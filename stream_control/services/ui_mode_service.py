from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class UiModeService(QObject):
    tablet_mode_changed = Signal(bool)

    def __init__(self, tablet_mode: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tablet_mode = bool(tablet_mode)

    @property
    def tablet_mode(self) -> bool:
        return self._tablet_mode

    def set_tablet_mode(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self._tablet_mode == normalized:
            return
        self._tablet_mode = normalized
        self.tablet_mode_changed.emit(self._tablet_mode)
