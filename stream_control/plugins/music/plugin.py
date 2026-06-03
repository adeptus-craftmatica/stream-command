from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stream_control.core.audio import SYSTEM_DEFAULT_AUDIO_OUTPUT_ID, list_audio_output_options
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
class MusicPlaylist:
    name: str
    track_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MusicPluginConfig:
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    library_directories: list[str] = field(default_factory=list)
    music_library: list[TrackRecord] = field(default_factory=list)
    music_volume: int = 75
    overlay_idle_message: str = "No Music Playing"
    show_artist: bool = True
    playlists: list[MusicPlaylist] = field(default_factory=list)
    selected_playlist_name: str = ""
    transition_mode: str = "none"
    transition_duration_ms: int = 900
    repeat_mode: str = "off"
    playback_order: str = "ordered"
    output_device_id: str = SYSTEM_DEFAULT_AUDIO_OUTPUT_ID
    library_column_widths: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "overlay": asdict(self.overlay),
            "library_directories": list(self.library_directories),
            "music_library": [asdict(track) for track in self.music_library],
            "music_volume": self.music_volume,
            "overlay_idle_message": self.overlay_idle_message,
            "show_artist": self.show_artist,
            "playlists": [asdict(playlist) for playlist in self.playlists],
            "selected_playlist_name": self.selected_playlist_name,
            "transition_mode": self.transition_mode,
            "transition_duration_ms": self.transition_duration_ms,
            "repeat_mode": self.repeat_mode,
            "playback_order": self.playback_order,
            "output_device_id": self.output_device_id,
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
            show_artist=bool(raw.get("show_artist", True)),
            playlists=[MusicPlaylist(**item) for item in raw.get("playlists", []) if isinstance(item, dict)],
            selected_playlist_name=str(raw.get("selected_playlist_name", "")),
            transition_mode=str(raw.get("transition_mode", "none") or "none"),
            transition_duration_ms=max(0, int(raw.get("transition_duration_ms", 900) or 900)),
            repeat_mode=str(raw.get("repeat_mode", "off") or "off"),
            playback_order=str(raw.get("playback_order", "ordered") or "ordered"),
            output_device_id=str(raw.get("output_device_id", SYSTEM_DEFAULT_AUDIO_OUTPUT_ID) or SYSTEM_DEFAULT_AUDIO_OUTPUT_ID),
            library_column_widths=[max(40, int(width)) for width in raw.get("library_column_widths", [])],
        )

    def playlist_by_name(self, name: str) -> MusicPlaylist | None:
        normalized = name.strip().lower()
        if not normalized:
            return None
        for playlist in self.playlists:
            if playlist.name.strip().lower() == normalized:
                return playlist
        return None

    def upsert_playlist(self, playlist: MusicPlaylist) -> None:
        existing = self.playlist_by_name(playlist.name)
        if existing is None:
            self.playlists.append(playlist)
            return
        existing.name = playlist.name
        existing.track_ids = list(playlist.track_ids)

    def remove_playlist(self, name: str) -> bool:
        normalized = name.strip().lower()
        if not normalized:
            return False
        original_count = len(self.playlists)
        self.playlists = [playlist for playlist in self.playlists if playlist.name.strip().lower() != normalized]
        return len(self.playlists) != original_count


class _CompactLibraryRenameDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setObjectName("inlineItemEditor")
        return editor


