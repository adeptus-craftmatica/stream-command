import asyncio

from stream_control.core.credentials import STREAMLABS_TOKEN
from stream_control.core.platform import MacOSHotkeyPermissions
from stream_control.core.models import AppConfig
from stream_control.services.overlay_server import OverlayServerStatus
from stream_control.services import setup_diagnostics_service as diagnostics_module
from stream_control.services.setup_diagnostics_service import SetupDiagnosticsService


class _FakeOutputService:
    def __init__(self, *, connected: bool = False, simulated: bool = False) -> None:
        self.is_connected = connected
        self.is_simulated = simulated


class _FakeChatService(_FakeOutputService):
    pass


class _FakeCredentialStore:
    def __init__(self, available: set[str] | None = None) -> None:
        self._available = available or set()

    def has_secret(self, reference) -> bool:
        return reference.username in self._available


def test_setup_diagnostics_reports_safe_testing_when_simulators_are_active() -> None:
    config = AppConfig()
    config.set_plugin_settings(
        "integrations",
        {
            "obs": {"host": "127.0.0.1", "port": 4455, "password": "", "auto_connect": False},
            "streamlabs": {"host": "127.0.0.1", "port": 59650, "token": "", "auto_connect": False},
        },
    )
    config.set_plugin_settings(
        "music",
        {
            "overlay": {"host": "127.0.0.1", "port": 18181, "enabled": False},
        },
    )

    service = SetupDiagnosticsService()
    snapshot = asyncio.run(
        service.build_snapshot(
            config,
            _FakeCredentialStore(),
            _FakeOutputService(connected=True, simulated=True),  # type: ignore[arg-type]
            _FakeOutputService(connected=False, simulated=False),  # type: ignore[arg-type]
            _FakeChatService(connected=False, simulated=False),  # type: ignore[arg-type]
        )
    )

    checks = snapshot.check_map()
    assert snapshot.headline == "Ready For Safe Testing"
    assert snapshot.safe_test_active is True
    assert snapshot.can_start_safe_test is False
    assert checks["output"].status == "testing"
    assert checks["broadcast"].status == "optional"


def test_setup_diagnostics_adds_macos_permissions_check_when_needed(monkeypatch) -> None:
    config = AppConfig()
    config.set_plugin_settings(
        "integrations",
        {
            "obs": {"host": "127.0.0.1", "port": 4455, "password": "", "auto_connect": False},
            "streamlabs": {"host": "127.0.0.1", "port": 59650, "token": "", "auto_connect": False},
        },
    )
    config.set_plugin_settings(
        "music",
        {
            "overlay": {"host": "127.0.0.1", "port": 18181, "enabled": False},
        },
    )

    monkeypatch.setattr(diagnostics_module, "is_macos", lambda: True)
    monkeypatch.setattr(
        diagnostics_module,
        "macos_hotkey_permissions",
        lambda: MacOSHotkeyPermissions(accessibility=False, input_monitoring=True),
    )

    service = SetupDiagnosticsService()
    snapshot = asyncio.run(
        service.build_snapshot(
            config,
            _FakeCredentialStore(),
            _FakeOutputService(connected=False, simulated=False),  # type: ignore[arg-type]
            _FakeOutputService(connected=False, simulated=False),  # type: ignore[arg-type]
            _FakeChatService(connected=False, simulated=False),  # type: ignore[arg-type]
        )
    )

    checks = snapshot.check_map()
    assert "permissions" in checks
    assert checks["permissions"].status == "attention"
    assert "Accessibility" in checks["permissions"].summary


def test_setup_diagnostics_recognizes_secure_stored_streamlabs_token(monkeypatch) -> None:
    config = AppConfig()
    config.set_plugin_settings(
        "integrations",
        {
            "streamlabs": {"host": "127.0.0.1", "port": 59650, "auto_connect": True},
        },
    )
    config.set_plugin_settings(
        "broadcast",
        {
            "output_target": "streamlabs",
        },
    )
    config.set_plugin_settings(
        "music",
        {
            "overlay": {"host": "127.0.0.1", "port": 18181, "enabled": False},
        },
    )

    async def _offline_probe(*_args, **_kwargs):
        return False, "offline"

    async def _no_process(*_args, **_kwargs):
        return False

    monkeypatch.setattr(SetupDiagnosticsService, "_probe_endpoint", _offline_probe)
    monkeypatch.setattr(SetupDiagnosticsService, "_detect_process", _no_process)

    service = SetupDiagnosticsService()
    snapshot = asyncio.run(
        service.build_snapshot(
            config,
            _FakeCredentialStore({STREAMLABS_TOKEN.username}),
            _FakeOutputService(connected=False, simulated=False),  # type: ignore[arg-type]
            _FakeOutputService(connected=False, simulated=False),  # type: ignore[arg-type]
            _FakeChatService(connected=False, simulated=False),  # type: ignore[arg-type]
        )
    )

    checks = snapshot.check_map()
    assert checks["streamlabs"].summary != "Remote token is missing."


def test_setup_diagnostics_reports_overlay_start_failure_from_runtime_status() -> None:
    config = AppConfig()
    config.set_plugin_settings(
        "music",
        {
            "overlay": {"host": "127.0.0.1", "port": 18181, "enabled": True},
        },
    )

    service = SetupDiagnosticsService()
    snapshot = asyncio.run(
        service.build_snapshot(
            config,
            _FakeCredentialStore(),
            _FakeOutputService(connected=False, simulated=False),  # type: ignore[arg-type]
            _FakeOutputService(connected=False, simulated=False),  # type: ignore[arg-type]
            _FakeChatService(connected=False, simulated=False),  # type: ignore[arg-type]
            OverlayServerStatus(
                enabled=True,
                running=False,
                url="http://127.0.0.1:18181/overlay/now-playing",
                last_error="[Errno 48] Address already in use",
            ),
        )
    )

    checks = snapshot.check_map()
    assert checks["overlay"].status == "attention"
    assert checks["overlay"].summary == "The overlay server failed to start."
    assert "Address already in use" in checks["overlay"].detail


def test_setup_diagnostics_reports_unavailable_output_services() -> None:
    config = AppConfig()
    config.set_plugin_settings(
        "integrations",
        {
            "obs": {"host": "127.0.0.1", "port": 4455, "password": "", "auto_connect": True},
            "streamlabs": {"host": "127.0.0.1", "port": 59650, "token": "", "auto_connect": False},
        },
    )
    config.set_plugin_settings(
        "broadcast",
        {
            "output_target": "obs",
        },
    )
    config.set_plugin_settings(
        "music",
        {
            "overlay": {"host": "127.0.0.1", "port": 18181, "enabled": False},
        },
    )

    service = SetupDiagnosticsService()
    snapshot = asyncio.run(
        service.build_snapshot(
            config,
            _FakeCredentialStore(),
            None,
            None,
            _FakeChatService(connected=False, simulated=False),  # type: ignore[arg-type]
        )
    )

    checks = snapshot.check_map()
    assert checks["output"].status == "attention"
    assert "unavailable" in checks["output"].summary.lower()
    assert checks["obs"].status == "attention"
    assert "did not finish loading" in checks["obs"].detail
