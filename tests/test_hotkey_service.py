from PySide6.QtWidgets import QApplication

from stream_control.core.platform import MacOSHotkeyPermissions
from stream_control.core.models import HotkeyBinding
from stream_control.services import hotkey_service as hotkey_service_module
from stream_control.services.hotkey_service import HotkeyService


def test_hotkey_service_reports_duplicates_and_unresolved_actions() -> None:
    app = QApplication.instance() or QApplication([])
    service = HotkeyService()
    service.set_action_handler("music.play_pause", lambda: None)

    report = service.build_report(
        [
            HotkeyBinding(
                action_id="music.play_pause",
                label="Play or pause music",
                combo="<ctrl>+<alt>+p",
                enabled=True,
            ),
            HotkeyBinding(
                action_id="music.next_track",
                label="Next track",
                combo="<ctrl>+<alt>+p",
                enabled=True,
            ),
            HotkeyBinding(
                action_id="missing.action",
                label="Missing action",
                combo="<ctrl>+<alt>+m",
                enabled=True,
            ),
        ]
    )

    assert report.registered_count == 1
    assert report.duplicate_combos == []
    assert report.unresolved_actions == ["Next track", "Missing action"]
    assert list(report.mapping) == ["<ctrl>+<alt>+p"]
    assert app is not None


def test_hotkey_service_duplicate_shortcuts_are_skipped_when_both_actions_exist() -> None:
    service = HotkeyService()
    service.set_action_handler("music.play_pause", lambda: None)
    service.set_action_handler("music.next_track", lambda: None)

    report = service.build_report(
        [
            HotkeyBinding("music.play_pause", "Play or pause music", "<ctrl>+<alt>+p", True),
            HotkeyBinding("music.next_track", "Next track", "<ctrl>+<alt>+p", True),
        ]
    )

    assert report.registered_count == 1
    assert report.duplicate_combos == ["<ctrl>+<alt>+p"]
    assert report.unresolved_actions == []


def test_hotkey_service_blocks_registration_when_macos_permissions_are_missing(monkeypatch) -> None:
    service = HotkeyService()
    service.set_action_handler("music.play_pause", lambda: None)
    attempted = False

    def fake_global_hotkeys(_mapping):
        nonlocal attempted
        attempted = True
        raise AssertionError("The listener should not start without permissions.")

    monkeypatch.setattr(hotkey_service_module, "GlobalHotKeys", fake_global_hotkeys)
    monkeypatch.setattr(
        hotkey_service_module,
        "macos_hotkey_permissions",
        lambda: MacOSHotkeyPermissions(accessibility=False, input_monitoring=False),
    )

    service.apply_bindings(
        [
            HotkeyBinding(
                action_id="music.play_pause",
                label="Play or pause music",
                combo="<ctrl>+<alt>+p",
                enabled=True,
            )
        ]
    )

    assert attempted is False
    assert "Accessibility and Input Monitoring" in service.last_status
