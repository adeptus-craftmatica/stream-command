from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication

from stream_control.core.audio import AudioOutputOption
from stream_control.core.models import TrackRecord
from stream_control.plugins.music.plugin import MusicPage, MusicPlaylist, MusicPluginConfig
from stream_control.plugins.music import plugin as music_plugin_module
from stream_control.services import music_service as music_service_module
from stream_control.services.music_service import MusicService


def test_music_service_overlay_uses_custom_idle_message_when_stopped() -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    service.set_overlay_idle_message("No Music Playing")
    service._current_track = TrackRecord(path="demo.mp3", title="Test Track", artist="Tester")

    service.stop_playback()
    payload = service.overlay_state()

    assert payload["status"] == "Stopped"
    assert payload["title"] == "No Music Playing"
    assert payload["artist"] == ""
    assert payload["is_playing"] is False
    assert app is not None


def test_music_service_can_hide_artist_from_overlay_and_playback_payload() -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    payloads: list[dict[str, object]] = []
    service.playback_changed.connect(payloads.append)
    service._current_track = TrackRecord(id="demo", path="demo.mp3", title="Test Track", artist="Tester")

    service.set_show_artist(False)
    overlay = service.overlay_state()

    assert overlay["artist"] == ""
    assert payloads[-1]["display_artist"] == ""
    assert payloads[-1]["show_artist"] is False
    assert app is not None


