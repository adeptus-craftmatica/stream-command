from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stream_control.core.models import OverlaySettings, TrackRecord
from stream_control.plugins.base import AppPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.services.music_service import MusicService
from stream_control.services.overlay_server import OverlayServer, OverlayServerStatus
from stream_control.ui.widgets.common import (
    PanelCard,
    capture_table_column_widths,
    restore_table_column_widths,
    set_status_label,
)


@dataclass(slots=True)
class MusicPluginConfig:
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    library_directories: list[str] = field(default_factory=list)
    music_library: list[TrackRecord] = field(default_factory=list)
    music_volume: int = 75
    overlay_idle_message: str = "No Music Playing"
    library_column_widths: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "overlay": asdict(self.overlay),
            "library_directories": list(self.library_directories),
            "music_library": [asdict(track) for track in self.music_library],
            "music_volume": self.music_volume,
            "overlay_idle_message": self.overlay_idle_message,
            "library_column_widths": list(self.library_column_widths),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "MusicPluginConfig":
        return cls(
            overlay=OverlaySettings(**raw.get("overlay", {})),
            library_directories=list(raw.get("library_directories", [])),
            music_library=[TrackRecord(**item) for item in raw.get("music_library", [])],
            music_volume=int(raw.get("music_volume", 75)),
            overlay_idle_message=str(raw.get("overlay_idle_message", "No Music Playing")),
            library_column_widths=[max(40, int(width)) for width in raw.get("library_column_widths", [])],
        )


