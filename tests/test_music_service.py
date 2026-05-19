from PySide6.QtWidgets import QApplication

from stream_control.core.models import TrackRecord
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
