from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from stream_control.core.models import TrackRecord

SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".wav",
    ".wma",
}


class MusicService(QObject):
    library_changed = Signal(object)
    queue_changed = Signal(object)
    playback_changed = Signal(object)
    status_message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._audio_output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.playbackStateChanged.connect(lambda *_: self._emit_playback_state())
        self._player.errorOccurred.connect(lambda *_: self._emit_error())

        self._library: list[TrackRecord] = []
        self._queue: list[TrackRecord] = []
        self._current_track: TrackRecord | None = None
        self._overlay_idle_message = "No Music Playing"
        self.set_volume(75)

    def set_volume(self, volume: int) -> None:
        volume = max(0, min(volume, 100))
        self._audio_output.setVolume(volume / 100)
        self._emit_playback_state()

    def load_library(self, directories: list[str]) -> list[TrackRecord]:
        seen: set[str] = set()
        tracks: list[TrackRecord] = []

        for directory in directories:
            root = Path(directory)
            if not root.exists():
                continue
            for file_path in sorted(root.rglob("*")):
                if file_path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                    continue
                resolved = str(file_path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                tracks.append(TrackRecord.from_path(file_path))

        self._library = tracks
        self.library_changed.emit(list(self._library))
        self.status_message.emit(f"Loaded {len(tracks)} tracks from {len(directories)} folders.")
        return list(self._library)

    def library(self) -> list[TrackRecord]:
        return list(self._library)

    def queue(self) -> list[TrackRecord]:
        return list(self._queue)

    def queue_track(self, track: TrackRecord) -> None:
        self._queue.append(track)
        self.queue_changed.emit(list(self._queue))
        self._emit_playback_state()

    def clear_queue(self) -> None:
        self._queue.clear()
        self.queue_changed.emit(list(self._queue))
        self._emit_playback_state()

    def set_overlay_idle_message(self, message: str) -> None:
        cleaned = message.strip()
        self._overlay_idle_message = cleaned or "No Music Playing"
        self._emit_playback_state()

    def play_track(self, track: TrackRecord) -> None:
        if not track.path:
            self.status_message.emit("Track is missing a file path.")
            return

        path = Path(track.path)
        if not path.exists():
            self.status_message.emit(f"Track file no longer exists: {path}")
            return

        self._current_track = track
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()
        self._emit_playback_state()

    def play_next(self) -> None:
        if self._queue:
            next_track = self._queue.pop(0)
            self.queue_changed.emit(list(self._queue))
            self.play_track(next_track)
            return

        self.stop_playback()

    def toggle_play_pause(self) -> None:
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        elif self._current_track:
            self._player.play()
        elif self._queue:
            self.play_next()
        elif self._library:
            self.play_track(self._library[0])
        else:
            self.status_message.emit("Load some music before starting playback.")

    def stop_playback(self) -> None:
        self._player.stop()
        self._current_track = None
        self.status_message.emit("Music stopped.")
        self._emit_playback_state()

    def overlay_state(self) -> dict[str, object]:
        status = self._status_label()
        track = self._current_track
        if track is None or status == "Stopped":
            return {
                "is_playing": False,
                "status": status,
                "title": self._overlay_idle_message,
                "artist": "",
                "queue_length": len(self._queue),
            }
        return {
            "is_playing": status == "Playing",
            "status": status,
            "title": track.title,
            "artist": track.artist,
            "queue_length": len(self._queue),
        }

    def current_track(self) -> TrackRecord | None:
        return self._current_track

    def _emit_error(self) -> None:
        self.status_message.emit(self._player.errorString() or "Audio playback failed.")
        self._emit_playback_state()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next()
            return
        self._emit_playback_state()

    def _status_label(self) -> str:
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            return "Playing"
        if state == QMediaPlayer.PlaybackState.PausedState:
            return "Paused"
        return "Stopped"

    def _emit_playback_state(self) -> None:
        track = self._current_track
        self.playback_changed.emit(
            {
                "status": self._status_label(),
                "current_track": track,
                "queue_length": len(self._queue),
                "volume": round(self._audio_output.volume() * 100),
            }
        )
