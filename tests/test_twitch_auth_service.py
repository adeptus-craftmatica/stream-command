import asyncio
import io
import json
from urllib import error

from stream_control.services import twitch_auth_service as twitch_auth_service_module
from stream_control.services.twitch_auth_service import (
    TwitchAuthError,
    TwitchAuthService,
    TwitchDeviceAuthorization,
    TwitchTokenBundle,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def test_twitch_auth_service_starts_device_authorization(monkeypatch) -> None:
    service = TwitchAuthService()
    seen: dict[str, object] = {}

    def _fake_urlopen(req, *, timeout, context):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["body"] = req.data.decode("utf-8")
        seen["context"] = context
        return _Response(
            {
                "device_code": "device-code",
                "user_code": "ABCD1234",
                "verification_uri": "https://www.twitch.tv/activate?device-code=ABCD1234",
                "expires_in": 1800,
                "interval": 5,
            }
        )

    monkeypatch.setattr(twitch_auth_service_module.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(twitch_auth_service_module, "tls_context", lambda: object())

    authorization = asyncio.run(
        service.start_device_authorization("client-id", ["user:read:chat", "user:write:chat"])
    )

    assert authorization.device_code == "device-code"
    assert authorization.user_code == "ABCD1234"
    assert authorization.requested_scopes == ["user:read:chat", "user:write:chat"]
    assert seen["url"] == TwitchAuthService.DEVICE_CODE_URL
    assert seen["timeout"] == 10
    assert "client_id=client-id" in str(seen["body"])
    assert "scopes=user%3Aread%3Achat+user%3Awrite%3Achat" in str(seen["body"])


def test_twitch_auth_service_polls_until_authorized(monkeypatch) -> None:
    service = TwitchAuthService()
    attempts = {"count": 0}
    sleeps: list[int] = []

    def _fake_exchange(client_id: str, device_code: str) -> TwitchTokenBundle:
        attempts["count"] += 1
        assert client_id == "client-id"
        assert device_code == "device-code"
        if attempts["count"] == 1:
            raise TwitchAuthError("Twitch authorization is still pending.")
        return TwitchTokenBundle(
            access_token="token-value",
            refresh_token="refresh-value",
            scopes=["user:read:chat"],
            token_type="bearer",
            expires_in=14400,
        )

    async def _fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(service, "_exchange_device_code_sync", _fake_exchange)
    monkeypatch.setattr(twitch_auth_service_module.asyncio, "sleep", _fake_sleep)

    token = asyncio.run(
        service.poll_device_authorization(
            "client-id",
            TwitchDeviceAuthorization(
                device_code="device-code",
                user_code="ABCD1234",
                verification_uri="https://www.twitch.tv/activate",
                expires_in=1800,
                interval=5,
                requested_scopes=["user:read:chat"],
            ),
        )
    )

    assert token.access_token == "token-value"
    assert attempts["count"] == 2
    assert sleeps == [5]


def test_twitch_auth_service_validates_access_tokens(monkeypatch) -> None:
    service = TwitchAuthService()

    def _fake_urlopen(req, *, timeout, context):
        assert req.full_url == TwitchAuthService.VALIDATE_URL
        assert req.headers["Authorization"] == "OAuth token-value"
        assert timeout == 10
        assert context == "tls-context"
        return _Response(
            {
                "client_id": "client-id",
                "login": "adeptus_craftmatica",
                "user_id": "42",
                "scopes": ["user:read:chat", "user:write:chat"],
                "expires_in": 3600,
            }
        )

    monkeypatch.setattr(twitch_auth_service_module.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(twitch_auth_service_module, "tls_context", lambda: "tls-context")

    validation = asyncio.run(service.validate_access_token("token-value"))

    assert validation.client_id == "client-id"
    assert validation.login == "adeptus_craftmatica"
    assert validation.user_id == "42"
    assert validation.scopes == ["user:read:chat", "user:write:chat"]


def test_twitch_auth_service_surfaces_denied_authorization(monkeypatch) -> None:
    service = TwitchAuthService()

    def _fake_urlopen(req, *, timeout, context):
        payload = io.BytesIO(b'{"error":"access_denied"}')
        raise error.HTTPError(req.full_url, 400, "bad request", hdrs=None, fp=payload)

    monkeypatch.setattr(twitch_auth_service_module.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(twitch_auth_service_module, "tls_context", lambda: object())

    try:
        asyncio.run(service.start_device_authorization("client-id", ["user:read:chat"]))
    except TwitchAuthError as exc:
        assert "denied" in str(exc).lower()
    else:
        raise AssertionError("Expected TwitchAuthError for denied authorization")
