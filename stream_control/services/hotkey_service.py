from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from stream_control.core.platform import is_macos, macos_hotkey_permissions

try:
    from pynput.keyboard import GlobalHotKeys
except Exception as exc:  # pragma: no cover - exercised only when pynput is unavailable
    GlobalHotKeys = None  # type: ignore[assignment]
    _PYNPUT_IMPORT_ERROR = exc
else:
    _PYNPUT_IMPORT_ERROR = None

from stream_control.core.models import HotkeyBinding


@dataclass(slots=True)
class HotkeyApplyReport:
    mapping: dict[str, Callable[[], None]]
    registered_count: int
    duplicate_combos: list[str]
    unresolved_actions: list[str]
    empty_combos: int


class HotkeyService(QObject):
    hotkey_triggered = Signal(str)
    status_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._listener: GlobalHotKeys | None = None
        self._action_handlers: dict[str, Callable[[], None]] = {}
        self._bindings: list[HotkeyBinding] = []
        self._suspend_reasons: set[str] = set()
        self._last_status = "Waiting for global hotkeys."
        self.hotkey_triggered.connect(self._dispatch_action)

    def set_action_handler(self, action_id: str, handler: Callable[[], None]) -> None:
        self._action_handlers[action_id] = handler

    def clear_action_handlers(self) -> None:
        self._action_handlers.clear()

    @property
    def last_status(self) -> str:
        return self._last_status

    @staticmethod
    def normalize_combo(combo: str) -> str:
        return combo.strip().lower()

    def build_report(self, bindings: list[HotkeyBinding]) -> HotkeyApplyReport:
        mapping: dict[str, Callable[[], None]] = {}
        duplicate_combos: list[str] = []
        unresolved_actions: list[str] = []
        empty_combos = 0

        for binding in bindings:
            if not binding.enabled:
                continue
            combo = self.normalize_combo(binding.combo)
            if not combo:
                empty_combos += 1
                continue
            if binding.action_id not in self._action_handlers:
                unresolved_actions.append(binding.label or binding.action_id)
                continue
            if combo in mapping:
                duplicate_combos.append(combo)
                continue
            mapping[combo] = lambda action_id=binding.action_id: self.hotkey_triggered.emit(action_id)

        return HotkeyApplyReport(
            mapping=mapping,
            registered_count=len(mapping),
            duplicate_combos=duplicate_combos,
            unresolved_actions=unresolved_actions,
            empty_combos=empty_combos,
        )

    def apply_bindings(self, bindings: list[HotkeyBinding]) -> None:
        self._bindings = list(bindings)
        self.stop()
        if not self.runtime_hotkeys_supported():
            self._set_status(self._disabled_status_message())
            return
        if self._should_pause_for_active_app():
            self._suspend_reasons.add("foreground_app")
        if self._suspend_reasons:
            self._set_status(self._suspended_status_message())
            return
        report = self.build_report(bindings)

        if not report.mapping:
            detail_bits: list[str] = []
            if report.duplicate_combos:
                detail_bits.append(f"{len(report.duplicate_combos)} duplicate shortcut(s)")
            if report.unresolved_actions:
                detail_bits.append(f"{len(report.unresolved_actions)} unresolved action(s)")
            detail = f" Skipped: {', '.join(detail_bits)}." if detail_bits else ""
            self._set_status(f"No global hotkeys are enabled.{detail}")
            return

        if GlobalHotKeys is None:
            self._set_status(
                "Global hotkeys are unavailable because pynput could not be loaded. "
                f"Details: {_PYNPUT_IMPORT_ERROR}"
            )
            return

        permissions = macos_hotkey_permissions()
        if permissions is not None and not permissions.is_ready:
            self._set_status(permissions.summary())
            return

        try:
            self._listener = GlobalHotKeys(report.mapping)
            self._listener.start()
            status = f"Registered {report.registered_count} global hotkey(s)."
            detail_bits: list[str] = []
            if report.duplicate_combos:
                detail_bits.append(f"{len(report.duplicate_combos)} duplicate shortcut(s) skipped")
            if report.unresolved_actions:
                detail_bits.append(f"{len(report.unresolved_actions)} unavailable action(s) skipped")
            if detail_bits:
                status += " " + "; ".join(detail_bits) + "."
            self._set_status(status)
        except Exception as exc:
            self._listener = None
            self._set_status(
                "Global hotkeys could not be started. On macOS, check Accessibility permissions. "
                f"Details: {exc}"
            )

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def suspend(self, reason: str = "manual") -> None:
        if not self.runtime_hotkeys_supported():
            self._set_status(self._disabled_status_message())
            return
        if reason in self._suspend_reasons:
            return
        self._suspend_reasons.add(reason)
        self.stop()
        self._set_status(self._suspended_status_message())

    def resume(self, reason: str = "manual") -> None:
        if not self.runtime_hotkeys_supported():
            self._set_status(self._disabled_status_message())
            return
        if reason not in self._suspend_reasons:
            return
        self._suspend_reasons.remove(reason)
        if self._suspend_reasons:
            self._set_status(self._suspended_status_message())
            return
        self.apply_bindings(self._bindings)

    def _dispatch_action(self, action_id: str) -> None:
        handler = self._action_handlers.get(action_id)
        if handler is None:
            return
        try:
            handler()
        except Exception as exc:
            self._set_status(f"Hotkey action failed: {exc}")

    def _set_status(self, message: str) -> None:
        self._last_status = message
        self.status_changed.emit(message)

    def _suspended_status_message(self) -> str:
        if not self.runtime_hotkeys_supported():
            return self._disabled_status_message()
        if "foreground_app" in self._suspend_reasons:
            return "Global hotkeys are paused while Stream Control is the active app on macOS."
        if "text_entry" in self._suspend_reasons:
            return "Global hotkeys are paused while typing in the app."
        return "Global hotkeys are temporarily paused."

    @staticmethod
    def runtime_hotkeys_supported() -> bool:
        return not is_macos()

    @staticmethod
    def _disabled_status_message() -> str:
        return "Global hotkeys are temporarily disabled on macOS while we work around a crash in the current hotkey backend."

    @staticmethod
    def _should_pause_for_active_app() -> bool:
        if not is_macos():
            return False
        app = QApplication.instance()
        if app is None:
            return False
        return app.applicationState() == Qt.ApplicationState.ApplicationActive