class MusicPage(QWidget):
    settings_changed = Signal()

    def __init__(self, settings: MusicPluginConfig, music_service: MusicService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._music_service = music_service
        self._all_library_tracks: list[TrackRecord] = list(settings.music_library)
        self._library_tracks: list[TrackRecord] = list(self._all_library_tracks)
        self._queue_tracks: list[TrackRecord] = []
        self._playlist_track_ids: list[str] = []
        self._is_scrubbing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("Music and Overlay")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Build playlists, manage your queue, and keep the now-playing overlay in one compact music workspace.",
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
        self.show_artist = QCheckBox("Show artist in now playing", overlay_card)
        self.show_artist.setChecked(self._settings.show_artist)
        self.show_artist.toggled.connect(self._store_show_artist)
        overlay_card.layout.addWidget(self.show_artist)

        controls_card = PanelCard("Playback Controls", self)
        controls_row = QHBoxLayout()
        self.controls_row = controls_row
        play_pause = QPushButton("Play or Pause", controls_card)
        play_pause.setObjectName("primaryButton")
        play_pause.clicked.connect(self._music_service.toggle_play_pause)
        stop_music = QPushButton("Stop", controls_card)
        stop_music.clicked.connect(self._music_service.stop_playback)
        next_track = QPushButton("Next Track", controls_card)
        next_track.clicked.connect(self._music_service.play_next)
        play_random = QPushButton("Start Shuffle", controls_card)
        play_random.clicked.connect(self._music_service.play_random_track)
        refresh_library = QPushButton("Refresh Library", controls_card)
        refresh_library.clicked.connect(self._reload_library)
        add_folder = QPushButton("Add Music Folder", controls_card)
        add_folder.clicked.connect(self._choose_folder)
        controls_row.addWidget(play_pause)
        controls_row.addWidget(stop_music)
        controls_row.addWidget(next_track)
        controls_row.addWidget(play_random)
        controls_row.addWidget(refresh_library)
        controls_row.addWidget(add_folder)
        controls_row.addStretch(1)
        controls_card.layout.addLayout(controls_row)

        self.now_playing = QLabel("Now playing: No track selected", controls_card)
        self.now_playing.setWordWrap(True)
        controls_card.layout.addWidget(self.now_playing)

        self.playback_position = QLabel("0:00 / 0:00", controls_card)
        self.playback_position.setObjectName("mutedText")
        controls_card.layout.addWidget(self.playback_position)

        self.progress_slider = QSlider(Qt.Orientation.Horizontal, controls_card)
        self.progress_slider.setRange(0, 0)
        self.progress_slider.setEnabled(False)
        self.progress_slider.sliderPressed.connect(self._start_scrub)
        self.progress_slider.sliderMoved.connect(self._preview_scrub)
        self.progress_slider.sliderReleased.connect(self._apply_scrub)
        controls_card.layout.addWidget(self.progress_slider)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Music volume", controls_card))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, controls_card)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._settings.music_volume)
        self.volume_slider.valueChanged.connect(self._set_volume)
        volume_row.addWidget(self.volume_slider)
        controls_card.layout.addLayout(volume_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Audio output", controls_card))
        self.output_device = QComboBox(controls_card)
        self.output_device.currentIndexChanged.connect(self._store_output_device)
        output_row.addWidget(self.output_device, 1)
        refresh_outputs = QPushButton("Refresh Outputs", controls_card)
        refresh_outputs.clicked.connect(self._populate_output_devices)
        output_row.addWidget(refresh_outputs)
        controls_card.layout.addLayout(output_row)

        transition_row = QHBoxLayout()
        transition_row.addWidget(QLabel("Transition", controls_card))
        self.transition_mode = QComboBox(controls_card)
        self.transition_mode.addItem("No transition", "none")
        self.transition_mode.addItem("Fade in", "fade_in")
        self.transition_mode.addItem("Fade out and in", "fade_out_in")
        self.transition_mode.setCurrentIndex(max(0, self.transition_mode.findData(self._settings.transition_mode)))
        self.transition_mode.currentIndexChanged.connect(self._store_transition_settings)
        transition_row.addWidget(self.transition_mode)
        self.transition_duration = QSpinBox(controls_card)
        self.transition_duration.setRange(0, 10_000)
        self.transition_duration.setSingleStep(100)
        self.transition_duration.setSuffix(" ms")
        self.transition_duration.setValue(self._settings.transition_duration_ms)
        self.transition_duration.valueChanged.connect(self._store_transition_settings)
        transition_row.addWidget(self.transition_duration)
        transition_row.addWidget(QLabel("Order", controls_card))
        self.playback_order = QComboBox(controls_card)
        self.playback_order.addItem("In Order", "ordered")
        self.playback_order.addItem("Shuffle", "shuffle")
        self.playback_order.setCurrentIndex(max(0, self.playback_order.findData(self._settings.playback_order)))
        self.playback_order.currentIndexChanged.connect(self._store_playback_order)
        transition_row.addWidget(self.playback_order)
        transition_row.addWidget(QLabel("Repeat", controls_card))
        self.repeat_mode = QComboBox(controls_card)
        self.repeat_mode.addItem("Repeat Off", "off")
        self.repeat_mode.addItem("Repeat One", "one")
        self.repeat_mode.addItem("Repeat All", "all")
        self.repeat_mode.setCurrentIndex(max(0, self.repeat_mode.findData(self._settings.repeat_mode)))
        self.repeat_mode.currentIndexChanged.connect(self._store_repeat_mode)
        transition_row.addWidget(self.repeat_mode)
        transition_row.addStretch(1)
        controls_card.layout.addLayout(transition_row)

        self.message_label = QLabel("", controls_card)
        self.message_label.setObjectName("mutedText")
        controls_card.layout.addWidget(self.message_label)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        top_row.addWidget(controls_card, 3)
        top_row.addWidget(overlay_card, 2)
        self.top_row = top_row
        layout.addLayout(top_row)

        self.music_tabs = QTabWidget(self)
        self.music_tabs.addTab(self._build_library_queue_tab(), "Library and Queue")
        self.music_tabs.addTab(self._build_playlists_tab(), "Playlists")
        layout.addWidget(self.music_tabs, 1)

        self._music_service.library_changed.connect(self._render_library)
        self._music_service.queue_changed.connect(self._render_queue)
        self._music_service.playback_changed.connect(self._update_playback)
        self._music_service.status_message.connect(self._set_message)

        if self._all_library_tracks:
            self._render_library(self._all_library_tracks)
        self._populate_output_devices()
        self._render_playlists()
        self._restore_playlist_state()
        if self._settings.library_column_widths:
            restore_table_column_widths(self.library_table, self._settings.library_column_widths)
        else:
            self._apply_default_library_widths()
        self._update_library_selection_actions()
        self._update_library_summary()
        self._update_queue_summary()
        self._update_queue_actions()
        self._update_playlist_actions()

    def set_tablet_mode(self, enabled: bool) -> None:
        self.top_row.setDirection(
            QBoxLayout.Direction.TopToBottom if enabled else QBoxLayout.Direction.LeftToRight
        )
        self.library_queue_row.setDirection(
            QBoxLayout.Direction.TopToBottom if enabled else QBoxLayout.Direction.LeftToRight
        )
        self.playlist_lists_row.setDirection(
            QBoxLayout.Direction.TopToBottom if enabled else QBoxLayout.Direction.LeftToRight
        )
        self.music_tabs.tabBar().setExpanding(enabled)

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
        self._settings.music_library = self._music_service.load_library(
            self._settings.library_directories,
            existing_tracks=self._settings.music_library,
        )
        self.settings_changed.emit()

    def _render_library(self, tracks: list[TrackRecord]) -> None:
        self._all_library_tracks = list(tracks)
        self._apply_library_filter()

    def _apply_library_filter(self) -> None:
        query = self.library_search.text().strip().lower() if hasattr(self, "library_search") else ""
        if not query:
            filtered = list(self._all_library_tracks)
        else:
            filtered = [
                track
                for track in self._all_library_tracks
                if query in track.title.lower()
                or query in track.artist.lower()
                or query in Path(track.path).stem.lower()
            ]
        self._library_tracks = filtered
        blocker = QSignalBlocker(self.library_table)
        self.library_table.setRowCount(len(filtered))
        for row, track in enumerate(filtered):
            title_item = QTableWidgetItem(track.title)
            title_item.setData(Qt.ItemDataRole.UserRole, track.id)
            artist_item = QTableWidgetItem(track.artist)
            self.library_table.setItem(row, 0, title_item)
            self.library_table.setItem(row, 1, artist_item)
        del blocker
        if self._settings.library_column_widths:
            restore_table_column_widths(self.library_table, self._settings.library_column_widths)
        else:
            self._apply_default_library_widths()
        self._update_library_summary()
        self._render_playlist_tracks()
        self._update_library_selection_actions()

    def _render_queue(self, tracks: list[TrackRecord]) -> None:
        self._queue_tracks = list(tracks)
        self.queue_list.clear()
        for track in tracks:
            item = QListWidgetItem(self._track_display_label(track))
            item.setData(Qt.ItemDataRole.UserRole, track.id)
            self.queue_list.addItem(item)
        self._update_queue_summary()
        self._update_queue_actions()

    def _selected_tracks(self) -> list[TrackRecord]:
        selection_model = self.library_table.selectionModel()
        if selection_model is None:
            return []
        rows = sorted({index.row() for index in selection_model.selectedRows()})
        return [self._library_tracks[row] for row in rows if 0 <= row < len(self._library_tracks)]

    def _queue_selected_track(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            self._set_message("Select at least one library track first.")
            return
        if len(tracks) == 1:
            self._music_service.queue_track(tracks[0])
            return
        self._music_service.queue_tracks(tracks)

    def _play_selected_track(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            self._set_message("Select at least one library track first.")
            return
        if len(tracks) == 1:
            self._music_service.play_track(tracks[0])
            return
        self._music_service.play_tracks(tracks)

    def _update_playback(self, payload: dict[str, object]) -> None:
        track = payload.get("current_track")
        position_ms = int(payload.get("position_ms", 0) or 0)
        duration_ms = int(payload.get("duration_ms", 0) or 0)
        self.progress_slider.setEnabled(track is not None and duration_ms > 0)
        if not self._is_scrubbing:
            blocker = QSignalBlocker(self.progress_slider)
            self.progress_slider.setRange(0, max(duration_ms, 0))
            self.progress_slider.setValue(min(position_ms, max(duration_ms, 0)))
            del blocker
            self.playback_position.setText(
                f"{payload.get('elapsed_label', '0:00')} / {payload.get('duration_label', '0:00')}"
            )
        if track is None:
            idle_message = self._settings.overlay_idle_message.strip() or "No Music Playing"
            self.now_playing.setText(f"Now playing: {idle_message} ({payload['status']})")
            if not self._is_scrubbing:
                self.playback_position.setText("0:00 / 0:00")
            return
        artist = str(payload.get("display_artist", ""))
        detail = track.title if not artist else f"{track.title} - {artist}"
        self.now_playing.setText(f"Now playing: {detail} ({payload['status']})")

    def _set_message(self, message: str) -> None:
        self.message_label.setText(message)

    def _set_volume(self, value: int) -> None:
        self._settings.music_volume = value
        self._music_service.set_volume(value)
        self.settings_changed.emit()

    def _populate_output_devices(self) -> None:
        current_device_id = self._settings.output_device_id
        options = list_audio_output_options()
        blocker = QSignalBlocker(self.output_device)
        self.output_device.clear()
        for option in options:
            self.output_device.addItem(option.label, option.device_id)
        selected_index = self.output_device.findData(current_device_id)
        if selected_index < 0:
            selected_index = 0
        self.output_device.setCurrentIndex(selected_index)
        del blocker
        self._store_output_device()

    def _store_transition_settings(self, *_args: object) -> None:
        self._settings.transition_mode = str(self.transition_mode.currentData() or "none")
        self._settings.transition_duration_ms = int(self.transition_duration.value())
        self._music_service.set_transition(
            self._settings.transition_mode,
            self._settings.transition_duration_ms,
        )
        self.settings_changed.emit()

    def _store_repeat_mode(self, *_args: object) -> None:
        self._settings.repeat_mode = str(self.repeat_mode.currentData() or "off")
        self._music_service.set_repeat_mode(self._settings.repeat_mode)
        self.settings_changed.emit()

    def _store_playback_order(self, *_args: object) -> None:
        self._settings.playback_order = str(self.playback_order.currentData() or "ordered")
        self._music_service.set_playback_order(self._settings.playback_order)
        self.settings_changed.emit()

    def _store_output_device(self, *_args: object) -> None:
        self._settings.output_device_id = str(self.output_device.currentData() or SYSTEM_DEFAULT_AUDIO_OUTPUT_ID)
        self._music_service.set_output_device(self._settings.output_device_id)
        self.settings_changed.emit()

    def _store_overlay_idle_message(self) -> None:
        self._settings.overlay_idle_message = self.overlay_idle_message.text().strip() or "No Music Playing"
        self.overlay_idle_message.setText(self._settings.overlay_idle_message)
        self._music_service.set_overlay_idle_message(self._settings.overlay_idle_message)
        self.settings_changed.emit()

    def _store_show_artist(self, checked: bool) -> None:
        self._settings.show_artist = bool(checked)
        self._music_service.set_show_artist(self._settings.show_artist)
        self.settings_changed.emit()

    def _start_scrub(self) -> None:
        self._is_scrubbing = True

    def _preview_scrub(self, value: int) -> None:
        duration_ms = max(self.progress_slider.maximum(), 0)
        self.playback_position.setText(f"{self._format_time(value)} / {self._format_time(duration_ms)}")

    def _apply_scrub(self) -> None:
        target = self.progress_slider.value()
        self._is_scrubbing = False
        self._music_service.seek(target)

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        total_seconds = max(0, int(milliseconds) // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def _store_library_layout(self, *_args: object) -> None:
        self._settings.library_column_widths = capture_table_column_widths(self.library_table)
        self.settings_changed.emit()

    def _apply_default_library_widths(self) -> None:
        viewport_width = max(self.library_table.viewport().width(), 760)
        self.library_table.setColumnWidth(0, int(viewport_width * 0.62))
        self.library_table.setColumnWidth(1, int(viewport_width * 0.34))

    def _update_library_summary(self) -> None:
        shown = len(self._library_tracks)
        total = len(self._all_library_tracks)
        if total == 0:
            self.library_summary.setText("Library is empty. Add a music folder to get started.")
            return
        if shown == total:
            self.library_summary.setText(f"{total} track(s) in your library.")
            return
        self.library_summary.setText(f"Showing {shown} of {total} library track(s).")

    def _update_queue_summary(self) -> None:
        count = len(self._queue_tracks)
        if count == 0:
            self.queue_summary.setText("Queue is empty.")
            return
        self.queue_summary.setText(f"{count} track(s) waiting in the queue.")

    @staticmethod
    def _track_display_label(track: TrackRecord) -> str:
        title = track.title.strip() or "Untitled Track"
        artist = track.artist.strip()
        return title if not artist else f"{title} - {artist}"

    @staticmethod
    def _fallback_track_title(track: TrackRecord) -> str:
        if track.title.strip():
            return track.title.strip()
        if track.path:
            stem = Path(track.path).stem.replace("_", " ").replace("-", " ").strip()
            if stem:
                return stem
        return "Untitled Track"

    def _build_playlist_card(self) -> PanelCard:
        card = PanelCard("Playlist Builder", self)
        card.layout.addWidget(
            QLabel(
                "Save reusable sets here, then use the Library tab to add highlighted tracks into the current playlist.",
                card,
            )
        )

        name_row = QHBoxLayout()
        self.playlist_name = QLineEdit(card)
        self.playlist_name.setPlaceholderText("Playlist name")
        name_row.addWidget(self.playlist_name)
        new_playlist = QPushButton("New Playlist", card)
        new_playlist.clicked.connect(self._new_playlist)
        save_playlist = QPushButton("Save Playlist", card)
        save_playlist.setObjectName("primaryButton")
        save_playlist.clicked.connect(self._save_playlist)
        delete_playlist = QPushButton("Delete Playlist", card)
        delete_playlist.clicked.connect(self._delete_playlist)
        name_row.addWidget(new_playlist)
        name_row.addWidget(save_playlist)
        name_row.addWidget(delete_playlist)
        card.layout.addLayout(name_row)

        lists_row = QHBoxLayout()
        self.playlist_lists_row = lists_row

        saved_column = QVBoxLayout()
        saved_column.addWidget(QLabel("Saved playlists", card))
        self.playlist_list = QListWidget(card)
        self.playlist_list.itemSelectionChanged.connect(self._handle_playlist_selection)
        saved_column.addWidget(self.playlist_list, 1)
        lists_row.addLayout(saved_column, 1)

        tracks_column = QVBoxLayout()
        tracks_column.addWidget(QLabel("Tracks in current playlist", card))
        self.playlist_tracks = QListWidget(card)
        self.playlist_tracks.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.playlist_tracks.itemSelectionChanged.connect(self._update_playlist_actions)
        tracks_column.addWidget(self.playlist_tracks, 1)
        lists_row.addLayout(tracks_column, 2)

        card.layout.addLayout(lists_row, 1)

        track_actions = QHBoxLayout()
        self.playlist_add_button = QPushButton("Add Track From Library", card)
        self.playlist_add_button.setObjectName("primaryButton")
        self.playlist_add_button.clicked.connect(self._add_selected_tracks_to_playlist)
        self.playlist_move_up_button = QPushButton("Move Up", card)
        self.playlist_move_up_button.clicked.connect(lambda: self._move_selected_playlist_tracks(-1))
        self.playlist_move_down_button = QPushButton("Move Down", card)
        self.playlist_move_down_button.clicked.connect(lambda: self._move_selected_playlist_tracks(1))
        self.remove_playlist_track_button = QPushButton("Remove Selected", card)
        self.remove_playlist_track_button.clicked.connect(self._remove_selected_playlist_tracks)
        self.clear_playlist_button = QPushButton("Clear Playlist", card)
        self.clear_playlist_button.clicked.connect(self._clear_playlist_tracks)
        track_actions.addWidget(self.playlist_add_button)
        track_actions.addWidget(self.playlist_move_up_button)
        track_actions.addWidget(self.playlist_move_down_button)
        track_actions.addWidget(self.remove_playlist_track_button)
        track_actions.addWidget(self.clear_playlist_button)
        track_actions.addStretch(1)
        card.layout.addLayout(track_actions)

        playlist_actions = QHBoxLayout()
        self.queue_playlist_button = QPushButton("Add Playlist To Queue", card)
        self.queue_playlist_button.clicked.connect(self._queue_playlist)
        self.play_playlist_button = QPushButton("Play Playlist", card)
        self.play_playlist_button.clicked.connect(self._play_playlist)
        playlist_actions.addWidget(self.queue_playlist_button)
        playlist_actions.addWidget(self.play_playlist_button)
        playlist_actions.addStretch(1)
        card.layout.addLayout(playlist_actions)

        self.playlist_status = QLabel("", card)
        self.playlist_status.setObjectName("mutedText")
        self.playlist_status.setWordWrap(True)
        card.layout.addWidget(self.playlist_status)
        return card

    def _build_library_queue_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        content_row = QHBoxLayout()
        content_row.setSpacing(12)
        self.library_queue_row = content_row

        library_card = PanelCard("Library", tab)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search", library_card))
        self.library_search = QLineEdit(library_card)
        self.library_search.setPlaceholderText("Filter by title, artist, or file name")
        self.library_search.textChanged.connect(lambda _text: self._apply_library_filter())
        search_row.addWidget(self.library_search, 1)
        clear_search = QPushButton("Clear", library_card)
        clear_search.clicked.connect(self.library_search.clear)
        search_row.addWidget(clear_search)
        library_card.layout.addLayout(search_row)

        self.library_summary = QLabel("", library_card)
        self.library_summary.setObjectName("mutedText")
        self.library_summary.setWordWrap(True)
        library_card.layout.addWidget(self.library_summary)

        self.library_table = QTableWidget(0, 2, library_card)
        self.library_table.setHorizontalHeaderLabels(["Title", "Artist"])
        self.library_table.setAlternatingRowColors(True)
        self.library_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.library_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.library_table.setItemDelegate(_CompactLibraryRenameDelegate(self.library_table))
        self.library_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.library_table.horizontalHeader().setStretchLastSection(True)
        self.library_table.verticalHeader().setDefaultSectionSize(36)
        self.library_table.verticalHeader().setMinimumSectionSize(32)
        self.library_table.horizontalHeader().sectionResized.connect(self._store_library_layout)
        self.library_table.itemSelectionChanged.connect(self._update_library_selection_actions)
        self.library_table.itemChanged.connect(self._store_library_item_edit)
        library_card.layout.addWidget(self.library_table, 1)

        library_actions = QHBoxLayout()
        queue_button = QPushButton("Queue Selected", library_card)
        queue_button.clicked.connect(self._queue_selected_track)
        play_button = QPushButton("Play Selected", library_card)
        play_button.clicked.connect(self._play_selected_track)
        self.add_playlist_track_button = QPushButton("Add Track From Library", library_card)
        self.add_playlist_track_button.setObjectName("primaryButton")
        self.add_playlist_track_button.clicked.connect(self._add_selected_tracks_to_playlist)
        library_actions.addWidget(queue_button)
        library_actions.addWidget(play_button)
        library_actions.addWidget(self.add_playlist_track_button)
        library_actions.addStretch(1)
        library_card.layout.addLayout(library_actions)

        queue_card = PanelCard("Queue", tab)
        self.queue_summary = QLabel("Queue is empty.", queue_card)
        self.queue_summary.setObjectName("mutedText")
        self.queue_summary.setWordWrap(True)
        queue_card.layout.addWidget(self.queue_summary)
        self.queue_list = QListWidget(queue_card)
        self.queue_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue_list.itemSelectionChanged.connect(self._update_queue_actions)
        queue_card.layout.addWidget(self.queue_list, 1)

        queue_primary_actions = QHBoxLayout()
        self.queue_move_up_button = QPushButton("Move Up", queue_card)
        self.queue_move_up_button.clicked.connect(lambda: self._move_selected_queue_tracks(-1))
        self.queue_move_down_button = QPushButton("Move Down", queue_card)
        self.queue_move_down_button.clicked.connect(lambda: self._move_selected_queue_tracks(1))
        self.queue_remove_button = QPushButton("Remove Selected", queue_card)
        self.queue_remove_button.clicked.connect(self._remove_selected_queue_tracks)
        queue_primary_actions.addWidget(self.queue_move_up_button)
        queue_primary_actions.addWidget(self.queue_move_down_button)
        queue_primary_actions.addWidget(self.queue_remove_button)
        queue_card.layout.addLayout(queue_primary_actions)

        queue_secondary_actions = QHBoxLayout()
        self.queue_shuffle_button = QPushButton("Shuffle Queue", queue_card)
        self.queue_shuffle_button.clicked.connect(self._shuffle_queue)
        self.save_queue_button = QPushButton("Save Queue As Playlist", queue_card)
        self.save_queue_button.clicked.connect(self._save_queue_as_playlist)
        self.clear_queue_button = QPushButton("Clear Queue", queue_card)
        self.clear_queue_button.clicked.connect(self._music_service.clear_queue)
        queue_secondary_actions.addWidget(self.queue_shuffle_button)
        queue_secondary_actions.addWidget(self.save_queue_button)
        queue_secondary_actions.addWidget(self.clear_queue_button)
        queue_card.layout.addLayout(queue_secondary_actions)

        content_row.addWidget(library_card, 2)
        content_row.addWidget(queue_card, 1)
        layout.addLayout(content_row, 1)
        return tab

    def _build_playlists_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_playlist_card(), 1)
        return tab

    def _render_playlists(self) -> None:
        selected_name = self._settings.selected_playlist_name.strip().lower()
        blocker = QSignalBlocker(self.playlist_list)
        self.playlist_list.clear()
        for playlist in self._settings.playlists:
            item = QListWidgetItem(playlist.name)
            item.setData(Qt.ItemDataRole.UserRole, playlist.name)
            self.playlist_list.addItem(item)
            if playlist.name.strip().lower() == selected_name:
                self.playlist_list.setCurrentItem(item)
        del blocker

    def _restore_playlist_state(self) -> None:
        selected_name = self._settings.selected_playlist_name.strip()
        if selected_name and self._settings.playlist_by_name(selected_name) is not None:
            self._load_playlist(selected_name)
            return
        if self._settings.playlists:
            self._load_playlist(self._settings.playlists[0].name)
            return
        self._new_playlist()

    def _handle_playlist_selection(self) -> None:
        item = self.playlist_list.currentItem()
        if item is None:
            return
        self._load_playlist(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))

    def _load_playlist(self, name: str) -> None:
        playlist = self._settings.playlist_by_name(name)
        if playlist is None:
            return
        self._settings.selected_playlist_name = playlist.name
        self.playlist_name.setText(playlist.name)
        self._playlist_track_ids = list(playlist.track_ids)
        self._render_playlists()
        self._render_playlist_tracks()
        self.settings_changed.emit()

    def _render_playlist_tracks(self) -> None:
        self.playlist_tracks.clear()
        track_lookup = {track.id: track for track in self._all_library_tracks}
        missing = 0
        for track_id in self._playlist_track_ids:
            track = track_lookup.get(track_id)
            if track is None:
                missing += 1
                label = f"Missing track ({track_id})"
            else:
                label = self._track_display_label(track)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, track_id)
            self.playlist_tracks.addItem(item)
        if self._playlist_track_ids:
            summary = f"{len(self._playlist_track_ids)} tracks in the current playlist."
            if missing:
                summary += f" {missing} track(s) are currently missing from the library."
            self.playlist_status.setText(summary)
        else:
            self.playlist_status.setText("Start a playlist, then add tracks from the library.")
        self._update_playlist_actions()

    def _new_playlist(self) -> None:
        blocker = QSignalBlocker(self.playlist_list)
        self.playlist_list.clearSelection()
        del blocker
        self._settings.selected_playlist_name = ""
        self.playlist_name.clear()
        self._playlist_track_ids = []
        self._render_playlist_tracks()
        self.settings_changed.emit()

    def _update_library_selection_actions(self) -> None:
        count = len(self._selected_tracks())
        buttons = [
            button
            for button in (
                getattr(self, "add_playlist_track_button", None),
                getattr(self, "playlist_add_button", None),
            )
            if button is not None
        ]
        if count == 1:
            for button in buttons:
                button.setText("Add Track From Library")
            return
        if count > 1:
            for button in buttons:
                button.setText("Add Tracks From Library")
            return
        for button in buttons:
            button.setText("Add Track From Library")

    def _selected_queue_rows(self) -> list[int]:
        return sorted({index.row() for index in self.queue_list.selectedIndexes()})

    def _select_queue_rows(self, rows: list[int]) -> None:
        self.queue_list.clearSelection()
        for row in rows:
            item = self.queue_list.item(row)
            if item is not None:
                item.setSelected(True)

    def _update_queue_actions(self) -> None:
        selected = self._selected_queue_rows()
        has_selection = bool(selected)
        can_move_up = has_selection and selected[0] > 0
        can_move_down = has_selection and selected[-1] < len(self._queue_tracks) - 1
        self.queue_move_up_button.setEnabled(can_move_up)
        self.queue_move_down_button.setEnabled(can_move_down)
        self.queue_remove_button.setEnabled(has_selection)
        self.queue_shuffle_button.setEnabled(len(self._queue_tracks) > 1)
        self.save_queue_button.setEnabled(bool(self._queue_tracks))
        self.clear_queue_button.setEnabled(bool(self._queue_tracks))

    def _remove_selected_queue_tracks(self) -> None:
        rows = self._selected_queue_rows()
        if not rows:
            self._set_message("Select at least one queued track first.")
            return
        self._music_service.remove_queue_indices(rows)

    def _move_selected_queue_tracks(self, direction: int) -> None:
        rows = self._selected_queue_rows()
        if not rows:
            self._set_message("Select at least one queued track first.")
            return
        new_rows = [
            row + direction
            if (
                (direction < 0 and row > 0 and row - 1 not in rows)
                or (direction > 0 and row < len(self._queue_tracks) - 1 and row + 1 not in rows)
            )
            else row
            for row in rows
        ]
        if self._music_service.move_queue_indices(rows, direction):
            self._select_queue_rows(new_rows)
            return
        edge = "top" if direction < 0 else "bottom"
        self._set_message(f"Selected queue track(s) are already at the {edge}.")

    def _shuffle_queue(self) -> None:
        if self._music_service.shuffle_queue():
            return
        self._set_message("Queue needs at least two tracks before it can be shuffled.")

    def _save_playlist(self) -> None:
        name = self.playlist_name.text().strip()
        if not name:
            self._set_message("Enter a playlist name before saving it.")
            return
        previous_name = self._settings.selected_playlist_name.strip()
        if previous_name and previous_name.lower() != name.lower():
            self._settings.remove_playlist(previous_name)
        self._settings.upsert_playlist(MusicPlaylist(name=name, track_ids=list(self._playlist_track_ids)))
        self._settings.selected_playlist_name = name
        self._render_playlists()
        self._render_playlist_tracks()
        self.settings_changed.emit()
        self._set_message(f"Saved playlist '{name}'.")

    def _store_library_item_edit(self, item: QTableWidgetItem) -> None:
        row = item.row()
        column = item.column()
        if column not in {0, 1} or row < 0 or row >= len(self._library_tracks):
            return

        track = self._library_tracks[row]
        if column == 0:
            updated = item.text().strip() or self._fallback_track_title(track)
            if updated != item.text():
                blocker = QSignalBlocker(self.library_table)
                item.setText(updated)
                del blocker
            if track.title == updated:
                return
            track.title = updated
        else:
            updated = item.text().strip()
            if track.artist == updated:
                return
            track.artist = updated

        self._apply_library_filter()
        self._render_queue(self._queue_tracks)
        self._render_playlist_tracks()
        self._music_service.refresh_playback_state()
        self.settings_changed.emit()

    def _delete_playlist(self) -> None:
        target_name = self._settings.selected_playlist_name.strip() or self.playlist_name.text().strip()
        if not target_name:
            self._set_message("Select a playlist before deleting it.")
            return
        if not self._settings.remove_playlist(target_name):
            self._set_message(f"Could not find playlist '{target_name}'.")
            return
        self._new_playlist()
        self._render_playlists()
        self.settings_changed.emit()
        self._set_message(f"Deleted playlist '{target_name}'.")

    def _add_selected_tracks_to_playlist(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            self._set_message("Select at least one library track first.")
            return
        self._playlist_track_ids.extend(track.id for track in tracks)
        self._render_playlist_tracks()
        self.settings_changed.emit()
        if len(tracks) == 1:
            self._set_message(f"Added {tracks[0].title} to the current playlist.")
            return
        self._set_message(f"Added {len(tracks)} tracks to the current playlist.")

    def _selected_playlist_rows(self) -> list[int]:
        return sorted({index.row() for index in self.playlist_tracks.selectedIndexes()})

    def _select_playlist_rows(self, rows: list[int]) -> None:
        self.playlist_tracks.clearSelection()
        for row in rows:
            item = self.playlist_tracks.item(row)
            if item is not None:
                item.setSelected(True)

    def _update_playlist_actions(self) -> None:
        selected = self._selected_playlist_rows()
        has_selection = bool(selected)
        can_move_up = has_selection and selected[0] > 0
        can_move_down = has_selection and selected[-1] < len(self._playlist_track_ids) - 1
        self.playlist_move_up_button.setEnabled(can_move_up)
        self.playlist_move_down_button.setEnabled(can_move_down)
        self.remove_playlist_track_button.setEnabled(has_selection)
        has_tracks = bool(self._playlist_track_ids)
        self.clear_playlist_button.setEnabled(has_tracks)
        self.queue_playlist_button.setEnabled(has_tracks)
        self.play_playlist_button.setEnabled(has_tracks)

    def _remove_selected_playlist_tracks(self) -> None:
        rows = self._selected_playlist_rows()
        if not rows:
            self._set_message("Select at least one playlist track first.")
            return
        removed_count = len(rows)
        for row in reversed(rows):
            del self._playlist_track_ids[row]
        self._render_playlist_tracks()
        self.settings_changed.emit()
        if removed_count == 1:
            self._set_message("Removed 1 track from the current playlist.")
            return
        self._set_message(f"Removed {removed_count} tracks from the current playlist.")

    def _move_selected_playlist_tracks(self, direction: int) -> None:
        rows = self._selected_playlist_rows()
        if not rows:
            self._set_message("Select at least one playlist track first.")
            return

        moved = False
        if direction < 0:
            for row in rows:
                if row <= 0 or row - 1 in rows:
                    continue
                self._playlist_track_ids[row - 1], self._playlist_track_ids[row] = (
                    self._playlist_track_ids[row],
                    self._playlist_track_ids[row - 1],
                )
                moved = True
        else:
            for row in reversed(rows):
                if row >= len(self._playlist_track_ids) - 1 or row + 1 in rows:
                    continue
                self._playlist_track_ids[row], self._playlist_track_ids[row + 1] = (
                    self._playlist_track_ids[row + 1],
                    self._playlist_track_ids[row],
                )
                moved = True

        if not moved:
            edge = "top" if direction < 0 else "bottom"
            self._set_message(f"Selected playlist track(s) are already at the {edge}.")
            return

        new_rows = [
            row + direction
            if (
                (direction < 0 and row > 0 and row - 1 not in rows)
                or (direction > 0 and row < len(self._playlist_track_ids) - 1 and row + 1 not in rows)
            )
            else row
            for row in rows
        ]
        self._render_playlist_tracks()
        self._select_playlist_rows(new_rows)
        self.settings_changed.emit()
        direction_label = "up" if direction < 0 else "down"
        self._set_message(f"Moved {len(rows)} playlist track(s) {direction_label}.")

    def _clear_playlist_tracks(self) -> None:
        self._playlist_track_ids = []
        self._render_playlist_tracks()
        self.settings_changed.emit()
        self._set_message("Cleared the current playlist draft.")

    def _resolve_playlist_tracks(self) -> tuple[list[TrackRecord], int]:
        track_lookup = {track.id: track for track in self._all_library_tracks}
        resolved: list[TrackRecord] = []
        missing = 0
        for track_id in self._playlist_track_ids:
            track = track_lookup.get(track_id)
            if track is None:
                missing += 1
                continue
            resolved.append(track)
        return resolved, missing

    def _queue_playlist(self) -> None:
        tracks, missing = self._resolve_playlist_tracks()
        if not tracks:
            self._set_message("Add tracks to the playlist before queueing it.")
            return
        self._music_service.queue_tracks(tracks)
        suffix = f" Skipped {missing} missing track(s)." if missing else ""
        self._set_message(f"Added {len(tracks)} playlist track(s) to the queue.{suffix}")

    def _save_queue_as_playlist(self) -> None:
        if not self._queue_tracks:
            self._set_message("Queue at least one track before saving it as a playlist.")
            return
        default_name = (
            self.playlist_name.text().strip()
            or self._settings.selected_playlist_name.strip()
            or "Queue Snapshot"
        )
        name, accepted = QInputDialog.getText(
            self,
            "Save Queue As Playlist",
            "Playlist name:",
            text=default_name,
        )
        if not accepted:
            return
        cleaned_name = name.strip()
        if not cleaned_name:
            self._set_message("Enter a playlist name before saving it.")
            return
        replaced = self._settings.playlist_by_name(cleaned_name) is not None
        self._settings.upsert_playlist(
            MusicPlaylist(
                name=cleaned_name,
                track_ids=[track.id for track in self._queue_tracks],
            )
        )
        self._load_playlist(cleaned_name)
        self.music_tabs.setCurrentIndex(1)
        if replaced:
            self._set_message(f"Updated playlist '{cleaned_name}' from the current queue.")
            return
        self._set_message(f"Saved current queue as playlist '{cleaned_name}'.")

    def _play_playlist(self) -> None:
        tracks, missing = self._resolve_playlist_tracks()
        if not tracks:
            self._set_message("Add tracks to the playlist before playing it.")
            return
        self._music_service.play_tracks(tracks)
        suffix = f" Skipped {missing} missing track(s)." if missing else ""
        self._set_message(f"Started playlist playback with {len(tracks)} track(s).{suffix}")


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
        self.music_service.set_transition(
            self._settings.transition_mode,
            self._settings.transition_duration_ms,
        )
        self.music_service.set_repeat_mode(self._settings.repeat_mode)
        self.music_service.set_playback_order(self._settings.playback_order)
        self.music_service.set_output_device(self._settings.output_device_id)
        self.music_service.set_overlay_idle_message(self._settings.overlay_idle_message)
        self.music_service.set_show_artist(self._settings.show_artist)
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
            self._settings.music_library = self.music_service.load_library(
                self._settings.library_directories,
                existing_tracks=self._settings.music_library,
            )
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
