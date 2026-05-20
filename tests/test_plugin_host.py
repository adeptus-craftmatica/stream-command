import asyncio

from PySide6.QtWidgets import QApplication, QWidget

from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths
from stream_control.plugins.base import AppPlugin, FailedPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.plugins.host import PluginHost


class _FakePlugin(AppPlugin):
    def __init__(self, plugin_id: str, nav_order: int, load_order: int, shutdown_log: list[str] | None = None) -> None:
        self.plugin_id = plugin_id
        self.display_name = plugin_id.title()
        self.nav_order = nav_order
        self.load_order = load_order
        self.activated = False
        self.loaded = False
        self.widget = QWidget()
        self.shutdown_calls: list[str] = []
        self._shutdown_log = shutdown_log

    def activate(self, context: PluginContext) -> None:
        self.activated = True
        context.register_service(f"{self.plugin_id}.service", self.plugin_id)

    def page(self) -> PluginPage | None:
        return PluginPage(self.plugin_id, self.display_name, self.widget, self.nav_order)

    def hotkey_actions(self) -> list[HotkeyAction]:
        return [HotkeyAction(f"{self.plugin_id}.action", self.display_name, lambda: None)]

    def on_plugins_loaded(self, host: PluginHost) -> None:
        self.loaded = True

    def shutdown(self) -> None:
        self.shutdown_calls.append(f"sync:{self.plugin_id}")
        if self._shutdown_log is not None:
            self._shutdown_log.append(f"sync:{self.plugin_id}")


class _AsyncShutdownPlugin(_FakePlugin):
    async def shutdown_async(self) -> None:
        await asyncio.sleep(0)
        self.shutdown_calls.append(f"async:{self.plugin_id}")
        if self._shutdown_log is not None:
            self._shutdown_log.append(f"async:{self.plugin_id}")


class _FailingActivationPlugin(_FakePlugin):
    def activate(self, context: PluginContext) -> None:
        context.register_service(f"{self.plugin_id}.service", self.plugin_id)
        raise RuntimeError("activation blew up")


class _FailingStartupPlugin(_FakePlugin):
    def on_plugins_loaded(self, host: PluginHost) -> None:
        host.context.register_service(f"{self.plugin_id}.late_service", "late")
        raise RuntimeError("startup blew up")


def test_plugin_host_orders_navigation_by_nav_order() -> None:
    app = QApplication.instance() or QApplication([])
    context = PluginContext(
        app_config=AppConfig(),
        app_paths=AppPaths.build(),
        qt_parent=QWidget(),
        save_callback=lambda: None,
    )
    plugin_a = _FakePlugin("alpha", nav_order=20, load_order=10)
    plugin_b = _FakePlugin("beta", nav_order=10, load_order=20)

    host = PluginHost(context, [plugin_a, plugin_b])
    host.activate_plugins()

    pages = host.navigation_pages()
    assert [page.plugin_id for page in pages] == ["beta", "alpha"]
    assert plugin_a.activated and plugin_b.activated
    assert plugin_a.loaded and plugin_b.loaded
    assert app is not None


def test_plugin_host_awaits_async_shutdown_in_reverse_load_order() -> None:
    app = QApplication.instance() or QApplication([])
    shutdown_log: list[str] = []
    context = PluginContext(
        app_config=AppConfig(),
        app_paths=AppPaths.build(),
        qt_parent=QWidget(),
        save_callback=lambda: None,
    )
    plugin_a = _AsyncShutdownPlugin("alpha", nav_order=20, load_order=10, shutdown_log=shutdown_log)
    plugin_b = _AsyncShutdownPlugin("beta", nav_order=10, load_order=20, shutdown_log=shutdown_log)

    host = PluginHost(context, [plugin_a, plugin_b])
    host.activate_plugins()

    asyncio.run(host.shutdown_async())

    assert shutdown_log == ["async:beta", "async:alpha"]
    assert plugin_a.shutdown_calls == ["async:alpha"]
    assert plugin_b.shutdown_calls == ["async:beta"]
    assert app is not None


def test_plugin_host_replaces_activation_failures_with_failed_plugin_and_rolls_back_services() -> None:
    app = QApplication.instance() or QApplication([])
    context = PluginContext(
        app_config=AppConfig(),
        app_paths=AppPaths.build(),
        qt_parent=QWidget(),
        save_callback=lambda: None,
    )
    broken = _FailingActivationPlugin("broken", nav_order=10, load_order=10)
    healthy = _FakePlugin("healthy", nav_order=20, load_order=20)

    host = PluginHost(context, [broken, healthy])
    host.activate_plugins()

    failed_plugin = host.plugin("broken")
    assert isinstance(failed_plugin, FailedPlugin)
    assert context.get_service("broken.service") is None
    assert healthy.activated and healthy.loaded
    assert app is not None


def test_plugin_host_replaces_startup_failures_with_failed_plugin_and_removes_plugin_services() -> None:
    app = QApplication.instance() or QApplication([])
    context = PluginContext(
        app_config=AppConfig(),
        app_paths=AppPaths.build(),
        qt_parent=QWidget(),
        save_callback=lambda: None,
    )
    broken = _FailingStartupPlugin("broken", nav_order=10, load_order=10)
    healthy = _FakePlugin("healthy", nav_order=20, load_order=20)

    host = PluginHost(context, [broken, healthy])
    host.activate_plugins()

    failed_plugin = host.plugin("broken")
    assert isinstance(failed_plugin, FailedPlugin)
    assert context.get_service("broken.service") is None
    assert context.get_service("broken.late_service") is None
    assert healthy.activated and healthy.loaded
    assert app is not None
