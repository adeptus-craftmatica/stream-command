from __future__ import annotations

import logging
from collections.abc import Iterable

from stream_control.plugins.base import AppPlugin, FailedPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext

logger = logging.getLogger(__name__)


class PluginHost:
    def __init__(self, context: PluginContext, plugins: list[AppPlugin]) -> None:
        self.context = context
        self._plugins = {plugin.plugin_id: plugin for plugin in plugins}
        self._activation_snapshots = {}

    def activate_plugins(self) -> None:
        for plugin in sorted(list(self._plugins.values()), key=lambda item: item.load_order):
            self._activate_plugin(plugin)
        for plugin in sorted(list(self._plugins.values()), key=lambda item: item.load_order):
            self._finalize_plugin(plugin)

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
        for plugin in self._plugins_in_shutdown_order():
            plugin.shutdown()

    async def shutdown_async(self) -> None:
        for plugin in self._plugins_in_shutdown_order():
            await plugin.shutdown_async()

    def _plugins_in_shutdown_order(self) -> Iterable[AppPlugin]:
        return reversed(sorted(self._plugins.values(), key=lambda item: item.load_order))

    def _activate_plugin(self, plugin: AppPlugin) -> None:
        snapshot = self.context.snapshot_runtime_state()
        try:
            self.context.register_plugin(plugin.plugin_id, plugin)
            plugin.activate(self.context)
            self._activation_snapshots[plugin.plugin_id] = snapshot
        except Exception as exc:
            logger.exception("Plugin %s failed during activation.", plugin.plugin_id)
            self._replace_with_failed_plugin(plugin, snapshot, "activation", exc)

    def _finalize_plugin(self, plugin: AppPlugin) -> None:
        try:
            plugin.on_plugins_loaded(self)
        except Exception as exc:
            logger.exception("Plugin %s failed during startup finalization.", plugin.plugin_id)
            try:
                plugin.shutdown()
            except Exception:
                logger.exception("Plugin %s also failed while shutting down after startup error.", plugin.plugin_id)
            rollback_snapshot = self._activation_snapshots.get(
                plugin.plugin_id,
                self.context.snapshot_runtime_state(),
            )
            self._replace_with_failed_plugin(plugin, rollback_snapshot, "startup", exc)

    def _replace_with_failed_plugin(
        self,
        plugin: AppPlugin,
        snapshot,
        phase: str,
        error: Exception,
    ) -> FailedPlugin:
        self.context.restore_runtime_state(snapshot)
        self._activation_snapshots.pop(plugin.plugin_id, None)
        failed = FailedPlugin.from_exception(
            plugin_id=plugin.plugin_id,
            display_name=plugin.display_name,
            nav_order=plugin.nav_order,
            load_order=plugin.load_order,
            phase=phase,
            error=error,
        )
        self._plugins[failed.plugin_id] = failed
        self.context.register_plugin(failed.plugin_id, failed)
        failed.activate(self.context)
        return failed
