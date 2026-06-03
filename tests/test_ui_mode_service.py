from PySide6.QtWidgets import QApplication

from stream_control.core.models import TrackRecord
from stream_control.plugins.dashboard.plugin import DashboardPage
from stream_control.plugins.music.plugin import MusicPage, MusicPluginConfig
from stream_control.services.music_service import MusicService
from stream_control.services.ui_mode_service import UiModeService
from stream_control.ui.theme import build_app_stylesheet


def test_ui_mode_service_emits_only_on_change() -> None:
    app = QApplication.instance() or QApplication([])
    service = UiModeService()
    changes: list[bool] = []
    service.tablet_mode_changed.connect(changes.append)

    service.set_tablet_mode(True)
    service.set_tablet_mode(True)
    service.set_tablet_mode(False)

    assert changes == [True, False]
    assert app is not None


def test_build_app_stylesheet_supports_tablet_mode() -> None:
    base = build_app_stylesheet(tablet_mode=False)
    tablet = build_app_stylesheet(tablet_mode=True)

    assert "font-size: 13px;" in base
    assert "font-size: 15px;" in tablet
    assert "min-height: 52px;" in tablet


def test_music_page_tablet_mode_reorients_major_layouts() -> None:
    app = QApplication.instance() or QApplication([])
    tracks = [TrackRecord(id="track-1", path="/tmp/one.mp3", title="One", artist="Artist")]
    page = MusicPage(MusicPluginConfig(music_library=tracks), MusicService())

    page.set_tablet_mode(True)
    assert page.top_row.direction() == page.top_row.Direction.TopToBottom
    assert page.library_queue_row.direction() == page.library_queue_row.Direction.TopToBottom
    assert page.playlist_lists_row.direction() == page.playlist_lists_row.Direction.TopToBottom
    assert page.music_tabs.tabBar().expanding() is True

    page.set_tablet_mode(False)
    assert page.top_row.direction() == page.top_row.Direction.LeftToRight
    assert page.library_queue_row.direction() == page.library_queue_row.Direction.LeftToRight
    assert page.playlist_lists_row.direction() == page.playlist_lists_row.Direction.LeftToRight
    assert page.music_tabs.tabBar().expanding() is False
    assert app is not None


def test_dashboard_page_tablet_mode_stacks_metrics_and_actions() -> None:
    app = QApplication.instance() or QApplication([])
    page = DashboardPage("http://127.0.0.1:18181/overlay/now-playing", None)

    page.set_tablet_mode(True)
    assert page.metrics_layout.itemAtPosition(0, 0).widget() is page.obs_metric
    assert page.metrics_layout.itemAtPosition(3, 0).widget() is page.hotkey_metric
    assert page.actions_layout.direction() == page.actions_layout.Direction.TopToBottom

    page.set_tablet_mode(False)
    assert page.metrics_layout.itemAtPosition(0, 1).widget() is page.streamlabs_metric
    assert page.metrics_layout.itemAtPosition(1, 1).widget() is page.hotkey_metric
    assert page.actions_layout.direction() == page.actions_layout.Direction.LeftToRight
    assert app is not None
