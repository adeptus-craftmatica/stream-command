from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

if False:  # pragma: no cover
    from stream_control.plugins.context import PluginContext
    from stream_control.plugins.host import PluginHost


@dataclass(slots=True)
class PluginPage:
    plugin_id: str
    title: str
    widget: QWidget
    nav_order: int


@dataclass(slots=True)
class HotkeyAction:
    action_id: str
    label: str
    handler: Callable[[], None]
    default_combo: str = ""
    default_enabled: bool = False


@dataclass(frozen=True, slots=True)
class PluginFailure:
    phase: str
    summary: str
    detail: str


class PluginFailurePage(QWidget):
    def __init__(self, display_name: str, failure: PluginFailure, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel(f"{display_name} is unavailable", self)
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        summary = QLabel(
            "This plugin did not finish loading, but the rest of the app can keep running.",
            self,
        )
        summary.setObjectName("mutedText")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        phase_label = QLabel(f"Stage: {failure.phase.title()}", self)
        phase_label.setObjectName("sectionTitle")
        layout.addWidget(phase_label)

        reason = QLabel(failure.summary, self)
        reason.setWordWrap(True)
        reason.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(reason)

        details = QPlainTextEdit(self)
        details.setReadOnly(True)
        details.setPlainText(failure.detail)
        details.setMinimumHeight(240)
        layout.addWidget(details, 1)


class AppPlugin:
    plugin_id = "plugin"
    display_name = "Plugin"
    nav_order = 100
    load_order = 100

    def activate(self, context: "PluginContext") -> None:
        raise NotImplementedError

    def page(self) -> PluginPage | None:
        return None

    def hotkey_actions(self) -> list[HotkeyAction]:
        return []

    def on_plugins_loaded(self, host: "PluginHost") -> None:
        return

    def shutdown(self) -> None:
        return

    async def shutdown_async(self) -> None:
        self.shutdown()


class FailedPlugin(AppPlugin):
    def __init__(
        self,
        *,
        plugin_id: str,
        display_name: str,
        nav_order: int,
        load_order: int,
        failure: PluginFailure,
    ) -> None:
        self.plugin_id = plugin_id
        self.display_name = display_name
        self.nav_order = nav_order
        self.load_order = load_order
        self.failure = failure
        self._page: PluginFailurePage | None = None

    @classmethod
    def from_exception(
        cls,
        *,
        plugin_id: str,
        display_name: str,
        nav_order: int,
        load_order: int,
        phase: str,
        error: Exception,
    ) -> "FailedPlugin":
        error_label = f"{type(error).__name__}: {error}"
        guidance = "This usually points to a missing dependency or a startup bug in the plugin."
        if isinstance(error, ModuleNotFoundError):
            guidance = "A required dependency was not available in this environment or packaged app build."
        elif isinstance(error, ImportError):
            guidance = "A required import failed while the plugin was loading."

        detail = "".join(traceback.format_exception(type(error), error, error.__traceback__)).strip()
        return cls(
            plugin_id=plugin_id,
            display_name=display_name,
            nav_order=nav_order,
            load_order=load_order,
            failure=PluginFailure(
                phase=phase,
                summary=f"{display_name} failed during {phase}. {error_label}",
                detail=f"{guidance}\n\nTechnical details:\n{detail}",
            ),
        )

    def activate(self, context: "PluginContext") -> None:
        if self._page is None:
            self._page = PluginFailurePage(self.display_name, self.failure, context.qt_parent)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)
