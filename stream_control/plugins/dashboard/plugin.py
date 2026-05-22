from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from stream_control.plugins.base import AppPlugin, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.ui.widgets.common import MetricCard, PanelCard

if TYPE_CHECKING:
    from stream_control.services.hotkey_service import HotkeyService
    from stream_control.services.music_service import MusicService
    from stream_control.services.obs_service import ObsService
    from stream_control.services.streamlabs_service import StreamlabsService


class DashboardPage(QWidget):
    def __init__(self, overlay_url: str, music_service: MusicService | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._music_service = music_service

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        hero = PanelCard(parent=self)
        hero.setObjectName("headerCard")
        hero.layout.setSpacing(8)
        title = QLabel("Streaming control center built around plugins.", hero)
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        subtitle = QLabel(
            "Every surface in this app is now backed by a plugin that owns its own UI, state, and runtime logic.",
            hero,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        hero.layout.addWidget(title)
        hero.layout.addWidget(subtitle)
        layout.addWidget(hero)

        metrics_layout = QGridLayout()
        metrics_layout.setHorizontalSpacing(16)
        metrics_layout.setVerticalSpacing(16)
        self.obs_metric = MetricCard("OBS", "Offline", "WebSocket control for scenes and actions.")
        self.streamlabs_metric = MetricCard("Streamlabs", "Offline", "Desktop remote bridge for Streamlabs Desktop.")
        self.library_metric = MetricCard("Music Library", "0 tracks", "Local folders can feed playback and overlays.")
        self.hotkey_metric = MetricCard("Hotkeys", "Standby", "Global shortcuts can trigger plugin actions.")

        metrics_layout.addWidget(self.obs_metric, 0, 0)
        metrics_layout.addWidget(self.streamlabs_metric, 0, 1)
        metrics_layout.addWidget(self.library_metric, 1, 0)
        metrics_layout.addWidget(self.hotkey_metric, 1, 1)
        metrics_layout.setColumnStretch(0, 1)
        metrics_layout.setColumnStretch(1, 1)
        layout.addLayout(metrics_layout)

        actions_card = PanelCard("Quick Actions", self)
        actions = QHBoxLayout()
        play_pause = QPushButton("Play or Pause Music", actions_card)
        play_pause.setObjectName("primaryButton")
        play_pause.setEnabled(self._music_service is not None)
        if self._music_service is not None:
            play_pause.clicked.connect(self._music_service.toggle_play_pause)
        next_track = QPushButton("Next Track", actions_card)
        next_track.setEnabled(self._music_service is not None)
        if self._music_service is not None:
            next_track.clicked.connect(self._music_service.play_next)
        actions.addWidget(play_pause)
        actions.addWidget(next_track)
        actions.addStretch(1)
        actions_card.layout.addLayout(actions)

        overlay_label = QLabel("Overlay browser source URL", actions_card)
        overlay_label.setObjectName("mutedText")
        self.overlay_url = QLineEdit(overlay_url, actions_card)
        self.overlay_url.setReadOnly(True)
        actions_card.layout.addWidget(overlay_label)
        actions_card.layout.addWidget(self.overlay_url)

        self.now_playing = QLabel("Now playing: No track selected", actions_card)
        self.now_playing.setWordWrap(True)
        actions_card.layout.addWidget(self.now_playing)
        layout.addWidget(actions_card)
        layout.addStretch(1)

    def set_obs_status(self, connected: bool, message: str) -> None:
        self.obs_metric.set_value("Connected" if connected else "Offline")
        self.obs_metric.set_detail(message)

    def set_streamlabs_status(self, connected: bool, message: str) -> None:
        self.streamlabs_metric.set_value("Connected" if connected else "Offline")
        self.streamlabs_metric.set_detail(message)

    def set_library_count(self, count: int) -> None:
        self.library_metric.set_value(f"{count} tracks")
        self.library_metric.set_detail("Ready for queueing, playback, and browser overlays.")

    def set_hotkey_status(self, message: str) -> None:
        self.hotkey_metric.set_value("Active" if "Registered" in message else "Standby")
        self.hotkey_metric.set_detail(message)

    def set_now_playing(self, title: str, artist: str, status: str) -> None:
        detail = title if not artist else f"{title} - {artist}"
        self.now_playing.setText(f"Now playing: {detail} ({status})")


class DashboardPlugin(AppPlugin):
    plugin_id = "dashboard"
    display_name = "Dashboard"
    nav_order = 0
    load_order = 100

    def __init__(self) -> None:
        self._page: DashboardPage | None = None

    def activate(self, context: PluginContext) -> None:
        music_plugin = context.get_plugin("music")
        music_service: MusicService | None = context.get_service("music.service")
        obs_service: ObsService | None = context.get_service("integrations.obs_service")
        streamlabs_service: StreamlabsService | None = context.get_service("integrations.streamlabs_service")
        hotkey_service: HotkeyService | None = context.get_service("hotkeys.service")

        overlay_url = getattr(music_plugin, "overlay_url", "Unavailable while the Music plugin is offline.")
        self._page = DashboardPage(overlay_url, music_service, context.qt_parent)

        if obs_service is None:
            self._page.obs_metric.set_value("Unavailable")
            self._page.obs_metric.set_detail("The OBS integration did not finish loading in this environment.")
        else:
            self._page.set_obs_status(False, "OBS is offline.")
            obs_service.connection_changed.connect(self._page.set_obs_status)

        if streamlabs_service is None:
            self._page.streamlabs_metric.set_value("Unavailable")
            self._page.streamlabs_metric.set_detail(
                "The Streamlabs Desktop integration did not finish loading in this environment."
            )
        else:
            self._page.set_streamlabs_status(False, "Streamlabs Desktop is offline.")
            streamlabs_service.connection_changed.connect(self._page.set_streamlabs_status)

        if music_service is None:
            self._page.library_metric.set_value("Unavailable")
            self._page.library_metric.set_detail("The Music plugin is offline, so library and playback data are unavailable.")
            self._page.now_playing.setText("Now playing: Music plugin unavailable")
        else:
            self._page.set_library_count(len(music_service.library()))
            music_service.library_changed.connect(lambda tracks: self._page.set_library_count(len(tracks)))
            music_service.playback_changed.connect(self._sync_now_playing)

        if hotkey_service is None:
            self._page.hotkey_metric.set_value("Unavailable")
            self._page.hotkey_metric.set_detail("The Hotkeys plugin did not finish loading in this environment.")
        else:
            self._page.set_hotkey_status("Waiting for global hotkeys.")
            hotkey_service.status_changed.connect(self._page.set_hotkey_status)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def _sync_now_playing(self, payload: dict[str, object]) -> None:
        if self._page is None:
            return
        track = payload.get("current_track")
        if track is None:
            self._page.set_now_playing("No track selected", "Stream Control", payload["status"])
            return
        self._page.set_now_playing(track.title, str(payload.get("display_artist", "")), payload["status"])
