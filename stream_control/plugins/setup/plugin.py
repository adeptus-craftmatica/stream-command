from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from stream_control.core.platform import is_macos
from stream_control.plugins.base import AppPlugin, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.services.overlay_server import OverlayServerStatus
from stream_control.services.setup_diagnostics_service import (
    SetupCheck,
    SetupDiagnosticsService,
    SetupSnapshot,
)
from stream_control.ui.widgets.common import PanelCard

if TYPE_CHECKING:
    from stream_control.services.obs_service import ObsService
    from stream_control.services.streamlabs_service import StreamlabsService
    from stream_control.services.twitch_chat_service import TwitchChatService


def _set_tone(label: QLabel, tone: str, text: str) -> None:
    label.setObjectName(
        {
            "good": "statusGood",
            "warn": "statusWarn",
            "info": "statusInfo",
        }.get(tone, "statusInfo")
    )
    label.setText(text)
    label.style().unpolish(label)
    label.style().polish(label)
    label.update()


def _first_sentence(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    period = cleaned.find(". ")
    if period == -1:
        return cleaned
    return cleaned[: period + 1]


class SetupCheckCard(PanelCard):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.layout.setContentsMargins(14, 14, 14, 14)
        self.layout.setSpacing(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.layout.addWidget(self.status_label)

        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("sectionTitle")
        self.summary_label.setWordWrap(True)
        self.layout.addWidget(self.summary_label)

        self.detail_label = QLabel(self)
        self.detail_label.setObjectName("mutedText")
        self.detail_label.setWordWrap(True)
        self.layout.addWidget(self.detail_label)

    def set_check(self, check: SetupCheck) -> None:
        tone = {
            "ready": "good",
            "attention": "warn",
            "testing": "info",
            "optional": "info",
        }.get(check.status, "info")
        label = {
            "ready": "Ready",
            "attention": "Needs Attention",
            "testing": "Testing",
            "optional": "Optional",
        }.get(check.status, check.status.title())
        _set_tone(self.status_label, tone, label)
        self.summary_label.setText(check.summary)
        detail = f"Next: {check.action}" if check.action else _first_sentence(check.detail)
        self.detail_label.setText(detail)
        tooltip = check.detail
        if check.action:
            tooltip = f"{tooltip}\nNext: {check.action}"
        self.setToolTip(tooltip)
        self.summary_label.setToolTip(tooltip)
        self.detail_label.setToolTip(tooltip)


class SetupPage(QWidget):
    request_refresh = Signal()
    request_start_safe_test = Signal()
    request_stop_safe_test = Signal()

    def __init__(self, config_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._check_columns = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.page_layout = layout

        title = QLabel("Setup Center", self)
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "See what is ready, what is missing, and what to fix next before you trust the app with a live stream.",
            self,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.summary_card = PanelCard(parent=self)
        self.summary_card.setObjectName("headerCard")
        self.summary_card.layout.setSpacing(8)
        self.headline_label = QLabel("Checking setup status...", self.summary_card)
        self.headline_label.setObjectName("sectionTitle")
        self.headline_label.setWordWrap(True)
        self.summary_card.layout.addWidget(self.headline_label)

        self.summary_label = QLabel(
            "Run the readiness check to inspect output control, Twitch sync, chat, and the music overlay.",
            self.summary_card,
        )
        self.summary_label.setObjectName("mutedText")
        self.summary_label.setWordWrap(True)
        self.summary_card.layout.addWidget(self.summary_label)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Run Readiness Check", self.summary_card)
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.request_refresh.emit)
        self.start_test_button = QPushButton("Start Safe Test Session", self.summary_card)
        self.start_test_button.clicked.connect(self.request_start_safe_test.emit)
        self.stop_test_button = QPushButton("Stop Test Session", self.summary_card)
        self.stop_test_button.clicked.connect(self.request_stop_safe_test.emit)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.start_test_button)
        buttons.addWidget(self.stop_test_button)
        buttons.addStretch(1)
        self.summary_card.layout.addLayout(buttons)

        self.action_status = QLabel(
            "Config file: " + config_path,
            self.summary_card,
        )
        self.action_status.setObjectName("mutedText")
        self.action_status.setWordWrap(True)
        self.summary_card.layout.addWidget(self.action_status)

        self.next_steps_heading = QLabel("Next Steps", self.summary_card)
        self.next_steps_heading.setObjectName("sectionTitle")
        self.summary_card.layout.addWidget(self.next_steps_heading)

        self.next_steps_label = QLabel(
            "1. Run the readiness check.\n2. Start a safe test session if you want a dry run.",
            self.summary_card,
        )
        self.next_steps_label.setWordWrap(True)
        self.summary_card.layout.addWidget(self.next_steps_label)
        layout.addWidget(self.summary_card)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)
        self.check_grid = grid
        self.check_cards = {
            "output": SetupCheckCard("Live Output Path", self),
            "obs": SetupCheckCard("OBS Studio", self),
            "streamlabs": SetupCheckCard("Streamlabs Desktop", self),
            "broadcast": SetupCheckCard("Twitch Broadcast Sync", self),
            "chat": SetupCheckCard("Chat Management", self),
            "overlay": SetupCheckCard("Music Overlay", self),
        }
        if is_macos():
            self.check_cards["permissions"] = SetupCheckCard("macOS Permissions", self)
        card_order = ["output", "obs", "streamlabs", "broadcast", "chat", "overlay"]
        if "permissions" in self.check_cards:
            card_order.insert(3, "permissions")
        self._check_order = card_order
        self._relayout_check_cards(force=True)
        layout.addLayout(grid)
        layout.addStretch(1)

    def render_snapshot(self, snapshot: SetupSnapshot) -> None:
        self.headline_label.setText(snapshot.headline)
        self.summary_label.setText(snapshot.summary)
        self.start_test_button.setEnabled(snapshot.can_start_safe_test)
        self.stop_test_button.setEnabled(snapshot.safe_test_active)

        for check in snapshot.checks:
            card = self.check_cards.get(check.key)
            if card is not None:
                card.set_check(check)

        self.next_steps_label.setText("\n".join(f"{index + 1}. {step}" for index, step in enumerate(snapshot.next_steps)))
        self._relayout_check_cards(force=True)

    def set_action_message(self, ok: bool, message: str) -> None:
        _set_tone(self.action_status, "good" if ok else "warn", message)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._relayout_check_cards()
        return super().event(event)

    def _relayout_check_cards(self, force: bool = False) -> None:
        available_width = max(self.width(), self.sizeHint().width())
        columns = 4 if available_width >= 1050 else 3 if available_width >= 780 else 2 if available_width >= 520 else 1
        if not force and columns == self._check_columns:
            return

        self._check_columns = columns
        while self.check_grid.count():
            self.check_grid.takeAt(0)

        for index, key in enumerate(self._check_order):
            row = index // columns
            column = index % columns
            self.check_grid.addWidget(self.check_cards[key], row, column)

        for column in range(4):
            self.check_grid.setColumnStretch(column, 1 if column < columns else 0)


