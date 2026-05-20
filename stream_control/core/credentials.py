from __future__ import annotations

import logging
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CredentialRef:
    namespace: str
    field: str

    @property
    def username(self) -> str:
        return f"{self.namespace}:{self.field}"


@dataclass(frozen=True, slots=True)
class SecretLoadResult:
    value: str
    should_persist_in_config: bool = False


OBS_PASSWORD = CredentialRef("integrations.obs", "password")
STREAMLABS_TOKEN = CredentialRef("integrations.streamlabs", "token")
BROADCAST_TWITCH_ACCESS_TOKEN = CredentialRef("broadcast.twitch", "access_token")
CHAT_TWITCH_ACCESS_TOKEN = CredentialRef("chat.twitch", "access_token")


class CredentialStore:
    def __init__(self, service_name: str = "Stream Control") -> None:
        self._service_name = service_name

    def get_secret(self, reference: CredentialRef) -> str:
        try:
            return keyring.get_password(self._service_name, reference.username) or ""
        except KeyringError as exc:
            logger.warning("Could not read %s from secure storage: %s", reference.username, exc)
            return ""

    def has_secret(self, reference: CredentialRef) -> bool:
        return bool(self.get_secret(reference))

    def load_or_migrate(self, reference: CredentialRef, legacy_value: str) -> SecretLoadResult:
        stored = self.get_secret(reference)
        if stored:
            return SecretLoadResult(stored, should_persist_in_config=False)
        if not legacy_value:
            return SecretLoadResult("", should_persist_in_config=False)
        if self.store_secret(reference, legacy_value):
            return SecretLoadResult(legacy_value, should_persist_in_config=False)
        return SecretLoadResult(legacy_value, should_persist_in_config=True)

    def store_secret(self, reference: CredentialRef, value: str) -> bool:
        if not value:
            return self.delete_secret(reference)
        try:
            keyring.set_password(self._service_name, reference.username, value)
            return True
        except KeyringError as exc:
            logger.warning("Could not store %s in secure storage: %s", reference.username, exc)
            return False

    def delete_secret(self, reference: CredentialRef) -> bool:
        try:
            keyring.delete_password(self._service_name, reference.username)
            return True
        except PasswordDeleteError:
            return True
        except KeyringError as exc:
            logger.warning("Could not delete %s from secure storage: %s", reference.username, exc)
            return False
