from keyring.errors import KeyringError

from stream_control.core import credentials as credentials_module
from stream_control.core.credentials import BROADCAST_TWITCH_ACCESS_TOKEN, CredentialStore, STREAMLABS_TOKEN


def _wire_memory_keyring(monkeypatch):
    secrets: dict[tuple[str, str], str] = {}

    def get_password(service_name: str, username: str) -> str | None:
        return secrets.get((service_name, username))

    def set_password(service_name: str, username: str, value: str) -> None:
        secrets[(service_name, username)] = value

    def delete_password(service_name: str, username: str) -> None:
        secrets.pop((service_name, username), None)

    monkeypatch.setattr(credentials_module.keyring, "get_password", get_password)
    monkeypatch.setattr(credentials_module.keyring, "set_password", set_password)
    monkeypatch.setattr(credentials_module.keyring, "delete_password", delete_password)
    return secrets


def test_credential_store_migrates_legacy_secret_into_secure_storage(monkeypatch) -> None:
    secrets = _wire_memory_keyring(monkeypatch)
    store = CredentialStore("Stream Control Tests")

    result = store.load_or_migrate(STREAMLABS_TOKEN, "legacy-streamlabs-token")

    assert result.value == "legacy-streamlabs-token"
    assert result.should_persist_in_config is False
    assert secrets[("Stream Control Tests", STREAMLABS_TOKEN.username)] == "legacy-streamlabs-token"


def test_credential_store_requests_config_fallback_when_secure_storage_fails(monkeypatch) -> None:
    monkeypatch.setattr(credentials_module.keyring, "get_password", lambda *_args: None)
    monkeypatch.setattr(
        credentials_module.keyring,
        "set_password",
        lambda *_args: (_ for _ in ()).throw(KeyringError("backend unavailable")),
    )
    monkeypatch.setattr(credentials_module.keyring, "delete_password", lambda *_args: None)
    store = CredentialStore("Stream Control Tests")

    result = store.load_or_migrate(BROADCAST_TWITCH_ACCESS_TOKEN, "legacy-broadcast-token")

    assert result.value == "legacy-broadcast-token"
    assert result.should_persist_in_config is True