class MusicPage(QWidget):
    settings_changed = Signal()

    def __init__(self, settings: MusicPluginConfig, music_service: MusicService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._music_service = music_service
        self._library_tracks: list[TrackRecord] = list(settings.music_library)
        self._queue_tracks: list[TrackRecord] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title = QLabel("Music and Overlay")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Load local music folders, queue tracks, and publish a browser-source overlay with the current track.",
            self,
        )
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        overlay_card = PanelCard("Now Playing Overlay", self)
        overlay_card.layout.addWidget(QLabel("Use this URL in an OBS or Streamlabs browser source:", overlay_card))
        self.overlay_url = QLineEdit(self._settings.overlay.now_playing_url, overlay_card)
        self.overlay_url.setReadOnly(True)
        overlay_card.layout.addWidget(self.overlay_url)
        self.overlay_status = QLabel("", overlay_card)
        self.overlay_status.setWordWrap(True)
        overlay_card.layout.addWidget(self.overlay_status)
        overlay_card.layout.addWidget(QLabel("Overlay message when music is stopped:", overlay_card))
        self.overlay_idle_message = QLineEdit(self._settings.overlay_idle_message, overlay_card)
        self.overlay_idle_message.setPlaceholderText("No Music Playing")
        self.overlay_idle_message.editingFinished.connect(self._store_overlay_idle_message)
        overlay_card.layout.addWidget(self.overlay_idle_message)
        layout.addWidget(overlay_card)

        controls_card = PanelCard("Playback Controls", self)
        controls_row = QHBoxLayout()
        play_pause = QPushButton("Play or Pause", controls_card)
        play_pause.setObjectName("primaryButton")
        play_pause.clicked.connect(self._music_service.toggle_play_pause)
        stop_music = QPushButton("Stop", controls_card)
        stop_music.clicked.connect(self._music_service.stop_playback)
        next_track = QPushButton("Next Track", controls_card)
        next_track.clicked.connect(self._music_service.play_next)
        refresh_library = QPushButton("Refresh Library", controls_card)
        refresh_library.clicked.connect(self._reload_library)
        add_folder = QPushButton("Add Music Folder", controls_card)
        add_folder.clicked.connect(self._choose_folder)
        controls_row.addWidget(play_pause)
        controls_row.addWidget(stop_music)
        controls_row.addWidget(next_track)
        controls_row.addWidget(refresh_library)
        controls_row.addWidget(add_folder)
        controls_row.addStretch(1)
        controls_card.layout.addLayout(controls_row)

        self.now_playing = QLabel("Now playing: No track selected", controls_card)
        self.now_playing.setWordWrap(True)
        controls_card.layout.addWidget(self.now_playing)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Music volume", controls_card))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, controls_card)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._settings.music_volume)
        self.volume_slider.valueChanged.connect(self._set_volume)
        volume_row.addWidget(self.volume_slider)
        controls_card.layout.addLayout(volume_row)

        self.message_label = QLabel("", controls_card)
        self.message_label.setObjectName("mutedText")
        controls_card.layout.addWidget(self.message_label)
        layout.addWidget(controls_card)

        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        library_card = PanelCard("Library", self)
        self.library_table = QTableWidget(0, 2, library_card)
        self.library_table.setHorizontalHeaderLabels(["Title", "Artist"])
        self.library_table.setAlternatingRowColors(True)
        self.library_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.library_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.library_table.horizontalHeader().sectionResized.connect(self._store_library_layout)
        library_card.layout.addWidget(self.library_table)

        library_actions = QHBoxLayout()
        queue_button = QPushButton("Queue Selected", library_card)
        queue_button.clicked.connect(self._queue_selected_track)
        play_button = QPushButton("Play Selected", library_card)
        play_button.clicked.connect(self._play_selected_track)
        library_actions.addWidget(queue_button)
        library_actions.addWidget(play_button)
        library_actions.addStretch(1)
        library_card.layout.addLayout(library_actions)

        queue_card = PanelCard("Queue", self)
        self.queue_list = QListWidget(queue_card)
        queue_card.layout.addWidget(self.queue_list)
        clear_queue = QPushButton("Clear Queue", queue_card)
        clear_queue.clicked.connect(self._music_service.clear_queue)
        queue_card.layout.addWidget(clear_queue)

        content_row.addWidget(library_card, 2)
        content_row.addWidget(queue_card, 1)
        layout.addLayout(content_row)
        layout.addStretch(1)

        self._music_service.library_changed.connect(self._render_library)
        self._music_service.queue_changed.connect(self._render_queue)
        self._music_service.playback_changed.connect(self._update_playback)
        self._music_service.status_message.connect(self._set_message)

        if self._library_tracks:
            self._render_library(self._library_tracks)
        if self._settings.library_column_widths:
            restore_table_column_widths(self.library_table, self._settings.library_column_widths)
        else:
            self._apply_default_library_widths()

    def set_overlay_url(self, overlay_url: str) -> None:
        self.overlay_url.setText(overlay_url)

    def set_overlay_status(self, tone: str, message: str) -> None:
        if tone == "good":
            set_status_label(self.overlay_status, True, message)
            return
        self.overlay_status.setObjectName("statusInfo" if tone == "info" else "statusWarn")
        self.overlay_status.setText(message)
        self.overlay_status.style().unpolish(self.overlay_status)
        self.overlay_status.style().polish(self.overlay_status)
        self.overlay_status.update()

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose a music folder")
        if not directory:
            return
        resolved = str(Path(directory).resolve())
        if resolved not in self._settings.library_directories:
            self._settings.library_directories.append(resolved)
            self.settings_changed.emit()
        self._reload_library()

    def _reload_library(self) -> None:
        self._settings.music_library = self._music_service.load_library(self._settings.library_directories)
        self.settings_changed.emit()

    def _render_library(self, tracks: list[TrackRecord]) -> None:
        self._library_tracks = list(tracks)
        self.library_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            title_item = QTableWidgetItem(track.title)
            title_item.setData(Qt.ItemDataRole.UserRole, track.id)
            artist_item = QTableWidgetItem(track.artist)
            self.library_table.setItem(row, 0, title_item)
            self.library_table.setItem(row, 1, artist_item)
        if self._settings.library_column_widths:
            restore_table_column_widths(self.library_table, self._settings.library_column_widths)
        else:
            self._apply_default_library_widths()

    def _render_queue(self, tracks: list[TrackRecord]) -> None:
        self._queue_tracks = list(tracks)
        self.queue_list.clear()
        for track in tracks:
            item = QListWidgetItem(f"{track.title} - {track.artist}")
            item.setData(Qt.ItemDataRole.UserRole, track.id)
            self.queue_list.addItem(item)

    def _selected_track(self) -> TrackRecord | None:
        row = self.library_table.currentRow()
        if row < 0 or row >= len(self._library_tracks):
            return None
        return self._library_tracks[row]

    def _queue_selected_track(self) -> None:
        track = self._selected_track()
        if track is None:
            self._set_message("Select a library track first.")
            return
        self._music_service.queue_track(track)

    def _play_selected_track(self) -> None:
        track = self._selected_track()
        if track is None:
            self._set_message("Select a library track first.")
            return
        self._music_service.play_track(track)

    def _update_playback(self, payload: dict[str, object]) -> None:
        track = payload.get("current_track")
        if track is None:
            idle_message = self._settings.overlay_idle_message.strip() or "No Music Playing"
            self.now_playing.setText(f"Now playing: {idle_message} ({payload['status']})")
            return
        self.now_playing.setText(f"Now playing: {track.title} - {track.artist} ({payload['status']})")

    def _set_message(self, message: str) -> None:
        self.message_label.setText(message)

    def _set_volume(self, value: int) -> None:
        self._settings.music_volume = value
        self._music_service.set_volume(value)
        self.settings_changed.emit()

    def _store_overlay_idle_message(self) -> None:
        self._settings.overlay_idle_message = self.overlay_idle_message.text().strip() or "No Music Playing"
        self.overlay_idle_message.setText(self._settings.overlay_idle_message)
        self._music_service.set_overlay_idle_message(self._settings.overlay_idle_message)
        self.settings_changed.emit()

    def _store_library_layout(self, *_args: object) -> None:
        self._settings.library_column_widths = capture_table_column_widths(self.library_table)
        self.settings_changed.emit()

    def _apply_default_library_widths(self) -> None:
        viewport_width = max(self.library_table.viewport().width(), 760)
        self.library_table.setColumnWidth(0, int(viewport_width * 0.62))
        self.library_table.setColumnWidth(1, int(viewport_width * 0.34))


