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
    monkeypatch.setattr(HotkeyService, "runtime_hotkeys_supported", staticmethod(lambda: True))
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


def test_hotkey_service_can_suspend_and_resume_bindings(monkeypatch) -> None:
    events: list[str] = []

    class FakeGlobalHotKeys:
        def __init__(self, mapping):
            self.mapping = mapping

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    service = HotkeyService()
    service.set_action_handler("music.play_pause", lambda: None)

    monkeypatch.setattr(hotkey_service_module, "GlobalHotKeys", FakeGlobalHotKeys)
    monkeypatch.setattr(HotkeyService, "runtime_hotkeys_supported", staticmethod(lambda: True))
    monkeypatch.setattr(hotkey_service_module, "macos_hotkey_permissions", lambda: None)

    bindings = [
        HotkeyBinding(
            action_id="music.play_pause",
            label="Play or pause music",
            combo="<ctrl>+<alt>+p",
            enabled=True,
        )
    ]

    service.apply_bindings(bindings)
    service.suspend("text_entry")
    service.resume("text_entry")

    assert events == ["start", "stop", "start"]
    assert service.last_status == "Registered 1 global hotkey(s)."


def test_hotkey_service_foreground_suspend_message_takes_priority() -> None:
    service = HotkeyService()
    service.runtime_hotkeys_supported = lambda: True  # type: ignore[method-assign]

    service.suspend("text_entry")
    service.suspend("foreground_app")

    assert service.last_status == "Global hotkeys are paused while Stream Control is the active app on macOS."


def test_hotkey_service_disables_runtime_on_macos(monkeypatch) -> None:
    service = HotkeyService()
    service.set_action_handler("music.play_pause", lambda: None)
    attempted = False

    def fake_global_hotkeys(_mapping):
        nonlocal attempted
        attempted = True
        raise AssertionError("macOS runtime hotkeys should stay disabled.")

    monkeypatch.setattr(hotkey_service_module, "GlobalHotKeys", fake_global_hotkeys)
    monkeypatch.setattr(HotkeyService, "runtime_hotkeys_supported", staticmethod(lambda: False))

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
    assert "temporarily disabled on macOS" in service.last_status
