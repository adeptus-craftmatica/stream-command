from __future__ import annotations

from stream_control.plugins.base import AppPlugin
from stream_control.plugins.broadcast.plugin import BroadcastPlugin
from stream_control.plugins.chat.plugin import ChatPlugin
from stream_control.plugins.dashboard.plugin import DashboardPlugin
from stream_control.plugins.hotkeys.plugin import HotkeysPlugin
from stream_control.plugins.integrations.plugin import IntegrationsPlugin
from stream_control.plugins.music.plugin import MusicPlugin
from stream_control.plugins.obs_production.plugin import ObsProductionPlugin
from stream_control.plugins.setup.plugin import SetupPlugin
from stream_control.plugins.soundboard.plugin import SoundboardPlugin


def build_builtin_plugins() -> list[AppPlugin]:
    return [
        IntegrationsPlugin(),
        ObsProductionPlugin(),
        BroadcastPlugin(),
        ChatPlugin(),
        MusicPlugin(),
        SoundboardPlugin(),
        HotkeysPlugin(),
        SetupPlugin(),
        DashboardPlugin(),
    ]
