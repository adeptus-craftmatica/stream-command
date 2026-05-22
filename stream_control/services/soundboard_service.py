from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from stream_control.core.audio import SYSTEM_DEFAULT_AUDIO_OUTPUT_ID, resolve_audio_output
from stream_control.core.models import SoundboardPad


class SoundboardService(QObject):
    pads_changed = Signal(object)
    status_message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pads: list[SoundboardPad] = []
        self._volume = 85
        self._output_device_id = SYSTEM_DEFAULT_AUDIO_OUTPUT_ID
        self._active_players: list[tuple[QMediaPlayer, QAudioOutput]] = []

    def set_pads(self, pads: list[SoundboardPad]) -> None:
        self._pads = list(pads)
        self.pads_changed.emit(list(self._pads))

    def pads(self) -> list[SoundboardPad]:
        return list(self._pads)

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(volume, 100))

    def set_output_device(self, device_id: str) -> None:
        self._output_device_id = device_id.strip()
        device = resolve_audio_output(self._output_device_id)
        for _player, audio_output in self._active_players:
            audio_output.setDevice(device)

    def assign_clip(self, pad_id: str, file_path: str) -> None:
        for pad in self._pads:
            if pad.id != pad_id:
                continue
            pad.file_path = file_path
            if not pad.label or pad.label.startswith("Pad "):
                pad.label = Path(file_path).stem.replace("_", " ").replace("-", " ")
            self.pads_changed.emit(list(self._pads))
            self.status_message.emit(f"Assigned clip to {pad.label}.")
            return

    def clear_clip(self, pad_id: str) -> None:
        for pad in self._pads:
            if pad.id != pad_id:
                continue
            pad.file_path = ""
            self.pads_changed.emit(list(self._pads))
            self.status_message.emit(f"Cleared {pad.label}.")
            return

    def trigger_pad(self, pad_id: str) -> None:
        pad = next((candidate for candidate in self._pads if candidate.id == pad_id), None)
        if pad is None:
            self.status_message.emit("Unknown soundboard pad.")
            return
        if not pad.file_path:
            self.status_message.emit(f"{pad.label} does not have a clip assigned.")
            return

        clip_path = Path(pad.file_path)
        if not clip_path.exists():
            self.status_message.emit(f"Clip file no longer exists: {clip_path}")
            return

        player = QMediaPlayer(self)
        audio_output = QAudioOutput(self)
        audio_output.setDevice(resolve_audio_output(self._output_device_id))
        audio_output.setVolume(self._volume / 100)
        player.setAudioOutput(audio_output)
        player.setSource(QUrl.fromLocalFile(str(clip_path)))
        self._active_players.append((player, audio_output))

        def cleanup(*_args: object) -> None:
            pair = (player, audio_output)
            if pair in self._active_players:
                self._active_players.remove(pair)
            player.deleteLater()
            audio_output.deleteLater()

        player.mediaStatusChanged.connect(
            lambda status: cleanup()
            if status in {
                QMediaPlayer.MediaStatus.EndOfMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
                QMediaPlayer.MediaStatus.NoMedia,
            }
            else None
        )
        player.errorOccurred.connect(lambda *_: cleanup())
        player.play()
        self.status_message.emit(f"Triggered {pad.label}.")
