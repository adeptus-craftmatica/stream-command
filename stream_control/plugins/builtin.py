from __future__ import annotations

import importlib
from dataclasses import dataclass

from stream_control.plugins.base import AppPlugin, FailedPlugin


@dataclass(frozen=True, slots=True)
class BuiltinPluginSpec:
    plugin_id: str
    display_name: str
    module_path: str
    class_name: str
    nav_order: int
    load_order: int


BUILTIN_PLUGIN_SPECS = [
    BuiltinPluginSpec("integrations", "Integrations", "stream_control.plugins.integrations.plugin", "IntegrationsPlugin", 10, 10),
    BuiltinPluginSpec("obs_production", "OBS Production", "stream_control.plugins.obs_production.plugin", "ObsProductionPlugin", 12, 12),
    BuiltinPluginSpec("broadcast", "Broadcast", "stream_control.plugins.broadcast.plugin", "BroadcastPlugin", 15, 15),
    BuiltinPluginSpec("chat", "Chat", "stream_control.plugins.chat.plugin", "ChatPlugin", 18, 18),
    BuiltinPluginSpec("music", "Music", "stream_control.plugins.music.plugin", "MusicPlugin", 20, 20),
    BuiltinPluginSpec("soundboard", "Soundboard", "stream_control.plugins.soundboard.plugin", "SoundboardPlugin", 30, 30),
    BuiltinPluginSpec("hotkeys", "Hotkeys", "stream_control.plugins.hotkeys.plugin", "HotkeysPlugin", 40, 40),
    BuiltinPluginSpec("setup", "Setup", "stream_control.plugins.setup.plugin", "SetupPlugin", 5, 95),
    BuiltinPluginSpec("dashboard", "Dashboard", "stream_control.plugins.dashboard.plugin", "DashboardPlugin", 0, 100),
]


def build_builtin_plugins() -> list[AppPlugin]:
    plugins: list[AppPlugin] = []
    for spec in BUILTIN_PLUGIN_SPECS:
        try:
            module = importlib.import_module(spec.module_path)
            plugin_class = getattr(module, spec.class_name)
            plugins.append(plugin_class())
        except Exception as exc:
            plugins.append(
                FailedPlugin.from_exception(
                    plugin_id=spec.plugin_id,
                    display_name=spec.display_name,
                    nav_order=spec.nav_order,
                    load_order=spec.load_order,
                    phase="import",
                    error=exc,
                )
            )
    return plugins
