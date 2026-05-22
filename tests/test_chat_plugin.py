import asyncio

from PySide6.QtWidgets import QApplication, QWidget

from stream_control.core.models import AppConfig
from stream_control.core.paths import AppPaths
from stream_control.plugins.chat import plugin as chat_plugin_module
from stream_control.plugins.chat.plugin import ChatPlugin
from stream_control.plugins.context import PluginContext
from stream_control.services.twitch_auth_service import (
    TwitchDeviceAuthorization,
    TwitchTokenBundle,
    TwitchTokenValidation,
)


def test_chat_plugin_connect_uses_live_form_values_and_shows_progress(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    context = PluginContext(
        app_config=AppConfig(),
        app_paths=AppPaths.build(),
        qt_parent=QWidget(),
        save_callback=lambda: None,
    )
    plugin = ChatPlugin()
    plugin.activate(context)

    assert plugin._page is not None
    assert plugin.chat_service is not None

    plugin._page.channel_name.setText("Adeptus_Craftmatica")
    plugin._page.client_id.setText("client-id")
    plugin._page.access_token.setText("token-value")

    seen: dict[str, str] = {}

    async def _fake_connect(settings) -> None:
        seen["channel"] = settings.channel
        seen["client_id"] = settings.client_id
        seen["access_token"] = settings.access_token

    monkeypatch.setattr(plugin.chat_service, "connect", _fake_connect)

    async def scenario() -> None:
        plugin._page._request_connect_now()
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert seen == {
        "channel": "Adeptus_Craftmatica",
        "client_id": "client-id",
        "access_token": "token-value",
    }
    assert "Connecting to Twitch chat for #Adeptus_Craftmatica" in plugin._page.connection_status.text()
    assert app is not None


def test_chat_plugin_authorizes_with_twitch_and_populates_fields(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    context = PluginContext(
        app_config=AppConfig(),
        app_paths=AppPaths.build(),
        qt_parent=QWidget(),
        save_callback=lambda: None,
    )
    plugin = ChatPlugin()
    plugin.activate(context)

    assert plugin._page is not None
    assert plugin.auth_service is not None

    plugin._page.client_id.setText("client-id")
    plugin._page.channel_name.setText("")

    opened: list[str] = []
    monkeypatch.setattr(context.credential_store, "store_secret", lambda *_args: True)
    monkeypatch.setattr(
        chat_plugin_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )

    async def _fake_start(client_id: str, scopes: list[str]) -> TwitchDeviceAuthorization:
        assert client_id == "client-id"
        assert "user:read:chat" in scopes
        return TwitchDeviceAuthorization(
            device_code="device-code",
            user_code="ABCD1234",
            verification_uri="https://www.twitch.tv/activate?device-code=ABCD1234",
            expires_in=1800,
            interval=5,
            requested_scopes=list(scopes),
        )

    async def _fake_poll(client_id: str, authorization: TwitchDeviceAuthorization) -> TwitchTokenBundle:
        assert client_id == "client-id"
        assert authorization.user_code == "ABCD1234"
        return TwitchTokenBundle(
            access_token="token-value",
            refresh_token="refresh-value",
            scopes=["user:read:chat", "user:write:chat"],
            token_type="bearer",
            expires_in=14400,
        )

    async def _fake_validate(access_token: str) -> TwitchTokenValidation:
        assert access_token == "token-value"
        return TwitchTokenValidation(
            client_id="client-id",
            login="adeptus_craftmatica",
            user_id="42",
            scopes=["user:read:chat", "user:write:chat"],
            expires_in=3600,
        )

    monkeypatch.setattr(plugin.auth_service, "start_device_authorization", _fake_start)
    monkeypatch.setattr(plugin.auth_service, "poll_device_authorization", _fake_poll)
    monkeypatch.setattr(plugin.auth_service, "validate_access_token", _fake_validate)

    asyncio.run(plugin._authorize_twitch())

    assert opened == ["https://www.twitch.tv/activate?device-code=ABCD1234"]
    assert plugin._settings.twitch.access_token == "token-value"
    assert plugin._settings.twitch.channel == "adeptus_craftmatica"
    assert plugin._settings.twitch.broadcaster_id == "42"
    assert plugin._page.access_token.text() == "token-value"
    assert plugin._page.channel_name.text() == "adeptus_craftmatica"
    assert "Token saved securely" in plugin._page.connection_status.text()
    assert app is not None
