import asyncio

from stream_control.core.models import AppConfig
from stream_control.services.setup_diagnostics_service import SetupDiagnosticsService


class _FakeOutputService:
    def __init__(self, *, connected: bool = False, simulated: bool = False) -> None:
        self.is_connected = connected
        self.is_simulated = simulated


class _FakeChatService(_FakeOutputService):
    pass


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