def test_music_service_exposes_timing_metadata_in_payload_and_overlay(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    payloads: list[dict[str, object]] = []
    track = TrackRecord(id="demo", path="demo.mp3", title="Test Track", artist="Tester")
    service.playback_changed.connect(payloads.append)
    service._current_track = track
    monkeypatch.setattr(service, "_position_ms", lambda: 43_000)
    monkeypatch.setattr(service, "_duration_ms", lambda: 207_000)
    monkeypatch.setattr(service, "_status_label", lambda: "Playing")

    service._emit_playback_state()
    overlay = service.overlay_state()

    assert payloads[-1]["elapsed_label"] == "0:43"
    assert payloads[-1]["duration_label"] == "3:27"
    assert payloads[-1]["progress_percent"] == 20.77
    assert overlay["elapsed_label"] == "0:43"
    assert overlay["duration_label"] == "3:27"
    assert overlay["progress_percent"] == 20.77
    assert app is not None


def test_music_service_queues_multiple_tracks_and_reports_transition_settings() -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    payloads: list[dict[str, object]] = []
    service.playback_changed.connect(payloads.append)
    tracks = [
        TrackRecord(id="one", path="/tmp/one.mp3", title="One", artist="Artist"),
        TrackRecord(id="two", path="/tmp/two.mp3", title="Two", artist="Artist"),
    ]

    service.set_transition("fade_out_in", 1400)
    service.queue_tracks(tracks)

    assert [track.id for track in service.queue()] == ["one", "two"]
    assert payloads[-1]["transition_mode"] == "fade_out_in"
    assert payloads[-1]["transition_duration_ms"] == 1400
    assert app is not None


def test_music_service_load_library_reuses_existing_track_ids(tmp_path) -> None:
    track_path = tmp_path / "demo-track.mp3"
    track_path.write_bytes(b"demo")
    original = TrackRecord(id="stable-track-id", path=str(track_path), title="Demo Track", artist="Local Library")
    service = MusicService()

    loaded = service.load_library([str(tmp_path)], existing_tracks=[original])

    assert len(loaded) == 1
    assert loaded[0].id == "stable-track-id"
    assert loaded[0].path == str(track_path)


def test_music_service_can_play_random_track_from_loaded_library(monkeypatch, tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first_track = tmp_path / "first.mp3"
    first_track.write_bytes(b"one")
    second_track = tmp_path / "second.mp3"
    second_track.write_bytes(b"two")
    service = MusicService()
    captured: dict[str, object] = {}

    loaded = service.load_library([str(tmp_path)])
    monkeypatch.setattr(music_service_module.random, "shuffle", lambda tracks: tracks.reverse())
    monkeypatch.setattr(
        service,
        "_play_track_with_transition",
        lambda track, allow_fade_out: captured.update(track=track, allow_fade_out=allow_fade_out),
    )

    service.play_random_track()

    assert len(loaded) == 2
    assert captured["track"].path == str(second_track)
    assert captured["allow_fade_out"] is True
    assert service._playback_context[0].path == str(second_track)
    assert service._playback_context[1].path == str(first_track)
    assert app is not None


def test_music_service_shuffle_mode_advances_in_shuffled_order(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    tracks = [
        TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist"),
        TrackRecord(id="track-2", path="/tmp/two.mp3", title="Two", artist="Artist"),
        TrackRecord(id="track-3", path="/tmp/three.mp3", title="Three", artist="Artist"),
    ]
    service._library = list(tracks)
    service.set_playback_order("shuffle")
    monkeypatch.setattr(music_service_module.random, "shuffle", lambda tracks: tracks.reverse())
    captured: dict[str, object] = {}

    service.play_track(tracks[0])
    monkeypatch.setattr(
        service,
        "_play_track_with_transition",
        lambda track, allow_fade_out: captured.update(track=track, allow_fade_out=allow_fade_out),
    )

    service.play_next()

    assert [track.id for track in service._playback_context] == ["track-1", "track-3", "track-2"]
    assert captured["track"] == tracks[2]
    assert captured["allow_fade_out"] is True
    assert app is not None


def test_music_service_auto_advances_to_next_library_track(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    tracks = [
        TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist"),
        TrackRecord(id="track-2", path="/tmp/two.mp3", title="Two", artist="Artist"),
    ]
    service._library = list(tracks)
    service._current_track = tracks[0]
    service._set_context_for_track(tracks[0])
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        service,
        "_play_track_with_transition",
        lambda track, allow_fade_out: captured.update(track=track, allow_fade_out=allow_fade_out),
    )

    service._advance_to_next_track(auto_advance=True)

    assert captured["track"] == tracks[1]
    assert captured["allow_fade_out"] is False
    assert app is not None


def test_music_service_repeat_one_replays_current_track(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    track = TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist")
    service._current_track = track
    service._set_playback_context([track], 0, source="single")
    service.set_repeat_mode("one")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        service,
        "_play_track_with_transition",
        lambda replay, allow_fade_out: captured.update(track=replay, allow_fade_out=allow_fade_out),
    )

    service._advance_to_next_track(auto_advance=True)

    assert captured["track"] == track
    assert captured["allow_fade_out"] is False
    assert app is not None


def test_music_service_repeat_all_wraps_to_start_of_context(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    service = MusicService()
    tracks = [
        TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist"),
        TrackRecord(id="track-2", path="/tmp/two.mp3", title="Two", artist="Artist"),
    ]
    service._library = list(tracks)
    service._current_track = tracks[1]
    service._set_context_for_track(tracks[1])
    service.set_repeat_mode("all")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        service,
        "_play_track_with_transition",
        lambda track, allow_fade_out: captured.update(track=track, allow_fade_out=allow_fade_out),
    )

    service._advance_to_next_track(auto_advance=True)

    assert captured["track"] == tracks[0]
    assert captured["allow_fade_out"] is False
    assert app is not None


def test_music_plugin_config_round_trip_preserves_playlists_and_transitions() -> None:
    config = MusicPluginConfig(
        music_library=[TrackRecord(id="track-1", path="demo.mp3", title="Demo", artist="Artist")],
        playlists=[MusicPlaylist(name="Warmup", track_ids=["track-1"])],
        selected_playlist_name="Warmup",
        transition_mode="fade_in",
        transition_duration_ms=1600,
        repeat_mode="all",
        playback_order="shuffle",
        output_device_id="BlackHole2ch",
        show_artist=False,
    )

    rebuilt = MusicPluginConfig.from_dict(config.to_dict())

    assert rebuilt.playlists[0].name == "Warmup"
    assert rebuilt.playlists[0].track_ids == ["track-1"]
    assert rebuilt.selected_playlist_name == "Warmup"
    assert rebuilt.transition_mode == "fade_in"
    assert rebuilt.transition_duration_ms == 1600
    assert rebuilt.repeat_mode == "all"
    assert rebuilt.playback_order == "shuffle"
    assert rebuilt.output_device_id == "BlackHole2ch"
    assert rebuilt.show_artist is False


def test_music_page_can_queue_selected_playlist(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    tracks = [
        TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist"),
        TrackRecord(id="track-2", path="/tmp/two.mp3", title="Two", artist="Artist"),
    ]
    settings = MusicPluginConfig(
        music_library=tracks,
        playlists=[MusicPlaylist(name="Warmup", track_ids=["track-1", "track-2"])],
        selected_playlist_name="Warmup",
    )
    service = MusicService()
    captured: dict[str, list[TrackRecord]] = {}

    monkeypatch.setattr(service, "queue_tracks", lambda queued: captured.setdefault("tracks", list(queued)))

    page = MusicPage(settings, service)
    page._queue_playlist()

    assert [track.id for track in captured["tracks"]] == ["track-1", "track-2"]
    assert "Added 2 playlist track(s) to the queue." in page.message_label.text()
    assert page.music_tabs.tabText(0) == "Library and Queue"
    assert page.music_tabs.tabText(1) == "Playlists"
    assert page.library_table.horizontalHeader().stretchLastSection() is True
    assert app is not None


def test_music_page_adds_multiple_selected_library_tracks_to_playlist() -> None:
    app = QApplication.instance() or QApplication([])
    tracks = [
        TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist"),
        TrackRecord(id="track-2", path="/tmp/two.mp3", title="Two", artist="Artist"),
        TrackRecord(id="track-3", path="/tmp/three.mp3", title="Three", artist="Artist"),
    ]
    settings = MusicPluginConfig(music_library=tracks)
    service = MusicService()

    page = MusicPage(settings, service)
    selection_model = page.library_table.selectionModel()
    assert selection_model is not None

    first_row = page.library_table.model().index(0, 0)
    second_row = page.library_table.model().index(1, 0)
    selection_model.select(
        first_row,
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert page.add_playlist_track_button.text() == "Add Track From Library"
    assert page.playlist_add_button.text() == "Add Track From Library"

    selection_model.select(
        second_row,
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert page.add_playlist_track_button.text() == "Add Tracks From Library"
    assert page.playlist_add_button.text() == "Add Tracks From Library"

    page._add_selected_tracks_to_playlist()

    assert page._playlist_track_ids == ["track-1", "track-2"]
    assert "Added 2 tracks to the current playlist." in page.message_label.text()
    assert app is not None


def test_music_page_can_save_queue_as_playlist(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    tracks = [
        TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist"),
        TrackRecord(id="track-2", path="/tmp/two.mp3", title="Two", artist="Artist"),
    ]
    settings = MusicPluginConfig(music_library=tracks)
    service = MusicService()
    page = MusicPage(settings, service)

    page._render_queue(tracks)
    monkeypatch.setattr(
        music_plugin_module.QInputDialog,
        "getText",
        lambda *args, **kwargs: ("Queue Capture", True),
    )

    page._save_queue_as_playlist()

    saved = settings.playlist_by_name("Queue Capture")
    assert saved is not None
    assert saved.track_ids == ["track-1", "track-2"]
    assert page._playlist_track_ids == ["track-1", "track-2"]
    assert page.music_tabs.currentIndex() == 1
    assert "Saved current queue as playlist 'Queue Capture'." in page.message_label.text()
    assert app is not None


def test_music_page_applies_selected_output_device(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    settings = MusicPluginConfig()
    service = MusicService()
    captured: list[str] = []

    monkeypatch.setattr(
        music_plugin_module,
        "list_audio_output_options",
        lambda: [
            AudioOutputOption("", "System Default"),
            AudioOutputOption("BlackHole2ch", "BlackHole 2ch"),
        ],
    )
    monkeypatch.setattr(service, "set_output_device", lambda device_id: captured.append(device_id))

    page = MusicPage(settings, service)
    page.output_device.setCurrentIndex(page.output_device.findData("BlackHole2ch"))

    assert captured[-1] == "BlackHole2ch"
    assert settings.output_device_id == "BlackHole2ch"
    assert app is not None


def test_music_page_updates_now_playing_without_artist_when_hidden() -> None:
    app = QApplication.instance() or QApplication([])
    settings = MusicPluginConfig(show_artist=False)
    service = MusicService()
    page = MusicPage(settings, service)
    track = TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist")

    page._update_playback({"current_track": track, "display_artist": "", "status": "Playing"})

    assert page.now_playing.text() == "Now playing: One (Playing)"
    assert app is not None


def test_music_page_scrub_release_seeks_player(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    settings = MusicPluginConfig()
    service = MusicService()
    requested: list[int] = []
    monkeypatch.setattr(service, "seek", lambda position_ms: requested.append(position_ms))

    page = MusicPage(settings, service)
    page.progress_slider.setEnabled(True)
    page.progress_slider.setRange(0, 240_000)
    page.progress_slider.setValue(61_000)
    page._start_scrub()
    page._apply_scrub()

    assert requested == [61_000]
    assert page._is_scrubbing is False
    assert app is not None
