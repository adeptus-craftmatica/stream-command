from stream_control.core.models import (
    AppConfig,
    OverlaySettings,
    TrackRecord,
    build_soundboard_bank,
    default_soundboard_bank,
)


def test_overlay_url_matches_host_and_port() -> None:
    settings = OverlaySettings(host="0.0.0.0", port=9000)
    assert settings.now_playing_url == "http://0.0.0.0:9000/overlay/now-playing"


def test_app_config_round_trip_preserves_plugin_payload() -> None:
    config = AppConfig()
    config.set_plugin_settings(
        "music",
        {
            "music_library": [
                {
                    "id": "track-1",
                    "path": "C:/music/test.mp3",
                    "title": "Test Track",
                    "artist": "Artist",
                },
            ],
            "music_volume": 75,
        },
    )

    rebuilt = AppConfig.from_dict(config.to_dict())

    assert rebuilt.plugin_settings("music")["music_volume"] == 75


def test_app_config_migrates_legacy_sections_into_plugins() -> None:
    rebuilt = AppConfig.from_dict(
        {
            "obs": {"host": "localhost", "port": 4455},
            "streamlabs": {"host": "localhost", "port": 59650, "token": "abc"},
            "music_volume": 42,
            "hotkeys": [{"action_id": "music.play_pause", "label": "Play", "combo": "x", "enabled": True}],
        }
    )

    assert rebuilt.plugin_settings("integrations")["obs"]["port"] == 4455
    assert rebuilt.plugin_settings("integrations")["streamlabs"]["token"] == "abc"
    assert rebuilt.plugin_settings("music")["music_volume"] == 42
    assert rebuilt.plugin_settings("hotkeys")["bindings"][0]["action_id"] == "music.play_pause"


def test_default_soundboard_bank_keeps_single_main_bank_shape() -> None:
    bank = default_soundboard_bank()

    assert bank.id == "main"
    assert bank.name == "Main Bank"
    assert len(bank.pads) == 9
    assert bank.pads[0].hotkey_action_id == "soundboard.pad_1"


def test_build_soundboard_bank_creates_bank_scoped_pad_ids() -> None:
    bank = build_soundboard_bank(name="BRB", bank_id="brb")

    assert bank.name == "BRB"
    assert len(bank.pads) == 9
    assert bank.pads[0].id == "brb_pad_1"
    assert bank.pads[0].hotkey_action_id == "soundboard.brb.pad_1"
