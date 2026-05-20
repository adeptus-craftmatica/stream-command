from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


def _new_id() -> str:
    return uuid4().hex


def _dict_or_default(value: Any, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    return {} if default is None else deepcopy(default)


def _list_or_default(value: Any, default: list[Any] | None = None) -> list[Any]:
    if isinstance(value, list):
        return deepcopy(value)
    return [] if default is None else deepcopy(default)


@dataclass(slots=True)
class TrackRecord:
    id: str = field(default_factory=_new_id)
    path: str = ""
    title: str = ""
    artist: str = ""

    @classmethod
    def from_path(cls, path: Path) -> "TrackRecord":
        title = path.stem.replace("_", " ").replace("-", " ").strip()
        return cls(path=str(path), title=title or path.stem, artist="Local Library")


@dataclass(slots=True)
class SoundboardPad:
    id: str
    label: str
    file_path: str = ""
    hotkey_action_id: str = ""


@dataclass(slots=True)
class SoundboardBank:
    id: str
    name: str
    pads: list[SoundboardPad] = field(default_factory=list)


@dataclass(slots=True)
class HotkeyBinding:
    action_id: str
    label: str
    combo: str
    enabled: bool = True


@dataclass(slots=True)
class ObsSettings:
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""
    auto_connect: bool = False


@dataclass(slots=True)
class StreamlabsSettings:
    host: str = "127.0.0.1"
    port: int = 59650
    token: str = ""
    auto_connect: bool = False


@dataclass(slots=True)
class OverlaySettings:
    host: str = "127.0.0.1"
    port: int = 18181
    enabled: bool = True

    @property
    def now_playing_url(self) -> str:
        return f"http://{self.host}:{self.port}/overlay/now-playing"


def default_soundboard_pads() -> list[SoundboardPad]:
    return [
        SoundboardPad(id=f"pad_{index}", label=f"Pad {index}", hotkey_action_id=f"soundboard.pad_{index}")
        for index in range(1, 10)
    ]


def default_soundboard_bank() -> SoundboardBank:
    return SoundboardBank(id="main", name="Main Bank", pads=default_soundboard_pads())


def build_soundboard_bank(name: str, bank_id: str | None = None) -> SoundboardBank:
    resolved_bank_id = (bank_id or _new_id()).strip() or _new_id()
    pads = [
        SoundboardPad(
            id=f"{resolved_bank_id}_pad_{index}",
            label=f"Pad {index}",
            hotkey_action_id=f"soundboard.{resolved_bank_id}.pad_{index}",
        )
        for index in range(1, 10)
    ]
    return SoundboardBank(id=resolved_bank_id, name=name.strip() or "New Bank", pads=pads)


def default_hotkeys() -> list[HotkeyBinding]:
    bindings = [
        HotkeyBinding(action_id="music.play_pause", label="Play or pause music", combo="<ctrl>+<alt>+p"),
        HotkeyBinding(action_id="music.next_track", label="Next track", combo="<ctrl>+<alt>+n"),
    ]
    for pad in default_soundboard_pads():
        bindings.append(
            HotkeyBinding(
                action_id=pad.hotkey_action_id,
                label=f"Trigger {pad.label}",
                combo=f"<ctrl>+<alt>+{pad.id[-1]}",
                enabled=False,
            )
        )
    return bindings


@dataclass(slots=True)
class AppConfig:
    plugins: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"plugins": deepcopy(self.plugins)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        if not isinstance(raw, dict):
            return cls()

        plugins_raw = raw.get("plugins", {})
        plugins = {
            str(plugin_id): deepcopy(settings)
            for plugin_id, settings in plugins_raw.items()
            if isinstance(plugin_id, str) and isinstance(settings, dict)
        } if isinstance(plugins_raw, dict) else {}
        legacy_integrations = {
            "obs": _dict_or_default(raw.get("obs")),
            "streamlabs": _dict_or_default(raw.get("streamlabs")),
        }
        legacy_music = {
            "overlay": _dict_or_default(raw.get("overlay")),
            "library_directories": [str(item) for item in _list_or_default(raw.get("library_directories"))],
            "music_library": _list_or_default(raw.get("music_library")),
            "music_volume": int(raw.get("music_volume", 75)),
        }
        legacy_soundboard = {
            "pads": _list_or_default(raw.get("soundboard_pads")),
            "volume": int(raw.get("soundboard_volume", 85)),
        }
        legacy_hotkeys = {
            "bindings": _list_or_default(raw.get("hotkeys")),
        }

        if "integrations" not in plugins and (
            legacy_integrations["obs"] or legacy_integrations["streamlabs"]
        ):
            plugins["integrations"] = legacy_integrations

        if "music" not in plugins and (
            legacy_music["overlay"]
            or legacy_music["library_directories"]
            or legacy_music["music_library"]
            or "music_volume" in raw
        ):
            plugins["music"] = legacy_music

        if "soundboard" not in plugins and (
            legacy_soundboard["pads"] or "soundboard_volume" in raw
        ):
            plugins["soundboard"] = legacy_soundboard

        if "hotkeys" not in plugins and legacy_hotkeys["bindings"]:
            plugins["hotkeys"] = legacy_hotkeys

        return cls(plugins=plugins)

    def plugin_settings(self, plugin_id: str) -> dict[str, Any]:
        return deepcopy(self.plugins.get(plugin_id, {}))

    def set_plugin_settings(self, plugin_id: str, settings: dict[str, Any]) -> None:
        self.plugins[plugin_id] = deepcopy(settings)
