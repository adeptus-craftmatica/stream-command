from types import ModuleType

from stream_control.plugins.base import AppPlugin, FailedPlugin
from stream_control.plugins.builtin import BuiltinPluginSpec, build_builtin_plugins


class _DummyPlugin(AppPlugin):
    plugin_id = "dummy"
    display_name = "Dummy"
    nav_order = 1
    load_order = 1

    def activate(self, context) -> None:
        return


def test_build_builtin_plugins_substitutes_failed_plugin_when_import_fails(monkeypatch) -> None:
    specs = [
        BuiltinPluginSpec("dummy", "Dummy", "dummy.module", "DummyPlugin", 1, 1),
        BuiltinPluginSpec("broken", "Broken", "broken.module", "BrokenPlugin", 2, 2),
    ]
    monkeypatch.setattr("stream_control.plugins.builtin.BUILTIN_PLUGIN_SPECS", specs)

    dummy_module = ModuleType("dummy.module")
    dummy_module.DummyPlugin = _DummyPlugin

    def fake_import(module_path: str):
        if module_path == "dummy.module":
            return dummy_module
        raise ModuleNotFoundError("missing dependency")

    monkeypatch.setattr("stream_control.plugins.builtin.importlib.import_module", fake_import)

    plugins = build_builtin_plugins()

    assert len(plugins) == 2
    assert isinstance(plugins[0], _DummyPlugin)
    assert isinstance(plugins[1], FailedPlugin)
    assert plugins[1].plugin_id == "broken"