class SetupPlugin(AppPlugin):
    plugin_id = "setup"
    display_name = "Setup"
    nav_order = 5
    load_order = 95

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._page: SetupPage | None = None
        self._diagnostics: SetupDiagnosticsService | None = None
        self.obs_service: ObsService | None = None
        self.streamlabs_service: StreamlabsService | None = None
        self.chat_service: TwitchChatService | None = None
        self.music_plugin: Any | None = None

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self.obs_service = context.get_service("integrations.obs_service")
        self.streamlabs_service = context.get_service("integrations.streamlabs_service")
        self.chat_service = context.get_service("chat.twitch_service")
        self.music_plugin = context.get_service("music.plugin")
        self._diagnostics = SetupDiagnosticsService(context.qt_parent)
        self._page = SetupPage(str(context.app_paths.config_file), context.qt_parent)

        self._page.request_refresh.connect(lambda: context.schedule(self._refresh_snapshot()))
        self._page.request_start_safe_test.connect(lambda: context.schedule(self._start_safe_test_session()))
        self._page.request_stop_safe_test.connect(lambda: context.schedule(self._stop_safe_test_session()))

        if self.obs_service is not None:
            self.obs_service.connection_changed.connect(lambda *_: context.schedule(self._refresh_snapshot()))
        if self.streamlabs_service is not None:
            self.streamlabs_service.connection_changed.connect(lambda *_: context.schedule(self._refresh_snapshot()))
        if self.chat_service is not None:
            self.chat_service.connection_changed.connect(lambda *_: context.schedule(self._refresh_snapshot()))

        context.register_service("setup.plugin", self)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def on_plugins_loaded(self, _host) -> None:
        if self._context is not None:
            self._context.schedule(self._refresh_snapshot())

    async def _refresh_snapshot(self) -> None:
        if (
            self._context is None
            or self._page is None
            or self._diagnostics is None
        ):
            return

        snapshot = await self._diagnostics.build_snapshot(
            self._context.app_config,
            self._context.credential_store,
            self.obs_service,
            self.streamlabs_service,
            self.chat_service,
            self._overlay_status(),
        )
        self._page.render_snapshot(snapshot)

    async def _start_safe_test_session(self) -> None:
        if self._page is None or self._context is None:
            return
        if self.obs_service is None or self.streamlabs_service is None:
            self._page.set_action_message(
                False,
                "Safe test mode is unavailable because the output integration services did not finish loading.",
            )
            await self._refresh_snapshot()
            return

        blocking_real_sessions = []
        if self.obs_service.is_connected and not self.obs_service.is_simulated:
            blocking_real_sessions.append("OBS Studio")
        if self.streamlabs_service.is_connected and not self.streamlabs_service.is_simulated:
            blocking_real_sessions.append("Streamlabs Desktop")
        if self.chat_service is not None and self.chat_service.is_connected and not self.chat_service.is_simulated:
            blocking_real_sessions.append("Twitch Chat")

        if blocking_real_sessions:
            joined = ", ".join(blocking_real_sessions)
            self._page.set_action_message(
                False,
                f"Safe test mode was not started because real sessions are active: {joined}. Disconnect them first if you want a fully isolated rehearsal.",
            )
            await self._refresh_snapshot()
            return

        await self.obs_service.connect_simulated()
        await self.streamlabs_service.connect_simulated()
        if self.chat_service is not None:
            chat_raw = self._context.app_config.plugin_settings("chat")
            chat_channel = str(dict(chat_raw.get("twitch", {})).get("channel", "")).strip() or "streamcontrol"
            await self.chat_service.connect_simulated(channel=chat_channel)

        self._page.set_action_message(True, "Safe test session started. OBS, Streamlabs, and chat are now using simulators.")
        await self._refresh_snapshot()

    async def _stop_safe_test_session(self) -> None:
        if self._page is None:
            return
        if self.obs_service is None or self.streamlabs_service is None:
            self._page.set_action_message(
                False,
                "Safe test mode is unavailable because the output integration services did not finish loading.",
            )
            return

        stopped_any = False
        if self.obs_service.is_simulated:
            self.obs_service.disconnect()
            stopped_any = True
        if self.streamlabs_service.is_simulated:
            self.streamlabs_service.disconnect()
            stopped_any = True
        if self.chat_service is not None and self.chat_service.is_simulated:
            await self.chat_service.disconnect(silent=True)
            stopped_any = True

        if stopped_any:
            self._page.set_action_message(True, "Safe test session stopped. Real integrations are no longer being simulated.")
        else:
            self._page.set_action_message(False, "No safe test session was active.")
        await self._refresh_snapshot()

    def _overlay_status(self) -> OverlayServerStatus | None:
        if self.music_plugin is None or not hasattr(self.music_plugin, "overlay_status"):
            return None
        return self.music_plugin.overlay_status()
