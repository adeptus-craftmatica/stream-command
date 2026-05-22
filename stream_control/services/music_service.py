from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from stream_control.core.audio import SYSTEM_DEFAULT_AUDIO_OUTPUT_ID, resolve_audio_output
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
        self._target_volume = 75
        self._transition_mode = "none"
        self._transition_duration_ms = 900
        self._repeat_mode = "off"
        self._playback_order = "ordered"
        self._output_device_id = SYSTEM_DEFAULT_AUDIO_OUTPUT_ID
        self._playback_context: list[TrackRecord] = []
        self._playback_context_index = -1
        self._playback_context_source = "single"
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._advance_fade)
        self._fade_steps = 0
        self._fade_step = 0
        self._fade_start_volume = 0.0
        self._fade_end_volume = 0.0
        self._fade_completion: Callable[[], None] | None = None
        self.set_volume(75)

    def set_volume(self, volume: int) -> None:
        volume = max(0, min(volume, 100))
        self._target_volume = volume
        if self._fade_timer.isActive() and self._fade_end_volume > self._fade_start_volume:
            self._fade_end_volume = volume / 100
        else:
            self._audio_output.setVolume(volume / 100)
        self._emit_playback_state()

    def set_transition(self, mode: str, duration_ms: int) -> None:
        normalized = mode.strip().lower()
        if normalized not in {"none", "fade_in", "fade_out_in"}:
            normalized = "none"
        self._transition_mode = normalized
        self._transition_duration_ms = max(0, min(int(duration_ms), 10_000))
        self._emit_playback_state()

    def set_repeat_mode(self, mode: str) -> None:
        normalized = mode.strip().lower()
        if normalized not in {"off", "one", "all"}:
            normalized = "off"
        self._repeat_mode = normalized
        self._emit_playback_state()

    def set_playback_order(self, order: str) -> None:
        normalized = order.strip().lower()
        if normalized not in {"ordered", "shuffle"}:
            normalized = "ordered"
        self._playback_order = normalized
        self._emit_playback_state()

    def set_output_device(self, device_id: str) -> None:
        self._output_device_id = device_id.strip()
        self._audio_output.setDevice(resolve_audio_output(self._output_device_id))
        self._emit_playback_state()

    def load_library(self, directories: list[str], existing_tracks: list[TrackRecord] | None = None) -> list[TrackRecord]:
        seen: set[str] = set()
        tracks: list[TrackRecord] = []
        existing_ids_by_path = {
            str(Path(track.path).resolve()): track.id
            for track in (existing_tracks or [])
            if track.path
        }

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
                track = TrackRecord.from_path(file_path)
                preserved_id = existing_ids_by_path.get(resolved)
                if preserved_id:
                    track.id = preserved_id
                tracks.append(track)

        self._library = tracks
        self.library_changed.emit(list(self._library))
        self.status_message.emit(f"Loaded {len(tracks)} tracks from {len(directories)} folders.")
        return list(self._library)

    def library(self) -> list[TrackRecord]:
        return list(self._library)

    def queue(self) -> list[TrackRecord]:
        return list(self._queue)

    def queue_track(self, track: TrackRecord) -> None:
        self.queue_tracks([track])

    def queue_tracks(self, tracks: list[TrackRecord]) -> None:
        added = [track for track in tracks if track.path]
        if not added:
            self.status_message.emit("Select at least one playable track first.")
            return
        self._queue.extend(added)
        self.queue_changed.emit(list(self._queue))
        if len(added) == 1:
            self.status_message.emit(f"Queued {added[0].title}.")
        else:
            self.status_message.emit(f"Queued {len(added)} tracks.")
        self._emit_playback_state()

    def clear_queue(self) -> None:
        self._queue.clear()
        if self._playback_context_source == "session":
            if self._current_track is None:
                self._playback_context = []
                self._playback_context_index = -1
            else:
                current_index = self._context_index_for_track(self._current_track)
                if current_index is not None:
                    self._playback_context = list(self._playback_context[: current_index + 1])
                    self._playback_context_index = current_index
        self.queue_changed.emit(list(self._queue))
        self._emit_playback_state()

    def set_overlay_idle_message(self, message: str) -> None:
        cleaned = message.strip()
        self._overlay_idle_message = cleaned or "No Music Playing"
        self._emit_playback_state()

    def play_track(self, track: TrackRecord) -> None:
        self._set_context_for_track(track)
        self._play_track_with_transition(track, allow_fade_out=True)

    def play_tracks(self, tracks: list[TrackRecord]) -> None:
        playable = [track for track in tracks if track.path]
        if not playable:
            self.status_message.emit("Select at least one playable track first.")
            return
        session_tracks = self._build_context_sequence(playable)
        self._set_playback_context(session_tracks, 0, source="session")
        self._queue = list(session_tracks[1:])
        self.queue_changed.emit(list(self._queue))
        self._play_track_with_transition(session_tracks[0], allow_fade_out=True)

    def play_random_track(self) -> None:
        playable = [
            track
            for track in self._library
            if track.path and Path(track.path).exists()
        ]
        if not playable:
            self.status_message.emit("Load some playable music before starting random playback.")
            return
        shuffled = self._build_context_sequence(playable, force_shuffle=True)
        self._set_playback_context(shuffled, 0, source="library")
        track = shuffled[0]
        self._play_track_with_transition(track, allow_fade_out=True)
        self.status_message.emit(f"Starting shuffle with {track.title}.")

    def _play_track_with_transition(self, track: TrackRecord, *, allow_fade_out: bool) -> None:
        if not track.path:
            self.status_message.emit("Track is missing a file path.")
            return

        path = Path(track.path)
        if not path.exists():
            self.status_message.emit(f"Track file no longer exists: {path}")
            return

        fade_in = self._transition_mode in {"fade_in", "fade_out_in"}
        if (
            allow_fade_out
            and self._transition_mode == "fade_out_in"
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and self._current_track is not None
        ):
            self._start_fade(
                self._audio_output.volume(),
                0.0,
                self._transition_duration_ms,
                lambda: self._play_track_immediately(track, fade_in=fade_in),
            )
            return
        self._play_track_immediately(track, fade_in=fade_in)

    def _play_track_immediately(self, track: TrackRecord, *, fade_in: bool) -> None:
        path = Path(track.path)
        self._stop_fade()
        self._current_track = track
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        if fade_in and self._transition_duration_ms > 0:
            self._audio_output.setVolume(0.0)
            self._player.play()
            self._start_fade(0.0, self._target_volume / 100, self._transition_duration_ms)
        else:
            self._audio_output.setVolume(self._target_volume / 100)
            self._player.play()
        self._emit_playback_state()

    def play_next(self) -> None:
        self._advance_to_next_track(auto_advance=False)

    def toggle_play_pause(self) -> None:
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_fade()
            self._player.pause()
        elif self._current_track:
            self._player.play()
        elif self._queue:
            self._advance_to_next_track(auto_advance=False)
        elif self._library:
            if self._playback_order == "shuffle":
                self.play_random_track()
            else:
                self.play_track(self._library[0])
        else:
            self.status_message.emit("Load some music before starting playback.")

    def stop_playback(self) -> None:
        if (
            self._transition_mode == "fade_out_in"
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and self._transition_duration_ms > 0
        ):
            self._start_fade(self._audio_output.volume(), 0.0, self._transition_duration_ms, self._finish_stop)
            return
        self._finish_stop()

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
        self._stop_fade()
        if self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self._current_track = None
        self.status_message.emit(self._player.errorString() or "Audio playback failed.")
        self._emit_playback_state()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._advance_to_next_track(auto_advance=True)
            return
        self._emit_playback_state()

    def _advance_to_next_track(self, *, auto_advance: bool) -> None:
        if auto_advance and self._repeat_mode == "one" and self._current_track is not None:
            self._play_track_with_transition(self._current_track, allow_fade_out=False)
            return
        if self._queue:
            if self._current_track is None and not self._playback_context:
                self._set_playback_context(list(self._queue), 0, source="session")
            next_track = self._queue.pop(0)
            self.queue_changed.emit(list(self._queue))
            self._sync_context_for_next_track(next_track)
            self._play_track_with_transition(next_track, allow_fade_out=not auto_advance)
            return
        next_track = self._resolve_next_context_track()
        if next_track is not None:
            self._play_track_with_transition(next_track, allow_fade_out=not auto_advance)
            return
        if auto_advance:
            self._player.stop()
            self._current_track = None
            self.status_message.emit("Queue finished.")
            self._emit_playback_state()
            return
        self.stop_playback()

    def _finish_stop(self) -> None:
        self._stop_fade()
        self._audio_output.setVolume(self._target_volume / 100)
        self._player.stop()
        self._current_track = None
        self.status_message.emit("Music stopped.")
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
                "target_volume": self._target_volume,
                "transition_mode": self._transition_mode,
                "transition_duration_ms": self._transition_duration_ms,
                "repeat_mode": self._repeat_mode,
                "playback_order": self._playback_order,
                "output_device_id": self._output_device_id,
            }
        )

    def _set_context_for_track(self, track: TrackRecord) -> None:
        library_index = self._track_index_in(self._library, track)
        if library_index is not None:
            ordered_tracks = self._build_context_sequence(self._library, preferred_first=track)
            self._set_playback_context(ordered_tracks, 0, source="library")
            return
        self._set_playback_context([track], 0, source="single")

    def _set_playback_context(self, tracks: list[TrackRecord], current_index: int, *, source: str) -> None:
        self._playback_context = list(tracks)
        self._playback_context_source = source
        if 0 <= current_index < len(self._playback_context):
            self._playback_context_index = current_index
            return
        self._playback_context_index = -1

    def _track_index_in(self, tracks: list[TrackRecord], target: TrackRecord | None) -> int | None:
        if target is None:
            return None
        for index, track in enumerate(tracks):
            if track.id == target.id:
                return index
        return None

    def _context_index_for_track(self, track: TrackRecord | None) -> int | None:
        if not self._playback_context:
            return None
        current_index = self._playback_context_index
        if (
            0 <= current_index < len(self._playback_context)
            and track is not None
            and self._playback_context[current_index].id == track.id
        ):
            return current_index
        return self._track_index_in(self._playback_context, track)

    def _sync_context_for_next_track(self, next_track: TrackRecord) -> None:
        found_index = self._context_index_for_track(next_track)
        if found_index is not None:
            self._playback_context_index = found_index
            return
        if not self._playback_context:
            self._set_playback_context([next_track], 0, source="single")

    def _resolve_next_context_track(self) -> TrackRecord | None:
        if not self._playback_context:
            return None
        current_index = self._context_index_for_track(self._current_track)
        if current_index is not None:
            self._playback_context_index = current_index
        if self._playback_context_index < 0:
            return None
        next_index = self._playback_context_index + 1
        if next_index < len(self._playback_context):
            self._playback_context_index = next_index
            return self._playback_context[next_index]
        if self._repeat_mode == "all" and self._playback_context:
            self._playback_context_index = 0
            return self._playback_context[0]
        return None

    def _build_context_sequence(
        self,
        tracks: list[TrackRecord],
        *,
        preferred_first: TrackRecord | None = None,
        force_shuffle: bool = False,
    ) -> list[TrackRecord]:
        ordered_tracks = [track for track in tracks if track.path]
        if len(ordered_tracks) <= 1:
            return ordered_tracks
        if not force_shuffle and self._playback_order != "shuffle":
            if preferred_first is None:
                return ordered_tracks
            preferred_index = self._track_index_in(ordered_tracks, preferred_first)
            if preferred_index is None:
                return ordered_tracks
            return ordered_tracks[preferred_index:] + ordered_tracks[:preferred_index]
        shuffled = list(ordered_tracks)
        if preferred_first is not None:
            preferred_index = self._track_index_in(shuffled, preferred_first)
            if preferred_index is not None:
                first_track = shuffled.pop(preferred_index)
                random.shuffle(shuffled)
                return [first_track, *shuffled]
        random.shuffle(shuffled)
        return shuffled

    def _start_fade(
        self,
        start_volume: float,
        end_volume: float,
        duration_ms: int,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self._stop_fade()
        if duration_ms <= 0:
            self._audio_output.setVolume(end_volume)
            if on_complete is not None:
                on_complete()
            self._emit_playback_state()
            return
        self._fade_start_volume = max(0.0, min(start_volume, 1.0))
        self._fade_end_volume = max(0.0, min(end_volume, 1.0))
        self._fade_steps = max(1, duration_ms // 45)
        self._fade_step = 0
        self._fade_completion = on_complete
        self._audio_output.setVolume(self._fade_start_volume)
        interval = max(15, duration_ms // self._fade_steps)
        self._fade_timer.start(interval)
        self._emit_playback_state()

    def _advance_fade(self) -> None:
        if self._fade_steps <= 0:
            self._stop_fade()
            return
        self._fade_step += 1
        progress = min(1.0, self._fade_step / self._fade_steps)
        next_volume = self._fade_start_volume + ((self._fade_end_volume - self._fade_start_volume) * progress)
        self._audio_output.setVolume(max(0.0, min(next_volume, 1.0)))
        if progress < 1.0:
            return
        callback = self._fade_completion
        self._stop_fade()
        if callback is not None:
            callback()
        self._emit_playback_state()

    def _stop_fade(self) -> None:
        self._fade_timer.stop()
        self._fade_steps = 0
        self._fade_step = 0
        self._fade_completion = None
