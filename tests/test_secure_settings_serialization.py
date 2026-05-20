from stream_control.core.models import ObsSettings, StreamlabsSettings
from stream_control.plugins.chat.plugin import ChatPluginConfig
from stream_control.plugins.integrations.plugin import IntegrationsPluginConfig
from stream_control.services.twitch_chat_service import TwitchChatSettings


def test_integrations_config_omits_secrets_from_persisted_settings() -> None:
    config = IntegrationsPluginConfig(
        obs=ObsSettings(password="obs-secret"),
        streamlabs=StreamlabsSettings(token="streamlabs-secret"),
    )

    payload = config.to_dict()

    assert "password" not in payload["obs"]
    assert "token" not in payload["streamlabs"]


def test_chat_config_omits_access_token_from_persisted_settings() -> None:
    config = ChatPluginConfig(
        twitch=TwitchChatSettings(
            channel="streamcontrol",
            client_id="client-id",
            access_token="chat-secret",
        )
    )

    payload = config.to_dict()

    assert payload["twitch"]["client_id"] == "client-id"
    assert "access_token" not in payload["twitch"]