class MusicPlugin(AppPlugin):
    plugin_id = "music"
    display_name = "Music"
    nav_order = 20
    load_order = 20

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._settings = MusicPluginConfig()
        self._page: MusicPage | None = None
        self.music_service: MusicService | None = None
        self.overlay_server: OverlayServer | None = None

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._settings = MusicPluginConfig.from_dict(context.plugin_settings(self.plugin_id))

        self.music_service = MusicService(context.qt_parent)
        self.music_service.set_volume(self._settings.music_volume)
        self.music_service.set_overlay_idle_message(self._settings.overlay_idle_message)
        self.overlay_server = OverlayServer(self._settings.overlay, self.music_service.overlay_state)

        self._page = MusicPage(self._settings, self.music_service, context.qt_parent)
        self._page.settings_changed.connect(self._save_settings)
        self._page.set_overlay_url(self._settings.overlay.now_playing_url)
        self._refresh_overlay_status(initial=True)

        context.register_service("music.service", self.music_service)
        context.register_service("music.plugin", self)

    def page(self) -> PluginPage | None:
        if self._page is None:
            return None
        return PluginPage(self.plugin_id, self.display_name, self._page, self.nav_order)

    def hotkey_actions(self) -> list[HotkeyAction]:
        if self.music_service is None:
            return []
        return [
            HotkeyAction(
                action_id="music.play_pause",
                label="Play or pause music",
                handler=self.music_service.toggle_play_pause,
                default_combo="<ctrl>+<alt>+p",
                default_enabled=True,
            ),
            HotkeyAction(
                action_id="music.next_track",
                label="Next track",
                handler=self.music_service.play_next,
                default_combo="<ctrl>+<alt>+n",
                default_enabled=True,
            ),
            HotkeyAction(
                action_id="music.stop",
                label="Stop music",
                handler=self.music_service.stop_playback,
                default_combo="",
                default_enabled=False,
            ),
        ]

    def on_plugins_loaded(self, _host) -> None:
        if self.overlay_server is not None:
            self.overlay_server.start()
            self._refresh_overlay_status()
        if self._settings.library_directories and self.music_service is not None:
            self._settings.music_library = self.music_service.load_library(self._settings.library_directories)
            self._save_settings()

    def shutdown(self) -> None:
        if self.overlay_server is not None:
            self.overlay_server.stop()

    @property
    def overlay_url(self) -> str:
        return self._settings.overlay.now_playing_url

    def overlay_status(self) -> OverlayServerStatus:
        if self.overlay_server is None:
            return OverlayServerStatus(
                enabled=self._settings.overlay.enabled,
                running=False,
                url=self.overlay_url,
            )
        return self.overlay_server.status()

    def _save_settings(self) -> None:
        if self._context is None:
            return
        self._context.save_plugin_settings(self.plugin_id, self._settings.to_dict())

    def _refresh_overlay_status(self, initial: bool = False) -> None:
        if self._page is None:
            return
        status = self.overlay_status()
        if not status.enabled:
            self._page.set_overlay_status("info", "Overlay server is disabled in config.")
            return
        if status.running:
            self._page.set_overlay_status("good", f"Overlay server is live at {status.url}.")
            return
        if status.last_error:
            self._page.set_overlay_status(
                "warn",
                f"Overlay failed to start: {status.last_error}. Free the port or update the overlay host/port in config.",
            )
            return
        if initial:
            self._page.set_overlay_status("info", "Overlay server will start when the app finishes loading.")
            return
        self._page.set_overlay_status("warn", "Overlay server is enabled but is not running yet.")
