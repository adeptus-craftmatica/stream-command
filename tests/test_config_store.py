import json

from stream_control.core.config import ConfigStore
from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths


def test_config_store_save_and_load_round_trip(tmp_path) -> None:
    paths = AppPaths(root=tmp_path, config_file=tmp_path / "config.json")
    store = ConfigStore(paths)
    config = AppConfig()
    config.set_plugin_settings(
        "music",
        {
            "music_volume": 75,
            "library_directories": ["/Users/test/Music"],
        },
    )

    store.save(config)

    raw = json.loads(paths.config_file.read_text(encoding="utf-8"))
    loaded = store.load()

    assert raw["plugins"]["music"]["music_volume"] == 75
    assert loaded.plugin_settings("music")["music_volume"] == 75


def test_config_store_load_quarantines_invalid_json(tmp_path) -> None:
    paths = AppPaths(root=tmp_path, config_file=tmp_path / "config.json")
    store = ConfigStore(paths)
    paths.config_file.write_text("{ definitely not valid json", encoding="utf-8")

    loaded = store.load()
    quarantined = list(tmp_path.glob("config.corrupt-*.json"))

    assert loaded == AppConfig()
    assert not paths.config_file.exists()
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{ definitely not valid json"


def test_app_config_from_dict_ignores_malformed_plugin_sections() -> None:
    rebuilt = AppConfig.from_dict(
        {
            "plugins": {
                "music": "broken",
                "chat": {"max_messages": 100},
            },
            "obs": "invalid",
            "library_directories": "invalid",
        }
    )

    assert rebuilt.plugin_settings("music") == {}
    assert rebuilt.plugin_settings("chat") == {"max_messages": 100}
    assert rebuilt.plugin_settings("integrations") == {}
