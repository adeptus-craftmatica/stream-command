from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

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
