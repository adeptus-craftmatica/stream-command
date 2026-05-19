from __future__ import annotations

from stream_control.plugins.base import AppPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext


class PluginHost:
    def __init__(self, context: PluginContext, plugins: list[AppPlugin]) -> None:
        self.context = context
        self._plugins = {plugin.plugin_id: plugin for plugin in plugins}

    def activate_plugins(self) -> None:
        for plugin in sorted(self._plugins.values(), key=lambda item: item.load_order):
            self.context.register_plugin(plugin.plugin_id, plugin)
            plugin.activate(self.context)
        for plugin in sorted(self._plugins.values(), key=lambda item: item.load_order):
            plugin.on_plugins_loaded(self)

    def navigation_pages(self) -> list[PluginPage]:
        pages = [plugin.page() for plugin in self._plugins.values()]
        visible_pages = [page for page in pages if page is not None]
        return sorted(visible_pages, key=lambda page: page.nav_order)

    def plugin(self, plugin_id: str) -> AppPlugin:
        return self._plugins[plugin_id]

    def plugins(self) -> list[AppPlugin]:
        return list(self._plugins.values())

    def collect_hotkey_actions(self) -> list[HotkeyAction]:
        actions: list[HotkeyAction] = []
        for plugin in sorted(self._plugins.values(), key=lambda item: item.load_order):
            actions.extend(plugin.hotkey_actions())
        return actions

    def shutdown(self) -> None:
        for plugin in reversed(sorted(self._plugins.values(), key=lambda item: item.load_order)):
            plugin.shutdown()
