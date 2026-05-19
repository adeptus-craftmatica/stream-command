from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject

from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def register(self, key: str, service: Any) -> None:
        self._services[key] = service

    def get(self, key: str, default: Any = None) -> Any:
        return self._services.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self._services:
            raise KeyError(f"Service '{key}' has not been registered.")
        return self._services[key]


@dataclass(slots=True)
class PluginContext:
    app_config: AppConfig
    app_paths: AppPaths
    qt_parent: QObject
    save_callback: Callable[[], None]
    services: ServiceRegistry = field(default_factory=ServiceRegistry)
    plugins: dict[str, Any] = field(default_factory=dict)

    def plugin_settings(self, plugin_id: str) -> dict[str, Any]:
        return self.app_config.plugin_settings(plugin_id)

    def save_plugin_settings(self, plugin_id: str, settings: dict[str, Any]) -> None:
        self.app_config.set_plugin_settings(plugin_id, settings)
        self.save_callback()

    def register_service(self, key: str, service: Any) -> None:
        self.services.register(key, service)

    def get_service(self, key: str, default: Any = None) -> Any:
        return self.services.get(key, default)

    def require_service(self, key: str) -> Any:
        return self.services.require(key)

    def register_plugin(self, plugin_id: str, plugin: Any) -> None:
        self.plugins[plugin_id] = plugin

    def get_plugin(self, plugin_id: str) -> Any | None:
        return self.plugins.get(plugin_id)

    def require_plugin(self, plugin_id: str) -> Any:
        if plugin_id not in self.plugins:
            raise KeyError(f"Plugin '{plugin_id}' has not been registered.")
        return self.plugins[plugin_id]

    def schedule(self, coro: Any) -> asyncio.Task[Any]:
        loop = asyncio.get_event_loop()
        return loop.create_task(coro)
