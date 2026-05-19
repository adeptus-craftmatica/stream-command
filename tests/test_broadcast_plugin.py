from stream_control.plugins.broadcast.plugin import (
    BroadcastChecklistItem,
    BroadcastPluginConfig,
    BroadcastPreset,
)


def test_broadcast_config_manages_presets_and_checklist() -> None:
    config = BroadcastPluginConfig(
        presets=[],
        checklist=[
            BroadcastChecklistItem("Mic checked", checked=False),
            BroadcastChecklistItem("Scene ready", checked=True),
        ],
    )

    config.upsert_preset(
        BroadcastPreset(
            name="Podcast",
            title="Episode 24",
            category_id="509658",
            category_name="Just Chatting",
            apply_info_before_live=True,
        )
    )
    config.upsert_preset(
        BroadcastPreset(
            name="podcast",
            title="Episode 25",
            category_id="509660",
            category_name="Art",
            apply_info_before_live=False,
        )
    )

    assert len(config.presets) == 1
    assert config.preset_by_name("Podcast").title == "Episode 25"  # type: ignore[union-attr]
    assert config.selected_preset_name == "podcast"
    assert config.incomplete_checklist_labels() == ["Mic checked"]

    config.reset_checklist()
    assert config.incomplete_checklist_labels() == ["Mic checked", "Scene ready"]
    assert config.remove_preset("Podcast") is True
    assert config.presets == []
