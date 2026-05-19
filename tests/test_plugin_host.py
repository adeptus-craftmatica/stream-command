from PySide6.QtWidgets import QApplication, QWidget

from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths
from stream_control.plugins.base import AppPlugin, HotkeyAction, PluginPage
from stream_control.plugins.context import PluginContext
from stream_control.plugins.host import PluginHost


class _FakePlugin(AppPlugin):
    def __init__(self, plugin_id: str, nav_order: int, load_order: int) -> None:
        self.plugin_id = plugin_id
        self.display_name = plugin_id.title()
        self.nav_order = nav_order
        self.load_order = load_order
        self.activated = False
        self.loaded = False
        self.widget = QWidget()

    def activate(self, context: PluginContext) -> None:
        self.activated = True
        context.register_service(f"{self.plugin_id}.service", self.plugin_id)

    def page(self) -> PluginPage | None:
        return PluginPage(self.plugin_id, self.display_name, self.widget, self.nav_order)

    def hotkey_actions(self) -> list[HotkeyAction]:
        return [HotkeyAction(f"{self.plugin_id}.action", self.display_name, lambda: None)]

    def on_plugins_loaded(self, host: PluginHost) -> None:
        self.loaded = True


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
