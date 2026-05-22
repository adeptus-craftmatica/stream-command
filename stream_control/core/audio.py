from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtMultimedia import QAudioDevice, QMediaDevices


SYSTEM_DEFAULT_AUDIO_OUTPUT_ID = ""


@dataclass(frozen=True, slots=True)
class AudioOutputOption:
    device_id: str
    label: str


def list_audio_output_options() -> list[AudioOutputOption]:
    options = [AudioOutputOption(SYSTEM_DEFAULT_AUDIO_OUTPUT_ID, "System Default")]
    for device in QMediaDevices.audioOutputs():
        device_id = bytes(device.id()).decode(errors="ignore")
        options.append(AudioOutputOption(device_id, device.description()))
    return options


def resolve_audio_output(device_id: str) -> QAudioDevice:
    normalized = device_id.strip()
    if not normalized:
        return QMediaDevices.defaultAudioOutput()
    for device in QMediaDevices.audioOutputs():
        candidate_id = bytes(device.id()).decode(errors="ignore")
        if candidate_id == normalized:
            return device
    return QMediaDevices.defaultAudioOutput()
