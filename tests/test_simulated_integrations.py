import asyncio
from pathlib import Path

from PySide6.QtWidgets import QApplication

from stream_control.services.obs_service import ObsService
from stream_control.services.streamlabs_service import StreamlabsService


def test_obs_service_simulator_provides_scenes() -> None:
    app = QApplication.instance() or QApplication([])
    service = ObsService()
    payloads: list[dict[str, object]] = []
    statuses: list[tuple[bool, str]] = []
    service.scenes_changed.connect(payloads.append)
    service.connection_changed.connect(lambda connected, message: statuses.append((connected, message)))

    asyncio.run(service.connect_simulated())
    asyncio.run(service.set_current_scene("Live"))

    assert payloads[-1]["current"] == "Live"
    assert any(status[0] and "simulator" in status[1].lower() for status in statuses)
    assert app is not None


def test_obs_service_simulator_supports_stream_status() -> None:
    app = QApplication.instance() or QApplication([])
    service = ObsService()
    payloads: list[dict[str, object]] = []
    service.stream_status_changed.connect(payloads.append)

    asyncio.run(service.connect_simulated())
    asyncio.run(service.start_streaming())
    asyncio.run(service.stop_streaming())

    assert payloads[0]["status"] == "Offline"
    assert any(payload["is_live"] for payload in payloads)
    assert payloads[-1]["is_live"] is False
    assert app is not None


def test_obs_service_simulator_supports_production_workflows() -> None:
    app = QApplication.instance() or QApplication([])
    service = ObsService()

    asyncio.run(service.connect_simulated())
    asyncio.run(service.set_preview_scene("Be Right Back"))
    asyncio.run(service.set_current_transition("Swipe"))
    asyncio.run(service.set_transition_duration(450))
    asyncio.run(service.set_scene_transition_override("Be Right Back", "Swipe", 450))
    asyncio.run(service.trigger_studio_transition())

    production = asyncio.run(service.refresh_production_state())
    override = asyncio.run(service.refresh_scene_transition_override("Be Right Back"))

    assert production["program_scene"] == "Be Right Back"
    assert production["current_transition"] == "Swipe"
    assert production["transition_duration"] == 450
    assert override["has_override"] is True
    assert override["transition_name"] == "Swipe"
    assert override["duration"] == 450
    assert app is not None


def test_obs_service_simulator_supports_sources_audio_and_tools(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    service = ObsService()

    asyncio.run(service.connect_simulated())
    sources = asyncio.run(service.refresh_source_items("Live"))
    first_item = sources["items"][0]
    asyncio.run(service.set_source_enabled("Live", int(first_item["id"]), False))
    sources = asyncio.run(service.refresh_source_items("Live"))

    audio = asyncio.run(service.refresh_audio_inputs())
    music_input = next(item for item in audio["inputs"] if item["name"] == "Music Bus")
    original_level = float(music_input["volume_db"])
    asyncio.run(service.duck_audio_input("Music Bus", -22.0))
    ducked_audio = asyncio.run(service.refresh_audio_inputs())
    ducked_music = next(item for item in ducked_audio["inputs"] if item["name"] == "Music Bus")
    asyncio.run(service.fade_audio_input("Music Bus", -30.0, 120))
    faded_audio = asyncio.run(service.refresh_audio_inputs())
    faded_music = next(item for item in faded_audio["inputs"] if item["name"] == "Music Bus")
    asyncio.run(service.restore_audio_input("Music Bus"))
    restored_audio = asyncio.run(service.refresh_audio_inputs())
    restored_music = next(item for item in restored_audio["inputs"] if item["name"] == "Music Bus")

    asyncio.run(service.start_replay_buffer())
    replay_state = asyncio.run(service.refresh_production_state())
    replay_save = asyncio.run(service.save_source_snapshot("Live", tmp_path, 640, 360))
    marker = asyncio.run(service.create_clip_marker("Round Win"))
    asyncio.run(service.set_current_scene_collection("Podcast"))
    asyncio.run(service.set_current_profile("Travel"))
    updated_state = asyncio.run(service.refresh_production_state())

    assert any(item["id"] == first_item["id"] and item["enabled"] is False for item in sources["items"])
    assert ducked_music["volume_db"] == -22.0
    assert faded_music["volume_db"] == -30.0
    assert restored_music["volume_db"] == original_level
    assert replay_state["replay_buffer_active"] is True
    assert replay_save["ok"] is True
    assert Path(str(replay_save["path"])).exists()
    assert marker["ok"] is True
    assert updated_state["current_scene_collection"] == "Podcast"
    assert updated_state["current_profile"] == "Travel"
    assert app is not None


def test_streamlabs_service_simulator_provides_scenes() -> None:
    app = QApplication.instance() or QApplication([])
    service = StreamlabsService()
    payloads: list[dict[str, object]] = []
    statuses: list[tuple[bool, str]] = []
    service.scenes_changed.connect(payloads.append)
    service.connection_changed.connect(lambda connected, message: statuses.append((connected, message)))

    asyncio.run(service.connect_simulated())
    asyncio.run(service.set_active_scene("slobs-live"))

    assert payloads[-1]["current"] == "slobs-live"
    assert any(status[0] and "simulator" in status[1].lower() for status in statuses)
    assert app is not None


def test_streamlabs_service_simulator_supports_stream_status() -> None:
    app = QApplication.instance() or QApplication([])
    service = StreamlabsService()
    payloads: list[dict[str, object]] = []
    service.stream_status_changed.connect(payloads.append)

    asyncio.run(service.connect_simulated())
    asyncio.run(service.start_streaming())
    asyncio.run(service.stop_streaming())

    assert payloads[0]["status"] == "Offline"
    assert any(payload["is_live"] for payload in payloads)
    assert payloads[-1]["is_live"] is False
    assert app is not None
